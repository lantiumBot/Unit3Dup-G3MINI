"""Duplicate detection: Gemini API + SQLite TTL cache (C).

4-phase pipeline (called from apply_duplicate_checks):
  Phase 1 — inventory match    : paths in history with source='gemini_inventory'
  Phase 2 — local history match: remaining history paths
  Phase 3 — Gemini name search : existing online check (keyword / title)
  Phase 4 — Gemini TMDB search : online check by TMDB ID for pending items that
             have a tmdb_id; also populates ``gemini_releases`` on every item
             that has a tmdb_id (for the releases column in the scan preview).

Cache storage was previously duplicate_check_cache.json (flat JSON file with a
threading.Lock for read-modify-write safety).  It is now backed by the SQLite
dup_cache table via db_dup_cache_lookup / db_dup_cache_store / db_dup_cache_prune.

Benefits:
  - No more full-file read on every cache hit
  - TTL filtering happens in SQL (WHERE checked_at >= ?) — no Python-side prune
    needed before every lookup
  - _dup_cache_lock removed: SQLite WAL + _DB_LOCK in db.py handle concurrency
  - Periodic prune is now a simple DELETE statement (cheap, O(expired rows))
"""
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path


class RateLimitError(Exception):
    """Raised when the tracker returns HTTP 429 — propagates out of ThreadPoolExecutor."""


# ── Source-type normalisation ─────────────────────────────────────────────────
# Maps lowercased / stripped source strings to a broad category so that
# a local WEB release is never flagged as a duplicate of a Blu-ray release.
# Guessit `source` values and Gemini API `type` values are both covered.
_SOURCE_GROUPS: dict[str, str] = {
    # Blu-ray (physical disc, any processing)
    "bluray":  "bluray",
    "bdrip":   "bluray",
    "bdmux":   "bluray",
    "remux":   "bluray",
    # Web streaming
    "webdl":   "web",
    "webrip":  "web",
    "web":     "web",
    # TV broadcast
    "hdtv":    "tv",
    "pdtv":    "tv",
    "sdtv":    "tv",
    "dsr":     "tv",
    # DVD
    "dvdrip":  "dvd",
    "dvd":     "dvd",
    # HD-DVD
    "hddvd":   "hddvd",
    # Low-quality / pre-release
    "cam":     "cam",
    "ts":      "cam",
    "tc":      "cam",
    "scr":     "cam",
    "r5":      "cam",
    "dvdscr":  "cam",
}


def _source_group(source: str) -> str:
    """Normalise a source/type string to a broad category."""
    key = source.lower().replace("-", "").replace(" ", "").replace("_", "")
    return _SOURCE_GROUPS.get(key, "")


def _source_types_match(local: str, remote: str) -> bool:
    """Return True if both sources belong to the same group, or if either is unknown."""
    if not local or not remote:
        return True
    g1 = _source_group(local)
    g2 = _source_group(remote)
    if not g1 or not g2:
        return True  # unrecognised group — don't filter
    return g1 == g2

_log = logging.getLogger("core.duplicate")

from core.conf import (
    load_unit3dbot,
    load_web_config,
    default_tracker_name,
)
from core.db import (
    db_dup_cache_lookup,
    db_dup_cache_store,
    db_dup_cache_prune,
)


# ── Config helpers ────────────────────────────────────────────────────────────

def _duplicate_skip_threshold_pct() -> float:
    try:
        return float(load_web_config().get("duplicate_ask_pct", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def _duplicate_cache_ttl_sec() -> int:
    try:
        return max(0, int(load_web_config().get("duplicate_cache_ttl_sec", 0) or 0))
    except (TypeError, ValueError):
        return 0


# ── Cache key ─────────────────────────────────────────────────────────────────

def _dup_cache_key(path_str: str, tracker: str) -> str:
    import hashlib
    p = Path(path_str).resolve()
    try:
        st = p.stat()
        ident = f"{st.st_mtime_ns}:{st.st_size}"
    except OSError:
        ident = "missing"
    try:
        tc  = load_unit3dbot().get("TRACKER_CONFIG", {})
        raw = f"{tc.get('Gemini_URL', '')}:{tc.get('Gemini_APIKEY', '')[:8]}"
        cfg_hash = hashlib.md5(raw.encode()).hexdigest()[:8]
    except Exception:
        cfg_hash = "x"
    return f"{tracker}|{p}|{ident}|{cfg_hash}"


# ── Public cache API (delegates to db_dup_cache_*) ───────────────────────────

def lookup_duplicate_cache(
    path_str: str, tracker: str, skip_th: float,
) -> "tuple[bool, dict | None, str | None] | None":
    """Check the SQLite cache for a previous duplicate-check result.

    Returns None on cache miss; (is_dup, info, status) on hit.
    TTL filtering is done in SQL — no Python-side prune needed.
    """
    ttl = _duplicate_cache_ttl_sec()
    key = _dup_cache_key(path_str, tracker)
    return db_dup_cache_lookup(key, skip_th, ttl)


def store_duplicate_cache(
    path_str: str,
    tracker: str,
    skip_th: float,
    is_dup: bool,
    info: "dict | None",
    status: "str | None",
) -> None:
    """Persist a duplicate-check result to the SQLite cache."""
    ttl = _duplicate_cache_ttl_sec()
    if ttl <= 0:
        return
    key = _dup_cache_key(path_str, tracker)
    db_dup_cache_store(key, str(Path(path_str).resolve()), tracker, skip_th,
                       is_dup, info, status)


# ── Gemini check ──────────────────────────────────────────────────────────────

def _media_for_scan_path(path_str: str):
    from unit3dup.media import Media
    p = Path(path_str).resolve()
    if p.is_file():
        media = Media(folder=str(p.parent), subfolder=p.name)
        media.display_name = p.stem
    else:
        media = Media(folder=str(p), subfolder="")
        media.display_name = p.name
    return media


def _duplicate_deltas_by_id(matches: list[dict]) -> list[dict]:
    by_key: dict[str, dict] = {}
    for m in matches:
        tmdb = m.get("tmdb_id") or 0
        igdb = m.get("igdb_id") or 0
        if tmdb:
            key, label = f"tmdb:{tmdb}", f"TMDB {tmdb}"
        elif igdb:
            key, label = f"igdb:{igdb}", f"IGDB {igdb}"
        else:
            tid = m.get("tracker_id") or "?"
            key, label = f"torrent:{tid}", f"#{tid}"
        prev = by_key.get(key)
        if not prev or m["delta_pct"] < prev["delta_pct"]:
            by_key[key] = {
                "label":     label,
                "delta_pct": m["delta_pct"],
                "name":      m.get("name", ""),
                "size_gb":   m.get("size_gb"),
            }
    return sorted(by_key.values(), key=lambda x: x["delta_pct"])


def gemini_duplicate_check(path_str: str, tracker_name: str) -> tuple[bool, dict | None]:
    import argparse
    import requests as _requests
    from common import title
    from common.settings import Load
    from unit3dup import config_settings
    from unit3dup.duplicate import Duplicate, CompareTitles

    Load()
    if not config_settings.user_preferences.DUPLICATE_ON:
        return False, None

    size_th = config_settings.user_preferences.SIZE_TH
    media   = _media_for_scan_path(path_str)
    cli     = argparse.Namespace(
        duplicate=True, notitle=None, watcher=False,
        noup=False, noseed=False, reseed=False, mt=False,
    )
    dup     = Duplicate(content=media, tracker_name=tracker_name, cli=cli)
    tracker = dup.torrent_info.tracker

    # Direct request — bypasses Tracker._get()'s 60 s sleep on 429 so we can
    # detect rate-limit immediately and abort the whole batch.
    keyword = dup.query.guessit_title.replace("-", " ")
    params  = {**tracker.params, "name": keyword, "perPage": 100}
    try:
        _r = _requests.get(
            url=tracker.filter_url,
            headers=tracker.headers,
            params=params,
            timeout=10,
        )
        if _r.status_code == 429:
            raise RateLimitError(f"Tracker 429 rate-limit on '{keyword}'")
        _r.raise_for_status()
        resp = _r.json()
    except RateLimitError:
        raise
    except Exception as exc:
        _log.warning("Erreur requête tracker pour '%s' : %s", keyword, exc)
        return False, None

    if not isinstance(resp, dict) or "data" not in resp:
        return False, None

    local_tag   = (dup.query.release_group or "").strip().upper()

    # Parse local resolution + source once for all comparisons in this batch.
    try:
        from guessit import guessit as _guessit
        _lg = _guessit(Path(path_str).name)
        _local_res    = str(_lg.get("screen_size") or "").strip()
        _local_source = str(_lg.get("source") or "").strip()
    except Exception:
        _local_res    = ""
        _local_source = ""

    matches: list[dict] = []
    for t_data in resp.get("data", []):
        attrs          = t_data.get("attributes", {})
        tracker_guessit = title.Guessit(attrs.get("name", ""))
        if not CompareTitles(
            tracker_file=tracker_guessit,
            content_file=dup.query,
        ).process():
            continue
        # Resolution guard: 1080p ≠ 2160p → not a duplicate even if sizes coincide.
        api_res = (attrs.get("resolution") or "").strip()
        if _local_res and api_res and _local_res.lower() != api_res.lower():
            continue
        # Source-type guard: WEB ≠ Blu-ray → not a duplicate.
        api_type = (attrs.get("type") or "").strip()
        if not _source_types_match(_local_source, api_type):
            continue
        delta       = dup._calculate_threshold(size=attrs.get("size") or 0)
        tracker_tag = (tracker_guessit.release_group or "").strip().upper()
        same_tag    = bool(local_tag and tracker_tag and local_tag == tracker_tag)
        # Same release group → minor packaging differences (extra audio, subs) are
        # expected; bypass SIZE_TH so we don't miss e.g. MULTi vs MULTi.VFF.AAC.
        if not same_tag and delta > size_th:
            continue
        size = attrs.get("size") or 0
        matches.append({
            "tracker_id": t_data.get("id"),
            "tmdb_id":    attrs.get("tmdb_id") or 0,
            "igdb_id":    attrs.get("igdb_id") or 0,
            "name":       attrs.get("name", ""),
            "size_gb":    round(size / (1024 ** 3), 2) if size else 0,
            "delta_pct":  round(delta, 2),
        })

    if not matches:
        return False, None

    matches.sort(key=lambda m: m["delta_pct"])
    best = dict(matches[0])
    best["size_th"]      = size_th
    best["local_gb"]     = dup.content_size
    best["matches"]      = matches
    best["deltas_by_id"] = _duplicate_deltas_by_id(matches)
    return True, best


# ── Phase 4 helpers ───────────────────────────────────────────────────────────

def _get_local_size_gb(path_str: str) -> float:
    """Compute local content size in GB for a path (file or directory).

    For directories, sums all video file sizes recursively.
    Returns 0.0 on error.
    """
    _VE = {".mkv", ".mp4", ".avi", ".m2ts"}
    p = Path(path_str)
    try:
        if p.is_file():
            return p.stat().st_size / (1024 ** 3)
        elif p.is_dir():
            total = 0
            for f in p.rglob("*"):
                if f.is_file() and f.suffix.lower() in _VE:
                    try:
                        total += f.stat().st_size
                    except OSError:
                        pass
            return total / (1024 ** 3)
    except OSError:
        pass
    return 0.0


def _parse_release_info(name: str) -> tuple[str, str, "int | None", "int | None"]:
    """Parse (resolution, tag, season, episode) from a Gemini release name.

    Returns empty strings / None for unknown fields.
    Uses guessit when available, falls back to simple heuristics.
    """
    try:
        from guessit import guessit as _guessit  # type: ignore[import-untyped]
        g = _guessit(name)
        resolution = str(g.get("screen_size") or "")
        tag = str(g.get("release_group") or "")
        if not tag and "-" in name:
            tag = name.rsplit("-", 1)[-1].strip()
        season  = g.get("season")
        episode = g.get("episode")
        return resolution, tag, (int(season) if season is not None else None), (int(episode) if episode is not None else None)
    except Exception:
        tag = name.rsplit("-", 1)[-1].strip() if "-" in name else ""
        return "", tag, None, None


def gemini_releases_for_tmdb_id(
    tmdb_id: int,
    tracker_name: str,
) -> list[dict]:
    """Fetch all Gemini releases that match a given TMDB ID.

    Returns a list of release dicts with keys:
      tracker_id, name, resolution, tag, size_gb

    Raises ``RateLimitError`` on HTTP 429 so callers can abort their batch.
    """
    import requests as _requests
    if not tmdb_id:
        return []
    try:
        from common.settings import Load
        from unit3dup.torrent import Torrent
        Load()
        t_obj   = Torrent(tracker_name)
        tracker = t_obj.tracker
        params  = {**tracker.params, "tmdbId": str(tmdb_id), "perPage": 100}
        r = _requests.get(
            url=tracker.filter_url,
            headers=tracker.headers,
            params=params,
            timeout=10,
        )
        if r.status_code == 429:
            raise RateLimitError(f"Tracker 429 on tmdb_id={tmdb_id}")
        r.raise_for_status()
        resp = r.json()
    except RateLimitError:
        raise
    except Exception as exc:
        _log.debug("gemini_releases_for_tmdb_id(%s): %s", tmdb_id, exc)
        return []

    releases: list[dict] = []
    for item_data in resp.get("data", []):
        attrs          = item_data.get("attributes", {})
        name           = attrs.get("name", "")
        api_resolution = attrs.get("resolution") or ""  # set by uploader — more reliable
        api_type       = attrs.get("type") or ""        # e.g. "BDRip", "REMUX", "WEB-DL"
        _parsed_res, tag, rel_season, rel_episode = _parse_release_info(name)
        size = attrs.get("size") or 0
        releases.append({
            "tracker_id": str(item_data.get("id") or ""),
            "name":       name,
            "resolution": api_resolution or _parsed_res,
            "type":       api_type,
            "tag":        tag,
            "size_gb":    round(size / (1024 ** 3), 2) if size else 0,
            "season":     rel_season,
            "episode":    rel_episode,
        })
    return releases


def apply_duplicate_checks(
    items: list,
) -> tuple[list, bool, list[str]]:
    """Return ``(items, rate_limited, unchecked_paths)``.

    Runs a 4-phase duplicate pipeline:

    Phase 1 — inventory match
        Items whose path appears in history with source='gemini_inventory' are
        marked status='history' with duplicate_source='inventory'.

    Phase 2 — local history match
        Remaining items whose path appears in the rest of history are marked
        status='history' with duplicate_source='local_history'.

    Phase 3 — TMDB ID search + releases enrichment  ← runs FIRST
        Fetches Gemini releases for every item that has a tmdb_id (grouped by
        tmdb_id to avoid redundant API calls).  Stores the list in
        ``item["gemini_releases"]`` for display.  For pending items, applies
        resolution + source-type guards before the size-delta check so that
        e.g. a 1080p WEB local release is never flagged as a duplicate of a
        2160p BluRay Gemini release.  Series-specific rules:
          - episode item + season pack on Gemini → status='skip'
          - season item: intégrale on Gemini does NOT block the upload
          - intégrale item: only another intégrale can be a duplicate

    Phase 4 — Gemini name search  ← fallback for still-pending items
        For items not resolved by Phase 3, queries the tracker API by title.
        Marks matches as 'duplicate' or 'duplicate_ask'.

    *rate_limited* is True when the tracker returned 429 and the batch was
    aborted early.  *unchecked_paths* lists the paths that were not verified.
    """
    tracker = default_tracker_name()
    if not tracker:
        _log.warning(
            "Vérification doublons ignorée : MULTI_TRACKER non configuré dans Unit3Dbot.json "
            "(Paramètres › Tracker › MULTI_TRACKER doit contenir au moins une entrée)"
        )
        return items, False, []
    try:
        from common.settings import Load
        from unit3dup import config_settings
        Load()
        if not config_settings.user_preferences.DUPLICATE_ON:
            _log.warning(
                "Vérification doublons ignorée : DUPLICATE_ON=false dans Unit3Dbot.json "
                "(Paramètres › Préférences › Vérification doublons)"
            )
            return items, False, []
        size_th = config_settings.user_preferences.SIZE_TH
    except Exception as exc:
        _log.warning("Vérification doublons ignorée : Load() a échoué — %s", exc)
        return items, False, []

    # ── Phase 1: Inventory paths (source='gemini_inventory') ─────────────────
    from core.db import db_history_paths_by_source, db_history_paths_set, db_history_info_by_source
    from core.conf import load_unit3dbot as _load_u3d
    inventory_info  = db_history_info_by_source("gemini_inventory")
    inventory_paths = set(inventory_info.keys())
    try:
        _gemini_url = (_load_u3d().get("TRACKER_CONFIG", {}).get("Gemini_URL") or "").rstrip("/")
    except Exception:
        _gemini_url = ""
    for item in items:
        if item.get("status") == "pending" and item.get("path") in inventory_paths:
            info = inventory_info[item["path"]]
            tid  = info.get("tracker_id") or ""
            item["status"]           = "inventory"
            item["duplicate_source"] = "inventory"
            item["tracker_id"]       = tid
            item["job_id"]           = info.get("job_id") or ""
            item["tracker_url"]      = f"{_gemini_url}/torrents/{tid}" if (tid and _gemini_url) else ""

    # ── Phase 2: All other history paths ─────────────────────────────────────
    hist_paths     = db_history_paths_set()
    non_inv_hist   = hist_paths - inventory_paths
    for item in items:
        if item.get("status") == "pending" and item.get("path") in non_inv_hist:
            item["status"]           = "history"
            item["duplicate_source"] = "local_history"

    skip_th = _duplicate_skip_threshold_pct()

    # ── Phase 3: TMDB ID search + releases enrichment ────────────────────────
    # Runs first — more precise than name search (exact TMDB ID, + resolution
    # and source-type guards prevent WEB/BluRay cross-matches).
    # Also populates gemini_releases on every item with a tmdb_id (for display).
    _phase4_enrich_releases(items, tracker, size_th, skip_th, pending_only=False)

    # ── Phase 4: Gemini name search (fallback for still-pending items) ────────
    # Only runs on items not already resolved by Phase 3.
    pending = [(i, it) for i, it in enumerate(items) if it.get("status") == "pending"]
    if not pending:
        return items, False, []

    # Prune expired cache entries opportunistically (once per batch, not per item)
    ttl = _duplicate_cache_ttl_sec()
    if ttl > 0:
        pruned = db_dup_cache_prune(ttl)
        if pruned:
            _log.debug("Cache doublons : %d entrée(s) expirée(s) supprimée(s)", pruned)

    # Shared abort flag: set by the first thread that hits a 429.
    abort_flag = [False]

    def _run_single_check(path: str) -> tuple[bool, "dict | None", "str | None", bool]:
        """Check one path (file or folder). Returns (is_dup, info, status, from_cache)."""
        cached = lookup_duplicate_cache(path, tracker, skip_th)
        if cached is not None:
            is_dup, info, status = cached
            return is_dup, info, status, True
        is_dup, info = gemini_duplicate_check(path, tracker)
        status = None
        if is_dup and info:
            delta  = float(info.get("delta_pct", 0))
            status = "duplicate" if delta <= skip_th else "duplicate_ask"
        store_duplicate_cache(path, tracker, skip_th, bool(is_dup and info), info, status)
        return is_dup, info, status, False

    def _check(entry):
        idx, it = entry
        path  = it["path"]
        files = it.get("files") or []

        if abort_flag[0]:
            return idx, False, None, None, False, True   # _rate_skipped=True

        try:
            if it.get("type") == "collection" and files:
                best_is_dup  = False
                best_info    = None
                best_status  = None
                any_from_cache = True
                for fpath in files:
                    if abort_flag[0]:
                        return idx, False, None, None, False, True
                    is_dup, info, status, from_cache = _run_single_check(fpath)
                    if not from_cache:
                        any_from_cache = False
                    if is_dup and info and status:
                        if best_info is None or float(info.get("delta_pct", 99)) < float(best_info.get("delta_pct", 99)):
                            best_is_dup = True
                            best_info   = info
                            best_status = status
                return idx, best_is_dup, best_info, best_status, any_from_cache, False
            else:
                is_dup, info, status, from_cache = _run_single_check(path)
                return idx, is_dup, info, status, from_cache, False

        except RateLimitError:
            abort_flag[0] = True
            raise
        except Exception as exc:
            store_duplicate_cache(path, tracker, skip_th, False, {"error": str(exc)[:120]}, None)
            return idx, False, {"error": str(exc)[:120]}, None, False, False

    rate_limited    = False
    checked_indices: set[int] = set()

    with ThreadPoolExecutor(max_workers=3) as ex:
        futures = {ex.submit(_check, entry): entry for entry in pending}
        for fut in as_completed(futures):
            try:
                idx, is_dup, info, status, _from_cache, _rate_skipped = fut.result()
            except RateLimitError:
                rate_limited = True
                _log.warning("Vérification doublons interrompue : rate-limit 429 du tracker")
                continue

            if _rate_skipped:
                continue
            checked_indices.add(idx)
            if info and info.get("error"):
                continue
            if not is_dup or not info or not status:
                continue
            items[idx]["status"]           = status
            items[idx]["duplicate"]        = info
            items[idx]["duplicate_source"] = "name_search"

    unchecked_paths = (
        [it["path"] for i, it in pending if i not in checked_indices]
        if rate_limited else []
    )

    # ── Post-process: re-classify user's own uploads detected as duplicates ──
    # Phase 3/4 may flag items as "duplicate" even when the matching Gemini release
    # was uploaded by the user (name mismatch: local "NCIS.S23E11" vs Gemini
    # "NCIS.2003.S23E11.VFF...").  If the matched tracker_id is in the user's
    # inventory, the item is already on the tracker by the user → "inventory".
    try:
        from core.gemini_inventory import get_inventory_list
        _inv_ids = {
            str(t.get("tracker_id") or "")
            for t in get_inventory_list()
            if t.get("tracker_id")
        }
        if _inv_ids:
            for item in items:
                if item.get("status") not in ("duplicate", "duplicate_ask", "skip"):
                    continue
                # Check tracker_id from duplicate dict (Phase 3 name-search)
                _dtid = str((item.get("duplicate") or {}).get("tracker_id") or "")
                _matched = _dtid if (_dtid and _dtid in _inv_ids) else ""
                # Also check gemini_releases entries (Phase 4 TMDB enrichment)
                if not _matched:
                    for _rel in (item.get("gemini_releases") or []):
                        _rtid = str(_rel.get("tracker_id") or "")
                        if _rtid and _rtid in _inv_ids:
                            _matched = _rtid
                            break
                if _matched:
                    item["status"]           = "inventory"
                    item["duplicate_source"] = "inventory"
                    item["tracker_id"]       = _matched
                    item["job_id"]           = item.get("job_id") or ""
                    item["tracker_url"]      = f"{_gemini_url}/torrents/{_matched}" if _gemini_url else ""
    except Exception as _exc:
        _log.warning("Reclassification inventaire ignorée : %s", _exc)

    return items, rate_limited, unchecked_paths


def _is_integrale_release(rel: dict) -> bool:
    """True if a Gemini release represents a complete series (Intégrale).

    Detection rules:
      - "INTEGRALE" present in the release name (case-insensitive)
      - OR Gemini metadata has season=0 and episode=0
    """
    name = (rel.get("name") or "").upper()
    if "INTEGRALE" in name:
        return True
    s = rel.get("season")
    e = rel.get("episode")
    try:
        return s is not None and int(s) == 0 and e is not None and int(e) == 0
    except (TypeError, ValueError):
        return False


def _is_season_pack_for(rel: dict, season_num: int) -> bool:
    """True if a Gemini release is a complete pack for ``season_num``.

    Detection rules:
      - Gemini metadata: season == season_num AND episode absent or == 0
      - OR "COMPLETE" present in the release name with matching season
    """
    name = (rel.get("name") or "").upper()
    s = rel.get("season")
    e = rel.get("episode")
    try:
        s_int = int(s) if s is not None else -1
        e_int = int(e) if e is not None else 0
    except (TypeError, ValueError):
        return False
    if s_int != season_num:
        return False
    return e_int == 0 or "COMPLETE" in name


def _phase4_enrich_releases(
    items: list,
    tracker_name: str,
    size_th: float,
    skip_th: float,
    *,
    pending_only: bool,
) -> None:
    """Phase 4: fetch Gemini releases by TMDB ID for all items that have one.

    Populates ``item["gemini_releases"]`` for the scan preview releases column.
    For items whose status is still 'pending', additionally checks size delta
    and may upgrade to 'duplicate' / 'duplicate_ask' (with duplicate_source
    set to 'tmdb_id').

    Groups unique tmdb_ids to avoid redundant API calls.
    Rate-limit errors abort the Phase 4 batch gracefully (Phase 3 results
    are preserved).
    """
    # Collect unique tmdb_ids
    tmdb_id_to_indices: dict[int, list[int]] = {}
    for i, item in enumerate(items):
        tid = item.get("tmdb_id") or 0
        if tid > 0:
            tmdb_id_to_indices.setdefault(tid, []).append(i)

    if not tmdb_id_to_indices:
        return

    # Fetch releases in parallel (one request per unique tmdb_id)
    releases_by_tmdb: dict[int, list[dict]] = {}
    phase4_abort = [False]

    def _fetch(tmdb_id: int) -> tuple[int, list[dict]]:
        if phase4_abort[0]:
            return tmdb_id, []
        try:
            return tmdb_id, gemini_releases_for_tmdb_id(tmdb_id, tracker_name)
        except RateLimitError:
            phase4_abort[0] = True
            _log.warning("Phase 4 doublons : rate-limit 429 — enrichissement TMDB interrompu")
            return tmdb_id, []
        except Exception as exc:
            _log.debug("Phase 4 fetch tmdb_id=%s: %s", tmdb_id, exc)
            return tmdb_id, []

    with ThreadPoolExecutor(max_workers=3) as ex:
        fmap = {ex.submit(_fetch, tid): tid for tid in tmdb_id_to_indices}
        for fut in as_completed(fmap):
            try:
                tmdb_id, releases = fut.result()
                releases_by_tmdb[tmdb_id] = releases
            except Exception:
                pass

    # Assign releases + run size-based duplicate detection for pending items
    for i, item in enumerate(items):
        tid = item.get("tmdb_id") or 0
        if not tid:
            continue
        releases = releases_by_tmdb.get(tid, [])
        item["gemini_releases"] = releases

        # Only attempt duplicate detection for items still pending after Phase 3
        if item.get("status") != "pending" or not releases:
            continue

        # ── Local item metadata ───────────────────────────────────────────────
        item_type     = item.get("type", "")
        local_season  = item.get("season_num")
        local_episode = item.get("episode_num")
        local_tag     = (item.get("tag") or "").strip().upper()
        local_res     = (item.get("resolution") or "").strip()
        local_source  = (item.get("source_type") or "").strip()

        def _res_src_ok(rel: dict) -> bool:
            """Return True when resolution + source type both match (or are unknown)."""
            remote_res  = (rel.get("resolution") or "").strip()
            remote_type = (rel.get("type") or "").strip()
            if local_res and remote_res and local_res.lower() != remote_res.lower():
                return False
            return _source_types_match(local_source, remote_type)

        # ── Series pre-check: season-pack blocks individual-episode upload ────
        # Rule: if a season pack already exists on Gemini for this season
        # (matching resolution + source), posting episode by episode is forbidden.
        if item_type == "file" and local_season is not None:
            for rel in releases:
                if not _res_src_ok(rel):
                    continue
                if _is_season_pack_for(rel, local_season):
                    item["status"]           = "skip"
                    item["duplicate_source"] = "season_pack"
                    item["duplicate"] = {
                        "tracker_id": rel.get("tracker_id", ""),
                        "name":       rel.get("name", ""),
                        "size_gb":    rel.get("size_gb", 0),
                        "delta_pct":  0,
                        "local_gb":   0,
                        "size_th":    skip_th,
                        "matches":    [rel],
                        "deltas_by_id": [],
                    }
                    break
            if item.get("status") == "skip":
                continue

        # ── Size-based duplicate detection ────────────────────────────────────
        local_gb = _get_local_size_gb(item["path"])
        if local_gb <= 0:
            continue

        best_delta: float | None = None
        best_release: dict | None = None
        best_tag_match = False
        for rel in releases:
            remote_gb = rel.get("size_gb", 0)
            if remote_gb <= 0:
                continue

            # ── Resolution + source-type guards (films & séries) ─────────────
            if not _res_src_ok(rel):
                continue

            # ── Series content-type guards ────────────────────────────────────
            if item_type == "season":
                # Intégrale on Gemini does NOT block a season upload (rule 1).
                if _is_integrale_release(rel):
                    continue
                # Only compare against the same season pack, not individual episodes.
                rel_season  = rel.get("season")
                rel_episode = rel.get("episode")
                try:
                    if int(rel_season or -1) != local_season:
                        continue
                    if int(rel_episode or -1) not in (0, -1):
                        continue   # individual episode, not a season pack
                except (TypeError, ValueError):
                    continue

            elif item_type == "integrale":
                # An intégrale upload can only be blocked by another intégrale.
                if not _is_integrale_release(rel):
                    continue

            elif item_type == "file":
                # Individual episode: skip packs & intégrales — size comparison
                # is meaningless across different content granularity.
                if _is_integrale_release(rel):
                    continue
                if local_season is not None and _is_season_pack_for(rel, local_season):
                    continue
                # Both local and remote must be specific episodes.
                rel_season  = rel.get("season")
                rel_episode = rel.get("episode")
                try:
                    r_ep = int(rel_episode) if rel_episode is not None else None
                    r_sn = int(rel_season)  if rel_season  is not None else None
                except (TypeError, ValueError):
                    continue
                if r_ep is None or r_ep == 0:
                    continue   # remote is a pack, not an episode
                if local_episode is not None and r_ep != local_episode:
                    continue
                if local_season is not None and r_sn is not None and r_sn != local_season:
                    continue

            else:
                # film / unknown: use original season/episode guards
                rel_season  = rel.get("season")
                rel_episode = rel.get("episode")
                if local_season is not None and local_episode is None and rel_episode is not None:
                    continue
                if local_episode is not None and rel_episode is not None:
                    if local_episode != rel_episode:
                        continue
                if local_season is not None and rel_season is not None:
                    if local_season != rel_season:
                        continue

            rel_tag   = (rel.get("tag") or "").strip().upper()
            tag_match = bool(local_tag and rel_tag and local_tag == rel_tag)
            delta     = abs(local_gb - remote_gb) / max(local_gb, remote_gb) * 100
            if (best_delta is None
                    or (tag_match and not best_tag_match)
                    or (tag_match == best_tag_match and delta < best_delta)):
                best_delta     = delta
                best_release   = rel
                best_tag_match = tag_match

        # Same release group → packaging differences expected; bypass SIZE_TH.
        if best_delta is None or (not best_tag_match and best_delta > size_th):
            continue

        # Same release group → definitive duplicate regardless of skip_th.
        dup_status = "duplicate" if (best_tag_match or best_delta <= skip_th) else "duplicate_ask"
        item["status"]           = dup_status
        item["duplicate_source"] = "tmdb_id"
        item["duplicate"] = {
            "tracker_id":  best_release.get("tracker_id", "") if best_release else "",
            "name":        best_release.get("name", "")        if best_release else "",
            "size_gb":     best_release.get("size_gb", 0)      if best_release else 0,
            "delta_pct":   round(best_delta, 2),
            "local_gb":    round(local_gb, 2),
            "size_th":     skip_th,
            "matches":     [{**best_release, "delta_pct": round(best_delta, 2)}] if best_release else [],
            "deltas_by_id": [],
        }


# ── Public Phase-4-only entry point ──────────────────────────────────────────

def apply_phase4_checks(items: list) -> list:
    """Run only Phase 4 (TMDB-ID duplicate detection + releases enrichment).

    Called by /api/scan/recheck-tmdb-dup when the user updates a TMDB ID and
    wants to refresh duplicate detection without re-running the full pipeline.

    Stale Phase 4 results (duplicate_source='tmdb_id') are reset to 'pending'
    and gemini_releases is cleared before re-running so the slate is clean.
    """
    tracker = default_tracker_name()
    if not tracker:
        _log.warning("apply_phase4_checks: MULTI_TRACKER non configuré — ignoré")
        return items
    try:
        from common.settings import Load
        from unit3dup import config_settings
        Load()
        if not config_settings.user_preferences.DUPLICATE_ON:
            return items
        size_th = config_settings.user_preferences.SIZE_TH
    except Exception as exc:
        _log.warning("apply_phase4_checks: Load() a échoué — %s", exc)
        return items

    skip_th = _duplicate_skip_threshold_pct()

    # Reset stale Phase 4 results so they are re-evaluated with the new TMDB ID.
    for item in items:
        if (item.get("duplicate_source") == "tmdb_id"
                and item.get("status") in ("duplicate", "duplicate_ask")):
            item["status"] = "pending"
            item.pop("duplicate", None)
            item.pop("duplicate_source", None)
        item.pop("gemini_releases", None)  # always clear — will be repopulated

    _phase4_enrich_releases(items, tracker, size_th, skip_th, pending_only=False)
    return items
