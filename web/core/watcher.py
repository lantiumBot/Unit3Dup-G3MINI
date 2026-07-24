"""WatcherService: unit3dup -watcher subprocess with live console streaming."""
import os
import pty
import queue
import re
import select
import signal
import subprocess
import threading
from datetime import datetime
from pathlib import Path

from core.conf import load_unit3dbot
from core.stream import ConsoleStream, strip_ansi
from extensions import socketio

_WATCHER_LINE_RE = {
    "processing": re.compile(r"\[Watcher\]\s*Processing\s*->\s*(.+)", re.I),
    "moved":      re.compile(r"\[Watcher\]\s*Moved to destination\s*->\s*(.+)", re.I),
    "failed":     re.compile(r"\[Watcher\]\s*Upload failed", re.I),
    "watchdog":   re.compile(r"WATCHDOG:\s*([\d.]+)\s*seconds", re.I),
    "empty":      re.compile(r"no files in the Watcher folder", re.I),
    "no_path":    re.compile(r"Watcher path does not exist", re.I),
}


def _cfg_path(val) -> Path | None:
    if val is None or val == "":
        return None
    return Path(str(val)).expanduser()


class WatcherService:
    """Watches a folder via unit3dup -watcher; streams state + console live."""

    def __init__(self):
        self._lock = threading.Lock()
        self._console = ConsoleStream()
        self._proc = None
        self._q: queue.Queue = queue.Queue()
        self._running = False
        self._started_at: str | None = None
        self._error: str | None = None
        self._phase = "stopped"
        self._current: dict | None = None
        self._last: dict | None = None
        self._watchdog_sec: float | None = None
        self._events: list[dict] = []
        self._paths: dict = {}
        self._refresh_timer: threading.Timer | None = None

    def _event(self, kind: str, **extra) -> dict:
        ev = {"ts": datetime.now().isoformat(), "kind": kind, **extra}
        with self._lock:
            self._events.append(ev)
            if len(self._events) > 100:
                self._events = self._events[-100:]
        return ev

    def _set_phase(self, phase: str):
        with self._lock:
            self._phase = phase
        self._emit_state()

    def _emit_state(self):
        socketio.emit("watcher_state", self.get_status())

    def _push_console(self, text: str):
        for ev in self._console.feed(strip_ansi(text)):
            socketio.emit("watcher_output", ev)
        for line in text.splitlines():
            self._parse_line(line)

    def _parse_line(self, line: str):
        s = strip_ansi(line).strip()
        if not s:
            return
        m = _WATCHER_LINE_RE["processing"].search(s)
        if m:
            path = m.group(1).strip()
            with self._lock:
                self._current = {
                    "name": Path(path).name, "path": path,
                    "started_at": datetime.now().isoformat(),
                }
                self._phase = "processing"
            self._event("processing", path=path, name=Path(path).name)
            self._emit_state()
            return
        m = _WATCHER_LINE_RE["moved"].search(s)
        if m:
            dest = m.group(1).strip()
            with self._lock:
                self._last = {
                    "kind": "moved", "path": dest,
                    "name": Path(dest).name, "at": datetime.now().isoformat(),
                }
                self._current = None
                self._phase = "idle"
            self._event("moved", path=dest, name=Path(dest).name)
            self._refresh_queue()
            self._emit_state()
            return
        if _WATCHER_LINE_RE["failed"].search(s):
            with self._lock:
                cur = dict(self._current) if self._current else {}
                self._last = {**cur, "kind": "failed", "at": datetime.now().isoformat()}
                self._current = None
                self._phase = "idle"
            self._event("failed", **cur)
            self._refresh_queue()
            self._emit_state()
            return
        m = _WATCHER_LINE_RE["watchdog"].search(s)
        if m:
            sec = float(m.group(1))
            with self._lock:
                self._watchdog_sec = sec
                if self._phase != "processing":
                    self._phase = "watchdog"
            self._event("watchdog", remaining_sec=sec)
            self._emit_state()
            return
        if _WATCHER_LINE_RE["empty"].search(s):
            self._set_phase("idle")
            self._event("queue_empty")
            self._refresh_queue()
            return
        if _WATCHER_LINE_RE["no_path"].search(s):
            self._error = "watcher_path_missing"
            self._event("error", message="watcher_path_missing")
            self._emit_state()

    def _watcher_prefs(self) -> dict:
        prefs = load_unit3dbot().get("USER_PREFERENCES", {}) or {}
        wp = _cfg_path(prefs.get("WATCHER_PATH"))
        dp = _cfg_path(prefs.get("WATCHER_DESTINATION_PATH"))
        try:
            interval = int(prefs.get("WATCHER_INTERVAL") or 60)
        except (TypeError, ValueError):
            interval = 60
        return {
            "watcher_path":       str(wp) if wp else "",
            "destination_path":   str(dp) if dp else "",
            "interval_sec":       max(10, interval),
            "watcher_exists":     bool(wp and wp.is_dir()),
            "destination_exists": bool(dp and dp.is_dir()),
        }

    def _list_queue(self, root: Path | None) -> list[dict]:
        if not root or not root.is_dir():
            return []
        items = []
        try:
            entries = sorted(
                [p for p in root.iterdir() if p.name and not p.name.startswith(".")],
                key=lambda p: p.name.lower(),
            )
        except OSError:
            return []
        for p in entries:
            try:
                size = p.stat().st_size if p.is_file() else None
            except OSError:
                size = None
            from core.conf import _human
            items.append({
                "name":       p.name,
                "path":       str(p.resolve()),
                "type":       "folder" if p.is_dir() else "file",
                "size_bytes": size,
                "size_human": _human(size) if size is not None else None,
            })
        return items

    def _refresh_queue(self):
        with self._lock:
            wp = _cfg_path(self._paths.get("watcher_path"))
        if wp:
            q = self._list_queue(wp)
            with self._lock:
                self._paths["queue"]       = q
                self._paths["queue_count"] = len(q)
        self._emit_state()

    def _schedule_refresh(self):
        if self._refresh_timer:
            self._refresh_timer.cancel()
        self._refresh_timer = threading.Timer(8.0, self._periodic_refresh)
        self._refresh_timer.daemon = True
        self._refresh_timer.start()

    def _periodic_refresh(self):
        if not self._running:
            return
        self._refresh_queue()
        self._schedule_refresh()

    def get_status(self) -> dict:
        prefs = self._watcher_prefs()
        wp    = _cfg_path(prefs.get("watcher_path"))
        queue_list = self._list_queue(wp) if wp else []
        with self._lock:
            paths = dict(self._paths)
            if self._running:
                queue_list = list(paths.get("queue", queue_list))
            return {
                "running":                self._running,
                "phase":                  self._phase,
                "started_at":             self._started_at,
                "error":                  self._error,
                "config": {
                    "watcher_path":       prefs.get("watcher_path", ""),
                    "destination_path":   prefs.get("destination_path", ""),
                    "interval_sec":       prefs.get("interval_sec", 60),
                    "watcher_exists":     prefs.get("watcher_exists", False),
                    "destination_exists": prefs.get("destination_exists", False),
                },
                "queue":                  queue_list,
                "queue_count":            len(queue_list),
                "current":                self._current,
                "last":                   self._last,
                "watchdog_remaining_sec": self._watchdog_sec,
                "events":                 list(self._events[-30:]),
            }

    def send_stdin(self, text: str):
        if self._running:
            self._q.put(text)

    def start(self) -> tuple[bool, str | None]:
        from core.conf import VENV_BIN
        with self._lock:
            if self._running:
                return False, "already_running"
        prefs = self._watcher_prefs()
        wp_s, dp_s = prefs["watcher_path"], prefs["destination_path"]
        if not wp_s:
            return False, "watcher_path_not_configured"
        wp = Path(wp_s)
        if not wp.is_dir():
            return False, "watcher_path_missing"

        bin_path = str(VENV_BIN) if VENV_BIN.exists() else "unit3dup"
        cmd = [bin_path, "-watcher", wp_s, dp_s] if dp_s else [bin_path, "-watcher"]

        with self._lock:
            self._running    = True
            self._started_at = datetime.now().isoformat()
            self._error      = None
            self._phase      = "starting"
            self._current    = None
            self._last       = None
            self._watchdog_sec = None
            self._events     = []
            self._paths      = {**prefs, "queue": [], "queue_count": 0}
            self._console    = ConsoleStream()

        self._refresh_queue()
        self._event("started", command=" ".join(cmd))
        self._emit_state()
        threading.Thread(target=self._run, args=(cmd,), daemon=True, name="Watcher").start()
        self._schedule_refresh()
        return True, None

    def stop(self):
        with self._lock:
            if not self._running:
                return
            proc = self._proc
        if proc and proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except Exception:
                try:
                    proc.terminate()
                except Exception:
                    pass
        self._finish(stopped=True)

    def _finish(self, *, stopped: bool = False, exit_code: int | None = None):
        if self._refresh_timer:
            self._refresh_timer.cancel()
            self._refresh_timer = None
        with self._lock:
            self._running = False
            self._proc    = None
            self._phase   = "stopped"
            self._current = None
            while True:
                try:
                    self._q.get_nowait()
                except queue.Empty:
                    break
        if stopped:
            self._event("stopped")
        elif exit_code is not None:
            self._event("exited", code=exit_code)
        self._emit_state()

    def _run(self, cmd: list):
        self._push_console(f"\n▶ {' '.join(cmd)}\n{'─'*60}\n")
        self._set_phase("running")
        try:
            master, slave = pty.openpty()
        except Exception as exc:
            self._push_console(f"[PTY ERROR] {exc}\n")
            with self._lock:
                self._error = str(exc)
            self._finish()
            return
        try:
            proc = subprocess.Popen(
                cmd, stdin=slave, stdout=slave, stderr=slave,
                close_fds=True, preexec_fn=os.setsid,
                env={**os.environ, "TERM": "xterm-256color", "FORCE_COLOR": "0"},
            )
            os.close(slave)
            with self._lock:
                self._proc = proc
            while True:
                poll = proc.poll()
                try:
                    r, _, _ = select.select([master], [], [], 0.05)
                except (OSError, ValueError):
                    break
                if r:
                    try:
                        chunk = os.read(master, 8192)
                        self._push_console(chunk.decode("utf-8", errors="replace"))
                    except OSError:
                        break
                try:
                    inp = self._q.get_nowait()
                    os.write(master, (inp + "\n").encode())
                    self._push_console(f"\n> {inp}\n")
                except queue.Empty:
                    pass
                if poll is not None:
                    for _ in range(30):
                        try:
                            r2, _, _ = select.select([master], [], [], 0.1)
                        except (OSError, ValueError):
                            break
                        if not r2:
                            break
                        try:
                            d = os.read(master, 8192)
                            if d:
                                self._push_console(d.decode("utf-8", errors="replace"))
                        except OSError:
                            break
                    break
            rc = proc.wait()
            self._push_console(f"\n[EXIT {rc}]\n")
            with self._lock:
                if rc != 0:
                    self._error = f"exit_{rc}"
            self._finish(exit_code=rc)
        except Exception as exc:
            self._push_console(f"[ERREUR] {exc}\n")
            with self._lock:
                self._error = str(exc)[:200]
            self._finish()
        finally:
            try:
                os.close(master)
            except OSError:
                pass


_watcher = WatcherService()
