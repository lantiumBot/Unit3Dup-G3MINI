"""Socket.IO event handlers — imported once by create_app() to register decorators."""
from flask import request as flask_request
from extensions import socketio
from shared import _jobs, _jobs_lock
from core.watcher import _watcher


@socketio.on("connect")
def on_connect():
    from flask import session as _sess
    from core.auth import auth_enabled, is_logged_in, _SERVER_EPOCH
    if auth_enabled():
        # Reject if not logged in OR if the session epoch is stale (server restarted)
        if not is_logged_in() or _sess.get("u3d_epoch") != _SERVER_EPOCH:
            return False  # reject unauthenticated / stale socket connections
    sid = flask_request.sid

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
            import logging
            logging.getLogger("sockets").warning("Socket connect sync failed: %s", exc)

    socketio.start_background_task(_sync_client)

    # Déclenche une sync inventory silencieuse si le TTL est expiré
    try:
        from core.conf import load_web_config
        from core.gemini_inventory import should_refresh_inventory, trigger_background_inventory
        _src = load_web_config().get("source_folder", "")
        if _src and should_refresh_inventory(_src):
            trigger_background_inventory(_src)
    except Exception:
        pass


@socketio.on("stdin")
def on_stdin(data):
    jid = data.get("id")
    with _jobs_lock:
        job = _jobs.get(jid)
    if job:
        job.send_stdin(data.get("text", ""))


@socketio.on("watcher_stdin")
def on_watcher_stdin(data):
    _watcher.send_stdin((data or {}).get("text", ""))
