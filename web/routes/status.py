from flask import Blueprint, jsonify
from core.checker import get_status_checks

bp = Blueprint("status_api", __name__)


@bp.route("/api/status")
def api_status():
    checks = get_status_checks()
    return jsonify({"checks": checks})
