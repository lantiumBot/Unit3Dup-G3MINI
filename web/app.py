#!/usr/bin/env python3
"""Unit3Dup Dashboard — Flask + Socket.IO"""

import os, re, json, uuid, pty, queue, select, signal, socket, threading, subprocess, time, logging

# Eventlet : pas de bannière « deprecated » au démarrage (voir eventlet/__init__.py)
os.environ.setdefault("EVENTLET_TESTS", "1")

# Moins de bruit en console (werkzeug / engineio par requête)
logging.getLogger("werkzeug").setLevel(logging.WARNING)
logging.getLogger("engineio").setLevel(logging.WARNING)
logging.getLogger("socketio").setLevel(logging.WARNING)
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from flask import Flask, render_template, request, jsonify, abort
from flask_socketio import SocketIO, emit

# ── Paths ────────────────────────────────────────────────────────────────────
WEB_DIR     = Path(__file__).parent
PROJECT_DIR = WEB_DIR.parent
_cfg_env    = os.environ.get("UNIT3DUP_CONFIG_ROOT")
CONFIG_ROOT = Path(_cfg_env).expanduser() if _cfg_env else Path.home() / "Unit3Dup_config"

UNIT3DBOT_JSON  = CONFIG_ROOT / "Unit3Dbot.json"
VALID_TAGS_JSON = PROJECT_DIR / "valid_tags.json"
WEB_CONFIG_JSON = WEB_DIR / "web_config.json"
HISTORY_JSON             = WEB_DIR / "history.json"
HISTORY_TRANSCRIPTS_JSON = WEB_DIR / "history_transcripts.json"
DUPLICATE_CACHE_JSON     = WEB_DIR / "duplicate_check_cache.json"
VENV_BIN        = PROJECT_DIR / ".venv" / "bin" / "unit3dup"

_dup_cache_lock = threading.Lock()
_log = logging.getLogger("app")

# ── Flask ────────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.config["SECRET_KEY"] = "u3dup-dashboard-secret"
def _socketio_async_mode() -> str:
    try:
        import eventlet  # noqa: F401
        return "eventlet"
    except ImportError:
        return "threading"

socketio = SocketIO(app, cors_allowed_origins="*", async_mode=_socketio_async_mode())

# ── Jobs ─────────────────────────────────────────────────────────────────────
_jobs: dict[str, "Job"] = {}
_jobs_lock = threading.Lock()
_job_queue: queue.Queue = queue.Queue()

# ── Helpers ───────────────────────────────────────────────────────────────────
_ANSI = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
strip_ansi = lambda t: _ANSI.sub("", t)

# tqdm / barres de progression : détection pour fusionner les lignes \\r
_PROGRESS_RE = re.compile(
    r"(\d+%\s*\d+/\d+|\d+it\s*\[|\?it/s|it/s\]|/s\]|/it\]|█|▏|▎|▍|▌|▋|▊|▉)"
)


class ConsoleStream:
    """Normalise la sortie PTY (ANSI, retours chariot tqdm) pour le web."""

    def __init__(self):
        self._lines: list[str] = []
        self._buf = ""

    @staticmethod
    def _clean(line: str) -> str:
        return strip_ansi(line).rstrip()

    @staticmethod
    def _is_progress(line: str) -> bool:
        s = line.strip()
        if len(s) < 3:
            return False
        return bool(_PROGRESS_RE.search(s))

    def _append_line(self, line: str, events: list[dict], *, with_newline: bool = True):
        line = self._clean(line)
        if not line.strip():
            return
        suffix = "\n" if with_newline else ""
        self._lines.append(line)
        events.append({"op": "append", "text": line + suffix})

    def _replace_last_progress(self, line: str, events: list[dict], *, with_newline: bool):
        line = self._clean(line)
        if not line.strip():
            return
        suffix = "\n" if with_newline else ""
        if self._lines and self._is_progress(self._lines[-1]):
            self._lines[-1] = line
            events.append({"op": "replace", "text": line + suffix})
        else:
            self._lines.append(line)
            events.append({"op": "append", "text": line + suffix})

    def _on_carriage_return(self, events: list[dict]):
        """\\r : sauvegarder le contenu utile avant que tqdm ne l'écrase."""
        raw = self._buf
        self._buf = ""
        line = self._clean(raw) if raw else ""
        if not line.strip():
            return
        if self._is_progress(line):
            self._replace_last_progress(line, events, with_newline=False)
        else:
            # Message Rich / log : conserver avant réécriture à la même ligne
            self._append_line(line, events, with_newline=True)

    def _on_newline(self, events: list[dict]):
        line = self._clean(self._buf) if self._buf else ""
        self._buf = ""
        if not line.strip():
            return
        if self._is_progress(line):
            self._replace_last_progress(line, events, with_newline=True)
        else:
            self._append_line(line, events, with_newline=True)

    def feed(self, text: str) -> list[dict]:
        if not text:
            return []
        events: list[dict] = []
        for ch in text:
            if ch == "\r":
                self._on_carriage_return(events)
            elif ch == "\b":
                if self._buf:
                    self._buf = self._buf[:-1]
            elif ch == "\n":
                self._on_newline(events)
            else:
                self._buf += ch
        if self._buf:
            line = self._clean(self._buf)
            if line.strip():
                if self._is_progress(line):
                    self._replace_last_progress(line, events, with_newline=False)
                else:
                    self._append_line(line, events, with_newline=False)
        return events

    def flush(self) -> list[dict]:
        events: list[dict] = []
        if self._buf.strip() or self._clean(self._buf).strip():
            self._on_newline(events)
        return events

    def transcript(self) -> str:
        self.flush()
        return "\n".join(self._lines)

    def display_lines(self) -> list[str]:
        """Lignes pour rechargement WebSocket (avec \\n)."""
        return [ln + "\n" for ln in self._lines]


def normalize_transcript(raw: str) -> str:
    """Ré-applique le formatage sur d'anciennes transcriptions brutes."""
    if not raw:
        return ""
    stream = ConsoleStream()
    for ev in stream.feed(strip_ansi(raw)):
        pass
    stream.flush()
    return stream.transcript()


# ── Parsing sortie unit3dup → JSON historique ─────────────────────────────────
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
    """Construit un JSON structuré à partir de la sortie console unit3dup."""

    def __init__(self, item: dict):
        self._events: list[dict] = []
        self._commands: list[str] = []
        self._exit_codes: list[int] = []
        self._stdin: list[str] = []
        self.parsed: dict = {
            "display_name":    None,
            "tmdb_keywords":   None,
            "tmdb_id":         None,
            "tracker":         None,
            "tracker_message": None,
            "your_file_size":  None,
            "size_th_pct":     None,
            "duplicate_scan": item.get("duplicate"),
            "watcher_destination": None,
            "upload_confirmed": None,
            "tracker_matches": [],
            "nfo": [],
            "errors": [],
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
        # Erreur API tracker (ex. validation Gemini) — pas un succès 200
        if "UploadBot" in line and (
            "obligatoire" in line or "required" in line.lower() or "error" in line.lower()
        ):
            self.parsed["errors"].append(line)
            self._event("tracker_error", message=line)

    def tracker_upload_failed(self) -> bool:
        """True si le tracker a refusé l'upload (réponse d'erreur dans la console)."""
        if self.parsed.get("errors"):
            return True
        tracker = (self.parsed.get("tracker") or "").lower()
        msg   = (self.parsed.get("tracker_message") or "").lower()
        if "uploadbot" in tracker:
            return True
        if any(x in msg for x in ("obligatoire", "required", "invalid", "erreur", "error")):
            return True
        return False

    def to_dict(self, *, status: str, started_at: str | None, ended_at: str | None) -> dict:
        return {
            "status":      status,
            "started_at":  started_at,
            "ended_at":    ended_at,
            "commands":    list(self._commands),
            "exit_codes":  list(self._exit_codes),
            "stdin":       list(self._stdin),
            "parsed":      dict(self.parsed),
            "events":      list(self._events),
        }


def _parse_transcript_to_result(transcript: str, meta: dict) -> dict:
    """Reconstruit le JSON structuré depuis une ancienne transcription."""
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


def _transcript_meaningful_line_count(text: str) -> int:
    n = 0
    for ln in text.splitlines():
        s = strip_ansi(ln).strip()
        if not s or s.startswith("▶") or s.startswith("─"):
            continue
        if _PROGRESS_RE.search(s):
            continue
        n += 1
    return n


def _build_transcript_from_result(result: dict) -> str:
    """Reconstruit une console lisible depuis le JSON structuré (fallback)."""
    if not result:
        return ""
    lines: list[str] = []
    seen_cmd = False
    for ev in result.get("events") or []:
        kind = ev.get("kind")
        if kind == "command" and not seen_cmd:
            lines.append(f"▶ {ev.get('command', '')}")
            lines.append("─" * 60)
            seen_cmd = True
        elif kind == "command_echo":
            continue
        elif kind == "tmdb_keywords":
            lines.append(f"'TMDB KEYWORDS'.. {ev.get('keywords', '')}")
        elif kind == "display_name":
            lines.append(f"'DISPLAYNAME'...{{{ev.get('value', '')}}}")
        elif kind == "tracker_response":
            lines.append(
                f"[RESPONSE]-> '{ev.get('tracker', '')}'....."
                f"{ev.get('message', '')}\n"
            )
        elif kind == "watcher_path":
            lines.append(f"           [Watcher] '{ev.get('path', '')}'")
        elif kind == "nfo":
            lines.append(f"[NFO] {ev.get('message', '')}")
        elif kind == "tracker_done":
            lines.append(f"Tracker '{ev.get('tracker', '')}' Done.")
        elif kind == "stdin":
            lines.append(f"> {ev.get('text', '')}")
        elif kind == "exit":
            lines.append(f"[EXIT {ev.get('code', '')}]")
        elif kind == "duplicate_match":
            lines.append(ev.get("line", ""))
        elif kind == "your_file":
            lines.append(f"Your file - size: '{ev.get('size', '')}'")
        elif kind == "size_th":
            lines.append(f"Size_TH: {ev.get('pct', '')}%")
        elif kind == "upload_confirmed":
            lines.append("  ✓ Upload confirmé")
        elif kind == "upload_cancelled":
            lines.append("  ✗ Upload annulé")
    return "\n".join(lines) + ("\n" if lines else "")


def _history_entry_full(entry: dict) -> dict:
    """Entrée historique complète avec result JSON (rétrocompat transcription)."""
    out = dict(entry)
    if not out.get("result") and out.get("transcript"):
        out["result"] = _parse_transcript_to_result(out["transcript"], out)
    raw = out.get("transcript", "") or ""
    norm = normalize_transcript(raw) if raw else ""
    rebuilt = _build_transcript_from_result(out.get("result") or {})
    if rebuilt and _transcript_meaningful_line_count(norm) < 2:
        out["transcript"] = rebuilt
        out["transcript_source"] = "rebuilt_from_result"
    else:
        out["transcript"] = norm or rebuilt
        out["transcript_source"] = (
            "rebuilt_from_result" if not norm and rebuilt else "console"
        )
    return out

def _human(b: int | float) -> str:
    b = float(b)
    for u in ("o", "Ko", "Mo", "Go", "To"):
        if abs(b) < 1024: return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} Po"

# ── Config I/O ────────────────────────────────────────────────────────────────
def _web_defaults() -> dict:
    return {
        "source_folder": "",
        "rules": {
            "integrale":          {"enabled": True, "upload_seasons": True},
            "complete_or_season": {"enabled": True, "require_valid_tag": True},
        },
        "confirm_mode": False,
        "dry_run": False,
        # Doublons Gemini au scan : 0 = ignorer (skip), >0 = demander (ask)
        "duplicate_ask_pct": 0.0,
        # Cache des checks doublons (s) ; 0 = toujours re-vérifier sur le tracker
        "duplicate_cache_ttl_sec": 0,
        "auto_manage": {
            "enabled": False,
            "interval_minutes": 60,
            "auto_remove": {"enabled": False, "after_days": 30, "min_seeders": 5},
            "auto_reseed": {"enabled": False, "below_seeders": 2, "gemini_dead_scan": False},
        },
    }

def load_web_config() -> dict:
    cfg = _web_defaults()
    if WEB_CONFIG_JSON.exists():
        cfg.update(json.loads(WEB_CONFIG_JSON.read_text()))
    return cfg

def save_web_config(data: dict):
    merged = load_web_config(); merged.update(data)
    WEB_CONFIG_JSON.write_text(json.dumps(merged, indent=2))

# Map JSON lowercase section keys ↔ internal UPPERCASE keys used by JS data-path
_JSON_TO_UPPER = {
    "tracker_config":        "TRACKER_CONFIG",
    "torrent_client_config": "TORRENT_CLIENT_CONFIG",
    "user_preferences":      "USER_PREFERENCES",
    "options":               "OPTIONS",
    "console_options":       "CONSOLE_OPTIONS",
    "uploader_tag":          "UPLOADER_TAG",
}
_UPPER_TO_JSON = {v: k for k, v in _JSON_TO_UPPER.items()}


def _stringify_unit3dbot_bools(obj):
    """Unit3Dbot.json attend true/false entre guillemets, pas des booléens JSON natifs."""
    if isinstance(obj, bool):
        return "true" if obj else "false"
    if isinstance(obj, dict):
        return {k: _stringify_unit3dbot_bools(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_stringify_unit3dbot_bools(v) for v in obj]
    return obj


def load_unit3dbot() -> dict:
    if not UNIT3DBOT_JSON.exists(): return {}
    raw = json.loads(UNIT3DBOT_JSON.read_text())
    return {_JSON_TO_UPPER.get(k, k): v for k, v in raw.items()}

def save_unit3dbot(data: dict):
    CONFIG_ROOT.mkdir(parents=True, exist_ok=True)
    normalized = {_UPPER_TO_JSON.get(k, k): v for k, v in data.items()}
    normalized = _stringify_unit3dbot_bools(normalized)
    UNIT3DBOT_JSON.write_text(json.dumps(normalized, indent=4))

def load_valid_tags() -> list[str]:
    if not VALID_TAGS_JSON.exists(): return []
    raw = json.loads(VALID_TAGS_JSON.read_text())
    return raw.get("tags", []) if isinstance(raw, dict) else list(raw)

def save_valid_tags(tags: list[str]):
    VALID_TAGS_JSON.write_text(json.dumps({"tags": tags}, indent=2))

# ── History ───────────────────────────────────────────────────────────────────
def _safe_read_json(path: Path, default):
    """Charge un JSON ; en cas de fichier tronqué/corrompu, sauvegarde .bak et retourne default."""
    if not path.exists():
        return default() if callable(default) else default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        bak = path.with_suffix(path.suffix + f".corrupt.{int(time.time())}.bak")
        try:
            path.rename(bak)
        except OSError:
            pass
        _log.error("JSON invalide %s (%s) — sauvegarde %s", path.name, exc, bak.name)
        return default() if callable(default) else default

def _atomic_write_json(path: Path, data, *, ensure_ascii: bool = True):
    """Écriture atomique pour éviter un fichier tronqué si le processus est interrompu."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    kwargs = {"indent": 2}
    if not ensure_ascii:
        kwargs["ensure_ascii"] = False
    tmp.write_text(json.dumps(data, **kwargs), encoding="utf-8")
    tmp.replace(path)

def load_history() -> dict:
    return _safe_read_json(HISTORY_JSON, dict)

def save_history(data: dict):
    _atomic_write_json(HISTORY_JSON, data)

def load_transcripts() -> dict:
    return _safe_read_json(HISTORY_TRANSCRIPTS_JSON, dict)

def save_transcripts(data: dict):
    _atomic_write_json(HISTORY_TRANSCRIPTS_JSON, data, ensure_ascii=False)

def save_job_transcript(job: "Job"):
    """Sauvegarde JSON structuré + transcription console (onglet live)."""
    data = load_transcripts()
    item_snapshot = {
        k: job.item.get(k)
        for k in ("id", "name", "path", "type", "tag", "tag_valid", "status",
                  "duplicate", "seasons")
        if k in job.item
    }
    data[job.id] = {
        "job_id":       job.id,
        "name":         job.item["name"],
        "path":         job.item["path"],
        "type":         job.item["type"],
        "tag":          job.item.get("tag", ""),
        "status":       job.status,
        "started_at":   job.started_at,
        "ended_at":     job.ended_at,
        "processed_at": job.ended_at or datetime.now().isoformat(),
        "item":         item_snapshot,
        "result":       job.result_json(),
        "transcript":   job.console_transcript(),
    }
    save_transcripts(data)

def add_to_history(job: "Job"):
    hist = load_history()
    # Track the main path + each season
    paths = [job.item["path"]] + job.item.get("seasons", [])
    for p in paths:
        hist[p] = {
            "name":         Path(p).name,
            "type":         job.item["type"],
            "tag":          job.item.get("tag", ""),
            "processed_at": job.ended_at or datetime.now().isoformat(),
            "status":       job.status,
            "job_id":       job.id,
        }
    save_history(hist)

# ── qBittorrent ───────────────────────────────────────────────────────────────
_QBIT_STATE = {
    "uploading":           ("Partage",         "success"),
    "stalledUP":           ("Partage",         "success"),
    "forcedUP":            ("Partage forcé",   "success"),
    "queuedUP":            ("File (seed)",     "secondary"),
    "checkingUP":          ("Vérification",    "warning"),
    "checkingDL":          ("Vérification",    "warning"),
    "checkingResumeData":  ("Vérification",    "warning"),
    "downloading":         ("Téléchargement",  "info"),
    "stalledDL":           ("En attente",      "secondary"),
    "queuedDL":            ("File (DL)",       "secondary"),
    "pausedUP":            ("Pause ↑",         "secondary"),
    "pausedDL":            ("Pause ↓",         "secondary"),
    "stoppedUP":           ("Stoppé ↑",        "secondary"),
    "stoppedDL":           ("Stoppé ↓",        "secondary"),
    "error":               ("Erreur",          "danger"),
    "missingFiles":        ("Fichiers manq.",  "danger"),
    "moving":              ("Déplacement",     "info"),
}

def get_qbit_torrents() -> tuple[list, str | None]:
    u3d = load_unit3dbot()
    cfg = u3d.get("TORRENT_CLIENT_CONFIG", {})
    if (cfg.get("TORRENT_CLIENT") or "").lower() != "qbittorrent":
        return [], "Client non configuré sur qBittorrent"
    host   = cfg.get("QBIT_HOST", "http://localhost")
    port   = cfg.get("QBIT_PORT", 8080)
    user   = cfg.get("QBIT_USER", "admin")
    passwd = cfg.get("QBIT_PASS") or ""
    tag    = cfg.get("TAG", "ADDED TORRENTS")
    try:
        import qbittorrentapi
        cl = qbittorrentapi.Client(
            host=host, port=port, username=user, password=passwd,
            VERIFY_WEBUI_CERTIFICATE=False, REQUESTS_ARGS={"timeout": 5},
        )
        cl.auth_log_in()
        out = []
        for t in cl.torrents_info(tag=tag):
            label, cls = _QBIT_STATE.get(t.state, (t.state, "secondary"))
            out.append({
                "name":                t.name,
                "hash":                t.hash,
                "size":                t.size,
                "size_human":          _human(t.size),
                "ratio":               round(t.ratio, 2),
                "state":               t.state,
                "state_label":         label,
                "state_class":         cls,
                "upload_speed":        t.upspeed,
                "upload_speed_human":  _human(t.upspeed) + "/s",
                "download_speed":      t.dlspeed,
                "download_speed_human":_human(t.dlspeed) + "/s",
                "uploaded":            t.uploaded,
                "uploaded_human":      _human(t.uploaded),
                "progress":            round(t.progress * 100, 1),
                "added_on":            t.added_on,
                "category":            t.category,
                "seeding_time":        t.get("seeding_time", 0) or 0,
                "num_complete":        t.get("num_complete", 0) or 0,
            })
        cl.auth_log_out()
        return out, None
    except Exception as exc:
        return [], str(exc)

# ── Scan logic ────────────────────────────────────────────────────────────────
_norm = lambda n: n.replace(".", " ").replace("_", " ").replace("-", " ")
_VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".m2ts"}

def _is_integrale(n):
    return bool(re.search(r"(?:^|[ ])integrale(?:[ ]|$)", _norm(n), re.I))

def _is_season(n):
    nd = _norm(n)
    return bool(
        re.search(r"(?:^|[ ])complete(?:[ ]|$)", nd, re.I) or
        re.search(r"(?:^|[ ])S\d{2}(?:[ ]|$)", nd)
    )

def _filename_is_s01e01(stem: str) -> bool:
    """True si le nom contient S01E01 (saison 1, épisode 1 uniquement)."""
    nd = _norm(stem)
    return bool(re.search(r"(?:^|[ ])S0?1E0?1(?:[ ]|$)", nd, re.I))

def _season_number_from_dirname(name: str) -> int | None:
    """Numéro de saison Sxx dans le nom du dossier, ou None (ex. COMPLETE sans Sxx)."""
    m = re.search(r"(?:^|[ ])S(\d{1,2})(?:[ ]|$)", _norm(name), re.I)
    return int(m.group(1)) if m else None

def pack_is_season1(name: str) -> bool:
    """Pack saison dont le nom indique explicitement la saison 1 (S01)."""
    return _season_number_from_dirname(name) == 1

def find_s01e01_upload_file(directory: str, *, search_sibling_s01: bool = False) -> str | None:
    """
    Premier fichier vidéo S01E01 sous *directory* (profondeur 2).
    *search_sibling_s01* : pour une intégrale, chercher aussi un dossier S01 voisin.
    """
    root = Path(directory)
    if not root.is_dir():
        return None
    skip = {"http_cache"}
    found: list[Path] = []

    def scan_dir(d: Path, depth: int = 0) -> None:
        if depth > 2:
            return
        for child in sorted(d.iterdir()):
            if child.name in skip:
                continue
            if child.is_file() and child.suffix.lower() in _VIDEO_EXTS and child.stat().st_size > 0:
                if _filename_is_s01e01(child.stem):
                    found.append(child)
            elif child.is_dir() and depth < 2:
                scan_dir(child, depth + 1)

    scan_dir(root)
    if not found and search_sibling_s01 and root.parent.is_dir():
        for sib in sorted(root.parent.iterdir()):
            if not sib.is_dir() or sib == root or sib.name in skip:
                continue
            if pack_is_season1(sib.name):
                scan_dir(sib)
                if found:
                    break
    return str(found[0]) if found else None

def episode_upload_for_item(item: dict) -> str | None:
    """Chemin S01E01 à envoyer en -u selon le type de release."""
    path = item.get("path") or ""
    name = item.get("name") or Path(path).name
    t    = item.get("type")
    if t == "integrale":
        return find_s01e01_upload_file(path, search_sibling_s01=True)
    if t == "season" and pack_is_season1(name):
        return find_s01e01_upload_file(path, search_sibling_s01=False)
    return None

def _tag(n):
    return n.rsplit("-", 1)[-1] if "-" in n else ""

def scan_source(
    source: str,
    valid_tags: list,
    rules: dict,
    *,
    include_history: bool = False,
) -> tuple[list, str | None]:
    p = Path(source)
    if not p.exists(): return [], f"Dossier introuvable: {source}"
    skip = {"http_cache"}
    hist = load_history()
    items = []

    for child in sorted(p.iterdir()):
        if child.name in skip: continue
        name     = child.name
        path_str = str(child)

        # ── Video file → unit3dup -u ──────────────────────────────────────────
        if child.is_file() and child.suffix.lower() in _VIDEO_EXTS and child.stat().st_size > 0:
            tag = _tag(child.stem)
            if path_str in hist:
                if not include_history:
                    continue
                e = hist[path_str]
                items.append({
                    "id": str(uuid.uuid4()), "path": path_str, "name": name,
                    "type": "file", "seasons": [],
                    "tag": e.get("tag", tag), "tag_valid": True,
                    "status": "history", "history": e,
                })
            else:
                items.append({
                    "id": str(uuid.uuid4()), "path": path_str, "name": name,
                    "type": "file", "seasons": [],
                    "tag": tag, "tag_valid": True, "status": "pending",
                })
            continue

        if not child.is_dir(): continue
        if not any(child.iterdir()): continue
        tag = _tag(name)

        # Already processed → show as history (optional)
        if path_str in hist:
            if not include_history:
                continue
            e = hist[path_str]
            items.append({
                "id": str(uuid.uuid4()), "path": path_str, "name": name,
                "type": e.get("type", "unknown"), "seasons": [],
                "tag": e.get("tag", ""), "tag_valid": True,
                "status": "history", "history": e,
            })
            continue

        if rules.get("integrale", {}).get("enabled") and _is_integrale(name):
            seasons = []
            if rules["integrale"].get("upload_seasons"):
                for sub in sorted(child.iterdir()):
                    if sub.is_dir() and sub.name not in skip and any(sub.iterdir()):
                        seasons.append(str(sub))
            ep = find_s01e01_upload_file(path_str, search_sibling_s01=True)
            items.append({
                "id": str(uuid.uuid4()), "path": path_str, "name": name,
                "type": "integrale", "seasons": seasons,
                "episode_upload": ep,
                "tag": tag, "tag_valid": True, "status": "pending",
            })

        elif rules.get("complete_or_season", {}).get("enabled") and _is_season(name):
            req = rules["complete_or_season"].get("require_valid_tag", True)
            ok  = not req or tag.upper() in {t.upper() for t in valid_tags}
            ep  = find_s01e01_upload_file(path_str) if ok and pack_is_season1(name) else None
            items.append({
                "id": str(uuid.uuid4()), "path": path_str, "name": name,
                "type": "season", "seasons": [],
                "episode_upload": ep,
                "tag": tag, "tag_valid": ok, "status": "pending" if ok else "skip",
            })
        else:
            items.append({
                "id": str(uuid.uuid4()), "path": path_str, "name": name,
                "type": "unknown", "seasons": [],
                "tag": tag, "tag_valid": False, "status": "skip",
            })

    return items, None


def _default_tracker_name() -> str | None:
    u3d   = load_unit3dbot()
    multi = u3d.get("TRACKER_CONFIG", {}).get("MULTI_TRACKER") or []
    if isinstance(multi, list) and multi:
        return str(multi[0]).lower()
    return None


def _media_for_scan_path(path_str: str):
    """Construit un objet Media minimal pour la détection de doublons."""
    from unit3dup.media import Media

    p = Path(path_str).resolve()
    if p.is_file():
        media = Media(folder=str(p.parent), subfolder=p.name)
        media.display_name = p.stem
    else:
        media = Media(folder=str(p), subfolder="")
        media.display_name = p.name
    return media


def _duplicate_skip_threshold_pct() -> float:
    """Seuil skip (web) : delta ≤ cette valeur → ignorer, delta > → demander."""
    try:
        return float(load_web_config().get("duplicate_ask_pct", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def _duplicate_cache_ttl_sec() -> int:
    """Durée de conservation du cache doublons (s). 0 = pas de cache."""
    try:
        return max(0, int(load_web_config().get("duplicate_cache_ttl_sec", 0) or 0))
    except (TypeError, ValueError):
        return 0


def _dup_cache_key(path_str: str, tracker: str) -> str:
    p = Path(path_str).resolve()
    try:
        st = p.stat()
        ident = f"{st.st_mtime_ns}:{st.st_size}"
    except OSError:
        ident = "missing"
    return f"{tracker}|{p}|{ident}"


def load_duplicate_cache() -> dict:
    if not DUPLICATE_CACHE_JSON.exists():
        return {"entries": {}}
    try:
        raw = json.loads(DUPLICATE_CACHE_JSON.read_text())
        if isinstance(raw, dict) and isinstance(raw.get("entries"), dict):
            return raw
    except Exception:
        pass
    return {"entries": {}}


def save_duplicate_cache(data: dict):
    DUPLICATE_CACHE_JSON.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
    )


def _prune_duplicate_cache(cache: dict, ttl: int) -> dict:
    if ttl <= 0:
        return cache
    now = time.time()
    entries = cache.get("entries") or {}
    kept = {}
    for key, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        try:
            age = now - datetime.fromisoformat(entry["checked_at"]).timestamp()
        except Exception:
            continue
        if age <= ttl:
            kept[key] = entry
    return {"entries": kept}


def lookup_duplicate_cache(
    path_str: str, tracker: str, skip_th: float,
) -> tuple[bool, dict | None, str | None] | None:
    """
    Retourne (is_duplicate, info, status) si entrée cache valide.
    None si TTL=0, miss ou entrée expirée.
    """
    ttl = _duplicate_cache_ttl_sec()
    if ttl <= 0:
        return None
    key = _dup_cache_key(path_str, tracker)
    with _dup_cache_lock:
        cache = _prune_duplicate_cache(load_duplicate_cache(), ttl)
        entry = (cache.get("entries") or {}).get(key)
        save_duplicate_cache(cache)
    if not entry:
        return None
    if float(entry.get("skip_th", -1)) != skip_th:
        return None
    if not entry.get("is_duplicate"):
        return False, None, None
    return True, entry.get("info"), entry.get("status")


def store_duplicate_cache(
    path_str: str,
    tracker: str,
    skip_th: float,
    is_dup: bool,
    info: dict | None,
    status: str | None,
):
    ttl = _duplicate_cache_ttl_sec()
    if ttl <= 0:
        return
    key = _dup_cache_key(path_str, tracker)
    with _dup_cache_lock:
        cache = _prune_duplicate_cache(load_duplicate_cache(), ttl)
        cache.setdefault("entries", {})[key] = {
            "path":          str(Path(path_str).resolve()),
            "tracker":       tracker,
            "checked_at":    datetime.now().isoformat(),
            "is_duplicate":  is_dup,
            "info":          info,
            "skip_th":       skip_th,
            "status":        status,
        }
        save_duplicate_cache(cache)


def _duplicate_deltas_by_id(matches: list[dict]) -> list[dict]:
    """Regroupe par TMDB / IGDB / torrent : delta minimal par identifiant."""
    by_key: dict[str, dict] = {}
    for m in matches:
        tmdb = m.get("tmdb_id") or 0
        igdb = m.get("igdb_id") or 0
        if tmdb:
            key, label = f"tmdb:{tmdb}", f"TMDB {tmdb}"
        elif igdb:
            key, label = f"igdb:{igdb}", f"IGDB {igdb}"
        else:
            tid = m.get("tracker_id") or "?"
            key, label = f"torrent:{tid}", f"#{tid}"
        prev = by_key.get(key)
        if not prev or m["delta_pct"] < prev["delta_pct"]:
            by_key[key] = {
                "label":      label,
                "delta_pct":  m["delta_pct"],
                "name":       m.get("name", ""),
                "size_gb":    m.get("size_gb"),
            }
    return sorted(by_key.values(), key=lambda x: x["delta_pct"])


def gemini_duplicate_check(path_str: str, tracker_name: str) -> tuple[bool, dict | None]:
    """
    Vérifie sur Gemini si une release similaire existe (logique Duplicate).
    Retourne le meilleur match + la liste des deltas par ID (itération tracker).
    """
    import argparse
    from common import title
    from common.settings import Load
    from unit3dup import config_settings
    from unit3dup.duplicate import Duplicate, CompareTitles

    Load()
    if not config_settings.user_preferences.DUPLICATE_ON:
        return False, None

    size_th = config_settings.user_preferences.SIZE_TH
    media   = _media_for_scan_path(path_str)
    cli     = argparse.Namespace(
        duplicate=True, notitle=None, watcher=False,
        noup=False, noseed=False, reseed=False, mt=False,
    )
    dup  = Duplicate(content=media, tracker_name=tracker_name, cli=cli)
    resp = dup.torrent_info.search(dup.query.guessit_title)
    if not isinstance(resp, dict) or "data" not in resp:
        return False, None

    matches: list[dict] = []

    for t_data in resp.get("data", []):
        attrs = t_data.get("attributes", {})
        if not CompareTitles(
            tracker_file=title.Guessit(attrs.get("name", "")),
            content_file=dup.query,
        ).process():
            continue
        delta = dup._calculate_threshold(size=attrs.get("size") or 0)
        if delta > size_th:
            continue
        size = attrs.get("size") or 0
        matches.append({
            "tracker_id": t_data.get("id"),
            "tmdb_id":    attrs.get("tmdb_id") or 0,
            "igdb_id":    attrs.get("igdb_id") or 0,
            "name":       attrs.get("name", ""),
            "size_gb":    round(size / (1024 ** 3), 2) if size else 0,
            "delta_pct":  round(delta, 2),
        })

    if not matches:
        return False, None

    matches.sort(key=lambda m: m["delta_pct"])
    best = dict(matches[0])
    best["size_th"]       = size_th
    best["local_gb"]      = dup.content_size
    best["matches"]       = matches
    best["deltas_by_id"]  = _duplicate_deltas_by_id(matches)
    return True, best


def apply_duplicate_checks(items: list) -> list:
    """
    Marque les doublons selon delta vs duplicate_ask_pct (config web) :
      delta ≤ seuil → duplicate (skip auto)
      delta > seuil → duplicate_ask (demander confirmation)
    """
    tracker = _default_tracker_name()
    if not tracker:
        return items

    try:
        from common.settings import Load
        from unit3dup import config_settings
        Load()
        if not config_settings.user_preferences.DUPLICATE_ON:
            return items
    except Exception:
        return items

    skip_th = _duplicate_skip_threshold_pct()
    pending = [(i, it) for i, it in enumerate(items) if it.get("status") == "pending"]
    if not pending:
        return items

    def _check(entry):
        idx, it = entry
        path = it["path"]
        cached = lookup_duplicate_cache(path, tracker, skip_th)
        if cached is not None:
            is_dup, info, status = cached
            if is_dup and info and status:
                return idx, True, info, status, True
            return idx, False, None, None, True

        try:
            is_dup, info = gemini_duplicate_check(path, tracker)
        except Exception as exc:
            store_duplicate_cache(path, tracker, skip_th, False, {"error": str(exc)[:120]}, None)
            return idx, False, {"error": str(exc)[:120]}, None, False

        status = None
        if is_dup and info:
            delta  = float(info.get("delta_pct", 0))
            status = "duplicate" if delta <= skip_th else "duplicate_ask"
        store_duplicate_cache(path, tracker, skip_th, bool(is_dup and info), info, status)
        return idx, is_dup, info, status, False

    with ThreadPoolExecutor(max_workers=3) as ex:
        for idx, is_dup, info, status, _from_cache in ex.map(_check, pending):
            if info and info.get("error"):
                continue
            if not is_dup or not info or not status:
                continue
            items[idx]["status"]    = status
            items[idx]["duplicate"] = info

    return items


# ── Job ───────────────────────────────────────────────────────────────────────
class Job:
    def __init__(self, item, unit3dup, confirm):
        self.id       = item["id"]
        self.item     = item
        self._bin     = unit3dup
        self._confirm = confirm
        self.status   = "pending"
        self._console = ConsoleStream()
        self.lines    = []
        self._q: queue.Queue = queue.Queue()
        self._proc    = None
        self.started_at = None
        self.ended_at   = None
        self._result    = JobResultCollector(item)

    def result_json(self) -> dict:
        return self._result.to_dict(
            status=self.status,
            started_at=self.started_at,
            ended_at=self.ended_at,
        )

    def to_dict(self):
        return {
            "id": self.id, "name": self.item["name"], "path": self.item["path"],
            "type": self.item["type"], "tag": self.item.get("tag", ""),
            "tag_valid": self.item.get("tag_valid", False),
            "seasons": len(self.item.get("seasons", [])),
            "status": self.status, "lines": self._console.display_lines()[-300:],
            "started_at": self.started_at, "ended_at": self.ended_at,
        }

    def console_transcript(self) -> str:
        return self._console.transcript()

    _TERMINAL = frozenset({"done", "error", "cancelled"})

    def send_stdin(self, text: str) -> tuple[bool, str]:
        """Envoie une ligne vers le PTY du job. Retourne (ok, message)."""
        if self.status in self._TERMINAL:
            return False, "job_finished"
        if not self._proc or self._proc.poll() is not None:
            return False, "no_process"
        if text:
            self._result.record_stdin(text)
            self._q.put(text)
        return True, "ok"

    def _cleanup_session(self):
        """Ferme le PTY / processus et vide la file stdin."""
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
        """Transcription → fermeture PTY → retrait du registre actif."""
        save_job_transcript(self)
        self._cleanup_session()

        def _unregister():
            with _jobs_lock:
                _jobs.pop(self.id, None)
            socketio.emit("jobs_cleared", {"ids": [self.id]})

        threading.Timer(1.5, _unregister).start()

    def _push(self, text):
        self._result.feed_text(text)
        for ev in self._console.feed(strip_ansi(text)):
            socketio.emit("job_output", {"id": self.id, **ev})
        self.lines = self._console.display_lines()

    def _set_status(self, s):
        self.status = s
        socketio.emit("job_status", {"id": self.id, "status": s, "ended_at": self.ended_at})

    def _commands(self):
        extra = ["-confirm"] if self._confirm else []
        t     = self.item["type"]
        if t == "file":
            return [[self._bin, "-u", self.item["path"]] + extra]

        ep = self.item.get("episode_upload") or episode_upload_for_item(self.item)
        u  = ([[self._bin, "-u", ep] + extra] if ep else [])
        f  = [[self._bin, "-f", self.item["path"]] + extra]

        # Intégrale : S01E01 seul → pack intégrale → packs saisons
        if t == "integrale":
            cmds = list(u) + f
            for s in self.item.get("seasons", []):
                cmds.append([self._bin, "-f", s] + extra)
            return cmds

        # Saison S01 : S01E01 seul → pack S01 ; autres saisons : pack seul
        if t == "season":
            return list(u) + f

        return f

    def _run_pty(self, cmd):
        try:
            master, slave = pty.openpty()
        except Exception as exc:
            self._push(f"[PTY ERROR] {exc}\n"); return 1
        try:
            proc = subprocess.Popen(
                cmd, stdin=slave, stdout=slave, stderr=slave,
                close_fds=True, preexec_fn=os.setsid,
                env={**os.environ, "TERM": "xterm-256color", "FORCE_COLOR": "0"},
            )
            os.close(slave); self._proc = proc
            while True:
                poll = proc.poll()
                try: r, _, _ = select.select([master], [], [], 0.05)
                except (OSError, ValueError): break
                if r:
                    try:
                        chunk = os.read(master, 8192)
                        self._push(chunk.decode("utf-8", errors="replace"))
                    except OSError: break
                try:
                    inp = self._q.get_nowait()
                    os.write(master, (inp + "\n").encode())
                    self._result.record_stdin(inp)
                except queue.Empty: pass
                if poll is not None:
                    for _ in range(20):
                        try: r2, _, _ = select.select([master], [], [], 0.1)
                        except (OSError, ValueError): break
                        if not r2: break
                        try:
                            d = os.read(master, 8192)
                            if d: self._push(d.decode("utf-8", errors="replace"))
                        except OSError: break
                    break
            proc.wait(); return proc.returncode
        except Exception as exc:
            self._push(f"[ERREUR] {exc}\n"); return 1
        finally:
            try: os.close(master)
            except OSError: pass

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
            try: os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
            except Exception: self._proc.terminate()
        self.ended_at = datetime.now().isoformat()
        self._set_status("cancelled")
        self._finalize()

# ── Scheduler ─────────────────────────────────────────────────────────────────
def _scheduler():
    while True:
        jid = _job_queue.get()
        with _jobs_lock:
            job = _jobs.get(jid)
        if job and job.status == "pending":
            job.run()
        _job_queue.task_done()

threading.Thread(target=_scheduler, daemon=True, name="Scheduler").start()

# ── AutoManager ───────────────────────────────────────────────────────────────
class AutoManager:
    _SEEDING_STATES = {"uploading", "stalledUP", "forcedUP", "queuedUP"}
    _PAUSED_STATES  = {"pausedUP", "stoppedUP"}

    def __init__(self):
        self._log: list[dict] = []
        self._lock = threading.Lock()
        threading.Thread(target=self._loop, daemon=True, name="AutoManager").start()

    def _entry(self, msg: str, level: str = "info") -> dict:
        return {"ts": datetime.now().isoformat(), "msg": msg, "level": level}

    def _log_add(self, msg: str, level: str = "info"):
        with self._lock:
            self._log.append(self._entry(msg, level))
            if len(self._log) > 200:
                self._log = self._log[-200:]

    def get_log(self) -> list:
        with self._lock:
            return list(self._log)

    def _qbit_client(self):
        u3d = load_unit3dbot()
        cfg = u3d.get("TORRENT_CLIENT_CONFIG", {})
        import qbittorrentapi
        cl = qbittorrentapi.Client(
            host=cfg.get("QBIT_HOST", "http://localhost"),
            port=cfg.get("QBIT_PORT", 8080),
            username=cfg.get("QBIT_USER", "admin"),
            password=cfg.get("QBIT_PASS") or "",
            VERIFY_WEBUI_CERTIFICATE=False,
            REQUESTS_ARGS={"timeout": 8},
        )
        cl.auth_log_in()
        return cl, cfg.get("TAG", "ADDED TORRENTS")

    def _do_auto_remove(self, cfg: dict, cl, tag: str):
        after_secs   = int(cfg.get("after_days", 30)) * 86400
        min_seeders  = int(cfg.get("min_seeders", 5))
        paused = []
        for t in cl.torrents_info(tag=tag):
            st = t.get("seeding_time", 0) or 0
            nc = t.get("num_complete", 0) or 0
            if t.state in self._SEEDING_STATES and st >= after_secs and nc >= min_seeders:
                paused.append(t.hash)
                self._log_add(
                    f"PAUSE: {t.name[:60]} — {st//86400}j seedé, {nc} seeders", "warning"
                )
        if paused:
            cl.torrents_pause(torrent_hashes=paused)
            self._log_add(f"{len(paused)} torrent(s) mis en pause", "warning")

    def _do_auto_reseed(self, cfg: dict, cl, tag: str):
        below = int(cfg.get("below_seeders", 2))
        resumed = []
        for t in cl.torrents_info(tag=tag):
            nc = t.get("num_complete", 0) or 0
            if t.state in self._PAUSED_STATES and nc < below:
                resumed.append(t.hash)
                self._log_add(
                    f"REPRISE: {t.name[:60]} — seulement {nc} seeders", "info"
                )
        if resumed:
            cl.torrents_resume(torrent_hashes=resumed)
            self._log_add(f"{len(resumed)} torrent(s) relancé(s)", "info")

    def _do_gemini_scan(self, cfg: dict, cl, tag: str):
        u3d   = load_unit3dbot()
        tc    = u3d.get("TRACKER_CONFIG", {})
        url   = (tc.get("Gemini_URL") or "").rstrip("/")
        apikey = tc.get("Gemini_APIKEY") or ""
        if not url or not apikey:
            self._log_add("Gemini non configuré (URL/APIKEY manquants)", "error")
            return
        try:
            import urllib.request, urllib.parse
            req = urllib.request.Request(
                f"{url}/api/torrents?api_token={apikey}&dead=1",
                headers={"Accept": "application/json", "User-Agent": "G3MINI-AutoManager/1.0"},
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                raw = json.loads(r.read())
        except Exception as exc:
            self._log_add(f"Gemini scan erreur: {exc}", "error")
            return

        dead_names = set()
        for item in (raw.get("data") or []):
            n = item.get("attributes", {}).get("name") or item.get("name") or ""
            if n:
                dead_names.add(n.lower())

        if not dead_names:
            self._log_add("Gemini: aucune release dead trouvée")
            return

        self._log_add(f"Gemini: {len(dead_names)} release(s) dead détectées")
        resumed = []
        for t in cl.torrents_info(tag=tag):
            if t.name.lower() in dead_names and t.state in self._PAUSED_STATES:
                resumed.append(t.hash)
                self._log_add(f"GEMINI REPRISE: {t.name[:60]}", "info")
        if resumed:
            cl.torrents_resume(torrent_hashes=resumed)

    def _cycle(self):
        web_cfg = load_web_config()
        am_cfg  = web_cfg.get("auto_manage", {})
        if not am_cfg.get("enabled"):
            return
        self._log_add("── Cycle AutoManager ──")
        try:
            cl, tag = self._qbit_client()
        except Exception as exc:
            self._log_add(f"qBit connexion échouée: {exc}", "error")
            return
        try:
            ar = am_cfg.get("auto_remove", {})
            if ar.get("enabled"):
                self._do_auto_remove(ar, cl, tag)
            rs = am_cfg.get("auto_reseed", {})
            if rs.get("enabled"):
                self._do_auto_reseed(rs, cl, tag)
                if rs.get("gemini_dead_scan"):
                    self._do_gemini_scan(rs, cl, tag)
        finally:
            try: cl.auth_log_out()
            except Exception: pass
        self._log_add("── Fin cycle ──")

    def run_now(self):
        threading.Thread(target=self._cycle, daemon=True, name="AM-manual").start()

    def _loop(self):
        time.sleep(10)  # startup delay
        while True:
            web_cfg  = load_web_config()
            interval = int(web_cfg.get("auto_manage", {}).get("interval_minutes", 60)) * 60
            self._cycle()
            time.sleep(max(60, interval))


_auto_manager = AutoManager()

# ── Watcher (unit3dup -watcher) ───────────────────────────────────────────────
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
    """Surveille un dossier via unit3dup -watcher ; état JSON + console live."""

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
                    "name": Path(path).name,
                    "path": path,
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
                    "kind": "moved",
                    "path": dest,
                    "name": Path(dest).name,
                    "at": datetime.now().isoformat(),
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
            items.append({
                "name":         p.name,
                "path":         str(p.resolve()),
                "type":         "folder" if p.is_dir() else "file",
                "size_bytes":   size,
                "size_human":   _human(size) if size is not None else None,
            })
        return items

    def _refresh_queue(self):
        with self._lock:
            wp = _cfg_path(self._paths.get("watcher_path"))
        if wp:
            q = self._list_queue(wp)
            with self._lock:
                self._paths["queue"] = q
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
        """État structuré JSON — sans transcription console."""
        prefs = self._watcher_prefs()
        wp = _cfg_path(prefs.get("watcher_path"))
        queue = self._list_queue(wp) if wp else []
        with self._lock:
            paths = dict(self._paths)
            if self._running:
                queue = list(paths.get("queue", queue))
            return {
                "running":              self._running,
                "phase":                self._phase,
                "started_at":           self._started_at,
                "error":                self._error,
                "config": {
                    "watcher_path":       prefs.get("watcher_path", ""),
                    "destination_path":   prefs.get("destination_path", ""),
                    "interval_sec":       prefs.get("interval_sec", 60),
                    "watcher_exists":     prefs.get("watcher_exists", False),
                    "destination_exists": prefs.get("destination_exists", False),
                },
                "queue":                queue,
                "queue_count":          len(queue),
                "current":              self._current,
                "last":                 self._last,
                "watchdog_remaining_sec": self._watchdog_sec,
                "events":               list(self._events[-30:]),
            }

    def send_stdin(self, text: str):
        if self._running:
            self._q.put(text)

    def start(self) -> tuple[bool, str | None]:
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
            self._running = True
            self._started_at = datetime.now().isoformat()
            self._error = None
            self._phase = "starting"
            self._current = None
            self._last = None
            self._watchdog_sec = None
            self._events = []
            self._paths = {**prefs, "queue": [], "queue_count": 0}
            self._console = ConsoleStream()

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
            self._proc = None
            self._phase = "stopped"
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

# ── Routes: pages ─────────────────────────────────────────────────────────────
@app.route("/")
def page_jobs():       return render_template("index.html",    page="jobs")

@app.route("/config")
def page_config():     return render_template("settings.html", page="config")

@app.route("/history")
def page_history():    return render_template("history.html",  page="history")

@app.route("/stats")
def page_stats():      return render_template("stats.html",    page="stats")

@app.route("/status")
def page_status():     return render_template("status.html",   page="status")

# ── Routes: settings ──────────────────────────────────────────────────────────
@app.route("/api/settings")
def api_get_settings():
    return jsonify({
        "web":        load_web_config(),
        "unit3dbot":  load_unit3dbot(),
        "valid_tags": load_valid_tags(),
    })

@app.route("/api/settings", methods=["POST"])
def api_save_settings():
    d = request.json or {}
    if "web"        in d: save_web_config(d["web"])
    if "unit3dbot"  in d: save_unit3dbot(d["unit3dbot"])
    if "valid_tags" in d: save_valid_tags(d["valid_tags"])
    return jsonify({"ok": True})

# ── Routes: scan / jobs ───────────────────────────────────────────────────────
@app.route("/api/scan", methods=["POST"])
def api_scan():
    d   = request.json or {}
    cfg = load_web_config()
    src = d.get("source_folder") or cfg.get("source_folder", "")
    if not src: return jsonify({"error": "source_folder non défini"}), 400
    include_history = bool(d.get("include_history", False))
    items, err = scan_source(
        src, load_valid_tags(), cfg.get("rules", {}), include_history=include_history
    )
    if err: return jsonify({"error": err}), 400
    items = apply_duplicate_checks(items)
    return jsonify({
        "items": items,
        "duplicate_skip_threshold_pct": _duplicate_skip_threshold_pct(),
        "duplicate_cache_ttl_sec":      _duplicate_cache_ttl_sec(),
    })

@app.route("/api/jobs")
def api_list_jobs():
    with _jobs_lock: return jsonify([j.to_dict() for j in _jobs.values()])

@app.route("/api/jobs", methods=["POST"])
def api_create_jobs():
    d       = request.json or {}
    cfg     = load_web_config()
    u3d_bin = str(VENV_BIN) if VENV_BIN.exists() else "unit3dup"
    confirm = cfg.get("confirm_mode", False)
    created = []
    with _jobs_lock:
        for item in d.get("items", []):
            if item.get("status") not in ("pending", "duplicate_ask"):
                continue
            job = Job(item, u3d_bin, confirm)
            _jobs[job.id] = job; _job_queue.put(job.id)
            created.append(job.to_dict())
    if created:
        socketio.emit("job_list", created)
    return jsonify({"created": created})

@app.route("/api/jobs/<jid>", methods=["DELETE"])
def api_cancel_job(jid):
    with _jobs_lock: job = _jobs.get(jid)
    if not job: abort(404)
    job.cancel(); return jsonify({"ok": True})

@app.route("/api/jobs/<jid>/input", methods=["POST"])
def api_job_input(jid):
    with _jobs_lock:
        job = _jobs.get(jid)
    if not job:
        return jsonify({"ok": False, "error": "job_not_found"}), 404
    text = (request.json or {}).get("text", "")
    ok, reason = job.send_stdin(text)
    if not ok:
        return jsonify({"ok": False, "error": reason}), 409
    return jsonify({"ok": True})

@app.route("/api/jobs/clear", methods=["POST"])
def api_clear_jobs():
    with _jobs_lock:
        done = [jid for jid, j in _jobs.items() if j.status in ("done", "error", "cancelled")]
        for jid in done:
            del _jobs[jid]
    socketio.emit("jobs_cleared", {"ids": done})
    return jsonify({"cleared": len(done), "ids": done})

# ── Routes: history ───────────────────────────────────────────────────────────
@app.route("/api/history")
def api_history():
    hist        = load_history()
    transcripts = load_transcripts()
    torrents, _ = get_qbit_torrents()
    # Build name→torrent map for matching
    tmap = {t["name"].lower(): t for t in torrents}

    rows = []
    for path, entry in sorted(hist.items(), key=lambda x: x[1].get("processed_at",""), reverse=True):
        name = entry.get("name", Path(path).name)
        torrent = tmap.get(name.lower())
        job_id = entry.get("job_id", "")
        tr_entry = transcripts.get(job_id, {}) if job_id else {}
        rows.append({
            "path":         path,
            "name":         name,
            "type":         entry.get("type", "?"),
            "tag":          entry.get("tag", ""),
            "processed_at": entry.get("processed_at", ""),
            "status":       entry.get("status", "done"),
            "job_id":       job_id,
            "has_detail":   bool(job_id and job_id in transcripts),
            "has_result":   bool(tr_entry.get("result") or tr_entry.get("transcript")),
            "torrent":      torrent,
        })
    return jsonify(rows)


@app.route("/api/history/detail/<job_id>")
def api_history_detail(job_id):
    """Retourne l'enregistrement complet (JSON structuré + métadonnées)."""
    entry = load_transcripts().get(job_id)
    if not entry:
        abort(404)
    return jsonify(_history_entry_full(entry))


@app.route("/api/history/transcript/<job_id>")
def api_history_transcript(job_id):
    """Rétrocompat — redirige vers le détail complet."""
    return api_history_detail(job_id)

@app.route("/api/history", methods=["DELETE"])
def api_history_clear():
    save_history({})
    save_transcripts({})
    return jsonify({"ok": True})

@app.route("/api/history/item", methods=["DELETE"])
def api_history_remove():
    path = (request.json or {}).get("path", "")
    hist = load_history()
    entry = hist.pop(path, None)
    if entry and entry.get("job_id"):
        tr = load_transcripts()
        tr.pop(entry["job_id"], None)
        save_transcripts(tr)
    save_history(hist)
    return jsonify({"ok": True})

# ── Routes: qBittorrent ───────────────────────────────────────────────────────
@app.route("/api/torrents")
def api_torrents():
    torrents, err = get_qbit_torrents()
    return jsonify({"torrents": torrents, "error": err})

# ── Routes: stats / sankey ────────────────────────────────────────────────────
@app.route("/api/stats")
def api_stats():
    hist             = load_history()
    torrents, qb_err = get_qbit_torrents()

    # History counters
    cnt = {"integrale": 0, "season": 0, "skip": 0, "error_upload": 0}
    for e in hist.values():
        t = e.get("type", "unknown"); s = e.get("status", "done")
        if s == "error":               cnt["error_upload"] += 1
        elif t == "integrale":         cnt["integrale"] += 1
        elif t == "season":            cnt["season"] += 1
        else:                          cnt["skip"] += 1

    # qBit state counters
    qbit_cnt: dict[str, int] = {}
    for t in torrents:
        lbl = t["state_label"]
        qbit_cnt[lbl] = qbit_cnt.get(lbl, 0) + 1

    # Build Sankey nodes + links
    nodes, links = [], []

    def add_node(n):
        if not any(x["name"] == n for x in nodes):
            nodes.append({"name": n})

    def add_link(src, tgt, val):
        if val > 0:
            add_node(src); add_node(tgt)
            links.append({"source": src, "target": tgt, "value": val})

    src_lbl = "Dossier source"
    add_link(src_lbl, "INTEGRALE",     cnt["integrale"])
    add_link(src_lbl, "SAISON",        cnt["season"])
    add_link(src_lbl, "Ignoré",        cnt["skip"])
    add_link(src_lbl, "Erreur upload", cnt["error_upload"])

    # Distribute qBit states across types proportionally
    total_tracked = cnt["integrale"] + cnt["season"]
    if total_tracked > 0 and qbit_cnt:
        for lbl, n in qbit_cnt.items():
            # Allocate proportionally to integrale/season
            if cnt["integrale"] > 0:
                v = max(1, round(n * cnt["integrale"] / total_tracked))
                add_link("INTEGRALE", lbl, v)
            if cnt["season"] > 0:
                v = max(1, round(n * cnt["season"] / total_tracked))
                add_link("SAISON", lbl, v)

    # Totals
    total_size    = sum(t["size"]     for t in torrents)
    total_up      = sum(t["uploaded"] for t in torrents)
    total_up_spd  = sum(t["upload_speed"]   for t in torrents)
    total_dl_spd  = sum(t["download_speed"] for t in torrents)

    return jsonify({
        "sankey":  {"nodes": nodes, "links": links},
        "totals":  {
            "torrents":            len(torrents),
            "size_human":          _human(total_size),
            "uploaded_human":      _human(total_up),
            "ratio":               round(total_up / total_size, 2) if total_size else 0,
            "upload_speed_human":  _human(total_up_spd) + "/s",
            "download_speed_human":_human(total_dl_spd) + "/s",
        },
        "history": {
            "total": len(hist),
            "done":  sum(1 for v in hist.values() if v.get("status") == "done"),
            "error": sum(1 for v in hist.values() if v.get("status") == "error"),
        },
        "qbit_error": qb_err,
    })

# ── Routes: status ────────────────────────────────────────────────────────────
@app.route("/api/status")
def api_status():
    import urllib.request, urllib.error

    u3d     = load_unit3dbot()
    web_cfg = load_web_config()
    tc_cfg  = u3d.get("TORRENT_CLIENT_CONFIG", {})
    tr_cfg  = u3d.get("TRACKER_CONFIG", {})

    def _ms(t0): return round((time.time() - t0) * 1000)

    def check_source():
        t0  = time.time()
        src = web_cfg.get("source_folder", "")
        if not src:
            return {"id": "source", "icon": "bi-folder2-open", "color": "text-info",
                    "status": "warn", "detail": "", "ms": 0}
        p = Path(src)
        ok = p.exists() and p.is_dir() and os.access(src, os.R_OK)
        return {"id": "source", "icon": "bi-folder2-open", "color": "text-info",
                "status": "ok" if ok else "error", "detail": src, "ms": _ms(t0)}

    def check_binary():
        t0       = time.time()
        bin_path = str(VENV_BIN) if VENV_BIN.exists() else "unit3dup"
        p        = Path(bin_path)
        if p.exists() and os.access(str(p), os.X_OK):
            try:
                r   = subprocess.run([bin_path, "--version"], capture_output=True, text=True, timeout=5)
                out = (r.stdout or r.stderr or "").strip().split("\n")[0][:80]
                return {"id": "unit3dup", "icon": "bi-terminal-fill", "color": "text-secondary",
                        "status": "ok", "detail": out or str(p), "ms": _ms(t0)}
            except Exception:
                return {"id": "unit3dup", "icon": "bi-terminal-fill", "color": "text-secondary",
                        "status": "ok", "detail": str(p), "ms": _ms(t0)}
        # fallback: PATH lookup
        try:
            r = subprocess.run(["which", "unit3dup"], capture_output=True, text=True, timeout=2)
            if r.returncode == 0:
                return {"id": "unit3dup", "icon": "bi-terminal-fill", "color": "text-secondary",
                        "status": "ok", "detail": r.stdout.strip(), "ms": _ms(t0)}
        except Exception:
            pass
        return {"id": "unit3dup", "icon": "bi-terminal-fill", "color": "text-secondary",
                "status": "error", "detail": str(bin_path), "ms": _ms(t0)}

    def check_config():
        t0 = time.time()
        ok = UNIT3DBOT_JSON.exists()
        return {"id": "config", "icon": "bi-file-earmark-code-fill", "color": "text-warning",
                "status": "ok" if ok else "error", "detail": str(UNIT3DBOT_JSON), "ms": _ms(t0)}

    def check_torrent():
        t0     = time.time()
        client = (tc_cfg.get("TORRENT_CLIENT") or "qbittorrent").lower()
        icon   = "bi-hdd-network-fill"
        color  = "text-success"

        if client == "qbittorrent":
            host   = tc_cfg.get("QBIT_HOST", "localhost")
            port   = tc_cfg.get("QBIT_PORT", 8080)
            user   = tc_cfg.get("QBIT_USER", "admin")
            passwd = tc_cfg.get("QBIT_PASS") or ""
            if not host:
                return {"id": "torrent", "icon": icon, "color": color, "client": client,
                        "status": "warn", "detail": "", "ms": 0}
            try:
                import qbittorrentapi
                cl = qbittorrentapi.Client(
                    host=host, port=port, username=user, password=passwd,
                    VERIFY_WEBUI_CERTIFICATE=False, REQUESTS_ARGS={"timeout": 5},
                )
                cl.auth_log_in()
                ver = cl.app_version()
                cl.auth_log_out()
                return {"id": "torrent", "icon": icon, "color": color, "client": client,
                        "status": "ok", "detail": f"{host}:{port} — v{ver}", "ms": _ms(t0)}
            except Exception as exc:
                return {"id": "torrent", "icon": icon, "color": color, "client": client,
                        "status": "error", "detail": str(exc)[:120], "ms": _ms(t0)}

        elif client == "transmission":
            host = tc_cfg.get("TRASM_HOST", "localhost")
            port = int(tc_cfg.get("TRASM_PORT", 9091))
        elif client == "rtorrent":
            host = tc_cfg.get("RTORR_HOST", "localhost")
            port = int(tc_cfg.get("RTORR_PORT", 9091))
        else:
            return {"id": "torrent", "icon": icon, "color": color, "client": client,
                    "status": "warn", "detail": "", "ms": 0}

        try:
            s = socket.create_connection((host, port), timeout=5)
            s.close()
            return {"id": "torrent", "icon": icon, "color": color, "client": client,
                    "status": "ok", "detail": f"{host}:{port}", "ms": _ms(t0)}
        except Exception as exc:
            return {"id": "torrent", "icon": icon, "color": color, "client": client,
                    "status": "error", "detail": str(exc)[:120], "ms": _ms(t0)}

    def check_tracker():
        t0  = time.time()
        url = (tr_cfg.get("Gemini_URL") or "").rstrip("/")
        key = tr_cfg.get("Gemini_APIKEY") or ""
        if not url:
            return {"id": "tracker", "icon": "bi-broadcast", "color": "text-primary",
                    "status": "warn", "detail": "", "ms": 0}
        endpoint = f"{url}/api/torrents?api_token={key}&page[number]=1&page[size]=1" if key else url
        try:
            req = urllib.request.Request(
                endpoint,
                headers={"Accept": "application/json", "User-Agent": "G3MINI-Status/1.0"},
            )
            with urllib.request.urlopen(req, timeout=8) as r:
                ok = r.status < 500
            return {"id": "tracker", "icon": "bi-broadcast", "color": "text-primary",
                    "status": "ok" if ok else "error", "detail": url, "ms": _ms(t0)}
        except urllib.error.HTTPError as exc:
            # 4xx means server responded — it's reachable
            if exc.code < 500:
                return {"id": "tracker", "icon": "bi-broadcast", "color": "text-primary",
                        "status": "ok", "detail": f"{url} (HTTP {exc.code})", "ms": _ms(t0)}
            return {"id": "tracker", "icon": "bi-broadcast", "color": "text-primary",
                    "status": "error", "detail": f"HTTP {exc.code}", "ms": _ms(t0)}
        except Exception as exc:
            return {"id": "tracker", "icon": "bi-broadcast", "color": "text-primary",
                    "status": "error", "detail": str(exc)[:120], "ms": _ms(t0)}

    def check_tmdb():
        t0  = time.time()
        key = tr_cfg.get("TMDB_APIKEY") or ""
        if not key:
            return {"id": "tmdb", "icon": "bi-film", "color": "text-warning",
                    "status": "warn", "detail": "", "ms": 0}
        try:
            req = urllib.request.Request(
                f"https://api.themoviedb.org/3/configuration?api_key={key}",
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=8) as r:
                ok = r.status == 200
            return {"id": "tmdb", "icon": "bi-film", "color": "text-warning",
                    "status": "ok" if ok else "error",
                    "detail": "api.themoviedb.org", "ms": _ms(t0)}
        except urllib.error.HTTPError as exc:
            detail = "Clé API invalide" if exc.code == 401 else f"HTTP {exc.code}"
            return {"id": "tmdb", "icon": "bi-film", "color": "text-warning",
                    "status": "error", "detail": detail, "ms": _ms(t0)}
        except Exception as exc:
            return {"id": "tmdb", "icon": "bi-film", "color": "text-warning",
                    "status": "error", "detail": str(exc)[:120], "ms": _ms(t0)}

    _ORDER = ["source", "unit3dup", "config", "torrent", "tracker", "tmdb"]
    checks: list[dict] = []
    fns = [check_source, check_binary, check_config, check_torrent, check_tracker, check_tmdb]
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(fn): fn.__name__ for fn in fns}
        for f in as_completed(futures):
            try:
                checks.append(f.result())
            except Exception as exc:
                checks.append({"id": "unknown", "icon": "bi-question-circle",
                                "color": "text-danger", "status": "error",
                                "detail": str(exc), "ms": 0})

    checks.sort(key=lambda c: _ORDER.index(c["id"]) if c["id"] in _ORDER else 99)
    return jsonify({"checks": checks})


# ── Routes: watcher ───────────────────────────────────────────────────────────
@app.route("/api/watcher/status")
def api_watcher_status():
    return jsonify(_watcher.get_status())


@app.route("/api/watcher/start", methods=["POST"])
def api_watcher_start():
    ok, err = _watcher.start()
    if not ok:
        return jsonify({"ok": False, "error": err, "state": _watcher.get_status()}), 400
    return jsonify({"ok": True, "state": _watcher.get_status()})


@app.route("/api/watcher/stop", methods=["POST"])
def api_watcher_stop():
    _watcher.stop()
    return jsonify({"ok": True, "state": _watcher.get_status()})


@app.route("/api/watcher/input", methods=["POST"])
def api_watcher_input():
    _watcher.send_stdin((request.json or {}).get("text", ""))
    return jsonify({"ok": True})


# ── Routes: automanager ──────────────────────────────────────────────────────
@app.route("/api/automanage/status")
def api_am_status():
    return jsonify({"log": _auto_manager.get_log()})

@app.route("/api/automanage/run", methods=["POST"])
def api_am_run():
    _auto_manager.run_now()
    return jsonify({"ok": True})

# ── Socket.IO ─────────────────────────────────────────────────────────────────
@socketio.on("connect")
def on_connect():
    """Sync état hors du handshake WS (évite races Werkzeug + emit bloquant)."""
    sid = request.sid

    def _sync_client():
        try:
            with _jobs_lock:
                jobs = [j.to_dict() for j in _jobs.values()]
            socketio.emit("job_list", jobs, to=sid)
            st = _watcher.get_status()
            socketio.emit("watcher_state", st, to=sid)
            if st.get("running"):
                socketio.emit(
                    "watcher_console_sync",
                    {"lines": _watcher._console.display_lines()[-300:]},
                    to=sid,
                )
        except Exception as exc:
            app.logger.warning("Socket connect sync failed: %s", exc)

    socketio.start_background_task(_sync_client)


@socketio.on("stdin")
def on_stdin(data):
    """Rétrocompatibilité — le dashboard envoie stdin via POST /api/jobs/<id>/input."""
    jid = data.get("id")
    with _jobs_lock:
        job = _jobs.get(jid)
    if job:
        job.send_stdin(data.get("text", ""))


@socketio.on("watcher_stdin")
def on_watcher_stdin(data):
    _watcher.send_stdin((data or {}).get("text", ""))

# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Unit3Dup Dashboard")
    ap.add_argument("--host",  default=os.environ.get("U3D_WEB_HOST", "0.0.0.0"))
    ap.add_argument("--port",  default=int(os.environ.get("U3D_WEB_PORT", "5000")), type=int)
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()
    # eventlet (requirements.txt) : WebSocket stable ; threading + Werkzeug = erreurs write()
    socketio.run(
        app,
        host=args.host,
        port=args.port,
        debug=args.debug,
        use_reloader=False,
        allow_unsafe_werkzeug=_socketio_async_mode() == "threading",
    )
