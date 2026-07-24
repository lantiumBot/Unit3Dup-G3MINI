from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for
from core.auth import (
    auth_enabled, is_logged_in, verify_password, hash_password,
    is_locked_out, record_failure, clear_failures, _SERVER_EPOCH,
    invalidate_auth_cache,
)
from core.conf import load_web_config, save_web_config

bp = Blueprint("auth", __name__)


@bp.route("/login")
def login_page():
    if not auth_enabled() or is_logged_in():
        return redirect(url_for("pages.page_jobs"))
    return render_template("login.html")


@bp.route("/api/auth/login", methods=["POST"])
def api_login():
    if is_locked_out():
        return jsonify({"ok": False, "error": "too_many_attempts"}), 429
    password = (request.json or {}).get("password", "")
    if not verify_password(password):
        record_failure()
        return jsonify({"ok": False, "error": "wrong_password"}), 401
    clear_failures()
    session.permanent = bool((request.json or {}).get("remember"))
    session["u3d_auth"]  = True
    session["u3d_epoch"] = _SERVER_EPOCH
    return jsonify({"ok": True})


@bp.route("/api/auth/logout", methods=["POST"])
def api_logout():
    session.pop("u3d_auth", None)
    return jsonify({"ok": True})


@bp.route("/api/auth/password", methods=["POST"])
def api_set_password():
    """Enable auth and set / change the password."""
    if auth_enabled() and not is_logged_in():
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    d        = request.json or {}
    current  = d.get("current_password", "")
    new_pwd  = d.get("new_password", "")
    if not new_pwd or len(new_pwd) < 6:
        return jsonify({"ok": False, "error": "too_short"}), 400
    # If auth already active, require current password confirmation
    if auth_enabled() and not verify_password(current):
        return jsonify({"ok": False, "error": "wrong_current"}), 401
    cfg = load_web_config()
    cfg["auth_enabled"]       = True
    cfg["auth_password_hash"] = hash_password(new_pwd)
    save_web_config(cfg)
    invalidate_auth_cache()
    session["u3d_auth"]  = True
    session["u3d_epoch"] = _SERVER_EPOCH
    return jsonify({"ok": True})


@bp.route("/api/auth/disable", methods=["POST"])
def api_disable_auth():
    """Disable authentication entirely."""
    if auth_enabled() and not is_logged_in():
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    cfg = load_web_config()
    cfg["auth_enabled"]       = False
    cfg["auth_password_hash"] = ""
    save_web_config(cfg)
    invalidate_auth_cache()
    return jsonify({"ok": True})


@bp.route("/api/auth/status")
def api_auth_status():
    cfg = load_web_config()
    # Epoch check: if the server restarted _SERVER_EPOCH changed, so the old
    # cookie is stale even though is_logged_in() still returns True.
    # This endpoint is exempt from _auth_guard, so we must check here ourselves.
    epoch_ok = session.get("u3d_epoch") == _SERVER_EPOCH
    actually_logged_in = is_logged_in() and epoch_ok
    return jsonify({
        "auth_enabled":            auth_enabled(),
        "logged_in":               actually_logged_in,
        "session_timeout_minutes": int(cfg.get("session_timeout_minutes") or 0),
    })
