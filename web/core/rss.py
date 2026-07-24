"""RSS feed management: polling, filtering, download dispatch, client progress."""
import logging
import re
import threading
import uuid
from datetime import datetime, timezone

import requests as _requests

from core.conf import WEB_DIR, _safe_read_json, _atomic_write_json, load_unit3dbot, _human

RSS_FEEDS_JSON = WEB_DIR / "rss_feeds.json"
RSS_ITEMS_JSON = WEB_DIR / "rss_items.json"
RSS_ITEMS_MAX  = 200  # max items kept per feed

_log      = logging.getLogger("core.rss")
_rss_lock = threading.Lock()

# Persistent HTTP session for TMDB — reuses TCP+TLS connection across calls
_tmdb_session = _requests.Session()


# ── Feed CRUD ─────────────────────────────────────────────────────────────────

def load_feeds() -> list[dict]:
    return _safe_read_json(RSS_FEEDS_JSON, list)


def save_feeds(feeds: list[dict]) -> None:
    _atomic_write_json(RSS_FEEDS_JSON, feeds)


def get_feed(feed_id: str) -> dict | None:
    return next((f for f in load_feeds() if f["id"] == feed_id), None)


def add_feed(data: dict) -> dict:
    feed = {
        "id":                str(uuid.uuid4()),
        "name":              data.get("name", ""),
        "url":               data.get("url", ""),
        "interval_minutes":  max(5, int(data.get("interval_minutes", 30))),
        "enabled":           bool(data.get("enabled", True)),
        "auto_download":     bool(data.get("auto_download", True)),
        "filters":           data.get("filters", []),
        "filter_match_all":  bool(data.get("filter_match_all", False)),
        "save_path":         data.get("save_path", ""),
        "tag":               (data.get("tag") or "RSS").strip() or "RSS",
        "torrent_client":    data.get("torrent_client", ""),
        "last_fetched":      None,
        "last_error":        None,
        "item_count":        0,
    }
    with _rss_lock:
        feeds = load_feeds()
        feeds.append(feed)
        save_feeds(feeds)
    return feed


def update_feed(feed_id: str, data: dict) -> dict | None:
    with _rss_lock:
        feeds = load_feeds()
        for i, f in enumerate(feeds):
            if f["id"] == feed_id:
                for key in ("name", "url", "interval_minutes", "enabled", "auto_download",
                            "filters", "filter_match_all", "save_path", "tag", "torrent_client"):
                    if key in data:
                        v = data[key]
                        if key == "interval_minutes":
                            v = max(5, int(v))
                        feeds[i][key] = v
                save_feeds(feeds)
                return feeds[i]
    return None


def delete_feed(feed_id: str) -> bool:
    with _rss_lock:
        feeds = load_feeds()
        new_feeds = [f for f in feeds if f["id"] != feed_id]
        if len(new_feeds) == len(feeds):
            return False
        save_feeds(new_feeds)
        items = _load_items_raw()
        save_items([it for it in items if it.get("feed_id") != feed_id])
    return True


# ── Items ─────────────────────────────────────────────────────────────────────

def _load_items_raw() -> list[dict]:
    return _safe_read_json(RSS_ITEMS_JSON, list)


def load_items(feed_id: str | None = None) -> list[dict]:
    items = _load_items_raw()
    if feed_id:
        return [it for it in items if it.get("feed_id") == feed_id]
    return items


def save_items(items: list[dict]) -> None:
    _atomic_write_json(RSS_ITEMS_JSON, items)


def _merge_items(existing: list[dict], new_items: list[dict], feed_id: str) -> tuple[list[dict], list[dict]]:
    existing_guids = {it["guid"] for it in existing if it.get("feed_id") == feed_id}
    to_add         = [it for it in new_items if it["guid"] not in existing_guids]
    feed_items     = [it for it in existing if it.get("feed_id") == feed_id] + to_add
    other_items    = [it for it in existing if it.get("feed_id") != feed_id]
    if len(feed_items) > RSS_ITEMS_MAX:
        feed_items = feed_items[-RSS_ITEMS_MAX:]
    return other_items + feed_items, to_add


# ── Filter logic ──────────────────────────────────────────────────────────────

def matches_filters(title: str, filters: list[dict], *, match_all_includes: bool = False) -> bool:
    if not filters:
        return True
    includes = [f for f in filters if f.get("type") == "include"]
    excludes = [f for f in filters if f.get("type") == "exclude"]
    for f in excludes:
        if _one_filter_matches(title, f):
            return False
    if not includes:
        return True
    if match_all_includes:
        return all(_one_filter_matches(title, f) for f in includes)
    return any(_one_filter_matches(title, f) for f in includes)


def _one_filter_matches(title: str, f: dict) -> bool:
    pattern = f.get("pattern", "")
    if not pattern:
        return False
    if f.get("mode") == "regex":
        try:
            return bool(re.search(pattern, title, re.IGNORECASE))
        except re.error:
            return False
    return pattern.lower() in title.lower()


# ── Filter re-application ─────────────────────────────────────────────────────

def refilter_feed_items(feed_id: str, filters: list[dict], *, match_all: bool = False) -> int:
    """Re-evaluate matched status for all stored items of a feed.

    Called when a feed's filters are updated so that existing items
    immediately reflect the new include/exclude rules.
    Returns the number of items whose status changed.
    """
    with _rss_lock:
        items   = _load_items_raw()
        changed = 0
        for j, it in enumerate(items):
            if it.get("feed_id") != feed_id:
                continue
            new_matched = matches_filters(it.get("title", ""), filters, match_all_includes=match_all)
            if items[j].get("matched") != new_matched:
                items[j]["matched"] = new_matched
                changed += 1
        if changed:
            save_items(items)
    return changed


# ── Feed fetching ─────────────────────────────────────────────────────────────

def fetch_feed(feed: dict) -> tuple[list[dict], str | None]:
    try:
        import feedparser
    except ImportError:
        return [], "feedparser non installé (pip install feedparser)"
    try:
        parsed = feedparser.parse(feed["url"])
        if parsed.bozo and not parsed.entries:
            return [], f"Erreur parsing : {parsed.bozo_exception}"
        out = []
        for entry in parsed.entries:
            title       = entry.get("title", "")
            guid        = entry.get("id") or entry.get("guid") or entry.get("link") or title
            torrent_url = _extract_torrent_url(entry)
            pub_date    = entry.get("published") or entry.get("updated") or ""
            tags        = entry.get("tags") or []
            rss_category = tags[0].get("term", "").strip() if tags else ""
            out.append({
                "feed_id":        feed["id"],
                "guid":           guid,
                "title":          title,
                "torrent_url":    torrent_url,
                "pub_date":       pub_date,
                "rss_category":   rss_category,
                "matched":        matches_filters(title, feed.get("filters", []),
                                              match_all_includes=feed.get("filter_match_all", False)),
                "downloaded":     False,
                "download_error": None,
                "fetched_at":     datetime.now(timezone.utc).isoformat(),
            })
        return out, None
    except Exception as exc:
        return [], str(exc)[:200]


def _extract_torrent_url(entry) -> str:
    if entry.get("enclosures"):
        for enc in entry.enclosures:
            href = enc.get("href", "") or enc.get("url", "")
            mime = enc.get("type", "")
            if "torrent" in mime.lower() or href.endswith(".torrent"):
                return href
    return entry.get("link", "")


# ── Category helpers ──────────────────────────────────────────────────────────

_GENRE_ANIMATION   = 16
_GENRE_DOCUMENTARY = 99

_RSS_ANIME_TERMS  = ("ANIME", "ANIM", "ANIMATION", "CARTOON")
_RSS_MOVIE_TERMS  = ("MOVIE", "FILM")
_RSS_TV_TERMS     = ("SERIE", "SERIES", "TV", "SHOW", "SAISON", "SEASON", "EPISODE")
_RSS_DOC_TERMS    = ("DOCU", "DOCUMENTARY", "DOCUMENTAIRE")


def _rss_category_hints(rss_category: str) -> dict:
    """Normalise a tracker RSS category string into item_type / animation / doc hints."""
    cat = rss_category.upper()
    is_animation = any(x in cat for x in _RSS_ANIME_TERMS)
    is_doc       = any(x in cat for x in _RSS_DOC_TERMS)
    is_movie     = not is_animation and any(x in cat for x in _RSS_MOVIE_TERMS)
    is_tv        = is_animation or any(x in cat for x in _RSS_TV_TERMS)
    item_type    = "movie" if is_movie else ("tv" if is_tv else None)
    return {"item_type": item_type, "is_animation": is_animation, "is_doc": is_doc}


def get_qbit_categories(cfg: dict) -> tuple[dict, str | None]:
    """Return {category_name: save_path} for all categories defined in qBittorrent."""
    host   = cfg.get("QBIT_HOST", "http://localhost")
    port   = cfg.get("QBIT_PORT", 8080)
    user   = cfg.get("QBIT_USER", "admin")
    passwd = cfg.get("QBIT_PASS") or ""
    try:
        import qbittorrentapi
        cl = qbittorrentapi.Client(
            host=host, port=port, username=user, password=passwd,
            VERIFY_WEBUI_CERTIFICATE=False, REQUESTS_ARGS={"timeout": 5},
        )
        cl.auth_log_in()
        cats = {name: (info.get("savePath") or "") for name, info in cl.torrents_categories().items()}
        cl.auth_log_out()
        return cats, None
    except Exception as exc:
        return {}, str(exc)[:200]


def _tmdb_group_key(title: str) -> tuple[str, int | None]:
    """Compute a deduplication key from a release title.

    Returns (normalized_series_title, year) so that S01E07 and S01E08 of the
    same show share the same key and trigger only one TMDB API call.
    """
    try:
        import guessit
        g = guessit.guessit(title)
        return str(g.get("title") or title).lower().strip(), g.get("year")
    except Exception:
        return title.lower().strip(), None


def _tmdb_auth() -> tuple[dict, dict]:
    """Return (headers, base_params) for TMDB API calls. Reads config once."""
    u3d     = load_unit3dbot()
    tc      = u3d.get("TRACKER_CONFIG", {})
    token   = tc.get("TMDB_ACCESS_TOKEN") or ""
    api_key = tc.get("TMDB_APIKEY") or ""
    headers: dict     = {}
    base_params: dict = {"language": "fr-FR"}
    if token and token not in ("", "no_key"):
        headers["Authorization"] = f"Bearer {token}"
    elif api_key:
        base_params["api_key"] = api_key
    return headers, base_params


def tmdb_search_rss_item(title: str, *,
                          rss_category: str = "",
                          _headers: dict | None = None,
                          _base_params: dict | None = None) -> dict:
    """Lightweight TMDB lookup for an RSS item title.

    rss_category: raw category string from the tracker RSS (e.g. "MOVIE", "ANIME").
    Pass pre-loaded _headers/_base_params to avoid re-reading the config
    when enriching many items in a loop.
    Returns {"item_type": "movie"|"tv"|None, "genre_ids": [...], "tmdb_id": int|None}.
    Does not raise — returns empty result on any error.
    """
    try:
        import guessit

        guess     = guessit.guessit(title)
        name      = str(guess.get("title") or title)
        year      = guess.get("year")
        ep_type   = str(guess.get("type") or "")
        item_type = "tv" if ep_type == "episode" else "movie"

        # RSS category is a stronger signal than guessit for item_type
        if rss_category:
            hints = _rss_category_hints(rss_category)
            if hints["item_type"]:
                item_type = hints["item_type"]

        if _headers is None or _base_params is None:
            _headers, _base_params = _tmdb_auth()

        if not _headers and "api_key" not in _base_params and "Authorization" not in _headers:
            return {"item_type": item_type, "genre_ids": [], "tmdb_id": None}

        params = {**_base_params, "query": name}
        if year:
            params["year"] = int(year)

        def _search(itype: str) -> list:
            url = (
                "https://api.themoviedb.org/3/search/tv"
                if itype == "tv"
                else "https://api.themoviedb.org/3/search/movie"
            )
            r = _tmdb_session.get(url, headers=_headers, params=params, timeout=(3, 5))
            return r.json().get("results", []) if r.ok else []

        results = _search(item_type)
        if not results:
            alt_type = "tv" if item_type == "movie" else "movie"
            results  = _search(alt_type)
            if results:
                item_type = alt_type

        if not results:
            return {"item_type": item_type, "genre_ids": [], "tmdb_id": None}

        best = results[0]
        return {
            "item_type": item_type,
            "genre_ids": best.get("genre_ids") or [],
            "tmdb_id":   best.get("id"),
        }
    except Exception as exc:
        _log.warning("TMDB RSS search '%s': %s", title, exc)
        return {"item_type": None, "genre_ids": [], "tmdb_id": None}


def detect_category_key(item_type: str | None, genre_ids: list, categories_cfg: dict,
                         rss_category: str = "") -> str | None:
    """Return the key of the best matching enabled category, or None.

    rss_category (e.g. "ANIME", "MOVIE") is used as a strong override signal:
    - forces item_type if ambiguous
    - forces animation detection even when TMDB genre 16 is absent
    """
    hints    = _rss_category_hints(rss_category) if rss_category else {}
    eff_type = hints.get("item_type") or item_type
    if not eff_type:
        return None

    cats     = categories_cfg.get("categories", {})
    is_movie = eff_type == "movie"
    # RSS "ANIME" keyword overrides TMDB genre list — avoids series/anime confusion
    is_anim  = hints.get("is_animation") or (_GENRE_ANIMATION in genre_ids)
    is_doc   = hints.get("is_doc")       or (_GENRE_DOCUMENTARY in genre_ids)

    if is_movie:
        if is_anim and cats.get("movies_animation",   {}).get("enabled"):
            return "movies_animation"
        if is_doc  and cats.get("movies_documentary", {}).get("enabled"):
            return "movies_documentary"
        if cats.get("movies", {}).get("enabled"):
            return "movies"
    else:
        if is_anim and cats.get("series_animation",   {}).get("enabled"):
            return "series_animation"
        if is_doc  and cats.get("series_documentary", {}).get("enabled"):
            return "series_documentary"
        if cats.get("series", {}).get("enabled"):
            return "series"
    return None


# ── Category resolution ───────────────────────────────────────────────────────

def _cat_qbit_name(it: dict, cats_cfg: dict) -> str | None:
    """Return the qBittorrent category name for an item.

    download_category is already a qbit_name (written by the UI select).
    suggested_category is an internal key that must be resolved to qbit_name.
    """
    if it.get("download_category"):
        return it["download_category"]
    key = it.get("suggested_category")
    if key:
        return cats_cfg.get(key, {}).get("qbit_name") or None
    return None


# ── Download dispatch ─────────────────────────────────────────────────────────

def download_item(item: dict, feed: dict, category: str | None = None) -> str | None:
    url = item.get("torrent_url") or ""
    if not url:
        return "Aucune URL de téléchargement"
    u3d             = load_unit3dbot()
    tc_cfg          = u3d.get("TORRENT_CLIENT_CONFIG", {})
    client_override = (feed.get("torrent_client") or "").lower()
    client          = client_override or (tc_cfg.get("TORRENT_CLIENT") or "qbittorrent").lower()
    save_path       = feed.get("save_path", "") or ""
    tag             = (feed.get("tag") or "RSS").strip() or "RSS"
    if client == "qbittorrent":
        return _dl_qbit(url, save_path, tc_cfg, tag, category=category)
    if client == "transmission":
        return _dl_transmission(url, save_path, tc_cfg, tag)
    if client == "rtorrent":
        return _dl_rtorrent(url, save_path, tc_cfg, tag)
    return f"Client inconnu : {client}"


def _dl_qbit(url: str, save_path: str, cfg: dict, tag: str = "RSS",
             category: str | None = None) -> str | None:
    host   = cfg.get("QBIT_HOST", "http://localhost")
    port   = cfg.get("QBIT_PORT", 8080)
    user   = cfg.get("QBIT_USER", "admin")
    passwd = cfg.get("QBIT_PASS") or ""
    try:
        import qbittorrentapi
        cl = qbittorrentapi.Client(
            host=host, port=port, username=user, password=passwd,
            VERIFY_WEBUI_CERTIFICATE=False, REQUESTS_ARGS={"timeout": 10},
        )
        cl.auth_log_in()
        opts: dict = {"tags": tag}
        if category:
            opts["category"] = category
            opts["use_auto_torrent_management"] = True
        elif save_path:
            opts["savepath"] = save_path
        cl.torrents_add(urls=url, **opts)
        cl.auth_log_out()
        return None
    except Exception as exc:
        return str(exc)[:200]


def _dl_transmission(url: str, save_path: str, cfg: dict, tag: str = "RSS") -> str | None:
    host   = cfg.get("TRASM_HOST", "127.0.0.1")
    port   = int(cfg.get("TRASM_PORT", 9091))
    user   = cfg.get("TRASM_USER") or None
    passwd = cfg.get("TRASM_PASS") or None
    try:
        import transmission_rpc
        cl = transmission_rpc.Client(
            host=host, port=port, username=user, password=passwd, timeout=10,
        )
        kwargs: dict = {"labels": [tag]}
        if save_path:
            kwargs["download_dir"] = save_path
        cl.add_torrent(url, **kwargs)
        return None
    except Exception as exc:
        return str(exc)[:200]


def _dl_rtorrent(url: str, save_path: str, cfg: dict, tag: str = "RSS") -> str | None:
    host = cfg.get("RTORR_HOST", "127.0.0.1")
    port = int(cfg.get("RTORR_PORT", 8080))
    try:
        import xmlrpc.client, requests as _req
        proxy = xmlrpc.client.ServerProxy(f"http://{host}:{port}/RPC2", allow_none=True)
        cmds  = [f"d.custom1.set={tag}"]
        if save_path:
            cmds.insert(0, f"d.directory.set={save_path}")
            r = _req.get(url, timeout=15)
            r.raise_for_status()
            proxy.load.raw_start("", xmlrpc.client.Binary(r.content), *cmds)
        else:
            proxy.load.start("", url, *cmds)
        return None
    except Exception as exc:
        return str(exc)[:200]


# ── Torrent client progress ───────────────────────────────────────────────────

def get_client_torrents(tags: set | None = None) -> tuple[list[dict], str | None]:
    u3d    = load_unit3dbot()
    tc_cfg = u3d.get("TORRENT_CLIENT_CONFIG", {})
    client = (tc_cfg.get("TORRENT_CLIENT") or "qbittorrent").lower()
    if client == "qbittorrent":
        return _get_qbit_rss_torrents(tc_cfg, tags)
    if client == "transmission":
        return _get_transmission_torrents(tc_cfg, tags)
    if client == "rtorrent":
        return _get_rtorrent_torrents(tc_cfg, tags)
    return [], f"Client non pris en charge : {client}"


def _get_qbit_rss_torrents(cfg: dict, tags: set | None) -> tuple[list[dict], str | None]:
    host   = cfg.get("QBIT_HOST", "http://localhost")
    port   = cfg.get("QBIT_PORT", 8080)
    user   = cfg.get("QBIT_USER", "admin")
    passwd = cfg.get("QBIT_PASS") or ""
    try:
        import qbittorrentapi
        from core.torrent import _QBIT_STATE
        cl = qbittorrentapi.Client(
            host=host, port=port, username=user, password=passwd,
            VERIFY_WEBUI_CERTIFICATE=False, REQUESTS_ARGS={"timeout": 5},
        )
        cl.auth_log_in()
        out = []
        for t in cl.torrents_info():
            if tags:
                torrent_tags = {s.strip() for s in (t.tags or "").split(",") if s.strip()}
                if not torrent_tags.intersection(tags):
                    continue
            label, cls = _QBIT_STATE.get(t.state, (t.state, "secondary"))
            out.append({
                "name":                 t.name,
                "hash":                 t.hash,
                "size":                 t.size,
                "size_human":           _human(t.size),
                "ratio":                round(t.ratio, 2),
                "state":                t.state,
                "state_label":          label,
                "state_class":          cls,
                "upload_speed":         t.upspeed,
                "upload_speed_human":   _human(t.upspeed) + "/s",
                "download_speed":       t.dlspeed,
                "download_speed_human": _human(t.dlspeed) + "/s",
                "uploaded":             t.uploaded,
                "uploaded_human":       _human(t.uploaded),
                "progress":             round(t.progress * 100, 1),
                "added_on":             t.added_on,
                "category":             t.category,
                "seeding_time":         t.get("seeding_time", 0) or 0,
                "num_complete":         t.get("num_complete", 0) or 0,
            })
        cl.auth_log_out()
        return out, None
    except Exception as exc:
        return [], str(exc)[:200]


def _get_transmission_torrents(cfg: dict, tags: set | None = None) -> tuple[list[dict], str | None]:
    host   = cfg.get("TRASM_HOST", "127.0.0.1")
    port   = int(cfg.get("TRASM_PORT", 9091))
    user   = cfg.get("TRASM_USER") or None
    passwd = cfg.get("TRASM_PASS") or None
    try:
        import transmission_rpc
        cl  = transmission_rpc.Client(host=host, port=port, username=user, password=passwd, timeout=10)
        out = []
        for t in cl.get_torrents():
            if tags:
                t_labels = {lb.strip() for lb in (t.labels or [])}
                if not t_labels.intersection(tags):
                    continue
            st = str(t.status)
            if st == "stopped":
                label, cls = "Stoppé", "secondary"
            elif "download" in st:
                label, cls = "Téléchargement", "info"
            elif "seed" in st:
                label, cls = "Partage", "success"
            elif "check" in st:
                label, cls = "Vérification", "warning"
            else:
                label, cls = st, "secondary"
            added = 0
            try:
                added = int(t.added_date.timestamp()) if t.added_date else 0
            except Exception:
                pass
            out.append({
                "name":                 t.name,
                "hash":                 t.hash_string,
                "size":                 t.total_size or 0,
                "size_human":           _human(t.total_size or 0),
                "ratio":                round(float(t.ratio or 0), 2),
                "state":                st,
                "state_label":          label,
                "state_class":          cls,
                "upload_speed":         t.rate_upload or 0,
                "upload_speed_human":   _human(t.rate_upload or 0) + "/s",
                "download_speed":       t.rate_download or 0,
                "download_speed_human": _human(t.rate_download or 0) + "/s",
                "uploaded":             t.uploaded_ever or 0,
                "uploaded_human":       _human(t.uploaded_ever or 0),
                "progress":             round(float(t.progress or 0), 1),
                "added_on":             added,
                "category":             "",
            })
        return out, None
    except Exception as exc:
        return [], str(exc)[:200]


def _get_rtorrent_torrents(cfg: dict, tags: set | None = None) -> tuple[list[dict], str | None]:
    host = cfg.get("RTORR_HOST", "127.0.0.1")
    port = int(cfg.get("RTORR_PORT", 8080))
    try:
        import xmlrpc.client
        proxy   = xmlrpc.client.ServerProxy(f"http://{host}:{port}/RPC2", allow_none=True)
        results = proxy.d.multicall2("", "default",
                                     "d.name=", "d.size_bytes=", "d.completed_bytes=",
                                     "d.up.total=", "d.up.rate=", "d.down.rate=",
                                     "d.ratio=", "d.is_active=", "d.is_open=", "d.hash=",
                                     "d.custom1=")
        out = []
        for row in results:
            name, size, done, up_total, up_rate, dl_rate, ratio_int, is_active, is_open, h, custom1 = row
            if tags and (custom1 or "").strip() not in tags:
                continue
            size     = int(size or 0)
            done     = int(done or 0)
            up_total = int(up_total or 0)
            up_rate  = int(up_rate or 0)
            dl_rate  = int(dl_rate or 0)
            ratio    = int(ratio_int or 0) / 1000
            pct      = round(done / size * 100, 1) if size else 0
            if not is_open:
                label, cls = "Stoppé", "secondary"
            elif not is_active:
                label, cls = "Inactif", "secondary"
            elif pct < 100:
                label, cls = "Téléchargement", "info"
            else:
                label, cls = "Partage", "success"
            out.append({
                "name":                 name,
                "hash":                 h,
                "size":                 size,
                "size_human":           _human(size),
                "ratio":                round(ratio, 2),
                "state":                "seeding" if pct >= 100 and is_active else "downloading",
                "state_label":          label,
                "state_class":          cls,
                "upload_speed":         up_rate,
                "upload_speed_human":   _human(up_rate) + "/s",
                "download_speed":       dl_rate,
                "download_speed_human": _human(dl_rate) + "/s",
                "uploaded":             up_total,
                "uploaded_human":       _human(up_total),
                "progress":             pct,
                "added_on":             0,
                "category":             "",
            })
        return out, None
    except Exception as exc:
        return [], str(exc)[:200]


# ── Gemini duplicate check (RSS variant) ─────────────────────────────────────

def rss_gemini_check(title: str, tracker_name: str) -> tuple[bool, dict | None]:
    """Check if an RSS title matches an existing torrent on Gemini.

    Works like gemini_duplicate_check() but takes a raw title string instead of
    a filesystem path, so no Media object is needed.
    Raises RateLimitError on HTTP 429.
    """
    import requests as _req
    from common import title as _title
    from common.settings import Load
    from unit3dup import config_settings
    from unit3dup.duplicate import CompareTitles

    Load()
    if not config_settings.user_preferences.DUPLICATE_ON:
        return False, None

    u3d       = load_unit3dbot()
    tc        = u3d.get("TRACKER_CONFIG", {})
    base_url  = (tc.get("Gemini_URL") or "").rstrip("/")
    api_token = tc.get("Gemini_APIKEY", "")
    if not base_url:
        return False, None

    query      = _title.Guessit(title)
    keyword    = query.guessit_title.replace("-", " ") or title
    filter_url = f"{base_url}/api/torrents/filter"
    headers    = {"Accept": "application/json", "Authorization": f"Bearer {api_token}"}
    params     = {"name": keyword, "perPage": 100}

    try:
        r = _req.get(filter_url, headers=headers, params=params, timeout=10)
        if r.status_code == 429:
            from core.duplicate import RateLimitError
            raise RateLimitError(f"Tracker 429 on '{keyword}'")
        r.raise_for_status()
        resp = r.json()
    except Exception as exc:
        from core.duplicate import RateLimitError
        if isinstance(exc, RateLimitError):
            raise
        _log.warning("RSS dup check '%s': %s", keyword, exc)
        return False, None

    matches = []
    for t_data in (resp.get("data") or []):
        attrs = t_data.get("attributes", {})
        if not CompareTitles(
            tracker_file=_title.Guessit(attrs.get("name", "")),
            content_file=query,
        ).process():
            continue
        size = attrs.get("size") or 0
        matches.append({
            "tracker_id": t_data.get("id"),
            "name":       attrs.get("name", ""),
            "size_gb":    round(size / (1024 ** 3), 2) if size else 0,
        })

    if not matches:
        return False, None
    return True, {"matches": matches, "count": len(matches)}


def apply_rss_duplicate_checks(feed_id: str) -> tuple[int, int, bool]:
    """Run Gemini duplicate checks on all matched, undownloaded items of a feed.

    Returns (checked, duplicates_found, rate_limited).
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from core.conf import default_tracker_name
    from core.duplicate import RateLimitError

    tracker = default_tracker_name()
    if not tracker:
        _log.warning("RSS dup check ignoré : MULTI_TRACKER non configuré")
        return 0, 0, False

    try:
        from common.settings import Load
        from unit3dup import config_settings
        Load()
        if not config_settings.user_preferences.DUPLICATE_ON:
            _log.warning("RSS dup check ignoré : DUPLICATE_ON=false")
            return 0, 0, False
    except Exception as exc:
        _log.warning("RSS dup check ignoré : Load() échoué — %s", exc)
        return 0, 0, False

    items    = load_items(feed_id)
    to_check = [
        it for it in items
        if it.get("matched") and not it.get("duplicate_status")
    ]
    if not to_check:
        return 0, 0, False

    abort_flag = [False]

    def _one(it):
        if abort_flag[0]:
            return it["guid"], None, None, True   # skipped
        try:
            is_dup, info = rss_gemini_check(it["title"], tracker)
            return it["guid"], is_dup, info, False
        except RateLimitError:
            abort_flag[0] = True
            raise
        except Exception as exc:
            _log.warning("RSS dup check error '%s': %s", it.get("title"), exc)
            return it["guid"], False, None, False

    rate_limited    = False
    result_map: dict = {}
    with ThreadPoolExecutor(max_workers=3) as ex:
        futures = {ex.submit(_one, it): it for it in to_check}
        for fut in as_completed(futures):
            try:
                guid, is_dup, info, skipped = fut.result()
                if not skipped:
                    result_map[guid] = (is_dup, info)
            except RateLimitError:
                rate_limited = True

    if not result_map:
        return 0, 0, rate_limited

    duplicates_found = 0
    with _rss_lock:
        all_items = _load_items_raw()
        for j, stored in enumerate(all_items):
            if stored.get("feed_id") != feed_id:
                continue
            guid = stored.get("guid")
            if guid not in result_map:
                continue
            is_dup, info = result_map[guid]
            all_items[j]["duplicate_status"] = "duplicate" if is_dup else "not_duplicate"
            all_items[j]["duplicate_info"]   = info
            if is_dup:
                duplicates_found += 1
        save_items(all_items)

    return len(result_map), duplicates_found, rate_limited


# ── Bulk download ────────────────────────────────────────────────────────────

def download_all_matched(feed_id: str) -> tuple[int, int]:
    """Download every matched, not-yet-downloaded item for a feed.

    Returns (launched, errors).
    """
    from core.conf import load_web_config
    feed = get_feed(feed_id)
    if feed is None:
        return 0, 0
    cfg      = load_web_config()
    cats_cfg = cfg.get("rss_categories", {}).get("categories", {})
    items    = load_items(feed_id)
    launched = errors = 0
    for it in items:
        if not it.get("matched") or it.get("downloaded"):
            continue
        if it.get("duplicate_status") == "duplicate":
            continue   # skip confirmed duplicates
        cat = _cat_qbit_name(it, cats_cfg)
        err = download_item(it, feed, category=cat)
        with _rss_lock:
            all_items = _load_items_raw()
            for j, stored in enumerate(all_items):
                if stored.get("feed_id") == feed_id and stored.get("guid") == it["guid"]:
                    all_items[j]["downloaded"]        = err is None
                    all_items[j]["download_error"]    = err
                    all_items[j]["download_category"] = cat
                    break
            save_items(all_items)
        if err:
            errors += 1
            _log.warning("RSS bulk DL error feed=%s: %s", feed.get("name"), err)
        else:
            launched += 1
            _log.info("RSS bulk DL ok feed=%s title=%s", feed.get("name"), it["title"])
    return launched, errors


# ── Background poller ─────────────────────────────────────────────────────────

class _RSSPoller(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name="rss-poller")
        self._stop_evt = threading.Event()

    def run(self) -> None:
        _log.info("RSS poller démarré")
        while not self._stop_evt.wait(60):
            self._tick()

    def _tick(self) -> None:
        now = datetime.now(timezone.utc)
        for feed in load_feeds():
            if not feed.get("enabled"):
                continue
            interval = int(feed.get("interval_minutes", 30)) * 60
            last_str = feed.get("last_fetched")
            if last_str:
                try:
                    last_dt = datetime.fromisoformat(last_str)
                    if last_dt.tzinfo is None:
                        last_dt = last_dt.replace(tzinfo=timezone.utc)
                    if (now - last_dt).total_seconds() < interval:
                        continue
                except Exception:
                    pass
            self._process_feed(feed)

    def _process_feed(self, feed: dict) -> None:
        _log.info("RSS fetch: %s (%s)", feed.get("name"), feed["id"])
        items, err = fetch_feed(feed)
        with _rss_lock:
            feeds = load_feeds()
            for i, f in enumerate(feeds):
                if f["id"] == feed["id"]:
                    feeds[i]["last_fetched"] = datetime.now(timezone.utc).isoformat()
                    feeds[i]["last_error"]   = err
                    feeds[i]["item_count"]   = len(items)
                    break
            save_feeds(feeds)
        if err or not items:
            return
        with _rss_lock:
            existing         = _load_items_raw()
            merged, to_add   = _merge_items(existing, items, feed["id"])
            save_items(merged)
        if not feed.get("auto_download", True):
            return
        from core.conf import load_web_config
        cfg        = load_web_config()
        rss_cats   = cfg.get("rss_categories", {})
        cats_enabled = rss_cats.get("enabled", False)
        cats_cfg   = rss_cats.get("categories", {})
        matched_new = [it for it in to_add if it.get("matched")]
        if not matched_new:
            return

        # ── Batch TMDB enrichment (1 call per unique title, parallel) ─────────
        # guid → {tmdb_enriched, tmdb_item_type, ..., suggested_category, _qbit_cat}
        enrichment_map: dict[str, dict] = {}
        if cats_enabled:
            from collections import defaultdict
            from concurrent.futures import ThreadPoolExecutor, as_completed
            tmdb_hdrs, tmdb_params = _tmdb_auth()

            groups: dict = defaultdict(list)
            for it in matched_new:
                groups[_tmdb_group_key(it["title"])].append(it)

            def _enrich_group(representative, group_items):
                rss_cat = representative.get("rss_category", "")
                tmdb    = tmdb_search_rss_item(representative["title"],
                                               rss_category=rss_cat,
                                               _headers=tmdb_hdrs, _base_params=tmdb_params)
                cat_key  = detect_category_key(tmdb["item_type"], tmdb["genre_ids"],
                                               rss_cats, rss_category=rss_cat)
                qbit_cat = cats_cfg.get(cat_key, {}).get("qbit_name") or None if cat_key else None
                payload  = {
                    "tmdb_enriched":      True,
                    "tmdb_item_type":     tmdb["item_type"],
                    "tmdb_genre_ids":     tmdb["genre_ids"],
                    "tmdb_id":            tmdb["tmdb_id"],
                    "suggested_category": cat_key,
                    "_qbit_cat":          qbit_cat,
                }
                return group_items, payload

            with ThreadPoolExecutor(max_workers=4) as ex:
                futures = {ex.submit(_enrich_group, items[0], items): None
                           for items in groups.values()}
                for fut in as_completed(futures):
                    try:
                        group_items, payload = fut.result()
                        for it in group_items:
                            enrichment_map[it["guid"]] = payload
                    except Exception as exc:
                        _log.warning("RSS TMDB batch error: %s", exc)

            # Persist enrichment for all items at once
            with _rss_lock:
                all_items = _load_items_raw()
                for j, stored in enumerate(all_items):
                    guid = stored.get("guid")
                    if guid in enrichment_map:
                        payload = {k: v for k, v in enrichment_map[guid].items()
                                   if not k.startswith("_")}
                        all_items[j].update(payload)
                save_items(all_items)

        # ── Download each matched item ─────────────────────────────────────────
        for it in matched_new:
            cat    = (enrichment_map.get(it["guid"]) or {}).get("_qbit_cat")
            dl_err = download_item(it, feed, category=cat)
            with _rss_lock:
                all_items = _load_items_raw()
                for j, stored in enumerate(all_items):
                    if stored.get("feed_id") == it["feed_id"] and stored.get("guid") == it["guid"]:
                        all_items[j]["downloaded"]        = dl_err is None
                        all_items[j]["download_error"]    = dl_err
                        all_items[j]["download_category"] = cat
                        break
                save_items(all_items)
            if dl_err:
                _log.warning("RSS DL error feed=%s: %s", feed.get("name"), dl_err)
            else:
                _log.info("RSS DL ok feed=%s title=%s cat=%s", feed.get("name"), it["title"], cat)


_poller: "_RSSPoller | None" = None


def start_rss_poller() -> None:
    global _poller
    if _poller is None or not _poller.is_alive():
        _poller = _RSSPoller()
        _poller.start()
