"""Auto-scan status route."""
from flask import Blueprint, jsonify

bp = Blueprint("autoscan", __name__)


@bp.route("/api/autoscan/status")
def api_autoscan_status():
    from core.autoscan import get_status
    return jsonify(get_status())
