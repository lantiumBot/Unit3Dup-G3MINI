"""Service connectivity checks with 30-second TTL cache."""
import shutil
import socket as _socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from core.conf import (
    UNIT3DBOT_JSON, VENV_BIN,
    load_unit3dbot, load_web_config,
)

# 30-second result cache — protected by a lock to prevent double-fetch under concurrent requests
_STATUS_CACHE: dict = {"ts": 0.0, "result": None}
_STATUS_TTL   = 30.0
_STATUS_LOCK  = threading.Lock()


def _ms(t0: float) -> int:
    return round((time.time() - t0) * 1000)


def _check_source(web_cfg: dict) -> dict:
    t0  = time.time()
    src = web_cfg.get("source_folder", "")
    if not src:
        return {"id": "source", "status": "warn", "detail": "", "ms": None}
    p  = Path(src)
    ok = p.exists() and p.is_dir() and p.stat()
    return {"id": "source", "status": "ok" if ok else "error", "detail": src, "ms": _ms(t0)}


def _check_binary() -> dict:
    import os
    t0 = time.time()
    if VENV_BIN.exists() and os.access(str(VENV_BIN), os.X_OK):
        bin_path = str(VENV_BIN)
    else:
        bin_path = shutil.which("unit3dup") or ""
    if not bin_path:
        return {"id": "unit3dup", "status": "error", "detail": "unit3dup introuvable", "ms": _ms(t0)}
    try:
        r   = subprocess.run([bin_path, "--version"], capture_output=True, text=True, timeout=5)
        out = (r.stdout or r.stderr or "").strip().split("\n")[0][:80]
        return {"id": "unit3dup", "status": "ok", "detail": out or bin_path, "ms": _ms(t0)}
    except Exception:
        return {"id": "unit3dup", "status": "ok", "detail": bin_path, "ms": _ms(t0)}


def _check_config() -> dict:
    t0 = time.time()
    ok = UNIT3DBOT_JSON.exists()
    return {
        "id": "config",
        "status": "ok" if ok else "error",
        "detail": str(UNIT3DBOT_JSON),
        "ms": _ms(t0),
    }


def _check_torrent(u3d: dict) -> dict:
    t0     = time.time()
    tc_cfg = u3d.get("TORRENT_CLIENT_CONFIG", {})
    client = (tc_cfg.get("TORRENT_CLIENT") or "qbittorrent").lower()

    if client == "qbittorrent":
        host   = tc_cfg.get("QBIT_HOST", "")
        port   = tc_cfg.get("QBIT_PORT", 8080)
        user   = tc_cfg.get("QBIT_USER", "admin")
        passwd = tc_cfg.get("QBIT_PASS") or ""
        if not host:
            return {"id": "torrent", "client": client, "status": "warn", "detail": "", "ms": None}
        try:
            import qbittorrentapi
            cl = qbittorrentapi.Client(
                host=host, port=port, username=user, password=passwd,
                VERIFY_WEBUI_CERTIFICATE=False, REQUESTS_ARGS={"timeout": 5},
            )
            cl.auth_log_in()
            ver = cl.app_version()
            cl.auth_log_out()
            return {"id": "torrent", "client": client,
                    "status": "ok", "detail": f"{host}:{port} — v{ver}", "ms": _ms(t0)}
        except Exception as exc:
            return {"id": "torrent", "client": client,
                    "status": "error", "detail": str(exc)[:120], "ms": _ms(t0)}

    if client == "transmission":
        host = tc_cfg.get("TRASM_HOST", "localhost")
        port = int(tc_cfg.get("TRASM_PORT", 9091))
    elif client == "rtorrent":
        host = tc_cfg.get("RTORR_HOST", "localhost")
        port = int(tc_cfg.get("RTORR_PORT", 9091))
    else:
        return {"id": "torrent", "client": client, "status": "warn", "detail": "", "ms": None}

    try:
        s = _socket.create_connection((host, port), timeout=5)
        s.close()
        return {"id": "torrent", "client": client,
                "status": "ok", "detail": f"{host}:{port}", "ms": _ms(t0)}
    except Exception as exc:
        return {"id": "torrent", "client": client,
                "status": "error", "detail": str(exc)[:120], "ms": _ms(t0)}


def _check_tracker(tr_cfg: dict) -> dict:
    t0  = time.time()
    url = (tr_cfg.get("Gemini_URL") or "").rstrip("/")
    key = tr_cfg.get("Gemini_APIKEY") or ""
    if not url:
        return {"id": "tracker", "status": "warn", "detail": "", "ms": None}
    # Probe the smallest possible endpoint — api_token goes in the Authorization
    # header, never in the URL query string (would appear in server logs).
    endpoint = f"{url}/api/torrents?page[number]=1&page[size]=1"
    headers  = {"Accept": "application/json", "User-Agent": "G3MINI-Status/1.0"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        req = urllib.request.Request(endpoint, headers=headers)
        with urllib.request.urlopen(req, timeout=8) as r:
            ok = r.status < 500
        return {"id": "tracker", "status": "ok" if ok else "error",
                "detail": url, "ms": _ms(t0)}
    except urllib.error.HTTPError as exc:
        if exc.code < 500:
            return {"id": "tracker", "status": "ok",
                    "detail": f"{url} (HTTP {exc.code})", "ms": _ms(t0)}
        return {"id": "tracker", "status": "error",
                "detail": f"HTTP {exc.code}", "ms": _ms(t0)}
    except Exception as exc:
        return {"id": "tracker", "status": "error",
                "detail": str(exc)[:120], "ms": _ms(t0)}


def _check_tmdb(tr_cfg: dict) -> dict:
    t0     = time.time()
    token  = tr_cfg.get("TMDB_ACCESS_TOKEN") or ""
    apikey = tr_cfg.get("TMDB_APIKEY") or ""
    if not token and not apikey:
        return {"id": "tmdb", "status": "warn", "detail": "", "ms": None}
    # Prefer Bearer token (v4) — keeps the key out of the URL and server logs.
    # Fall back to v3 api_key query param only when no token is configured.
    if token and token != "no_key":
        url     = "https://api.themoviedb.org/3/configuration"
        headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    else:
        url     = f"https://api.themoviedb.org/3/configuration?api_key={apikey}"
        headers = {"Accept": "application/json"}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=8) as r:
            ok = r.status == 200
        return {"id": "tmdb", "status": "ok" if ok else "error",
                "detail": "api.themoviedb.org", "ms": _ms(t0)}
    except urllib.error.HTTPError as exc:
        detail = "Clé API invalide" if exc.code == 401 else f"HTTP {exc.code}"
        return {"id": "tmdb", "status": "error", "detail": detail, "ms": _ms(t0)}
    except Exception as exc:
        return {"id": "tmdb", "status": "error", "detail": str(exc)[:120], "ms": _ms(t0)}


_ORDER = ["source", "unit3dup", "config", "torrent", "tracker", "tmdb"]


def get_status_checks(*, force: bool = False) -> list[dict]:
    """Run all checks (parallel) and return sorted results, with 30s TTL cache.

    The lock prevents two simultaneous requests from both launching a full set
    of 6 network checks when the cache is cold or expired.
    """
    with _STATUS_LOCK:
        now = time.time()
        if not force and _STATUS_CACHE["result"] and (now - _STATUS_CACHE["ts"]) < _STATUS_TTL:
            return _STATUS_CACHE["result"]

        u3d     = load_unit3dbot()
        web_cfg = load_web_config()
        tr_cfg  = u3d.get("TRACKER_CONFIG", {})

        fns = [
            lambda: _check_source(web_cfg),
            _check_binary,
            _check_config,
            lambda: _check_torrent(u3d),
            lambda: _check_tracker(tr_cfg),
            lambda: _check_tmdb(tr_cfg),
        ]

        checks: list[dict] = []
        with ThreadPoolExecutor(max_workers=6) as ex:
            futures = {ex.submit(fn): fn for fn in fns}
            for f in as_completed(futures):
                try:
                    checks.append(f.result())
                except Exception as exc:
                    checks.append({"id": "unknown", "status": "error",
                                    "detail": str(exc), "ms": 0})

        checks.sort(key=lambda c: _ORDER.index(c["id"]) if c["id"] in _ORDER else 99)
        _STATUS_CACHE["ts"]     = now
        _STATUS_CACHE["result"] = checks
        return checks
