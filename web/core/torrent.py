"""qBittorrent client: torrent list with state labels."""
import re
import threading
import time
from urllib.parse import urlparse

from core.conf import load_unit3dbot, _human

# 15-second in-process cache — avoids a qBit round-trip on every /api/history page load
_QBIT_CACHE: dict = {"ts": 0.0, "data": None, "err": None}
_QBIT_TTL   = 15.0
_QBIT_LOCK  = threading.Lock()

_QBIT_STATE = {
    "uploading":          ("Partage",         "success"),
    "stalledUP":          ("Partage",         "success"),
    "forcedUP":           ("Partage forcé",   "success"),
    "queuedUP":           ("File (seed)",     "secondary"),
    "checkingUP":         ("Vérification",    "warning"),
    "checkingDL":         ("Vérification",    "warning"),
    "checkingResumeData": ("Vérification",    "warning"),
    "downloading":        ("Téléchargement",  "info"),
    "stalledDL":          ("En attente",      "secondary"),
    "queuedDL":           ("File (DL)",       "secondary"),
    "pausedUP":           ("Pause ↑",         "secondary"),
    "pausedDL":           ("Pause ↓",         "secondary"),
    "stoppedUP":          ("Stoppé ↑",        "secondary"),
    "stoppedDL":          ("Stoppé ↓",        "secondary"),
    "error":              ("Erreur",          "danger"),
    "missingFiles":       ("Fichiers manq.",  "danger"),
    "moving":             ("Déplacement",     "info"),
}


def get_qbit_torrents(*, force: bool = False) -> tuple[list, str | None]:
    """Return the cached torrent list, refreshing at most every _QBIT_TTL seconds.

    Pass ``force=True`` from the Stats page (live view) to bypass the cache.
    """
    with _QBIT_LOCK:
        now = time.time()
        if not force and _QBIT_CACHE["data"] is not None and (now - _QBIT_CACHE["ts"]) < _QBIT_TTL:
            return _QBIT_CACHE["data"], _QBIT_CACHE["err"]

        result, err = _fetch_qbit_torrents()
        _QBIT_CACHE["data"] = result
        _QBIT_CACHE["err"]  = err
        _QBIT_CACHE["ts"]   = now
        return result, err


# ── Gemini seeding IDs (by tracker comment) ───────────────────────────────────

_SEEDING_CACHE: dict = {"ts": 0.0, "data": None, "err": None, "url": ""}
_SEEDING_TTL   = 60.0
_SEEDING_LOCK  = threading.Lock()
_COMMENT_ID_RE = re.compile(r"/torrents/(\d+)")


def get_qbit_gemini_seeding_ids(gemini_url: str) -> tuple[set[str], str | None]:
    """Return the set of Gemini tracker_ids currently in qBittorrent.

    Detection method: for each qBit torrent whose announce URL contains the
    Gemini domain, read the torrent comment (UNIT3D sets it to the tracker page
    URL, e.g. https://gemini-tracker.org/torrents/5312) and extract the ID.
    Result is cached for 60 seconds.
    """
    with _SEEDING_LOCK:
        now = time.time()
        if (
            _SEEDING_CACHE["data"] is not None
            and _SEEDING_CACHE["url"] == gemini_url
            and (now - _SEEDING_CACHE["ts"]) < _SEEDING_TTL
        ):
            return _SEEDING_CACHE["data"], _SEEDING_CACHE["err"]

    result, err = _fetch_gemini_seeding_ids(gemini_url)

    with _SEEDING_LOCK:
        _SEEDING_CACHE["ts"]   = time.time()
        _SEEDING_CACHE["data"] = result
        _SEEDING_CACHE["err"]  = err
        _SEEDING_CACHE["url"]  = gemini_url
    return result, err


def _fetch_gemini_seeding_ids(gemini_url: str) -> tuple[set[str], str | None]:
    if not gemini_url:
        return set(), "Gemini_URL non configuré"

    u3d = load_unit3dbot()
    cfg = u3d.get("TORRENT_CLIENT_CONFIG", {})
    if (cfg.get("TORRENT_CLIENT") or "").lower() != "qbittorrent":
        return set(), "Client non configuré sur qBittorrent"

    gemini_host = urlparse(gemini_url).netloc.lower()
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

        # Step 1 — collect hashes of torrents that have Gemini in any tracker URL.
        # The active `tracker` field is often empty for paused/stalled/stopped torrents,
        # so we fall back to torrents_trackers() which lists ALL configured trackers.
        gemini_hashes: list[str] = []
        for t in cl.torrents_info():
            if gemini_host in (t.get("tracker") or "").lower():
                gemini_hashes.append(t.hash)
                continue
            try:
                all_trackers = cl.torrents_trackers(torrent_hash=t.hash) or []
                if any(gemini_host in (tr.get("url") or "").lower() for tr in all_trackers):
                    gemini_hashes.append(t.hash)
            except Exception:
                pass

        # Step 2 — read comment of each Gemini torrent and extract tracker_id
        ids: set[str] = set()
        for h in gemini_hashes:
            try:
                props   = cl.torrents_properties(torrent_hash=h)
                comment = props.get("comment") or ""
                m       = _COMMENT_ID_RE.search(comment)
                if m:
                    ids.add(m.group(1))
            except Exception:
                pass

        cl.auth_log_out()
        return ids, None

    except Exception as exc:
        return set(), str(exc)


def _fetch_qbit_torrents() -> tuple[list, str | None]:
    u3d = load_unit3dbot()
    cfg = u3d.get("TORRENT_CLIENT_CONFIG", {})
    if (cfg.get("TORRENT_CLIENT") or "").lower() != "qbittorrent":
        return [], "Client non configuré sur qBittorrent"
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
        out = []
        for t in cl.torrents_info():
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
        return [], str(exc)
