import json
from flask import Blueprint, Response, jsonify, request
from core.conf import (
    load_web_config, save_web_config,
    load_unit3dbot, save_unit3dbot,
    load_valid_tags, save_valid_tags,
    _JSON_TO_UPPER,
)

bp = Blueprint("settings", __name__)


@bp.route("/api/settings")
def api_get_settings():
    return jsonify({
        "web":        load_web_config(),
        "unit3dbot":  load_unit3dbot(),
        "valid_tags": load_valid_tags(),
    })


@bp.route("/api/settings", methods=["POST"])
def api_save_settings():
    d = request.json or {}
    if "web" in d:
        save_web_config(d["web"])
        # Wake up autoscan thread if interval/enabled changed
        if "auto_scan" in d["web"] or "auto_upload" in d["web"]:
            try:
                from core.autoscan import wakeup
                wakeup()
            except Exception:
                pass
    if "unit3dbot"  in d:
        _invalidate_if_tracker_changed(d["unit3dbot"])
        # Merge into existing config so sections the UI doesn't manage (e.g.
        # console_options) are preserved rather than overwritten with null/missing.
        existing = load_unit3dbot()
        for section, values in d["unit3dbot"].items():
            if isinstance(values, dict) and isinstance(existing.get(section), dict):
                existing[section].update(values)
            else:
                existing[section] = values
        save_unit3dbot(existing)
    if "valid_tags" in d: save_valid_tags(d["valid_tags"])
    return jsonify({"ok": True})


@bp.route("/api/settings/import", methods=["POST"])
def api_import_settings():
    """Import a Unit3Dbot.json file — merges all sections into the current config."""
    data = request.json
    if not data or not isinstance(data, dict):
        return jsonify({"ok": False, "error": "Fichier JSON vide ou invalide"}), 400
    # Normalize section names to UPPER (load_unit3dbot returns UPPER, save expects UPPER)
    incoming = {_JSON_TO_UPPER.get(k, k): v for k, v in data.items()}
    _invalidate_if_tracker_changed(incoming)
    existing = load_unit3dbot()
    for section, values in incoming.items():
        if isinstance(values, dict) and isinstance(existing.get(section), dict):
            existing[section].update(values)
        else:
            existing[section] = values
    save_unit3dbot(existing)
    return jsonify({"ok": True})


@bp.route("/api/settings/bookmarks", methods=["POST"])
def api_bookmarks():
    """Add or remove a source-folder bookmark.
    Body: {"action": "add"|"remove", "path": "/mnt/folder"}
    """
    d      = request.json or {}
    action = d.get("action")
    path   = (d.get("path") or "").strip()
    if not path or action not in ("add", "remove"):
        return jsonify({"ok": False, "error": "action et path requis"}), 400

    cfg       = load_web_config()
    bookmarks = cfg.get("source_folder_bookmarks") or []
    if action == "add":
        if path not in bookmarks:
            bookmarks = [path] + bookmarks          # plus récent en premier
    else:
        bookmarks = [b for b in bookmarks if b != path]
    cfg["source_folder_bookmarks"] = bookmarks[:20]  # limite à 20 entrées
    save_web_config(cfg)
    return jsonify({"ok": True, "bookmarks": cfg["source_folder_bookmarks"]})


@bp.route("/api/settings/export")
def api_export_web_config():
    """Download the current web_config.json as a file."""
    cfg  = load_web_config()
    data = json.dumps(cfg, indent=2, ensure_ascii=False)
    return Response(
        data,
        mimetype="application/json",
        headers={"Content-Disposition": 'attachment; filename="web_config.json"'},
    )


@bp.route("/api/settings/web-import", methods=["POST"])
def api_import_web_config():
    """Import a previously exported web_config.json — deep-merges into current config."""
    data = request.json
    if not data or not isinstance(data, dict):
        return jsonify({"ok": False, "error": "Fichier JSON vide ou invalide"}), 400
    # Sensitive fields are never imported to prevent accidental credential overwrite
    for _sensitive in ("auth_password_hash",):
        data.pop(_sensitive, None)
    save_web_config(data)
    return jsonify({"ok": True})


def _invalidate_if_tracker_changed(new_unit3dbot: dict) -> None:
    """Invalidate inventory cache if tracker credentials or username changed."""
    old = load_unit3dbot().get("TRACKER_CONFIG", {})
    new = new_unit3dbot.get("TRACKER_CONFIG", {})
    watched = ("Gemini_URL", "Gemini_APIKEY", "Gemini_USERNAME")
    if any(old.get(k) != new.get(k) for k in watched):
        from core.gemini_inventory import invalidate_inventory_cache
        invalidate_inventory_cache()
