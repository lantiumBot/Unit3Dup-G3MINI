"""Auto-scan scheduler — periodic background folder scan.

Uses a threading.Event for interruptible sleeps so config changes
(enabled/interval) take effect within seconds rather than waiting
for the current sleep to expire.  Call wakeup() or reload() after
saving the config to force an immediate re-check.
"""
import logging
import threading
import time
from datetime import datetime

from core.conf import load_web_config, load_valid_tags

_log   = logging.getLogger("core.autoscan")
_lock  = threading.Lock()
_event = threading.Event()   # set to interrupt the current sleep early


class _State:
    last_run:   str | None = None
    last_count: int = 0
    enabled:    bool = False
    interval_m: int = 60


_state = _State()


def get_status() -> dict:
    with _lock:
        return {
            "enabled":    _state.enabled,
            "interval_m": _state.interval_m,
            "last_run":   _state.last_run,
            "last_count": _state.last_count,
        }


def wakeup() -> None:
    """Interrupt the current inter-scan sleep (e.g. after a config change)."""
    _event.set()


def _do_scan() -> None:
    from extensions import socketio
    from core.scanner import scan_source

    cfg = load_web_config()
    src = cfg.get("source_folder", "")
    if not src:
        _log.warning("Auto-scan: source_folder non configuré — ignoré")
        return

    _log.info("Auto-scan démarré sur %s", src)
    items_result = []
    try:
        socketio.emit("scan_progress", {"phase": "scanning"})
        items, err, skipped = scan_source(
            src,
            load_valid_tags(),
            cfg.get("rules", {}),
            include_history=False,
            recursive=cfg.get("recursive_scan", False),
        )
        socketio.emit("scan_progress", {"phase": "done"})
        if err:
            _log.error("Auto-scan erreur : %s", err)
            return

        items_result = items
        to_upload = len([i for i in items if i.get("status") == "pending"])
        ran_at    = datetime.now().isoformat()

        with _lock:
            _state.last_run   = ran_at
            _state.last_count = to_upload

        socketio.emit("auto_scan_done", {
            "items":     items,
            "skipped":   skipped,
            "ran_at":    ran_at,
            "to_upload": to_upload,
        })
        _log.info("Auto-scan terminé : %d items, %d à uploader", len(items), to_upload)
    except Exception as exc:
        _log.exception("Auto-scan exception : %s", exc)
        try:
            from extensions import socketio as _sio
            _sio.emit("scan_progress", {"phase": "done"})
        except Exception:
            pass
        return

    # ── Auto-upload (G) ──────────────────────────────────────────────────────
    au_cfg = cfg.get("auto_upload", {})
    if not au_cfg.get("enabled"):
        return

    pending = [i for i in items_result if i.get("status") == "pending"]
    if not pending:
        return

    max_per_run = max(1, int(au_cfg.get("max_per_run", 5)))
    to_launch   = pending[:max_per_run]
    _log.info("Auto-upload : %d/%d items pending, lancement de %d jobs",
              len(pending), len(items_result), len(to_launch))

    try:
        import uuid
        from pathlib import Path
        from shared import _jobs, _jobs_lock, _job_queue, _queue_cv
        from core.conf import VENV_BIN, add_to_queue_state
        from core.job import Job

        u3d_bin = str(VENV_BIN) if VENV_BIN.exists() else "unit3dup"
        confirm = cfg.get("confirm_mode", False)
        created = []

        for item in to_launch:
            if item.get("type") == "collection":
                for file_path in (item.get("files") or []):
                    file_item = {
                        **item,
                        "id":      str(uuid.uuid4()),
                        "path":    file_path,
                        "name":    Path(file_path).name,
                        "type":    "file",
                        "status":  "pending",
                        "seasons": [],
                        "files":   [],
                    }
                    job = Job(file_item, u3d_bin, confirm)
                    with _jobs_lock:
                        _jobs[job.id] = job
                    with _queue_cv:
                        _job_queue.append(job.id)
                        _queue_cv.notify()
                    add_to_queue_state(job.id, file_item)
                    created.append(job.to_dict())
            else:
                job_item = {**item, "id": str(uuid.uuid4()), "status": "pending"}
                job = Job(job_item, u3d_bin, confirm)
                with _jobs_lock:
                    _jobs[job.id] = job
                with _queue_cv:
                    _job_queue.append(job.id)
                    _queue_cv.notify()
                add_to_queue_state(job.id, job_item)
                created.append(job.to_dict())

        if created:
            from extensions import socketio as _sio
            _sio.emit("job_list", created)
            _log.info("Auto-upload : %d jobs créés", len(created))
    except Exception as exc:
        _log.exception("Auto-upload : erreur création jobs — %s", exc)


def _loop() -> None:
    # Initial 30-second delay to let Flask fully start up
    _event.wait(30)
    _event.clear()

    while True:
        cfg        = load_web_config()
        as_cfg     = cfg.get("auto_scan", {})
        enabled    = bool(as_cfg.get("enabled", False))
        interval_m = max(5, int(as_cfg.get("interval_minutes", 60)))

        with _lock:
            _state.enabled    = enabled
            _state.interval_m = interval_m

        if enabled:
            _do_scan()

        # Wait for the configured interval, but wake up early if _event is set
        _event.wait(interval_m * 60)
        _event.clear()


threading.Thread(target=_loop, daemon=True, name="AutoScan").start()
