"""Gemini tracker inventory sync — fetch user uploads and register them in history.db.

Uses the same Unit3d / Torrent API stack as the duplicate checker:
  - filterAPI.get_uploader(username) → GET /api/torrents/filter?uploader=<username>
  - Tracker._next_page(links.next)   → pagination
  - Tracker._get() handles HTTP 429 (rate-limit) automatically

Writes are additive (INSERT OR IGNORE / targeted UPDATE) — the history table is
never wiped.  State is persisted in the SQLite app_state table under the key
"gemini_inventory".

Optimisation deux niveaux :
  1. TTL (inventory_cache_ttl_hours) : si le dernier check est récent → skip total.
  2. Count check : si TTL expiré → une requête API perPage=1 récupère meta.total.
     Si le total est identique au précédent run → skip (pas de re-fetch).
     Si le total a changé → re-fetch complet (delta ajouté via INSERT OR IGNORE).
"""
import logging
import threading
import time
from datetime import datetime
from pathlib import Path

from core.conf import (
    load_unit3dbot,
    default_tracker_name,
)
from core.db import db_state_get, db_state_set

_log         = logging.getLogger("core.gemini_inventory")
_lock        = threading.Lock()   # protects state reads/writes
_fetch_mutex = threading.Lock()   # serialises full network fetches (anti-race BG vs scan)

_STATE_KEY = "gemini_inventory"


# ── Persistent state helpers ──────────────────────────────────────────────────

def _load_state() -> dict:
    return db_state_get(_STATE_KEY)


def _save_state(state: dict) -> None:
    db_state_set(_STATE_KEY, state)


# ── Background sync status ─────────────────────────────────────────────────────

_bg_status: dict = {
    "running":     False,
    "last_run_at": None,
    "total":       0,
    "added":       0,
    "cached":      False,
    "error":       None,
}
_bg_status_lock = threading.Lock()


# ── Progress emission (throttled) ─────────────────────────────────────────────

_last_progress_ts: float = 0.0


def _emit(event: str, data: dict) -> None:
    global _last_progress_ts
    if event == "scan_progress":
        now = time.time()
        is_page_update = data.get("page", 0) > 0 and data.get("phase") == "inventory"
        if is_page_update and (now - _last_progress_ts) < 0.5:
            return
        _last_progress_ts = now
    try:
        from extensions import socketio
        socketio.emit(event, data)
    except Exception:
        pass


# ── TTL helpers ───────────────────────────────────────────────────────────────

def _within_ttl(ts_str: str | None, hours: int) -> bool:
    """Return True if *ts_str* (ISO datetime) is fresher than *hours*."""
    if not ts_str or hours <= 0:
        return False
    try:
        ts = datetime.fromisoformat(ts_str)
        age_h = (datetime.now() - ts).total_seconds() / 3600
        return age_h < hours
    except Exception:
        return False


def should_refresh_inventory(source_folder: str) -> bool:  # noqa: ARG001
    """Return True if a background inventory refresh is warranted (TTL expired)."""
    from core.conf import load_web_config
    ttl_hours = int(load_web_config().get("inventory_cache_ttl_hours", 24))
    if ttl_hours <= 0:
        return False
    with _bg_status_lock:
        if _bg_status["running"]:
            return False  # already in progress
    state = _load_state()
    last_checked = state.get("last_checked_at")
    if not last_checked:
        return True
    return not _within_ttl(last_checked, ttl_hours)


# ── Count check (lightweight single-request) ─────────────────────────────────

def _get_remote_total(tracker, username: str) -> int | None:
    """Single lightweight API request returning the total upload count (meta.total)."""
    import requests
    try:
        url = f"{tracker.base_url.rstrip('/')}/api/torrents/filter"
        resp = requests.get(
            url,
            headers=tracker.headers,
            params={"api_token": tracker.api_token, "uploader": username, "perPage": 1},
            timeout=15,
        )
        if resp.status_code == 429:
            _log.warning("Inventory count check : rate-limited (429)")
            return None
        resp.raise_for_status()
        data  = resp.json()
        meta  = data.get("meta") or {}
        total = meta.get("total")
        return int(total) if total is not None else None
    except Exception as exc:
        _log.warning("Inventory count check échoué : %s", exc)
        return None


# ── Username resolution ───────────────────────────────────────────────────────

_api_username_cache: dict = {"value": None}


def _get_username_from_api(tracker) -> str | None:
    import requests
    try:
        url  = f"{tracker.base_url.rstrip('/')}/api/user"
        resp = requests.get(
            url,
            headers=tracker.headers,
            params={"api_token": tracker.api_token},
            timeout=15,
        )
        resp.raise_for_status()
        data  = resp.json()
        node  = data.get("data") or {}
        attrs = node.get("attributes") or {}
        uname = (
            attrs.get("username") or attrs.get("name")
            or node.get("username") or node.get("name")
            or data.get("username") or data.get("name")
        )
        if not uname:
            _log.warning(
                "GET /api/user : réponse reçue mais username introuvable. "
                "Réponse brute (500 premiers chars) : %s", str(data)[:500],
            )
        return uname or None
    except Exception as exc:
        _log.warning("Impossible de récupérer le compte utilisateur via /api/user : %s", exc)
        return None


def _resolve_username(tracker, username_cfg: str | None) -> str | None:
    if username_cfg:
        _api_username_cache["value"] = None
        return username_cfg
    if _api_username_cache["value"]:
        return _api_username_cache["value"]
    username = _get_username_from_api(tracker)
    if username:
        _api_username_cache["value"] = username
    return username


# ── File-release extraction ───────────────────────────────────────────────────

def _extract_file_releases(torrents: list[dict]) -> list[dict]:
    releases: list[dict] = []
    for t in torrents:
        attrs = t.get("attributes") or {}
        for f in (attrs.get("files") or []):
            name = f.get("name") or ""
            size = f.get("size") or 0
            if name:
                releases.append({"name": name, "size": size})
    return releases


# ── Tracker pagination ────────────────────────────────────────────────────────

def _fetch_all_user_torrents(
    tracker_name: str,
    username: str,
    *,
    silent: bool = False,
) -> list[dict]:
    """Paginate GET /api/torrents/filter?uploader=<username> following links.next.

    When *silent* is True, scan_progress events are suppressed (used for background
    syncs to avoid polluting the foreground scan's progress bar).
    """
    from unit3dup.torrent import Torrent

    torrent_obj = Torrent(tracker_name=tracker_name)
    torrent_obj.tracker.params["sortField"]     = "created_at"
    torrent_obj.tracker.params["sortDirection"] = "desc"
    results: list[dict] = []
    page = 1

    if not silent:
        _emit("scan_progress", {"phase": "inventory", "page": page, "fetched": 0})

    try:
        data = torrent_obj.get_by_uploader(username=username)
    except Exception as exc:
        _log.warning("Erreur lors de la récupération des uploads (page 1) : %s", exc)
        return results

    while True:
        items = data.get("data") or []
        if not items:
            break
        results.extend(items)
        _log.debug("Page %d : %d torrents (total %d)", page, len(items), len(results))

        next_url = (data.get("links") or {}).get("next")
        if not next_url:
            break

        page += 1
        if not silent:
            _emit("scan_progress", {"phase": "inventory", "page": page, "fetched": len(results)})
        time.sleep(0.7)

        try:
            data = torrent_obj.tracker.next(url=next_url)
        except Exception as exc:
            _log.warning("Erreur lors de la récupération des uploads (page %d) : %s", page, exc)
            break

    return results


# ── Public API ────────────────────────────────────────────────────────────────

def sync_gemini_inventory(
    source_folder: str,
    *,
    force: bool = False,
    silent: bool = False,
) -> tuple[int, int, bool]:
    """Fetch user uploads from tracker and register local matches in history.db.

    Optimisation à deux niveaux :
    1. TTL (``inventory_cache_ttl_hours``) — si le dernier check est frais → skip total.
    2. Count check — requête ``perPage=1`` pour lire ``meta.total``.
       Si total inchangé → mise à jour de ``last_checked_at`` + skip.
       Si total modifié → re-fetch complet (delta via ``INSERT OR IGNORE``).

    *silent=True* suppresses scan_progress socket events (used for background syncs).

    Returns ``(added_count, total_fetched, was_cached)``.
    """
    tracker_name = default_tracker_name()
    if not tracker_name:
        _log.warning(
            "Inventory annulé : MULTI_TRACKER non configuré dans Unit3Dbot.json "
            "(Paramètres › Tracker › MULTI_TRACKER doit contenir au moins une entrée)"
        )
        return 0, 0, False

    try:
        from unit3dup.pvtTracker import Unit3d
        tracker = Unit3d(tracker_name=tracker_name)
    except Exception as exc:
        _log.warning("Inventory annulé : impossible d'initialiser le tracker — %s", exc)
        return 0, 0, False

    u3d          = load_unit3dbot()
    username_cfg = (u3d.get("TRACKER_CONFIG", {}).get("Gemini_USERNAME") or "").strip() or None
    username     = _resolve_username(tracker, username_cfg)

    if not username:
        _log.warning(
            "Inventory annulé : impossible de résoudre le username. "
            "Configurez 'Gemini_USERNAME' dans Paramètres › Tracker."
        )
        return 0, 0, False

    now_str = datetime.now().isoformat()

    # ── Niveau 1 : TTL (indépendant du chemin source) ─────────────────────
    # Le cache est valide dès que last_checked_at est frais ET le username
    # correspond — le dossier source n'est pas dans la clé car l'inventaire
    # Gemini est per-user, pas per-dossier.
    with _lock:
        state           = _load_state()
        last_total      = state.get("total_fetched", -1)
        cached_username = state.get("username", "")

        _log.info(
            "Inventory : username='%s' cached_username='%s' last_total=%d force=%s",
            username, cached_username, last_total, force,
        )

        _has_cache = last_total >= 0 and (not cached_username or cached_username == username)

        if not force and _has_cache:
            from core.conf import load_web_config
            ttl_hours    = int(load_web_config().get("inventory_cache_ttl_hours", 24))
            last_checked = state.get("last_checked_at")

            if ttl_hours > 0 and _within_ttl(last_checked, ttl_hours):
                _log.info(
                    "Inventory : dans le TTL (%dh depuis %s) — ignoré",
                    ttl_hours, last_checked,
                )
                return 0, max(last_total, 0), True

            _log.info("Inventory : TTL expiré — vérification du count Gemini…")

    # ── Niveau 2 : count check (hors lock, I/O réseau) ────────────────────
    if not force and _has_cache:
        remote_total = _get_remote_total(tracker, username)
        if remote_total is None:
            # API inaccessible → conserver le cache silencieusement
            _log.warning("Inventory : count check inaccessible — cache conservé")
            return 0, max(last_total, 0), True
        if remote_total == last_total:
            _log.info(
                "Inventory : count inchangé (%d) — pas de re-fetch nécessaire",
                remote_total,
            )
            with _lock:
                st = _load_state()
                st["last_checked_at"] = now_str
                _save_state(st)
            return 0, remote_total, True
        _log.info(
            "Inventory : count %d → %d — re-fetch complet requis",
            last_total, remote_total,
        )

    # ── Sérialisation du fetch (anti-race BG vs scan) ────────────────────
    # Si un autre thread est déjà en train de fetcher (sync background lancée
    # au connect puis scan déclenché avant qu'elle ait fini), on attend qu'il
    # termine et on réutilise son résultat plutôt que de lancer un 2e fetch.
    _got_mutex = _fetch_mutex.acquire(blocking=False)
    if not _got_mutex:
        _log.info("Inventory : fetch déjà en cours (autre thread) — attente (max 10 min)…")
        _fetch_mutex.acquire(blocking=True, timeout=600)
        _fetch_mutex.release()
        with _lock:
            _fresh = _load_state()
        _log.info(
            "Inventory : fetch concurrent terminé — total=%d, cached",
            _fresh.get("total_fetched", 0),
        )
        return 0, _fresh.get("total_fetched", 0), True

    # ── Fetch complet (mutex held — released in finally) ──────────────────
    try:
        _log.info("Inventory Gemini pour '%s' via tracker '%s'…", username, tracker_name)
        torrents = _fetch_all_user_torrents(tracker_name, username, silent=silent)
        _log.info("Inventory : %d torrents récupérés", len(torrents))
        releases = _extract_file_releases(torrents)
        _log.info("Inventory : %d fichiers extraits (files.name + files.size)", len(releases))

        src   = Path(source_folder)
        added = 0

        _log.info("Inventory : indexation du dossier source '%s'…", src)
        name_index: dict[str, Path] = {}
        try:
            for child in src.iterdir():
                name_index[child.name] = child
        except OSError as exc:
            _log.warning("Inventory : impossible d'indexer '%s' — %s", src, exc)
        _log.info("Inventory : %d entrées indexées localement", len(name_index))

        # ── Construire la liste complète pour la page Inventaire ──────────
        torrents_list = []
        for t in torrents:
            attrs = t.get("attributes") or {}
            tname = attrs.get("name") or ""
            if tname:
                torrents_list.append({
                    "name":         tname,
                    "tracker_id":   str(t["id"]) if t.get("id") else None,
                    "processed_at": attrs.get("created_at") or now_str,
                })

        if not name_index:
            _log.info("Inventory : dossier source vide ou inaccessible — passage au scan normal")
            with _lock:
                _save_state({
                    "last_run_key":    username,
                    "last_run_at":     now_str,
                    "last_checked_at": now_str,
                    "username":        username,
                    "source_folder":   source_folder,
                    "total_fetched":   len(torrents),
                    "added":           0,
                    "releases":        releases,
                    "torrents_list":   torrents_list,
                })
            return 0, len(torrents), False

        from core.db import (
            db_path_in_history,
            db_add_history_entries,
            db_update_tracker_id_if_missing,
        )

        with _lock:
            new_entries: dict = {}
            for t in torrents:
                attrs = t.get("attributes") or {}
                name  = attrs.get("name") or ""
                if not name:
                    continue
                candidate = name_index.get(name)
                if candidate is None:
                    continue
                path_str = str(candidate)

                if db_path_in_history(path_str):
                    if t.get("id"):
                        db_update_tracker_id_if_missing(path_str, str(t["id"]))
                    continue

                new_entries[path_str] = {
                    "name":         name,
                    "type":         attrs.get("category_id", ""),
                    "tag":          "",
                    "processed_at": attrs.get("created_at") or now_str,
                    "status":       "done",
                    "job_id":       None,
                    "source":       "gemini_inventory",
                    "tracker_id":   str(t["id"]) if t.get("id") else None,
                }
                added += 1

            if new_entries:
                db_add_history_entries(new_entries)
                _log.info("Inventory : %d chemins ajoutés à l'historique", added)

            _save_state({
                "last_run_key":    username,
                "last_run_at":     now_str,
                "last_checked_at": now_str,
                "username":        username,
                "source_folder":   source_folder,
                "total_fetched":   len(torrents),
                "added":           added,
                "releases":        releases,
                "torrents_list":   torrents_list,
            })

        return added, len(torrents), False

    finally:
        _fetch_mutex.release()


def get_inventory_list() -> list[dict]:
    """Return the full list of torrents from the last inventory sync.

    Each entry: {name, tracker_id, processed_at}.
    Returns [] if no sync has run yet.
    """
    state = _load_state()
    return state.get("torrents_list") or []


def get_inventory_status() -> dict:
    """Return current inventory state (for API endpoint)."""
    with _bg_status_lock:
        bg = dict(_bg_status)
    state = _load_state()
    return {
        "running":        bg["running"],
        "last_run_at":    state.get("last_run_at"),
        "last_checked_at": state.get("last_checked_at"),
        "total_fetched":  state.get("total_fetched", 0),
        "added":          state.get("added", 0),
        "username":       state.get("username"),
        "source_folder":  state.get("source_folder"),
        "error":          bg["error"],
    }


def trigger_background_inventory(source_folder: str, *, force: bool = False) -> bool:
    """Start inventory sync in a background daemon thread.

    Returns True if the thread was started, False if already running.
    """
    with _bg_status_lock:
        if _bg_status["running"]:
            _log.info("Inventory background : déjà en cours — ignoré")
            return False
        _bg_status["running"] = True
        _bg_status["error"]   = None

    _emit("inventory_started", {"force": force})

    def _bg() -> None:
        try:
            added, total, cached = sync_gemini_inventory(source_folder, force=force, silent=True)
            now = datetime.now().isoformat()
            with _bg_status_lock:
                _bg_status.update({
                    "total":       total,
                    "added":       added,
                    "cached":      cached,
                    "last_run_at": now,
                })
            _emit("inventory_done", {"added": added, "total": total, "cached": cached})
            _log.info(
                "Inventory background terminé : total=%d added=%d cached=%s",
                total, added, cached,
            )
        except Exception as exc:
            _log.exception("Inventory background : erreur inattendue — %s", exc)
            with _bg_status_lock:
                _bg_status["error"] = str(exc)
            _emit("inventory_done", {"error": str(exc)})
        finally:
            with _bg_status_lock:
                _bg_status["running"] = False

    threading.Thread(target=_bg, daemon=True, name="bg-inventory").start()
    return True


def invalidate_inventory_cache() -> None:
    """Force re-run on next sync (called when tracker settings change)."""
    with _lock:
        _api_username_cache["value"] = None
        state = _load_state()
        state.pop("last_run_key",    None)   # legacy key
        state.pop("username",        None)   # reset username → force re-resolve
        state.pop("last_checked_at", None)
        _save_state(state)
    _log.debug("Cache inventory invalidé (last_run_key + last_checked_at effacés)")
