from flask import Blueprint, jsonify
from core.automanager import _auto_manager

bp = Blueprint("automanage", __name__)


@bp.route("/api/automanage/status")
def api_am_status():
    return jsonify({"log": _auto_manager.get_log()})


@bp.route("/api/automanage/run", methods=["POST"])
def api_am_run():
    _auto_manager.run_now()
    return jsonify({"ok": True})
