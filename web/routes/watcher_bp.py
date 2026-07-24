from flask import Blueprint, jsonify, request
from core.watcher import _watcher

bp = Blueprint("watcher", __name__)


@bp.route("/api/watcher/status")
def api_watcher_status():
    return jsonify(_watcher.get_status())


@bp.route("/api/watcher/start", methods=["POST"])
def api_watcher_start():
    ok, err = _watcher.start()
    if not ok:
        return jsonify({"ok": False, "error": err, "state": _watcher.get_status()}), 400
    return jsonify({"ok": True, "state": _watcher.get_status()})


@bp.route("/api/watcher/stop", methods=["POST"])
def api_watcher_stop():
    _watcher.stop()
    return jsonify({"ok": True, "state": _watcher.get_status()})


@bp.route("/api/watcher/input", methods=["POST"])
def api_watcher_input():
    _watcher.send_stdin((request.json or {}).get("text", ""))
    return jsonify({"ok": True})
