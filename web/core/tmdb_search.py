"""Lightweight TMDB ID search for scan items.

Does NOT use the full CLI stack (no DbOnline, no diskcache, no Media object).
Uses requests directly with credentials from load_unit3dbot().

Public API
----------
search_tmdb_item(name, item_type)
    -> {"tmdb_id": int, "tmdb_title": str, "tmdb_year": str|None} | None
    Returns None on error or no match.

enrich_items_with_tmdb(items)
    -> same list with tmdb_id / tmdb_title added to pending items that lack one.
"""
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

from core.conf import load_unit3dbot

_log = logging.getLogger("core.tmdb_search")

_TMDB_BASE = "https://api.themoviedb.org/3"

# Items whose type maps to TV search
_TV_TYPES = {"integrale", "season"}

# TMDB genre ID for documentary content (same for movies and TV shows)
_TMDB_GENRE_DOCUMENTARY = 99

# In-process cache for English titles — avoids redundant TMDB calls within a session
_en_title_cache: dict[tuple, str | None] = {}

# Cache (tmdb_id, abs_ep) → (season_num, ep_in_season)
_season_ep_cache: dict[tuple, tuple] = {}

# Detect episode files by name (SxxExx pattern)
_EPISODE_NAME_RE = re.compile(r'\bS\d{1,2}E\d{1,4}\b', re.IGNORECASE)


def _item_search_type(item: dict) -> str:
    """Return the TMDB search category for an item ('season'=TV or the raw type).

    TMDB has separate numeric ID spaces for movies and TV shows.
    A 'file' item that is an episode (SxxExx name or episode_num present)
    must use the TV endpoint — not the movie endpoint — to avoid hitting a
    completely different title that happens to share the same integer ID.
    """
    t = item.get("type", "file")
    if t in _TV_TYPES:
        return t
    if t == "file":
        if item.get("episode_num") is not None or item.get("season_num") is not None:
            return "season"
        if _EPISODE_NAME_RE.search(item.get("name", "")):
            return "season"
    return t


def _extract_origin(data: dict) -> str:
    """Extract a comma-joined ISO-3166-1 country string from a TMDB result dict.

    Handles both search results (origin_country: ["FR", "US"]) and detail
    endpoints (production_countries: [{"iso_3166_1": "US", ...}]).
    """
    codes = data.get("origin_country")
    if codes and isinstance(codes, list):
        return ", ".join(str(c) for c in codes if c)
    countries = data.get("production_countries")
    if countries and isinstance(countries, list):
        return ", ".join(
            str(c.get("iso_3166_1") or "")
            for c in countries
            if c.get("iso_3166_1")
        )
    return ""


# ── Credentials ───────────────────────────────────────────────────────────────

def _tmdb_auth() -> tuple[dict, dict]:
    """Return (headers, params) for TMDB auth from Unit3Dbot.json.

    Priority:
      1. TMDB_ACCESS_TOKEN → Authorization: Bearer (v3/v4 recommended)
      2. TMDB_APIKEY        → api_key query param (legacy)
    Returns ({}, {}) when no credentials are configured.
    """
    tc = load_unit3dbot().get("TRACKER_CONFIG", {})
    token = tc.get("TMDB_ACCESS_TOKEN", "") or ""
    if isinstance(token, str) and token.strip().lower() not in {"", "no_key"}:
        return {"Authorization": f"Bearer {token.strip()}"}, {}
    key = tc.get("TMDB_APIKEY", "") or ""
    if isinstance(key, str) and key.strip().lower() not in {"", "no_key"}:
        return {}, {"api_key": key.strip()}
    return {}, {}


# ── Name parsing ──────────────────────────────────────────────────────────────

def _parse_title_year(name: str) -> tuple[str, int | None]:
    """Use guessit to extract (title, year) from a release name.

    Falls back to a simple strip of resolution/codec markers.
    """
    try:
        from guessit import guessit
        g = guessit(name)
        title = str(g.get("title") or "").strip() or name
        year  = g.get("year")
        return title, (int(year) if year else None)
    except Exception:
        # Minimal fallback: strip common release junk
        clean = re.sub(
            r"[\._\-]?(4K|UHD|FRENCH|MULTI|VOSTFR|BluRay|BDRip|WEB[\.\-]?DL"
            r"|HDTV|x264|x265|H\.?264|H\.?265|HEVC|AAC|DTS|AC3|10bit|HDR"
            r"|REMUX|PROPER|REPACK).*$",
            "", name, flags=re.I,
        ).strip(" .-_")
        # Extract year if present
        m = re.search(r"\b(19|20)\d{2}\b", clean)
        year = int(m.group(0)) if m else None
        if year:
            clean = clean[:m.start()].strip(" .-_")
        return clean or name, year


# ── TMDB HTTP search ──────────────────────────────────────────────────────────

def _tmdb_search(title: str, year: int | None, category: str) -> dict | None:
    """Direct TMDB API search. Returns first matching result dict or None."""
    headers, auth_params = _tmdb_auth()
    if not headers and not auth_params:
        return None  # no credentials

    endpoint = f"{_TMDB_BASE}/search/{'tv' if category == 'tv' else 'movie'}"
    params = {**auth_params, "query": title, "language": "fr-FR"}
    if year:
        year_key = "first_air_date_year" if category == "tv" else "primary_release_year"
        params[year_key] = year

    try:
        r = requests.get(endpoint, headers=headers, params=params, timeout=8)
        if r.status_code == 401:
            _log.warning("TMDB auth failed (401) — vérifier TMDB_APIKEY / TMDB_ACCESS_TOKEN")
            return None
        r.raise_for_status()
        results = r.json().get("results") or []
        if not results:
            # retry without year constraint if we had one
            if year:
                params.pop("primary_release_year", None)
                params.pop("first_air_date_year", None)
                r2 = requests.get(endpoint, headers=headers, params=params, timeout=8)
                r2.raise_for_status()
                results = r2.json().get("results") or []
        if not results:
            return None
        hit = results[0]
        hit_title = hit.get("title") or hit.get("name") or ""
        hit_date  = hit.get("release_date") or hit.get("first_air_date") or ""
        hit_year  = hit_date[:4] if hit_date else None
        return {
            "tmdb_id":     int(hit.get("id") or 0),
            "tmdb_title":  hit_title,
            "tmdb_year":   hit_year,
            "tmdb_origin": _extract_origin(hit),
            "genre_ids":   [int(g) for g in (hit.get("genre_ids") or []) if g],
        }
    except Exception as exc:
        _log.debug("TMDB search error for '%s': %s", title, exc)
        return None


# ── Public helpers ────────────────────────────────────────────────────────────

def tmdb_lookup_by_id(tmdb_id: int, item_type: str) -> dict | None:
    """Fetch title and year for a known TMDB ID via /movie/{id} or /tv/{id}.

    For file-type items (single episodes), both endpoints are tried in order
    (movie first, then tv) because the user may have entered a TV show ID for
    a file-type item — the type field alone is not reliable for episodes.

    When the primary endpoint returns a result without origin country, the
    function continues to the next candidate so that a TV endpoint with a
    populated origin_country takes precedence.

    Returns {"tmdb_id": int, "tmdb_title": str, "tmdb_year": str|None,
             "tmdb_origin": str} or None.
    """
    headers, auth_params = _tmdb_auth()
    if not headers and not auth_params:
        return None

    primary = "tv" if item_type in _TV_TYPES else "movie"
    # For file/collection types, also try tv in case the user entered a series ID.
    candidates = [primary] if primary == "tv" else [primary, "tv"]
    params = {**auth_params, "language": "fr-FR"}

    best: dict | None = None
    for cat in candidates:
        try:
            r = requests.get(f"{_TMDB_BASE}/{cat}/{tmdb_id}",
                             headers=headers, params=params, timeout=8)
            if r.status_code == 404:
                continue
            r.raise_for_status()
            d      = r.json()
            title  = d.get("title") or d.get("name") or ""
            date   = d.get("release_date") or d.get("first_air_date") or ""
            year   = date[:4] if date else None
            origin = _extract_origin(d)
            gids   = [int(g["id"]) for g in (d.get("genres") or []) if g.get("id")]
            if title:
                result = {
                    "tmdb_id":     tmdb_id,
                    "tmdb_title":  title,
                    "tmdb_year":   year,
                    "tmdb_origin": origin,
                    "genre_ids":   gids,
                }
                if origin:
                    return result  # has origin — no need to try further
                if best is None:
                    best = result  # valid but no origin — keep as fallback
        except Exception as exc:
            _log.debug("tmdb_lookup_by_id %s/%s failed: %s", cat, tmdb_id, exc)

    return best


def get_tmdb_english_title(tmdb_id: int, item_type: str) -> str | None:
    """Fetch the English (en-US) title for a TMDB ID.

    Used by the normalizer to get the international title for non-French films.
    Returns None when credentials are absent, the ID is unknown, or the call fails.
    A simple in-process dict cache avoids redundant requests within a server session.
    """
    cache_key = (tmdb_id, item_type)
    if cache_key in _en_title_cache:
        return _en_title_cache[cache_key]

    headers, auth_params = _tmdb_auth()
    if not headers and not auth_params:
        return None

    primary    = "tv" if item_type in _TV_TYPES else "movie"
    candidates = [primary] if primary == "tv" else [primary, "tv"]
    params     = {**auth_params, "language": "en-US"}

    for cat in candidates:
        try:
            r = requests.get(
                f"{_TMDB_BASE}/{cat}/{tmdb_id}",
                headers=headers, params=params, timeout=8,
            )
            if r.status_code == 404:
                continue
            r.raise_for_status()
            d     = r.json()
            title = d.get("title") or d.get("name") or ""
            if title:
                _en_title_cache[cache_key] = title
                return title
        except Exception as exc:
            _log.debug("get_tmdb_english_title %s/%s: %s", cat, tmdb_id, exc)

    _en_title_cache[cache_key] = None
    return None


def _find_season_for_abs_ep(tmdb_id: int, abs_ep: int,
                            headers: dict, params: dict) -> tuple[int | None, int | None]:
    """Return (season_num, ep_in_season) by accumulating TMDB season episode counts.

    Makes a single /tv/{id} call to get all seasons' episode_count, then walks
    them in order to find which season contains absolute episode abs_ep.
    Season 0 (specials) is excluded from the count.
    Results are cached in-process.
    """
    key = (tmdb_id, abs_ep)
    if key in _season_ep_cache:
        return _season_ep_cache[key]
    try:
        r = requests.get(f"{_TMDB_BASE}/tv/{tmdb_id}",
                         headers=headers,
                         params={**params, "language": "fr-FR"},
                         timeout=5)
        if not r.ok:
            _season_ep_cache[key] = (None, None)
            return None, None
        seasons = sorted(
            (s for s in (r.json().get("seasons") or []) if (s.get("season_number") or 0) > 0),
            key=lambda s: s["season_number"],
        )
        cumulative = 0
        for s in seasons:
            count = s.get("episode_count") or 0
            if cumulative < abs_ep <= cumulative + count:
                result: tuple = (s["season_number"], abs_ep - cumulative)
                _season_ep_cache[key] = result
                return result
            cumulative += count
    except Exception as exc:
        _log.debug("_find_season_for_abs_ep tmdb=%s ep=%s: %s", tmdb_id, abs_ep, exc)
    _season_ep_cache[key] = (None, None)
    return None, None


def _tag_ok_simple(tag: str, valid_tags: list) -> bool:
    """True if tag is valid against valid_tags (case-insensitive), or if no filtering configured."""
    if not valid_tags:
        return True
    return tag.upper() in {t.upper() for t in valid_tags}


def apply_documentary_tag(item: dict, result: dict, valid_tags: list | None = None) -> None:
    """Set tag='DOC' on item when TMDB genre_id 99 (documentary) is present in result."""
    if _TMDB_GENRE_DOCUMENTARY in (result.get("genre_ids") or []):
        item["tag"] = "DOC"
        item["tag_valid"] = _tag_ok_simple("DOC", valid_tags or [])


def search_tmdb_item(name: str, item_type: str) -> dict | None:
    """Search TMDB for *name* (a release/folder name).

    Returns {"tmdb_id": int, "tmdb_title": str, "tmdb_year": str|None} or None.
    """
    category = "tv" if item_type in _TV_TYPES else "movie"
    title, year = _parse_title_year(name)
    if not title:
        return None
    return _tmdb_search(title, year, category)


def enrich_items_with_tmdb(items: list, *, socketio=None, valid_tags: list | None = None) -> list:
    """Add tmdb_id / tmdb_title to pending items that don't already have one.

    Runs lookups concurrently (max 4 threads, 8 s timeout each).
    Items with type 'collection' use the folder name (not individual files).
    Emits scan_progress "tmdb" events if *socketio* is provided.
    Items already carrying a non-zero tmdb_id are left untouched.
    When TMDB genre_id 99 (documentary) is detected, tag is automatically set to 'DOC'.
    """
    to_enrich = [
        (i, it)
        for i, it in enumerate(items)
        if it.get("status") in ("pending", "duplicate_ask")
        and not (it.get("tmdb_id") or 0)
    ]
    if not to_enrich:
        return items

    # Check credentials once before spawning threads
    headers, params = _tmdb_auth()
    if not headers and not params:
        _log.debug("enrich_items_with_tmdb: pas de credentials TMDB — skip")
        return items

    total     = len(to_enrich)
    done      = [0]
    done_lock = threading.Lock()

    def _enrich_one(idx_item):
        idx, it = idx_item
        name = it.get("name") or Path(it.get("path", "")).name
        result = search_tmdb_item(name, _item_search_type(it))
        with done_lock:
            done[0] += 1
            n = done[0]
        if socketio:
            socketio.emit("scan_progress", {
                "phase": "tmdb",
                "done":  n,
                "total": total,
            })
        return idx, result

    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(_enrich_one, entry): entry for entry in to_enrich}
        for fut in as_completed(futures):
            try:
                idx, result = fut.result()
            except Exception as exc:
                _log.debug("tmdb enrich thread error: %s", exc)
                continue
            if result and result.get("tmdb_id"):
                items[idx]["tmdb_id"]     = result["tmdb_id"]
                items[idx]["tmdb_title"]  = result.get("tmdb_title", "")
                if result.get("tmdb_year"):
                    items[idx]["tmdb_year"]   = result["tmdb_year"]
                if result.get("tmdb_origin"):
                    items[idx]["tmdb_origin"] = result["tmdb_origin"]
                apply_documentary_tag(items[idx], result, valid_tags)

    # Second pass: resolve season number for absolute-episode items (anime Exx notation)
    abs_ep_items = [
        (i, it) for i, it in enumerate(items)
        if it.get("absolute_episode") and it.get("tmdb_id") and it.get("episode_num")
    ]
    if abs_ep_items:
        for i, it in abs_ep_items:
            abs_ep = it["episode_num"]
            season_num, ep_in_season = _find_season_for_abs_ep(
                it["tmdb_id"], abs_ep, headers, params
            )
            if season_num is not None:
                items[i]["season_num"]  = season_num
                items[i]["episode_num"] = ep_in_season
                _log.debug(
                    "Abs ep E%s → S%02dE%02d (tmdb_id=%s name=%s)",
                    abs_ep, season_num, ep_in_season, it["tmdb_id"], it.get("name"),
                )

    return items
