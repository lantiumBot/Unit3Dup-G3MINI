"""Single-user session authentication."""
import threading
import time
import uuid

from flask import session, request
from werkzeug.security import generate_password_hash, check_password_hash

from core.conf import load_web_config

# ── Server epoch — invalidates all sessions on process restart ───────────────
# A new UUID is generated every time this module is imported (= every start).
# login_user() writes it into the Flask session; _auth_guard checks it.
# Any session created before a restart will have a stale epoch → auto-logout.
_SERVER_EPOCH: str = str(uuid.uuid4())

# ── auth_enabled() TTL cache ─────────────────────────────────────────────────
# auth_enabled() is called by _auth_guard, the Jinja2 context processor and
# api_auth_status() on every request — up to 3 disk reads per request.
# A 5-second TTL cache reduces this to at most one read per 5-second window.
_auth_cache_lock  = threading.Lock()
_auth_cache: dict = {"value": None, "ts": 0.0}
_AUTH_CACHE_TTL   = 5.0   # seconds


# ── Brute-force guard with exponential backoff (#19) ─────────────────────────
# Window grows exponentially after 5 failures: 60 s → 120 s → 240 s → … → 3600 s
# State is in-memory; resets on server restart (acceptable for a local dashboard).
_fail_lock:  threading.Lock = threading.Lock()
_fail_times: dict[str, list[float]] = {}
_fail_count: dict[str, int]         = {}   # consecutive failure count per IP


def _get_ip() -> str:
    return request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()


def _backoff_window(ip: str) -> float:
    """Lockout window in seconds — grows exponentially after 5 failures.

    count ≤ 5  →  60 s  (first strike; 5 attempts allowed)
    count  6   → 120 s  (2×)
    count  7   → 240 s  (4×)
    count  8   → 480 s  (8×)
    …          → max 3600 s (1 h)
    """
    count = _fail_count.get(ip, 0)
    if count <= 5:
        return 60.0
    return min(60.0 * (2 ** (count - 5)), 3600.0)


def is_locked_out() -> bool:
    ip     = _get_ip()
    now    = time.time()
    window = _backoff_window(ip)
    with _fail_lock:
        times = [t for t in _fail_times.get(ip, []) if now - t < window]
        _fail_times[ip] = times
        return len(times) >= 5


def record_failure():
    ip = _get_ip()
    with _fail_lock:
        _fail_times.setdefault(ip, []).append(time.time())
        _fail_count[ip] = _fail_count.get(ip, 0) + 1


def clear_failures():
    ip = _get_ip()
    with _fail_lock:
        _fail_times.pop(ip, None)
        _fail_count.pop(ip, None)


# ── Config helpers ────────────────────────────────────────────────────────────
def auth_enabled() -> bool:
    """Return True when password auth is active.

    Result is cached for 5 seconds (_AUTH_CACHE_TTL) so that the three callers
    per request (_auth_guard, context processor, api_auth_status) share a single
    disk read rather than each loading web_config.json independently.

    The cache is invalidated immediately by save_web_config() via
    invalidate_auth_cache() so toggling auth on/off takes effect instantly.
    """
    now = time.time()
    with _auth_cache_lock:
        if _auth_cache["value"] is not None and (now - _auth_cache["ts"]) < _AUTH_CACHE_TTL:
            return _auth_cache["value"]
    cfg = load_web_config()
    result = bool(cfg.get("auth_enabled")) and bool(cfg.get("auth_password_hash"))
    with _auth_cache_lock:
        _auth_cache["value"] = result
        _auth_cache["ts"]    = now
    return result


def invalidate_auth_cache() -> None:
    """Force next auth_enabled() call to re-read from disk (used by auth routes)."""
    with _auth_cache_lock:
        _auth_cache["value"] = None
        _auth_cache["ts"]    = 0.0

def is_logged_in() -> bool:
    return session.get("u3d_auth") is True

def hash_password(plain: str) -> str:
    return generate_password_hash(plain, method="pbkdf2:sha256")

def verify_password(plain: str) -> bool:
    cfg = load_web_config()
    h   = cfg.get("auth_password_hash") or ""
    return bool(h) and check_password_hash(h, plain)
