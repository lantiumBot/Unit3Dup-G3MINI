from flask import Blueprint, jsonify, request
from core.conf import _human
from core.db import db_history_stats, db_history_chart_data
from core.torrent import get_qbit_torrents, _QBIT_STATE

bp = Blueprint("stats", __name__)


@bp.route("/api/stats/chart")
def api_stats_chart():
    days = min(365, max(7, int(request.args.get("days", 30))))
    return jsonify(db_history_chart_data(days))


@bp.route("/api/torrents")
def api_torrents():
    torrents, err = get_qbit_torrents()
    return jsonify({"torrents": torrents, "error": err})


@bp.route("/api/stats")
def api_stats():
    # B: SQL GROUP BY — no full history load into memory regardless of table size
    hs               = db_history_stats()
    cnt              = hs["counts"]
    torrents, qb_err = get_qbit_torrents()

    qbit_cnt: dict[str, int] = {}
    for t in torrents:
        lbl = t["state_label"]
        qbit_cnt[lbl] = qbit_cnt.get(lbl, 0) + 1

    nodes: list[dict] = []
    links: list[dict] = []

    def add_node(n):
        if not any(x["name"] == n for x in nodes):
            nodes.append({"name": n})

    def add_link(src, tgt, val):
        if val > 0:
            add_node(src); add_node(tgt)
            links.append({"source": src, "target": tgt, "value": val})

    src_lbl = "Dossier source"
    add_link(src_lbl, "INTEGRALE",     cnt["integrale"])
    add_link(src_lbl, "SAISON",        cnt["season"])
    add_link(src_lbl, "Ignoré",        cnt["skip"])
    add_link(src_lbl, "Erreur upload", cnt["error_upload"])

    total_tracked = cnt["integrale"] + cnt["season"]
    if total_tracked > 0 and qbit_cnt:
        for lbl, n in qbit_cnt.items():
            if cnt["integrale"] > 0:
                v = max(1, round(n * cnt["integrale"] / total_tracked))
                add_link("INTEGRALE", lbl, v)
            if cnt["season"] > 0:
                v = max(1, round(n * cnt["season"] / total_tracked))
                add_link("SAISON", lbl, v)

    total_size   = sum(t["size"]     for t in torrents)
    total_up     = sum(t["uploaded"] for t in torrents)
    total_up_spd = sum(t["upload_speed"]   for t in torrents)
    total_dl_spd = sum(t["download_speed"] for t in torrents)

    return jsonify({
        "sankey": {"nodes": nodes, "links": links},
        "totals": {
            "torrents":             len(torrents),
            "size_human":           _human(total_size),
            "uploaded_human":       _human(total_up),
            "ratio":                round(total_up / total_size, 2) if total_size else 0,
            "upload_speed_human":   _human(total_up_spd) + "/s",
            "download_speed_human": _human(total_dl_spd) + "/s",
        },
        "history": {
            "total": hs["total"],
            "done":  hs["done"],
            "error": hs["error"],
        },
        "qbit_error": qb_err,
    })
