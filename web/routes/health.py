"""Health-check endpoint."""
import time
from flask import Blueprint, jsonify

_start_time = time.time()
_VERSION    = "0.8.21"

bp = Blueprint("health", __name__)


@bp.route("/api/health")
def api_health():
    return jsonify({
        "status":  "ok",
        "version": _VERSION,
        "uptime":  int(time.time() - _start_time),
    })
