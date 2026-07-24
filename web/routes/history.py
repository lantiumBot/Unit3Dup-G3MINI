import csv
import io
from pathlib import Path
from flask import Blueprint, Response, abort, jsonify, request, stream_with_context
from core.conf import _history_entry_full
from core.db import (
    db_history_query,
    db_load_history,
    db_save_history,
    db_get_transcript,
    db_delete_transcript,
    db_clear_transcripts,
    db_delete_history_path,
    db_load_transcripts,
    db_transcript_job_ids,
    db_iter_history_csv_chunks,
)
from core.torrent import get_qbit_torrents

bp = Blueprint("history", __name__)


@bp.route("/api/history")
def api_history():
    try:
        page  = max(1, int(request.args.get("page",  1)))
        limit = max(1, min(200, int(request.args.get("limit", 50))))
    except (ValueError, TypeError):
        page, limit = 1, 50

    search    = request.args.get("search",    "").lower().strip()
    ftype     = request.args.get("type",      "").strip()
    fstatus   = request.args.get("status",    "").strip()
    date_from = request.args.get("date_from", "").strip()
    date_to   = request.args.get("date_to",   "").strip()

    # Paginated, filtered SQL query — no full load into memory
    rows, total = db_history_query(
        search=search, ftype=ftype, fstatus=fstatus,
        date_from=date_from, date_to=date_to,
        page=page, limit=limit
    )

    # Enrich with live qBit data (cached 15 s) and transcript flags
    torrents, _ = get_qbit_torrents()
    tmap = {t["name"].lower(): t for t in torrents}

    # Batch-check transcript existence — single query instead of N per-row queries
    job_ids_on_page = [r.get("job_id") for r in rows if r.get("job_id")]
    transcript_ids  = db_transcript_job_ids(job_ids_on_page)

    enriched = []
    for r in rows:
        name    = r.get("name") or Path(r["path"]).name
        job_id  = r.get("job_id") or ""
        torrent = tmap.get(name.lower())
        has_tr  = job_id in transcript_ids
        enriched.append({
            "path":         r["path"],
            "name":         name,
            "type":         r.get("type", "?"),
            "tag":          r.get("tag", ""),
            "processed_at": r.get("processed_at", ""),
            "status":       r.get("status", "done"),
            "job_id":       job_id,
            "has_detail":   has_tr,
            "has_result":   has_tr,
            "torrent":      torrent,
        })

    return jsonify({
        "rows":  enriched,
        "total": total,
        "page":  page,
        "pages": max(1, (total + limit - 1) // limit),
        "limit": limit,
    })


@bp.route("/api/history/detail/<job_id>")
def api_history_detail(job_id):
    entry = db_get_transcript(job_id)
    if not entry:
        abort(404)
    return jsonify(_history_entry_full(entry))


@bp.route("/api/history/transcript/<job_id>")
def api_history_transcript(job_id):
    return api_history_detail(job_id)


@bp.route("/api/history", methods=["DELETE"])
def api_history_clear():
    db_save_history({})
    db_clear_transcripts()
    return jsonify({"ok": True})


@bp.route("/api/history/item", methods=["DELETE"])
def api_history_remove():
    path   = (request.json or {}).get("path", "")
    job_id = db_delete_history_path(path)
    if job_id:
        db_delete_transcript(job_id)
    return jsonify({"ok": True})


@bp.route("/api/history/recheck", methods=["POST"])
def api_history_recheck():
    """Re-check a list of history paths against the tracker duplicate API.

    Body: {"paths": ["/path/to/item", ...]}
    Returns: {"results": [{"path": ..., "status": "duplicate"|"ok"|"error", "detail": ...}]}
    """
    import logging
    _rlog = logging.getLogger("routes.history.recheck")

    paths = (request.json or {}).get("paths", [])
    if not paths:
        return jsonify({"results": []})

    try:
        from core.duplicate import gemini_duplicate_check
    except ImportError:
        return jsonify({"ok": False, "error": "Module duplicate non disponible"}), 500

    results = []
    for path in paths:
        try:
            is_dup, detail = gemini_duplicate_check(path)
            results.append({
                "path":   path,
                "status": "duplicate" if is_dup else "ok",
                "detail": detail,
            })
        except Exception as exc:
            _rlog.warning("Recheck échoué pour %s : %s", path, exc)
            results.append({"path": path, "status": "error", "detail": str(exc)})

    return jsonify({"results": results})


@bp.route("/api/history/export.csv")
def api_history_export_csv():
    """Stream the CSV export using a cursor-based SQL iterator.

    Works correctly for 50 000+ entries: each 500-row chunk is serialised and
    flushed independently — the full table never has to fit in memory.
    Response is streamed via Flask's stream_with_context so the connection
    stays alive until all rows have been sent.
    """
    fields = ["name", "type", "tag", "processed_at", "status", "path"]

    def generate():
        # Header line
        buf = io.StringIO()
        csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore").writeheader()
        yield buf.getvalue()

        # Data in 500-row chunks — db cursor never loads the full table
        for chunk in db_iter_history_csv_chunks(500):
            buf = io.StringIO()
            writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
            for row in chunk:
                writer.writerow(row)
            yield buf.getvalue()

    return Response(
        stream_with_context(generate()),
        mimetype="text/csv",
        headers={"Content-Disposition": 'attachment; filename="history.csv"'},
    )
