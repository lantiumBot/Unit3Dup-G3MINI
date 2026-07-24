"""Path constants, config I/O, history, transcripts — no Flask dependency."""
import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
WEB_DIR     = Path(__file__).parent.parent
PROJECT_DIR = WEB_DIR.parent
_cfg_env    = os.environ.get("UNIT3DUP_CONFIG_ROOT")
CONFIG_ROOT = Path(_cfg_env).expanduser() if _cfg_env else Path.home() / "Unit3Dup_config"

UNIT3DBOT_JSON           = CONFIG_ROOT / "Unit3Dbot.json"
VALID_TAGS_JSON          = PROJECT_DIR / "valid_tags.json"
WEB_CONFIG_JSON          = WEB_DIR / "web_config.json"
VENV_BIN                 = PROJECT_DIR / ".venv" / "bin" / "unit3dup"
LOGS_UPLOAD_DIR          = WEB_DIR / "logs" / "upload"
LOGS_APP_DIR             = WEB_DIR / "logs" / "app"

LOGS_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
LOGS_APP_DIR.mkdir(parents=True, exist_ok=True)

_log = logging.getLogger("core.conf")


# ── Byte formatter ────────────────────────────────────────────────────────────
def _human(b: int | float) -> str:
    b = float(b)
    for u in ("o", "Ko", "Mo", "Go", "To"):
        if abs(b) < 1024:
            return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} Po"


# ── Atomic / safe JSON I/O ────────────────────────────────────────────────────
def _safe_read_json(path: Path, default):
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
    # Resolve symlinks so writes go to the real target (critical in Docker where
    # web_config.json / valid_tags.json are symlinks into the /data volume).
    # os.replace() would otherwise replace the symlink itself with a plain file,
    # causing settings to be lost on container rebuild.
    real = path.resolve()
    tmp = real.with_suffix(real.suffix + ".tmp")
    kwargs: dict = {"indent": 2}
    if not ensure_ascii:
        kwargs["ensure_ascii"] = False
    tmp.write_text(json.dumps(data, **kwargs), encoding="utf-8")
    tmp.replace(real)


# ── Web config ────────────────────────────────────────────────────────────────
def _web_defaults() -> dict:
    return {
        "source_folder": "",
        "rules": {
            "integrale":          {"enabled": True, "upload_seasons": True},
            "complete_or_season": {"enabled": True, "require_valid_tag": True},
            # collection_tags: list of tag values that identify a multi-movie collection.
            # Empty list = legacy behaviour (any multi-video folder is a collection).
            # Example: ["Collection", "Pack", "Saga", "Trilogy"]
            "collection":         {"enabled": True, "require_valid_tag": False,
                                   "collection_tags": []},
        },
        "confirm_mode": False,
        "dry_run": False,
        "recursive_scan": False,
        "duplicate_ask_pct": 0.0,
        "duplicate_cache_ttl_sec": 0,
        "auto_manage": {
            "enabled": False,
            "interval_minutes": 60,
            "auto_remove": {"enabled": False, "after_days": 30, "min_seeders": 5},
            "auto_reseed": {"enabled": False, "below_seeders": 2, "gemini_dead_scan": False},
            "night_mode": {"enabled": False, "start_hour": 0, "end_hour": 7},
        },
        "auto_scan": {
            "enabled": False,
            "interval_minutes": 60,
        },
        "max_concurrent_jobs": 1,
        "source_folder_bookmarks": [],   # liste de dossiers sources favoris
        "auto_retry_on_error":     False,
        "auto_retry_max":          1,
        "job_timeout_minutes":     0,    # 0 = désactivé
        "auto_upload": {
            "enabled":     False,
            "max_per_run": 5,            # nombre max de jobs lancés par cycle auto-scan
        },
        "webhook_url": "",               # URL POST notifiée à chaque fin de job (vide = désactivé)
        "webhook_format": "raw",         # "raw" | "discord"
        "log_retention_days": 30,        # purge upload logs older than N days (0 = disabled)
        "inventory_cache_ttl_hours": 24,  # 0 = vérifie le count à chaque connexion
        "rss_categories": {
            "enabled": False,
            "categories": {
                "movies":             {"qbit_name": "Films",                "enabled": False},
                "series":             {"qbit_name": "Séries",               "enabled": False},
                "movies_animation":   {"qbit_name": "Films Animations",     "enabled": False},
                "series_animation":   {"qbit_name": "Séries Animations",    "enabled": False},
                "movies_documentary": {"qbit_name": "Films Documentaires",  "enabled": False},
                "series_documentary": {"qbit_name": "Séries Documentaires", "enabled": False},
            },
        },
    }


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into *base* (base is not mutated).

    Dict-valued keys are merged recursively so that a partial override (e.g.
    only ``rules.integrale`` present in web_config.json) preserves the default
    values of sibling keys (``rules.collection``, ``rules.complete_or_season``).
    Non-dict values in *override* always win.
    """
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


# ── web_config.json TTL cache ──────────────────────────────────────────────────
# load_web_config() is called on every request (_auth_guard, most routes,
# _max_concurrent).  A 5-second TTL reduces repeated disk reads to at most
# one per 5-second window under normal usage.
# save_web_config() invalidates the cache immediately after writing.
_cfg_cache_lock = threading.Lock()
_cfg_cache: dict = {"value": None, "ts": 0.0}
_CFG_CACHE_TTL  = 5.0   # seconds


def load_web_config() -> dict:
    """Return the merged web_config.json (defaults + disk values).

    Cached for up to _CFG_CACHE_TTL seconds to avoid redundant disk reads.
    A shallow copy is returned so callers cannot mutate the cached dict.
    """
    now = time.time()
    with _cfg_cache_lock:
        if _cfg_cache["value"] is not None and (now - _cfg_cache["ts"]) < _CFG_CACHE_TTL:
            return dict(_cfg_cache["value"])
    cfg = _web_defaults()
    raw = _safe_read_json(WEB_CONFIG_JSON, dict)
    if isinstance(raw, dict) and raw:
        cfg = _deep_merge(cfg, raw)
    with _cfg_cache_lock:
        _cfg_cache["value"] = cfg
        _cfg_cache["ts"]    = now
    return dict(cfg)


def _invalidate_cfg_cache() -> None:
    """Force the next load_web_config() call to re-read from disk."""
    with _cfg_cache_lock:
        _cfg_cache["value"] = None
        _cfg_cache["ts"]    = 0.0


def save_web_config(data: dict):
    """Persist *data* into web_config.json using a deep-merge strategy.

    The caller may pass a **partial** dict (only the keys to change).
    The current on-disk config is loaded first, merged recursively with *data*
    (nested dicts are merged, not replaced), then written atomically.
    The in-memory cache is invalidated immediately after the write.

    Example — update only one sub-key without losing the others::

        save_web_config({"rules": {"integrale": {"enabled": False}}})
        # Preserves rules.collection, rules.complete_or_season, etc.

    Auth-related changes (``auth_enabled``, ``auth_password_hash``) are handled
    by ``routes/auth_bp.py`` which calls ``invalidate_auth_cache()`` after saving
    so the 5-second TTL does not delay the effect.
    """
    merged = load_web_config()
    merged = _deep_merge(merged, data)
    _atomic_write_json(WEB_CONFIG_JSON, merged)
    _invalidate_cfg_cache()


# ── Unit3Dbot config ──────────────────────────────────────────────────────────
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
    if isinstance(obj, bool):
        return "true" if obj else "false"
    if isinstance(obj, dict):
        return {k: _stringify_unit3dbot_bools(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_stringify_unit3dbot_bools(v) for v in obj]
    return obj


def load_unit3dbot() -> dict:
    raw = _safe_read_json(UNIT3DBOT_JSON, dict)
    if not isinstance(raw, dict) or not raw:
        return {}
    return {_JSON_TO_UPPER.get(k, k): v for k, v in raw.items()}


def save_unit3dbot(data: dict):
    CONFIG_ROOT.mkdir(parents=True, exist_ok=True)
    normalized = {_UPPER_TO_JSON.get(k, k): v for k, v in data.items()}
    normalized = _stringify_unit3dbot_bools(normalized)
    _atomic_write_json(UNIT3DBOT_JSON, normalized, ensure_ascii=False)
    # Reset the CLI Load() singleton so the next scan picks up the new config.
    # Load() uses __new__ and caches config_settings at first call — without this
    # reset, changes made via the web UI (DUPLICATE_ON, SIZE_TH, …) would be
    # ignored until the process restarts.
    try:
        from common.settings import Load
        Load._instance = None
    except Exception:
        pass


# ── Valid tags ────────────────────────────────────────────────────────────────
def load_valid_tags() -> list[str]:
    raw = _safe_read_json(VALID_TAGS_JSON, dict)
    if not isinstance(raw, dict):
        return []
    return raw.get("tags", [])


def save_valid_tags(tags: list[str]):
    _atomic_write_json(VALID_TAGS_JSON, {"tags": tags})


# ── Tracker helpers ───────────────────────────────────────────────────────────
def default_tracker_name() -> str | None:
    """Return the first entry of MULTI_TRACKER from Unit3Dbot.json, lowercased.

    Single definition shared by duplicate.py and gemini_inventory.py to avoid
    duplicated logic.
    """
    multi = load_unit3dbot().get("TRACKER_CONFIG", {}).get("MULTI_TRACKER") or []
    if isinstance(multi, list) and multi:
        return str(multi[0]).lower()
    return None


# ── History ───────────────────────────────────────────────────────────────────
def load_history() -> dict:
    """Load full history dict.  Delegates to SQLite (via db.py)."""
    from core.db import db_load_history
    return db_load_history()


def save_history(data: dict) -> None:
    """Replace history (bulk).  Delegates to SQLite (via db.py)."""
    from core.db import db_save_history
    db_save_history(data)


def load_transcripts() -> dict:
    """Load all transcripts dict.  Delegates to SQLite (via db.py)."""
    from core.db import db_load_transcripts
    return db_load_transcripts()


def save_transcripts(data: dict) -> None:
    """Bulk-replace transcripts.  Kept for backward compat; prefer db_save_transcript per entry."""
    from core.db import db_save_transcript
    for job_id, entry in data.items():
        if entry and isinstance(entry, dict):
            db_save_transcript(job_id, entry)


def save_job_transcript(job) -> None:
    """Persists structured JSON + console transcript for a completed job."""
    from core.db import db_save_transcript
    item_snapshot = {
        k: job.item.get(k)
        for k in ("id", "name", "path", "type", "tag", "tag_valid", "status",
                  "duplicate", "seasons")
        if k in job.item
    }
    entry = {
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
    # Write individual job log to logs/upload/<job_id>.json (rotation handled separately)
    log_path = LOGS_UPLOAD_DIR / f"{job.id}.json"
    try:
        _atomic_write_json(log_path, entry, ensure_ascii=False)
        _rotate_upload_logs()
    except Exception as exc:
        _log.warning("Impossible d'écrire %s : %s", log_path, exc)
    # Persist to SQLite (replaces history_transcripts.json)
    db_save_transcript(job.id, entry)


def add_to_history(job) -> None:
    from core.db import db_upsert_history_paths
    paths = [job.item["path"]] + job.item.get("seasons", [])
    proto = {
        "type":         job.item["type"],
        "tag":          job.item.get("tag", ""),
        "processed_at": job.ended_at or datetime.now().isoformat(),
        "status":       job.status,
        "job_id":       job.id,
        "source":       None,
        "tracker_id":   None,
    }
    db_upsert_history_paths(paths, proto)


# ── Upload log rotation ───────────────────────────────────────────────────────
_UPLOAD_LOG_MAX = 500  # keep at most this many .json files in logs/upload/


def _rotate_upload_logs(max_files: int = _UPLOAD_LOG_MAX) -> None:
    """Delete old job log files — by count (max_files) and by age (log_retention_days)."""
    try:
        files = sorted(
            LOGS_UPLOAD_DIR.glob("*.json"),
            key=lambda f: f.stat().st_mtime,
        )
        # Purge by age
        try:
            retention = int(load_web_config().get("log_retention_days") or 0)
        except Exception:
            retention = 0
        if retention > 0:
            import time as _t
            cutoff = _t.time() - retention * 86400
            for f in list(files):
                try:
                    if f.stat().st_mtime < cutoff:
                        f.unlink()
                        files.remove(f)
                except OSError:
                    pass
        # Purge by count
        excess = len(files) - max_files
        for f in files[:excess]:
            try:
                f.unlink()
            except OSError:
                pass
    except Exception as exc:
        _log.debug("_rotate_upload_logs: %s", exc)


# ── Job queue persistence (D) — backed by SQLite job_queue table ──────────────
# _QUEUE_LOCK removed: SQLite WAL + _DB_LOCK in db.py handle concurrency.
# The legacy QUEUE_JSON / INVENTORY_STATE_JSON / DUPLICATE_CACHE_JSON path
# constants have been moved to core/db.py (only needed there for migration).

def load_queue_state() -> dict:
    """Return all pending queue entries as {job_id: item_dict}."""
    from core.db import db_queue_load
    return db_queue_load()


def add_to_queue_state(job_id: str, item: dict) -> None:
    """Persist a new job to the SQLite queue."""
    from core.db import db_queue_add
    db_queue_add(job_id, item)


def remove_from_queue_state(job_id: str) -> None:
    """Remove a finished job from the SQLite queue."""
    from core.db import db_queue_remove
    db_queue_remove(job_id)


# ── Transcript helpers ────────────────────────────────────────────────────────
def _history_entry_full(entry: dict) -> dict:
    from core.job import _parse_transcript_to_result
    from core.stream import normalize_transcript
    out = dict(entry)
    if not out.get("result") and out.get("transcript"):
        out["result"] = _parse_transcript_to_result(out["transcript"], out)
    raw   = out.get("transcript", "") or ""
    norm  = normalize_transcript(raw) if raw else ""
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


def _transcript_meaningful_line_count(text: str) -> int:
    import re
    _PROGRESS_RE = re.compile(
        r"(\d+%\s*\d+/\d+|\d+it\s*\[|\?it/s|it/s\]|/s\]|/it\]|█|▏|▎|▍|▌|▋|▊|▉)"
    )
    _ANSI = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
    n = 0
    for ln in text.splitlines():
        s = _ANSI.sub("", ln).strip()
        if not s or s.startswith("▶") or s.startswith("─"):
            continue
        if _PROGRESS_RE.search(s):
            continue
        n += 1
    return n


def _build_transcript_from_result(result: dict) -> str:
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
