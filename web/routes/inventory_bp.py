"""Inventory management endpoints.

GET  /api/inventory/status         — état courant (dernier run, total, running…)
POST /api/inventory/sync           — déclenche une sync en arrière-plan
                                      body: {force: bool}  (optionnel)
GET  /api/inventory/items          — liste paginée des entrées d'inventaire
                                      query: ?page=1&limit=50&search=
GET  /api/inventory/download/<id>  — proxy de téléchargement .torrent
"""
import logging
import math

import requests as _requests
from flask import Blueprint, Response, jsonify, request, stream_with_context

from core.conf import load_web_config, load_unit3dbot
from core.gemini_inventory import (
    get_inventory_status,
    get_inventory_list,
    trigger_background_inventory,
)

bp  = Blueprint("inventory", __name__)
_log = logging.getLogger("routes.inventory")


@bp.route("/api/inventory/status")
def api_inventory_status():
    return jsonify(get_inventory_status())


@bp.route("/api/inventory/sync", methods=["POST"])
def api_inventory_sync():
    d     = request.json or {}
    force = bool(d.get("force", False))

    cfg = load_web_config()
    src = cfg.get("source_folder", "")
    if not src:
        return jsonify({"error": "source_folder non défini"}), 400

    started = trigger_background_inventory(src, force=force)
    return jsonify({"started": started, "already_running": not started})


@bp.route("/api/inventory/items")
def api_inventory_items():
    search = request.args.get("search", "").strip().lower()
    page   = max(1, int(request.args.get("page",  1)   or 1))
    limit  = min(200, max(10, int(request.args.get("limit", 100) or 100)))
    sort   = request.args.get("sort",  "date")   # "name" | "date"
    order  = request.args.get("order", "desc")   # "asc"  | "desc"

    all_rows = list(get_inventory_list())

    # Search
    if search:
        all_rows = [r for r in all_rows if search in (r.get("name") or "").lower()]

    # Sort
    reverse = (order == "desc")
    if sort == "name":
        all_rows.sort(key=lambda r: (r.get("name") or "").lower(), reverse=reverse)
    else:  # date
        all_rows.sort(key=lambda r: r.get("processed_at") or "", reverse=reverse)

    total  = len(all_rows)
    offset = (page - 1) * limit
    rows   = all_rows[offset : offset + limit]

    u3d        = load_unit3dbot()
    tr_cfg     = u3d.get("TRACKER_CONFIG", {})
    gemini_url = (tr_cfg.get("Gemini_URL") or "").rstrip("/")

    for row in rows:
        tid = row.get("tracker_id")
        # Tracker page: {url}/torrents/{id}
        row["tracker_url"]  = f"{gemini_url}/torrents/{tid}" if tid and gemini_url else None
        # Direct .torrent download: {url}/torrents/download/{id}  (browser session auth)
        row["download_url"] = f"{gemini_url}/torrents/download/{tid}" if tid and gemini_url else None

    return jsonify({
        "rows":  rows,
        "total": total,
        "page":  page,
        "pages": math.ceil(total / limit) if total else 1,
        "limit": limit,
    })


@bp.route("/api/inventory/seeding")
def api_inventory_seeding():
    """Return set of Gemini tracker_ids currently seeding in qBittorrent.

    Detection via torrent comment field (UNIT3D sets it to the tracker page URL).
    Cached 60 s in core.torrent.
    """
    from core.torrent import get_qbit_gemini_seeding_ids
    u3d        = load_unit3dbot()
    gemini_url = (u3d.get("TRACKER_CONFIG", {}).get("Gemini_URL") or "").rstrip("/")
    ids, err   = get_qbit_gemini_seeding_ids(gemini_url)
    return jsonify({"ids": sorted(ids), "count": len(ids), "error": err})


@bp.route("/api/inventory/download/<int:tracker_id>")
def api_inventory_download(tracker_id: int):
    u3d        = load_unit3dbot()
    tr_cfg     = u3d.get("TRACKER_CONFIG", {})
    gemini_url = (tr_cfg.get("Gemini_URL") or "").rstrip("/")
    api_key    = tr_cfg.get("Gemini_APIKEY") or ""
    if not gemini_url:
        return jsonify({"error": "Gemini_URL non configuré"}), 400

    url     = f"{gemini_url}/torrents/{tracker_id}/download"
    headers = {"Accept": "*/*", "User-Agent": "G3MINI-Inventory/1.0"}
    params  = {}
    if api_key:
        params["api_token"] = api_key

    try:
        upstream = _requests.get(url, params=params, headers=headers, timeout=20, stream=True)
    except _requests.RequestException as exc:
        return jsonify({"error": str(exc)}), 502

    if upstream.status_code != 200:
        return jsonify({"error": f"Tracker returned {upstream.status_code}"}), upstream.status_code

    content_disposition = (
        upstream.headers.get("Content-Disposition")
        or f'attachment; filename="torrent_{tracker_id}.torrent"'
    )
    return Response(
        stream_with_context(upstream.iter_content(chunk_size=8192)),
        status=200,
        content_type="application/x-bittorrent",
        headers={"Content-Disposition": content_disposition},
    )
