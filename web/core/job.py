"""Job class, JobResultCollector, background scheduler thread."""
import os
import pty
import queue
import re
import select
import signal
import subprocess
import threading
import time as _time
from datetime import datetime

from core.conf import (
    VENV_BIN, add_to_history, save_job_transcript, remove_from_queue_state,
)
from core.stream import ConsoleStream, strip_ansi, _PROGRESS_RE
from extensions import socketio
from shared import _jobs, _jobs_lock, _job_queue, _queue_cv

# ── Output parsing regexes ────────────────────────────────────────────────────
_JOB_LINE_RE = {
    "command":        re.compile(r"^▶\s+(.+)$"),
    "response":       re.compile(r"\[RESPONSE\]->\s*'([^']+)'\.*\.*\.*\.*(.+)", re.I),
    "display_name":   re.compile(r"'DISPLAYNAME'\.\.\.\{([^}]+)\}", re.I),
    "tmdb_keywords":  re.compile(r"'TMDB KEYWORDS'\.\.\s*(.+)", re.I),
    "tmdb_id":        re.compile(r"TMDB[- ]ID\s+(\d+)", re.I),
    "your_file":      re.compile(r"Your file - size:\s*'([^']+)'", re.I),
    "tracker_match":  re.compile(r"Tracker - size:", re.I),
    "size_th":        re.compile(r"Size_TH:\s*([\d.]+)\s*%", re.I),
    "exit":           re.compile(r"\[EXIT\s+(\d+)\]"),
    "upload_ok":      re.compile(r"Upload confirmé", re.I),
    "upload_cancel":  re.compile(r"Upload annulé", re.I),
    "watcher_skip":   re.compile(r"Watcher Active\.\.\s*skip", re.I),
    "watcher_path":   re.compile(r"\[Watcher\]\s+'([^']+)'"),
    "nfo":            re.compile(r"\[NFO\]\s*(.+)"),
    "tracker_done":   re.compile(r"Tracker\s+'([^']+)'\s+Done", re.I),
    "error_response": re.compile(r"\[RESPONSE\]->\s*'([^']+)'", re.I),
}


class JobResultCollector:
    """Builds structured JSON from unit3dup console output."""

    def __init__(self, item: dict):
        self._events: list[dict] = []
        self._commands: list[str] = []
        self._exit_codes: list[int] = []
        self._stdin: list[str] = []
        self.parsed: dict = {
            "display_name":       None,
            "tmdb_keywords":      None,
            "tmdb_id":            None,
            "tracker":            None,
            "tracker_message":    None,
            "your_file_size":     None,
            "size_th_pct":        None,
            "duplicate_scan":     item.get("duplicate"),
            "watcher_destination": None,
            "upload_confirmed":   None,
            "tracker_matches":    [],
            "nfo":                [],
            "errors":             [],
        }

    def _event(self, kind: str, **kw) -> dict:
        ev = {"ts": datetime.now().isoformat(), "kind": kind, **kw}
        self._events.append(ev)
        if len(self._events) > 300:
            self._events = self._events[-300:]
        return ev

    def add_command(self, cmd: list):
        line = " ".join(cmd)
        self._commands.append(line)
        self._event("command", command=line)

    def record_stdin(self, text: str):
        self._stdin.append(text)
        self._event("stdin", text=text)
        if re.fullmatch(r"\d+", text.strip()):
            self.parsed["tmdb_id"] = int(text.strip())

    def set_exit(self, code: int):
        self._exit_codes.append(code)
        self._event("exit", code=code)

    def feed_text(self, text: str):
        for raw in text.splitlines():
            line = strip_ansi(raw).strip()
            if not line or _PROGRESS_RE.search(line):
                continue
            self._parse_line(line)

    def _parse_line(self, line: str):
        m = _JOB_LINE_RE["command"].match(line)
        if m:
            cmd = m.group(1).strip()
            if cmd and cmd not in self._commands:
                self._commands.append(cmd)
            self._event("command_echo", command=cmd)
            return
        m = _JOB_LINE_RE["display_name"].search(line)
        if m:
            self.parsed["display_name"] = m.group(1).strip()
            self._event("display_name", value=self.parsed["display_name"])
            return
        m = _JOB_LINE_RE["tmdb_keywords"].search(line)
        if m:
            kw = m.group(1).strip().rstrip("'").strip()
            self.parsed["tmdb_keywords"] = kw
            self._event("tmdb_keywords", keywords=kw)
            return
        m = _JOB_LINE_RE["tmdb_id"].search(line)
        if m:
            self.parsed["tmdb_id"] = int(m.group(1))
            self._event("tmdb_id", id=self.parsed["tmdb_id"])
            return
        m = _JOB_LINE_RE["response"].search(line)
        if m:
            self.parsed["tracker"] = m.group(1).strip()
            self.parsed["tracker_message"] = m.group(2).strip()
            self._event("tracker_response", tracker=self.parsed["tracker"],
                        message=self.parsed["tracker_message"])
            return
        if _JOB_LINE_RE["tracker_match"].search(line):
            self.parsed["tracker_matches"].append(line)
            self._event("duplicate_match", line=line)
            return
        m = _JOB_LINE_RE["your_file"].search(line)
        if m:
            self.parsed["your_file_size"] = m.group(1).strip()
            self._event("your_file", size=self.parsed["your_file_size"])
            return
        m = _JOB_LINE_RE["size_th"].search(line)
        if m:
            self.parsed["size_th_pct"] = float(m.group(1))
            self._event("size_th", pct=self.parsed["size_th_pct"])
            return
        m = _JOB_LINE_RE["exit"].search(line)
        if m:
            self.set_exit(int(m.group(1)))
            return
        if _JOB_LINE_RE["upload_ok"].search(line):
            self.parsed["upload_confirmed"] = True
            self._event("upload_confirmed")
            return
        if _JOB_LINE_RE["upload_cancel"].search(line):
            self.parsed["upload_confirmed"] = False
            self._event("upload_cancelled")
            return
        m = _JOB_LINE_RE["watcher_path"].search(line)
        if m:
            self.parsed["watcher_destination"] = m.group(1).strip()
            self._event("watcher_path", path=self.parsed["watcher_destination"])
            return
        if _JOB_LINE_RE["watcher_skip"].search(line):
            self._event("watcher_skip", line=line)
            return
        m = _JOB_LINE_RE["nfo"].search(line)
        if m:
            msg = m.group(1).strip()
            self.parsed["nfo"].append(msg)
            self._event("nfo", message=msg)
            return
        m = _JOB_LINE_RE["tracker_done"].search(line)
        if m:
            self._event("tracker_done", tracker=m.group(1).strip())
            return
        if _JOB_LINE_RE["error_response"].search(line) and "error" in line.lower():
            self.parsed["errors"].append(line)
            self._event("error", message=line)
            return
        if "UploadBot" in line and (
            "obligatoire" in line or "required" in line.lower() or "error" in line.lower()
        ):
            self.parsed["errors"].append(line)
            self._event("tracker_error", message=line)

    def tracker_upload_failed(self) -> bool:
        if self.parsed.get("errors"):
            return True
        tracker = (self.parsed.get("tracker") or "").lower()
        msg = (self.parsed.get("tracker_message") or "").lower()
        if "uploadbot" in tracker:
            return True
        if any(x in msg for x in ("obligatoire", "required", "invalid", "erreur", "error")):
            return True
        return False

    def to_dict(self, *, status: str, started_at: str | None, ended_at: str | None) -> dict:
        return {
            "status":     status,
            "started_at": started_at,
            "ended_at":   ended_at,
            "commands":   list(self._commands),
            "exit_codes": list(self._exit_codes),
            "stdin":      list(self._stdin),
            "parsed":     dict(self.parsed),
            "events":     list(self._events),
        }


def _parse_transcript_to_result(transcript: str, meta: dict) -> dict:
    item = {
        "id":   meta.get("job_id"),
        "name": meta.get("name"),
        "path": meta.get("path"),
        "type": meta.get("type"),
        "tag":  meta.get("tag"),
    }
    col = JobResultCollector(item)
    col.feed_text(transcript)
    return col.to_dict(
        status=meta.get("status", "unknown"),
        started_at=meta.get("started_at"),
        ended_at=meta.get("ended_at"),
    )


# ── Job ───────────────────────────────────────────────────────────────────────
class Job:
    _TERMINAL = frozenset({"done", "error", "cancelled"})

    def __init__(self, item: dict, unit3dup: str, confirm: bool):
        self.id       = item["id"]
        self.item     = item
        self._bin     = unit3dup
        self._confirm = confirm
        self.status   = "pending"
        self._console = ConsoleStream()
        self.lines: list[str] = []
        self._q: queue.Queue = queue.Queue()
        self._proc    = None
        self.started_at: str | None = None
        self.ended_at: str | None   = None
        self._result  = JobResultCollector(item)

    def result_json(self) -> dict:
        return self._result.to_dict(
            status=self.status,
            started_at=self.started_at,
            ended_at=self.ended_at,
        )

    def to_dict(self, *, include_lines: bool = True) -> dict:
        d = {
            "id":          self.id,
            "name":        self.item["name"],
            "path":        self.item["path"],
            "type":        self.item["type"],
            "tag":         self.item.get("tag", ""),
            "tag_valid":   self.item.get("tag_valid", False),
            "seasons":     len(self.item.get("seasons", [])),
            "status":      self.status,
            "started_at":  self.started_at,
            "ended_at":    self.ended_at,
            "retry_count": self.item.get("_retry_count", 0),
            "tmdb_id":     self.item.get("tmdb_id", 0) or 0,
        }
        if include_lines:
            d["lines"] = self._console.display_lines()[-300:]
        return d

    def console_transcript(self) -> str:
        return self._console.transcript()

    def send_stdin(self, text: str) -> tuple[bool, str]:
        if self.status in self._TERMINAL:
            return False, "job_finished"
        if not self._proc or self._proc.poll() is not None:
            return False, "no_process"
        if text:
            self._result.record_stdin(text)
            self._q.put(text)
        return True, "ok"

    def _cleanup_session(self):
        if self._proc and self._proc.poll() is None:
            try:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
            except Exception:
                try:
                    self._proc.terminate()
                except Exception:
                    pass
        self._proc = None
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                break

    def _finalize(self):
        save_job_transcript(self)
        remove_from_queue_state(self.id)
        self._cleanup_session()
        _fire_webhook(self)

    def _push(self, text: str):
        self._result.feed_text(text)
        self._console.feed(strip_ansi(text))  # update lines/transcript, no per-event emit
        self.lines = self._console.display_lines()
        socketio.emit("job_output", {"id": self.id, "text": text})

    def _set_status(self, s: str):
        self.status = s
        socketio.emit("job_status", {"id": self.id, "status": s, "ended_at": self.ended_at})

    def _commands(self) -> list[list[str]]:
        from core.scanner import episode_upload_for_item
        extra = ["-confirm"] if self._confirm else []
        t = self.item["type"]
        if t == "file":
            return [[self._bin, "-u", self.item["path"]] + extra]
        ep = self.item.get("episode_upload") or episode_upload_for_item(self.item)
        u = ([[self._bin, "-u", ep] + extra] if ep else [])
        f = [[self._bin, "-f", self.item["path"]] + extra]
        if t == "integrale":
            cmds = list(u) + f
            for s in self.item.get("seasons", []):
                cmds.append([self._bin, "-f", s] + extra)
            return cmds
        if t == "season":
            return list(u) + f
        return f

    def _run_pty(self, cmd: list[str]) -> int:
        try:
            master, slave = pty.openpty()
        except Exception as exc:
            self._push(f"[PTY ERROR] {exc}\n")
            return 1
        try:
            env = {**os.environ, "TERM": "xterm-256color", "FORCE_COLOR": "0"}
            custom_name = (self.item.get("custom_name") or "").strip()
            # Only inject U3D_CUSTOM_RELEASE_NAME for the main -f <item_path> command.
            # Sub-commands (-u S01E01 or -f season) must NOT inherit the integrale name.
            is_main_folder_cmd = (
                len(cmd) >= 3 and cmd[1] == "-f"
                and os.path.normpath(cmd[-1]) == os.path.normpath(self.item.get("path", ""))
            )
            if custom_name and (self.item.get("type") == "file" or is_main_folder_cmd):
                env["U3D_CUSTOM_RELEASE_NAME"] = custom_name
            sub_custom_names = self.item.get("sub_custom_names") or {}
            if sub_custom_names:
                import json as _json_env
                env["U3D_CUSTOM_NAME_MAP"] = _json_env.dumps(sub_custom_names)
            tmdb_origin = (self.item.get("tmdb_origin") or "").strip()
            if tmdb_origin:
                env["U3D_TMDB_ORIGIN"] = tmdb_origin
            tmdb_id_val = self.item.get("tmdb_id") or 0
            if isinstance(tmdb_id_val, (int, float)) and int(tmdb_id_val) > 0:
                env["U3D_TMDB_ID"] = str(int(tmdb_id_val))
            proc = subprocess.Popen(
                cmd, stdin=slave, stdout=slave, stderr=slave,
                close_fds=True, preexec_fn=os.setsid,
                env=env,
            )
            os.close(slave)
            self._proc = proc
            timeout_min = _get_job_timeout_minutes()
            deadline    = (_time.time() + timeout_min * 60) if timeout_min > 0 else None
            while True:
                # ── Timeout watchdog ────────────────────────────────────────
                if deadline and _time.time() > deadline:
                    self._push(f"\n[TIMEOUT {timeout_min}min — job annulé automatiquement]\n")
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    except Exception:
                        try:
                            proc.terminate()
                        except Exception:
                            pass
                    break
                poll = proc.poll()
                try:
                    r, _, _ = select.select([master], [], [], 0.05)
                except (OSError, ValueError):
                    break
                if r:
                    try:
                        chunk = os.read(master, 8192)
                        self._push(chunk.decode("utf-8", errors="replace"))
                    except OSError:
                        break
                try:
                    inp = self._q.get_nowait()
                    os.write(master, (inp + "\n").encode())
                    # record_stdin already called in send_stdin() when queued
                except queue.Empty:
                    pass
                if poll is not None:
                    for _ in range(20):
                        try:
                            r2, _, _ = select.select([master], [], [], 0.1)
                        except (OSError, ValueError):
                            break
                        if not r2:
                            break
                        try:
                            d = os.read(master, 8192)
                            if d:
                                self._push(d.decode("utf-8", errors="replace"))
                        except OSError:
                            break
                    break
            proc.wait()
            return proc.returncode
        except Exception as exc:
            self._push(f"[ERREUR] {exc}\n")
            return 1
        finally:
            try:
                os.close(master)
            except OSError:
                pass

    def run(self):
        self.started_at = datetime.now().isoformat()
        self._set_status("running")
        for cmd in self._commands():
            self._result.add_command(cmd)
            self._push(f"\n▶ {' '.join(cmd)}\n{'─'*60}\n")
            rc = self._run_pty(cmd)
            self._result.set_exit(rc)
            if rc == 0 and self._result.tracker_upload_failed():
                self._push(
                    "\n[ERREUR TRACKER] Upload refusé par Gemini — "
                    "voir la ligne [RESPONSE] (souvent description vide si SKIP_SCREENSHOTS).\n"
                )
                rc = 1
            if rc != 0:
                self._push(f"\n[EXIT {rc}]\n")
                self.ended_at = datetime.now().isoformat()
                self._set_status("error")
                add_to_history(self)
                self._finalize()
                return
        self.ended_at = datetime.now().isoformat()
        self._set_status("done")
        add_to_history(self)
        self._finalize()

    def cancel(self):
        if self._proc and self._proc.poll() is None:
            try:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
            except Exception:
                self._proc.terminate()
        self.ended_at = datetime.now().isoformat()
        self._set_status("cancelled")
        self._finalize()


# ── Webhook ───────────────────────────────────────────────────────────────────

def _fire_webhook(job: "Job") -> None:
    """POST a JSON payload to webhook_url (if configured) after each job completes."""
    import logging as _logging
    _wlog = _logging.getLogger("core.job.webhook")
    try:
        from core.conf import load_web_config
        url = (load_web_config().get("webhook_url") or "").strip()
    except Exception:
        return
    if not url:
        return
    payload = {
        "job_id":     job.id,
        "name":       job.item.get("name", ""),
        "path":       job.item.get("path", ""),
        "type":       job.item.get("type", ""),
        "tag":        job.item.get("tag", ""),
        "status":     job.status,
        "started_at": job.started_at,
        "ended_at":   job.ended_at,
        "retry_count": job.item.get("_retry_count", 0),
    }
    try:
        import requests
        from datetime import datetime as _dt
        fmt = (load_web_config().get("webhook_format") or "raw").strip()
        if fmt == "discord":
            color = {"done": 3066993, "error": 15158332, "cancelled": 15105570}.get(job.status, 8421504)
            icon = "✅" if job.status == "done" else "❌" if job.status == "error" else "⚠️"
            discord_payload = {
                "embeds": [{
                    "title": f"{icon} {job.item.get('name', '')}",
                    "color": color,
                    "fields": [
                        {"name": "Statut",  "value": job.status,               "inline": True},
                        {"name": "Type",    "value": job.item.get("type", ""), "inline": True},
                        {"name": "Tag",     "value": job.item.get("tag", "") or "—", "inline": True},
                        {"name": "Retry",   "value": str(payload.get("retry_count", 0)), "inline": True},
                    ],
                    "timestamp": job.ended_at or _dt.now().isoformat(),
                    "footer": {"text": "Unit3Dup G3MINI"},
                }]
            }
            resp = requests.post(url, json=discord_payload, timeout=10)
        else:
            resp = requests.post(url, json=payload, timeout=10)
        _wlog.info("Webhook envoyé → %s  HTTP %s", url, resp.status_code)
    except Exception as exc:
        _wlog.warning("Webhook échoué (%s) : %s", url, exc)


# ── Scheduler ─────────────────────────────────────────────────────────────────
_running_count = 0  # protected by _queue_cv

# TTL cache for max_concurrent_jobs — avoids a disk read on every scheduler tick
_max_concurrent_cache: dict = {"value": 1, "ts": 0.0}
_MAX_CONCURRENT_TTL = 5.0  # seconds

# TTL cache for job_timeout_minutes
_timeout_cache: dict = {"value": 0, "ts": 0.0}
_TIMEOUT_TTL = 30.0  # seconds


def _get_job_timeout_minutes() -> int:
    """Return configured job timeout (minutes); 0 = disabled. Cached 30 s."""
    now = _time.time()
    if now - _timeout_cache["ts"] < _TIMEOUT_TTL:
        return _timeout_cache["value"]
    try:
        from core.conf import load_web_config
        v = max(0, int(load_web_config().get("job_timeout_minutes", 0)))
    except (TypeError, ValueError):
        v = 0
    _timeout_cache["value"] = v
    _timeout_cache["ts"]    = now
    return v


def _max_concurrent() -> int:
    import time as _t
    from core.conf import load_web_config
    now = _t.time()
    if now - _max_concurrent_cache["ts"] < _MAX_CONCURRENT_TTL:
        return _max_concurrent_cache["value"]
    try:
        v = max(1, int(load_web_config().get("max_concurrent_jobs", 1)))
    except (TypeError, ValueError):
        v = 1
    _max_concurrent_cache["value"] = v
    _max_concurrent_cache["ts"]    = now
    return v


def _auto_retry(job: "Job") -> None:
    """Create and enqueue a new job if auto-retry is configured and budget allows."""
    import uuid as _uuid
    from core.conf import load_web_config, add_to_queue_state, VENV_BIN
    cfg = load_web_config()
    if not cfg.get("auto_retry_on_error"):
        return
    max_retries = max(1, int(cfg.get("auto_retry_max", 1)))
    retry_count = job.item.get("_retry_count", 0)
    if retry_count >= max_retries:
        return
    u3d_bin  = str(VENV_BIN) if VENV_BIN.exists() else "unit3dup"
    confirm  = cfg.get("confirm_mode", False)
    new_item = {**job.item, "id": str(_uuid.uuid4()), "status": "pending",
                "_retry_count": retry_count + 1}
    new_job  = Job(new_item, u3d_bin, confirm)
    with _jobs_lock:
        _jobs[new_job.id] = new_job
    with _queue_cv:
        _job_queue.append(new_job.id)
        _queue_cv.notify()
    add_to_queue_state(new_job.id, new_item)
    socketio.emit("job_list", [new_job.to_dict()])
    import logging as _log
    _log.getLogger("core.job").info(
        "Auto-retry %d/%d : %s → %s", retry_count + 1, max_retries,
        job.id[:8], new_job.id[:8],
    )


def _run_job_in_thread(job: "Job") -> None:
    global _running_count
    try:
        job.run()
    finally:
        with _queue_cv:
            _running_count -= 1
            _queue_cv.notify_all()
    # Auto-retry after releasing the slot so the new job can start immediately
    if job.status == "error":
        _auto_retry(job)


def _scheduler():
    global _running_count
    while True:
        with _queue_cv:
            _queue_cv.wait_for(
                lambda: bool(_job_queue) and _running_count < _max_concurrent()
            )
            jid = _job_queue.pop(0)
            _running_count += 1
        with _jobs_lock:
            job = _jobs.get(jid)
        if job and job.status == "pending":
            threading.Thread(
                target=_run_job_in_thread, args=(job,),
                daemon=True, name=f"Job-{job.id[:8]}"
            ).start()
        else:
            with _queue_cv:
                _running_count -= 1
                _queue_cv.notify_all()


threading.Thread(target=_scheduler, daemon=True, name="Scheduler").start()


def _restore_queue():
    from core.conf import load_queue_state, load_web_config
    state = load_queue_state()
    if not state:
        return
    cfg     = load_web_config()
    u3d_bin = str(VENV_BIN) if VENV_BIN.exists() else "unit3dup"
    confirm = cfg.get("confirm_mode", False)
    restored = 0
    for job_id, item in state.items():
        # Only restore if not already in _jobs (fresh start)
        with _jobs_lock:
            if job_id in _jobs:
                continue
            job = Job(item, u3d_bin, confirm)
            job.id = job_id  # keep original ID
            _jobs[job_id] = job
        with _queue_cv:
            if job_id not in _job_queue:
                _job_queue.append(job_id)
                _queue_cv.notify()
        restored += 1
    if restored:
        import logging
        logging.getLogger("core.job").info("Queue restaurée : %d job(s) pending", restored)


_restore_queue()
