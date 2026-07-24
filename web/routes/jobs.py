import logging
import os
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path
from flask import Blueprint, abort, jsonify, request
from extensions import socketio
from shared import _jobs, _jobs_lock, _job_queue, _queue_cv
from core.conf import load_web_config, load_valid_tags, VENV_BIN, add_to_queue_state
from core.job import Job
from core.scanner import scan_source
from core.duplicate import apply_duplicate_checks, apply_phase4_checks, _duplicate_skip_threshold_pct, _duplicate_cache_ttl_sec
from core.gemini_inventory import sync_gemini_inventory, invalidate_inventory_cache
from core.tmdb_search import enrich_items_with_tmdb, apply_documentary_tag
from core.db import db_scan_cache_get, db_scan_cache_set, db_scan_cache_clear

bp  = Blueprint("jobs", __name__)
_log = logging.getLogger("routes.jobs")

# ── Async scan task registry ───────────────────────────────────────────────────
_scan_tasks: dict = {}   # task_id -> {"status": "running"|"done"|"error", "result": dict|None, "error": str|None}
_scan_task_lock = threading.Lock()


@bp.route("/api/scan", methods=["POST"])
def api_scan():
    d   = request.json or {}
    cfg = load_web_config()
    src = d.get("source_folder") or cfg.get("source_folder", "")
    if not src:
        return jsonify({"error": "source_folder non défini"}), 400
    include_history = bool(d.get("include_history", False))
    force_inventory = bool(d.get("force_inventory", False))
    recursive       = bool(d.get("recursive", cfg.get("recursive_scan", False)))

    task_id   = str(uuid.uuid4())
    cancel_ev = threading.Event()
    with _scan_task_lock:
        _scan_tasks[task_id] = {"status": "running", "result": None, "error": None, "cancel": cancel_ev}

    def _run():
        def _mark_cancelled():
            with _scan_task_lock:
                if task_id in _scan_tasks:
                    _scan_tasks[task_id]["status"] = "cancelled"
            socketio.emit("scan_done", {"task_id": task_id, "cancelled": True})

        try:
            socketio.emit("scan_progress", {"phase": "inventory", "page": 0, "fetched": 0})
            inv_added, inv_total, inv_cached = sync_gemini_inventory(src, force=force_inventory)

            if cancel_ev.is_set():
                _mark_cancelled()
                return

            socketio.emit("scan_progress", {"phase": "scanning"})
            vt = load_valid_tags()
            items, err, skipped = scan_source(
                src, vt, cfg.get("rules", {}),
                include_history=include_history,
                recursive=recursive,
            )
            if err:
                with _scan_task_lock:
                    _scan_tasks[task_id]["status"] = "error"
                    _scan_tasks[task_id]["error"]  = err
                socketio.emit("scan_progress", {"phase": "done"})
                socketio.emit("scan_done", {"task_id": task_id, "error": err})
                return

            if cancel_ev.is_set():
                _mark_cancelled()
                return

            socketio.emit("scan_progress", {"phase": "tmdb", "done": 0, "total": 0})
            items = enrich_items_with_tmdb(items, socketio=socketio, valid_tags=vt)

            if cancel_ev.is_set():
                _mark_cancelled()
                return

            socketio.emit("scan_progress", {"phase": "duplicates"})
            items, rate_limited, unchecked_paths = apply_duplicate_checks(items)

            result = {
                "items":   items,
                "skipped": skipped,
                "duplicate_skip_threshold_pct": _duplicate_skip_threshold_pct(),
                "duplicate_cache_ttl_sec":      _duplicate_cache_ttl_sec(),
                "inventory_added":  inv_added,
                "inventory_total":  inv_total,
                "inventory_cached": inv_cached,
                "rate_limited":     rate_limited,
                "unchecked_paths":  unchecked_paths,
            }
            with _scan_task_lock:
                _scan_tasks[task_id]["status"] = "done"
                _scan_tasks[task_id]["result"] = result
            socketio.emit("scan_done", {"task_id": task_id})
        except Exception as exc:
            _log.exception("api_scan task %s: unexpected error — %s", task_id, exc)
            with _scan_task_lock:
                _scan_tasks[task_id]["status"] = "error"
                _scan_tasks[task_id]["error"]  = str(exc)
            socketio.emit("scan_done", {"task_id": task_id, "error": str(exc)})
        finally:
            socketio.emit("scan_progress", {"phase": "done"})

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return jsonify({"task_id": task_id})


@bp.route("/api/scan/status/<task_id>")
def api_scan_status(task_id):
    with _scan_task_lock:
        task = _scan_tasks.get(task_id)
    if not task:
        return jsonify({"status": "not_found"}), 404
    if task["status"] == "done":
        result = task["result"]
        with _scan_task_lock:
            _scan_tasks.pop(task_id, None)  # cleanup
        return jsonify({"status": "done", "result": result})
    if task["status"] == "error":
        err = task["error"]
        with _scan_task_lock:
            _scan_tasks.pop(task_id, None)
        return jsonify({"status": "error", "error": err})
    if task["status"] == "cancelled":
        with _scan_task_lock:
            _scan_tasks.pop(task_id, None)
        return jsonify({"status": "cancelled"})
    return jsonify({"status": "running"})


@bp.route("/api/scan/<task_id>/cancel", methods=["POST"])
def api_scan_cancel(task_id):
    """Signal the running scan thread to stop after its current phase."""
    with _scan_task_lock:
        task = _scan_tasks.get(task_id)
    if not task or task["status"] != "running":
        return jsonify({"ok": False, "error": "not_running"}), 404
    task["cancel"].set()
    return jsonify({"ok": True})


@bp.route("/api/scan/tmdb", methods=["POST"])
def api_check_tmdb():
    """Re-run TMDB enrichment on provided items (from a previous scan).

    Existing tmdb_id values are preserved unless force=true is passed.
    When force=true:
      - All tmdb_id/tmdb_title are cleared and a fresh name-search runs.
      - For items that come back with no match but had a prior tmdb_id,
        that ID is preserved and a direct /movie|tv/{id} lookup is done
        to populate tmdb_title and tmdb_year.
    """
    from core.tmdb_search import tmdb_lookup_by_id
    d     = request.json or {}
    items = d.get("items", [])
    force = bool(d.get("force", False))
    if not items:
        return jsonify({"items": []})

    # Locked items: user manually set the tmdb_id — skip name-search, enrich by ID only.
    # Non-locked items: normal flow (clear + name-search + ID fallback).
    saved_ids:   dict = {}   # non-locked items that had an ID before clearing
    locked_ids:  dict = {}   # items with tmdb_id_locked=true

    if force:
        for it in items:
            tid      = it.get("tmdb_id") or 0
            is_locked = bool(it.get("tmdb_id_locked") and tid > 0)
            call_type = _tmdb_call_type(it)
            if is_locked:
                # Keep tmdb_id so enrich_items_with_tmdb skips the name-search;
                # clear stale title/year/origin so we re-fetch them by ID.
                locked_ids[it["id"]] = {"tmdb_id": int(tid), "type": call_type}
                it.pop("tmdb_title",  None)
                it.pop("tmdb_year",   None)
                it.pop("tmdb_origin", None)
            else:
                if tid:
                    saved_ids[it["id"]] = {"tmdb_id": int(tid), "type": call_type}
                it.pop("tmdb_id",     None)
                it.pop("tmdb_title",  None)
                it.pop("tmdb_year",   None)
                it.pop("tmdb_origin", None)

    vt = load_valid_tags()
    try:
        socketio.emit("scan_progress", {"phase": "tmdb", "done": 0, "total": 0})
        items = enrich_items_with_tmdb(items, socketio=socketio, valid_tags=vt)

        # For non-locked items the name-search missed, look up by saved ID.
        if saved_ids:
            for it in items:
                if (it.get("tmdb_id") or 0) == 0:
                    saved = saved_ids.get(it.get("id", ""))
                    if saved:
                        result = tmdb_lookup_by_id(saved["tmdb_id"], saved["type"])
                        if result:
                            it["tmdb_id"]     = result["tmdb_id"]
                            it["tmdb_title"]  = result.get("tmdb_title", "")
                            if result.get("tmdb_year"):
                                it["tmdb_year"]   = result["tmdb_year"]
                            if result.get("tmdb_origin"):
                                it["tmdb_origin"] = result["tmdb_origin"]
                            apply_documentary_tag(it, result, vt)
                        else:
                            it["tmdb_id"] = saved["tmdb_id"]

        # For locked items: enrich_items_with_tmdb skipped them (tmdb_id already set).
        # Do an ID lookup to get fresh title / year / origin for the user's chosen ID.
        from core.scanner import _parse_season_from_text
        for it in items:
            locked = locked_ids.get(it.get("id", ""))
            if not locked:
                continue
            result = tmdb_lookup_by_id(locked["tmdb_id"], locked["type"])
            if result:
                it["tmdb_id"]     = locked["tmdb_id"]          # always keep user's ID
                it["tmdb_title"]  = result.get("tmdb_title", "")
                if result.get("tmdb_year"):
                    it["tmdb_year"]   = result["tmdb_year"]
                if result.get("tmdb_origin"):
                    it["tmdb_origin"] = result["tmdb_origin"]
                apply_documentary_tag(it, result, vt)
            # If season_num wasn't detected at scan time, try text-based extraction now
            if not it.get("season_num"):
                s = _parse_season_from_text(it.get("name", ""))
                if s is not None:
                    it["season_num"] = s

        # Fill tmdb_origin for items that have a tmdb_id but no origin yet.
        needs_origin = [
            it for it in items
            if (it.get("tmdb_id") or 0) and not (it.get("tmdb_origin") or "")
        ]
        for it in needs_origin:
            result = tmdb_lookup_by_id(int(it["tmdb_id"]), it.get("type", "file"))
            if result and result.get("tmdb_origin"):
                it["tmdb_origin"] = result["tmdb_origin"]

        return jsonify({"items": items})
    except Exception as exc:
        _log.exception("api_check_tmdb: unexpected error — %s", exc)
        return jsonify({"error": f"Erreur interne : {exc}"}), 500
    finally:
        socketio.emit("scan_progress", {"phase": "done"})


@bp.route("/api/scan/recheck-tmdb-dup", methods=["POST"])
def api_recheck_tmdb_dup():
    """Re-run Phase 4 only (TMDB-ID duplicate detection) on provided items.

    Called after the user updates a TMDB ID and clicks btn-recheck-tmdb.
    Stale Phase 4 results are reset before re-running so the slate is clean.
    """
    d     = request.json or {}
    items = d.get("items", [])
    if not items:
        return jsonify({"items": []})
    try:
        socketio.emit("scan_progress", {"phase": "duplicates"})
        items = apply_phase4_checks(items)
        return jsonify({"items": items})
    except Exception as exc:
        _log.exception("api_recheck_tmdb_dup: %s", exc)
        return jsonify({"error": f"Erreur interne : {exc}"}), 500
    finally:
        socketio.emit("scan_progress", {"phase": "done"})


@bp.route("/api/scan/duplicates", methods=["POST"])
def api_check_duplicates():
    """Re-run duplicate check on provided items (from a previous scan)."""
    d     = request.json or {}
    items = d.get("items", [])
    if not items:
        return jsonify({"items": []})
    try:
        socketio.emit("scan_progress", {"phase": "duplicates"})
        items, rate_limited, unchecked_paths = apply_duplicate_checks(items)
        return jsonify({
            "items": items,
            "duplicate_skip_threshold_pct": _duplicate_skip_threshold_pct(),
            "duplicate_cache_ttl_sec":      _duplicate_cache_ttl_sec(),
            "rate_limited":    rate_limited,
            "unchecked_paths": unchecked_paths,
        })
    except Exception as exc:
        _log.exception("api_check_duplicates: unexpected error — %s", exc)
        return jsonify({"error": f"Erreur interne : {exc}"}), 500
    finally:
        socketio.emit("scan_progress", {"phase": "done"})


@bp.route("/api/jobs")
def api_list_jobs():
    with _jobs_lock:
        return jsonify([j.to_dict() for j in _jobs.values()])


@bp.route("/api/jobs", methods=["POST"])
def api_create_jobs():
    d       = request.json or {}
    cfg     = load_web_config()
    u3d_bin = str(VENV_BIN) if VENV_BIN.exists() else "unit3dup"
    confirm = cfg.get("confirm_mode", False)
    created = []
    for item in d.get("items", []):
        if item.get("status") not in ("pending", "duplicate_ask"):
            continue

        if item.get("type") == "collection":
            # Expand collection: one job per video file in the collection
            for file_path in (item.get("files") or []):
                file_item = {
                    **item,
                    "id":      str(uuid.uuid4()),
                    "path":    file_path,
                    "name":    Path(file_path).name,
                    "type":    "file",
                    "status":  "pending",
                    "seasons": [],
                    "files":   [],
                }
                job = Job(file_item, u3d_bin, confirm)
                with _jobs_lock:
                    _jobs[job.id] = job
                with _queue_cv:
                    _job_queue.append(job.id)
                    _queue_cv.notify()
                add_to_queue_state(job.id, file_item)
                created.append(job.to_dict())
        else:
            job = Job(item, u3d_bin, confirm)
            with _jobs_lock:
                _jobs[job.id] = job
            with _queue_cv:
                _job_queue.append(job.id)
                _queue_cv.notify()
            add_to_queue_state(job.id, item)
            created.append(job.to_dict())
    if created:
        socketio.emit("job_list", created)
    return jsonify({"created": created})


@bp.route("/api/jobs/<jid>", methods=["DELETE"])
def api_cancel_job(jid):
    with _jobs_lock:
        job = _jobs.get(jid)
    if not job:
        abort(404)
    job.cancel()
    return jsonify({"ok": True})


@bp.route("/api/jobs/<jid>/input", methods=["POST"])
def api_job_input(jid):
    with _jobs_lock:
        job = _jobs.get(jid)
    if not job:
        return jsonify({"ok": False, "error": "job_not_found"}), 404
    text = (request.json or {}).get("text", "")
    ok, reason = job.send_stdin(text)
    if not ok:
        return jsonify({"ok": False, "error": reason}), 409
    return jsonify({"ok": True})


@bp.route("/api/jobs/reorder", methods=["POST"])
def api_reorder_jobs():
    ids = (request.json or {}).get("ids", [])
    with _queue_cv:
        pending_set = set(_job_queue)
        reordered   = [jid for jid in ids if jid in pending_set]
        remaining   = [jid for jid in _job_queue if jid not in set(reordered)]
        _job_queue[:] = reordered + remaining
    return jsonify({"ok": True})


@bp.route("/api/jobs/<jid>/retry", methods=["POST"])
def api_retry_job(jid):
    with _jobs_lock:
        old = _jobs.get(jid)
    if not old or old.status not in ("error", "cancelled"):
        return jsonify({"ok": False, "error": "not_terminal"}), 409
    cfg     = load_web_config()
    u3d_bin = str(VENV_BIN) if VENV_BIN.exists() else "unit3dup"
    confirm = cfg.get("confirm_mode", False)
    new_item = {**old.item, "id": str(uuid.uuid4()), "status": "pending"}
    job = Job(new_item, u3d_bin, confirm)
    with _jobs_lock:
        _jobs[job.id] = job
    with _queue_cv:
        _job_queue.append(job.id)
        _queue_cv.notify()
    add_to_queue_state(job.id, new_item)
    socketio.emit("job_list", [job.to_dict()])
    return jsonify({"created": job.to_dict()})


@bp.route("/api/jobs/skip-duplicates", methods=["POST"])
def api_skip_duplicates():
    """Save ignored duplicate / duplicate_ask items to history so they don't re-appear at next scan.

    Accepts items with status "duplicate" (auto-skipped) or "duplicate_ask" (user-ignored).
    Both are written with status="duplicate" and source="ignored_duplicate".
    """
    from core.db import db_upsert_history_paths
    items = (request.json or {}).get("items", [])
    now   = datetime.now().isoformat()
    saved = 0
    for item in items:
        if item.get("status") not in ("duplicate", "duplicate_ask"):
            continue
        path = item.get("path", "")
        if not path:
            continue
        paths = [path] + [s for s in item.get("seasons", []) if s]
        db_upsert_history_paths(paths, {
            "type":         item.get("type", ""),
            "tag":          item.get("tag", ""),
            "processed_at": now,
            "status":       "duplicate",
            "job_id":       None,
            "source":       "ignored_duplicate",
            "tracker_id":   None,
        })
        saved += len(paths)
    return jsonify({"saved": saved})


@bp.route("/api/jobs/ignore-selected", methods=["POST"])
def api_ignore_selected():
    """Save any selected scan items to history so they don't re-appear at next scan."""
    from core.db import db_upsert_history_paths
    items = (request.json or {}).get("items", [])
    now   = datetime.now().isoformat()
    saved = 0
    for item in items:
        path = item.get("path", "")
        if not path:
            continue
        paths = [path] + [s for s in item.get("seasons", []) if s]
        db_upsert_history_paths(paths, {
            "type":         item.get("type", ""),
            "tag":          item.get("tag", ""),
            "processed_at": now,
            "status":       "ignored",
            "job_id":       None,
            "source":       "ignored",
            "tracker_id":   None,
        })
        saved += len(paths)
    return jsonify({"saved": saved})


_VIDEO_EXTS = frozenset({".mkv", ".mp4", ".avi", ".m2ts", ".ts"})


def _find_video_file(item: dict) -> "Path | None":
    """Return the video file to run MediaInfo on for a given scan item.

    For file items, returns the path directly if it is a video file.
    For pack items (season/integrale/collection), returns the largest video
    file inside the folder (representative sample for the pack).
    """
    raw_path = item.get("path", "")
    if not raw_path:
        return None
    p = Path(raw_path)
    # Direct video file
    if p.is_file() and p.suffix.lower() in _VIDEO_EXTS:
        return p
    # Folder (episode folder, movie folder, season pack, integrale…):
    # pick the largest video file inside (the .mkv is always larger than the .nfo).
    if p.is_dir():
        candidates = [f for f in p.rglob("*") if f.is_file() and f.suffix.lower() in _VIDEO_EXTS]
        return max(candidates, key=lambda f: f.stat().st_size) if candidates else None
    return None


def _get_mediainfo(item: dict) -> "tuple[str | None, bool]":
    """Return (mediainfo_text, is_silent) using the same MediaFile class as the CLI.

    Mirrors UploadBot.normalize_release_name() in unit3dup/upload.py.
    Returns (None, False) when MediaInfo is unavailable or the file is missing.
    """
    video_file = _find_video_file(item)
    if not video_file:
        return None, False
    try:
        from common.mediainfo import MediaFile
        mf = MediaFile(str(video_file))
        return mf.info or None, mf.is_silent
    except Exception as exc:
        _log.warning("MediaFile failed for %s: %s", video_file, exc)
        return None, False


# Detects SXX season number anywhere inside a directory/file name.
# Negative lookbehind: not preceded by a letter.
# Negative lookahead: not followed by a digit or 'E' (avoids matching SxxEyy).
_SEASON_IN_DIR_RE = re.compile(r'(?<![A-Za-z])S(\d{1,2})(?![0-9E])', re.IGNORECASE)
_EP01_IN_FILE_RE  = re.compile(r'[Ss]01[Ee]01', re.IGNORECASE)
_VIDEO_EXTS_NORM  = {'.mkv', '.avi', '.mp4', '.mov', '.ts', '.m2ts', '.wmv', '.flv'}
_YEAR_TOKEN_RE    = re.compile(r'\.\((?:19|20)\d{2}\)')


def _strip_year_token(src: str) -> str:
    """Remove .(YYYY) tokens from a dot-separated release name.

    Used so the normalizer falls through to tv_year instead of using the
    per-season year embedded in the folder name (e.g. S02 (2018)).
    """
    result = _YEAR_TOKEN_RE.sub('.', src)
    return re.sub(r'\.{2,}', '.', result).strip('.')


def _scan_season_dirs(path: str) -> list[tuple[int, str]]:
    """Return sorted [(season_num, dir_name), …] for season sub-directories."""
    result = []
    try:
        for entry in os.scandir(path):
            if entry.is_dir():
                m = _SEASON_IN_DIR_RE.search(entry.name)
                if m:
                    result.append((int(m.group(1)), entry.name))
    except (PermissionError, OSError):
        pass
    return sorted(result)


def _find_s01e01_stem(season_dir: str) -> str | None:
    """Return the stem (no extension) of the S01E01 video file in *season_dir*."""
    try:
        for entry in os.scandir(season_dir):
            if entry.is_file() and Path(entry.name).suffix.lower() in _VIDEO_EXTS_NORM:
                if _EP01_IN_FILE_RE.search(entry.name):
                    return Path(entry.name).stem
    except (PermissionError, OSError):
        pass
    return None


def _find_first_video_stem(path: str) -> str | None:
    """Return the stem of the first video file found in *path* (non-recursive)."""
    try:
        for entry in sorted(os.scandir(path), key=lambda e: e.name):
            if entry.is_file() and Path(entry.name).suffix.lower() in _VIDEO_EXTS_NORM:
                return Path(entry.name).stem
    except (PermissionError, OSError):
        pass
    return None


def _tmdb_call_type(item: dict) -> str:
    """Return the TMDB endpoint category to use for this item.

    Delegates to core.tmdb_search._item_search_type which handles the
    episode-file → TV endpoint mapping (TMDB has separate ID spaces for
    movies and TV shows — same integer = different entity on each endpoint).
    """
    from core.tmdb_search import _item_search_type
    return _item_search_type(item)


def _apply_origin_lang_fix(name: str, tmdb_origin: str) -> str:
    """Fix language codes based on TMDB production country.

    VFF → VOF when origin contains FR (France original, not VF dub)
    VFQ → VOQ when origin contains CA (Quebec original)
    """
    if not tmdb_origin or not name:
        return name
    origins = {o.strip().upper() for o in tmdb_origin.split(",")}
    if "FR" in origins:
        name = re.sub(r'(?<![A-Za-z])VFF(?![A-Za-z])', 'VOF', name)
    if "CA" in origins:
        name = re.sub(r'(?<![A-Za-z])VFQ(?![A-Za-z])', 'VOQ', name)
    return name


def _build_sub_items(
    item: dict,
    normalize_fn=None,
    tv_year: int | None = None,
    tmdb_title: str | None = None,
    is_documentary: bool = False,
) -> list[dict]:
    """Return sub-upload preview items for integrale and S01 season packs.

    Each sub-item carries:
      ``source_name`` — the CLI release name (folder/file stem with spaces→dots).
                        Used as lookup key in U3D_CUSTOM_NAME_MAP.
      ``name``        — normalised upload name (pre-filled in the editable input).
    """
    item_type  = item.get("type", "")
    item_path  = item.get("path", "")
    season_num = item.get("season_num")

    def _norm(src: str, pack: bool, real_path: str | None = None) -> str:
        if normalize_fn:
            try:
                mi_text: str | None = None
                is_silent: bool = False
                if real_path:
                    fake = {"path": real_path, "type": "season" if pack else "file"}
                    mi_text, is_silent = _get_mediainfo(fake)
                return normalize_fn(
                    src,
                    mediainfo_text=mi_text,
                    is_silent=is_silent,
                    torrent_pack=pack,
                    tv_year=tv_year,
                    tmdb_title=tmdb_title,
                    is_documentary=is_documentary,
                )
            except Exception:
                pass
        return src

    if item_type == "integrale":
        season_dirs = _scan_season_dirs(item_path) if item_path else []
        if not season_dirs:
            return []
        sub_items = []
        s01_dir_path: str | None = None
        for sn, dir_name in season_dirs:
            src          = dir_name.replace(" ", ".")
            season_dir   = os.path.join(item_path, dir_name)
            # Strip the per-season year so the normalizer falls through to tv_year
            # (series premiere year) instead of e.g. 2018 for S02.
            src_norm = _strip_year_token(src)
            sub_items.append({
                "type":        "season",
                "label":       f"Saison {sn}",
                "source_name": src,       # original key for CLI U3D_CUSTOM_NAME_MAP lookup
                "name":        _norm(src_norm, pack=True, real_path=season_dir),
            })
            if sn == 1:
                s01_dir_path = season_dir
        # S01E01 episode
        if s01_dir_path:
            ep_stem = _find_s01e01_stem(s01_dir_path)
            if ep_stem:
                ep_file = next(
                    (str(f) for f in Path(s01_dir_path).iterdir()
                     if f.stem == ep_stem and f.suffix.lower() in _VIDEO_EXTS_NORM),
                    None,
                )
                ep_src = ep_stem.replace(" ", ".")
                sub_items.append({
                    "type":        "episode",
                    "label":       "S01E01",
                    "source_name": ep_src,
                    "name":        _norm(ep_src, pack=False, real_path=ep_file or s01_dir_path),
                })
        return sub_items

    if item_type == "season" and season_num == 1:
        ep_stem = _find_s01e01_stem(item_path) if item_path else None
        if not ep_stem:
            return []
        ep_file = next(
            (str(f) for f in Path(item_path).iterdir()
             if f.stem == ep_stem and f.suffix.lower() in _VIDEO_EXTS_NORM),
            None,
        )
        ep_src = ep_stem.replace(" ", ".")
        return [{
            "type":        "episode",
            "label":       "S01E01",
            "source_name": ep_src,
            "name":        _norm(ep_src, pack=False, real_path=ep_file or item_path),
        }]

    return []


@bp.route("/api/normalize", methods=["POST"])
def api_normalize_names():
    """Return the normalized upload name for each submitted item.

    Each item must have at least ``id`` and ``name``.  The normalizer is
    called with ``torrent_pack=True`` for season / integrale / collection
    types so that the ``COMPLETE`` suffix is handled correctly.
    MediaInfo is run on the actual video file when available to improve
    codec, language and audio detection.

    Response: ``{"items": [{"id": ..., "name": ..., "normalized": ...}]}``
    """
    items = (request.json or {}).get("items", [])
    try:
        from unit3dup.release_normalizer import normalize_release_name
    except ImportError as exc:
        _log.warning("release_normalizer unavailable: %s", exc)
        # Graceful degradation: return names unchanged
        return jsonify({
            "items": [
                {"id": it.get("id", ""), "name": it.get("name", ""), "normalized": it.get("name", ""), "sub_items": []}
                for it in items
            ]
        })

    from core.tmdb_search import get_tmdb_english_title

    result = []
    for item in items:
        name      = item.get("name", "")
        item_type = item.get("type", "file")
        is_pack   = item_type in ("season", "integrale", "collection")

        if not name:
            result.append({"id": item.get("id", ""), "name": name, "normalized": name, "sub_items": []})
            continue

        # Build parent_names context so the normalizer can fill in
        # lang/source/codec/team missing from the release name itself.
        parent_names = None
        raw_path = item.get("path", "")
        if item_type == "file":
            # Episode: use the season/show folder name as context.
            if raw_path:
                parent = Path(raw_path).parent.name
                if parent and parent not in (".", ""):
                    parent_names = [parent]
        elif item_type == "integrale":
            # Integrale folder names are often sparse (e.g. "Show - iNTEGRALE (YYYY)").
            # Use the first season subdirectory name — it carries the full tech spec.
            if raw_path:
                season_dirs = _scan_season_dirs(raw_path)
                if season_dirs:
                    parent_names = [season_dirs[0][1]]  # first season dir name
        elif item_type == "season":
            # Season pack: use the first video file in the folder as context
            # in case the folder name itself is sparse.
            if raw_path:
                stem = _find_first_video_stem(raw_path)
                if stem:
                    parent_names = [stem]

        mediainfo_text, is_silent = _get_mediainfo(item)

        tmdb_year_raw = item.get("tmdb_year")
        tv_year = int(tmdb_year_raw) if tmdb_year_raw and str(tmdb_year_raw).isdigit() else None

        # International title: use English TMDB title unless the film is from France.
        # "Film français" = primary production country is FR → keep French extracted title.
        # Use _tmdb_call_type so episode files target /tv/ before /movie/ — TMDB
        # maintains separate ID spaces and the same integer can be a different entity.
        tmdb_origin    = item.get("tmdb_origin", "") or ""
        tmdb_id_raw    = item.get("tmdb_id") or 0
        tmdb_call_type = _tmdb_call_type(item)

        # When tmdb_origin is missing (manually edited ID — client clears stale origin),
        # fetch it from TMDB so that is_french and VFF→VOF corrections are accurate.
        if tmdb_id_raw and not tmdb_origin:
            from core.tmdb_search import tmdb_lookup_by_id as _lu_fn
            try:
                _lu = _lu_fn(int(tmdb_id_raw), tmdb_call_type)
                if _lu and _lu.get("tmdb_origin"):
                    tmdb_origin = _lu["tmdb_origin"]
            except Exception as exc:
                _log.debug("origin lookup tmdb_id=%s: %s", tmdb_id_raw, exc)

        origins   = {o.strip().upper() for o in tmdb_origin.split(",") if o.strip()} if tmdb_origin else set()
        is_french = "FR" in origins   # film from France → keep French title
        eng_title: str | None = None
        if tmdb_id_raw and not is_french:
            try:
                eng_title = get_tmdb_english_title(int(tmdb_id_raw), tmdb_call_type)
            except Exception as exc:
                _log.debug("get_tmdb_english_title(%s): %s", tmdb_id_raw, exc)

        is_doc = (item.get("tag") or "").upper() == "DOC"
        try:
            normalized = normalize_release_name(
                name,
                mediainfo_text=mediainfo_text,
                is_silent=is_silent,
                torrent_pack=is_pack,
                parent_names=parent_names,
                tv_year=tv_year,
                tmdb_title=eng_title,
                is_documentary=is_doc,
            )
        except Exception as exc:
            _log.warning("normalize_release_name(%r): %s", name, exc)
            normalized = name
        normalized = _apply_origin_lang_fix(normalized, tmdb_origin)
        sub_items  = _build_sub_items(
            item,
            normalize_fn=normalize_release_name,
            tv_year=tv_year,
            tmdb_title=eng_title,
            is_documentary=is_doc,
        )
        sub_items  = [
            {**s, "name": _apply_origin_lang_fix(s["name"], tmdb_origin)}
            for s in sub_items
        ]
        result.append({"id": item.get("id", ""), "name": name, "normalized": normalized, "sub_items": sub_items})

    return jsonify({"items": result})


@bp.route("/api/jobs/clear", methods=["POST"])
def api_clear_jobs():
    with _jobs_lock:
        done = [jid for jid, j in _jobs.items() if j.status in ("done", "error", "cancelled")]
        for jid in done:
            del _jobs[jid]
    socketio.emit("jobs_cleared", {"ids": done})
    return jsonify({"cleared": len(done), "ids": done})


# ── Scan cache (server-side) ───────────────────────────────────────────────────

@bp.route("/api/scan/cache", methods=["GET"])
def api_scan_cache_get_ep():
    folder = request.args.get("folder", "").strip()
    if not folder:
        return jsonify({"items": None})
    items = db_scan_cache_get(folder)
    return jsonify({"items": items})


@bp.route("/api/scan/cache", methods=["POST"])
def api_scan_cache_set_ep():
    d      = request.json or {}
    folder = d.get("folder", "").strip()
    items  = d.get("items", [])
    action = d.get("action", "set")
    if not folder:
        return jsonify({"ok": False, "error": "folder required"}), 400
    if action == "clear":
        db_scan_cache_clear(folder)
    else:
        db_scan_cache_set(folder, items)
    return jsonify({"ok": True})
