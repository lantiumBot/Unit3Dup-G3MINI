"""RSS feed management blueprint."""
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

bp = Blueprint("rss", __name__)


@bp.route("/api/rss/feeds", methods=["GET"])
def api_rss_feeds():
    from core.rss import load_feeds
    return jsonify(load_feeds())


@bp.route("/api/rss/feeds", methods=["POST"])
def api_rss_feed_add():
    from core.rss import add_feed
    data = request.get_json(force=True) or {}
    if not (data.get("url") or "").strip():
        return jsonify({"error": "URL requise"}), 400
    feed = add_feed(data)
    return jsonify(feed), 201


@bp.route("/api/rss/feeds/<feed_id>", methods=["PUT"])
def api_rss_feed_update(feed_id: str):
    from core.rss import update_feed, refilter_feed_items
    data = request.get_json(force=True) or {}
    feed = update_feed(feed_id, data)
    if feed is None:
        return jsonify({"error": "Feed introuvable"}), 404
    # Re-evaluate matched on all existing items when filters or match mode change
    if "filters" in data or "filter_match_all" in data:
        refilter_feed_items(feed_id, feed.get("filters", []),
                            match_all=feed.get("filter_match_all", False))
    return jsonify(feed)


@bp.route("/api/rss/feeds/<feed_id>", methods=["DELETE"])
def api_rss_feed_delete(feed_id: str):
    from core.rss import delete_feed
    if not delete_feed(feed_id):
        return jsonify({"error": "Feed introuvable"}), 404
    return jsonify({"ok": True})


@bp.route("/api/rss/feeds/<feed_id>/fetch", methods=["POST"])
def api_rss_feed_fetch(feed_id: str):
    from core.rss import (
        get_feed, fetch_feed, _load_items_raw, save_items,
        _merge_items, _rss_lock, load_feeds, save_feeds, matches_filters,
    )
    feed = get_feed(feed_id)
    if feed is None:
        return jsonify({"error": "Feed introuvable"}), 404
    items, err = fetch_feed(feed)
    with _rss_lock:
        feeds = load_feeds()
        current_filters = []
        for i, f in enumerate(feeds):
            if f["id"] == feed_id:
                feeds[i]["last_fetched"] = datetime.now(timezone.utc).isoformat()
                feeds[i]["last_error"]   = err
                feeds[i]["item_count"]   = len(items)
                current_filters          = f.get("filters", [])
                break
        save_feeds(feeds)
        existing = _load_items_raw()
        if not err and items:
            existing, _ = _merge_items(existing, items, feed_id)
        # Re-apply current filters to every item of this feed so that
        # adding/removing a keyword immediately updates matched status.
        match_all = next((f.get("filter_match_all", False) for f in feeds if f["id"] == feed_id), False)
        for j, it in enumerate(existing):
            if it.get("feed_id") != feed_id:
                continue
            existing[j]["matched"] = matches_filters(it.get("title", ""), current_filters,
                                                     match_all_includes=match_all)
        save_items(existing)
    return jsonify({"items": len(items), "error": err})


@bp.route("/api/rss/feeds/<feed_id>/items", methods=["GET"])
def api_rss_feed_items(feed_id: str):
    from core.rss import load_items
    items = load_items(feed_id)
    items.sort(key=lambda x: x.get("fetched_at", ""), reverse=True)
    return jsonify(items)


@bp.route("/api/rss/download", methods=["POST"])
def api_rss_download():
    from core.rss import _load_items_raw, save_items, get_feed, download_item, _rss_lock
    data     = request.get_json(force=True) or {}
    feed_id  = data.get("feed_id", "")
    guid     = data.get("guid", "")
    category = data.get("category") or None   # optional qBit category name
    if not feed_id or not guid:
        return jsonify({"error": "feed_id et guid requis"}), 400
    all_items = _load_items_raw()
    item = next((it for it in all_items if it.get("feed_id") == feed_id and it.get("guid") == guid), None)
    if item is None:
        return jsonify({"error": "Item introuvable"}), 404
    feed = get_feed(feed_id)
    if feed is None:
        return jsonify({"error": "Feed introuvable"}), 404
    err = download_item(item, feed, category=category)
    with _rss_lock:
        all_items = _load_items_raw()
        for j, it in enumerate(all_items):
            if it.get("feed_id") == feed_id and it.get("guid") == guid:
                all_items[j]["downloaded"]        = err is None
                all_items[j]["download_error"]    = err
                all_items[j]["download_category"] = category
                break
        save_items(all_items)
    if err:
        return jsonify({"error": err}), 500
    return jsonify({"ok": True})


@bp.route("/api/rss/feeds/<feed_id>/check-duplicates", methods=["POST"])
def api_rss_check_duplicates(feed_id: str):
    from core.rss import get_feed, apply_rss_duplicate_checks
    if get_feed(feed_id) is None:
        return jsonify({"error": "Feed introuvable"}), 404
    checked, duplicates, rate_limited = apply_rss_duplicate_checks(feed_id)
    return jsonify({"checked": checked, "duplicates": duplicates, "rate_limited": rate_limited})


@bp.route("/api/rss/feeds/<feed_id>/download-matched", methods=["POST"])
def api_rss_download_matched(feed_id: str):
    from core.rss import get_feed, download_all_matched
    if get_feed(feed_id) is None:
        return jsonify({"error": "Feed introuvable"}), 404
    launched, errors = download_all_matched(feed_id)
    return jsonify({"launched": launched, "errors": errors})


@bp.route("/api/rss/categories/check", methods=["GET"])
def api_rss_categories_check():
    """Compare configured RSS categories against qBittorrent's existing categories."""
    from core.rss import get_qbit_categories
    from core.conf import load_unit3dbot, load_web_config

    u3d    = load_unit3dbot()
    tc_cfg = u3d.get("TORRENT_CLIENT_CONFIG", {})
    client = (tc_cfg.get("TORRENT_CLIENT") or "qbittorrent").lower()

    if client != "qbittorrent":
        return jsonify({"error": "Catégories disponibles uniquement avec qBittorrent"}), 400

    qbit_cats, err = get_qbit_categories(tc_cfg)
    cfg            = load_web_config()
    rss_cats_cfg   = cfg.get("rss_categories", {}).get("categories", {})

    result = {}
    for key, cat_cfg in rss_cats_cfg.items():
        qbit_name = cat_cfg.get("qbit_name", "")
        result[key] = {
            "qbit_name": qbit_name,
            "exists":    bool(qbit_name) and qbit_name in qbit_cats,
            "save_path": qbit_cats.get(qbit_name, ""),
        }

    return jsonify({"categories": result, "qbit_cats": list(qbit_cats.keys()), "error": err})


@bp.route("/api/rss/items/enrich", methods=["POST"])
def api_rss_items_enrich():
    """TMDB-enrich RSS items and compute a suggested category for each."""
    from core.rss import (tmdb_search_rss_item, detect_category_key,
                          _load_items_raw, save_items, _rss_lock)
    from core.conf import load_web_config

    data    = request.get_json(force=True) or {}
    feed_id = data.get("feed_id", "")
    guids   = set(data.get("guids", []))

    if not feed_id:
        return jsonify({"error": "feed_id requis"}), 400

    cfg      = load_web_config()
    rss_cats = cfg.get("rss_categories", {})
    enabled  = rss_cats.get("enabled", False)

    all_items = _load_items_raw()
    to_enrich = [
        it for it in all_items
        if it.get("feed_id") == feed_id
        and (not guids or it.get("guid") in guids)
        and not it.get("tmdb_enriched")
        and it.get("matched")
        and not it.get("downloaded")
        and it.get("duplicate_status") != "duplicate"
    ]

    if not to_enrich:
        return jsonify({"enriched": 0, "results": {}})

    from core.rss import _tmdb_auth, _tmdb_group_key
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from collections import defaultdict

    hdrs, base_params = _tmdb_auth()

    # Group by (base_title, year) — one TMDB call per unique series/movie
    groups: dict = defaultdict(list)
    for it in to_enrich:
        groups[_tmdb_group_key(it["title"])].append(it)

    def _one(representative, group_items):
        rss_cat = representative.get("rss_category", "")
        tmdb    = tmdb_search_rss_item(representative["title"],
                                       rss_category=rss_cat,
                                       _headers=hdrs, _base_params=base_params)
        cat_key = detect_category_key(tmdb["item_type"], tmdb["genre_ids"], rss_cats,
                                      rss_category=rss_cat) if enabled else None
        return group_items, tmdb, cat_key

    results: dict = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(_one, items[0], items): key
                   for key, items in groups.items()}
        for fut in as_completed(futures):
            try:
                group_items, tmdb, cat_key = fut.result()
                for it in group_items:
                    results[it["guid"]] = {
                        "item_type":          tmdb["item_type"],
                        "genre_ids":          tmdb["genre_ids"],
                        "tmdb_id":            tmdb["tmdb_id"],
                        "suggested_category": cat_key,
                    }
            except Exception:
                pass

    with _rss_lock:
        all_items = _load_items_raw()
        for j, it in enumerate(all_items):
            if it.get("feed_id") != feed_id:
                continue
            guid = it.get("guid")
            if guid not in results:
                continue
            all_items[j].update({
                "tmdb_enriched":      True,
                "tmdb_item_type":     results[guid]["item_type"],
                "tmdb_genre_ids":     results[guid]["genre_ids"],
                "tmdb_id":            results[guid]["tmdb_id"],
                "suggested_category": results[guid]["suggested_category"],
            })
        save_items(all_items)

    return jsonify({"enriched": len(results), "results": results})


@bp.route("/api/rss/torrents", methods=["GET"])
def api_rss_torrents():
    from core.rss import get_client_torrents, load_feeds
    feeds = load_feeds()
    tags  = {(f.get("tag") or "RSS").strip() or "RSS" for f in feeds} or {"RSS"}
    torrents, err = get_client_torrents(tags)
    return jsonify({"torrents": torrents, "error": err})
