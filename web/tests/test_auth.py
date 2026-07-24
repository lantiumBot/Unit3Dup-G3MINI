"""Tests for core/auth.py.

Covers:
- auth_enabled() return value and TTL cache behaviour
- invalidate_auth_cache() — forces immediate re-read
- _SERVER_EPOCH — non-empty UUID string
- _backoff_window() — exponential growth + 3600 s cap
- is_locked_out() / record_failure() — brute-force guard

Requires Flask (core/auth.py imports flask.session and flask.request).
When Flask is not installed, all tests in this file are skipped by conftest.py.
"""
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Guard: skip the whole module if Flask is unavailable
try:
    import flask  # noqa: F401
except ImportError:
    import pytest
    pytestmark = pytest.mark.skip(reason="Flask not installed")

import pytest
from unittest.mock import patch, MagicMock


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cfg(enabled=False, pw_hash=""):
    return {"auth_enabled": enabled, "auth_password_hash": pw_hash}


@pytest.fixture(autouse=True)
def _reset_auth_state():
    """Reset auth cache and brute-force counters before and after every test."""
    import core.auth as _auth
    _wipe(_auth)
    yield
    _wipe(_auth)


def _wipe(auth_mod):
    with auth_mod._auth_cache_lock:
        auth_mod._auth_cache["value"] = None
        auth_mod._auth_cache["ts"]    = 0.0
    with auth_mod._fail_lock:
        auth_mod._fail_times.clear()
        auth_mod._fail_count.clear()


# ── auth_enabled() ────────────────────────────────────────────────────────────

class TestAuthEnabled:
    def test_false_when_disabled(self):
        from core.auth import auth_enabled
        with patch("core.auth.load_web_config", return_value=_cfg(False)):
            assert auth_enabled() is False

    def test_false_when_no_hash(self):
        """auth_enabled requires BOTH auth_enabled=True AND a non-empty hash."""
        from core.auth import auth_enabled
        with patch("core.auth.load_web_config", return_value=_cfg(True, "")):
            assert auth_enabled() is False

    def test_true_when_configured(self):
        from core.auth import auth_enabled
        with patch("core.auth.load_web_config", return_value=_cfg(True, "pbkdf2:sha256:…")):
            assert auth_enabled() is True

    def test_cache_avoids_second_disk_read(self):
        """Within TTL, load_web_config must be called only once."""
        from core.auth import auth_enabled
        mock_load = MagicMock(return_value=_cfg(True, "hash"))
        with patch("core.auth.load_web_config", mock_load):
            auth_enabled()  # primes cache
            auth_enabled()  # must hit cache
        assert mock_load.call_count == 1

    def test_cache_expires_after_ttl(self):
        """After manual TTL expiry, the next call re-reads config."""
        import core.auth as _auth
        from core.auth import auth_enabled
        mock_load = MagicMock(return_value=_cfg(True, "hash"))
        with patch("core.auth.load_web_config", mock_load):
            auth_enabled()  # fill cache
            with _auth._auth_cache_lock:
                _auth._auth_cache["ts"] = 0.0  # expire
            auth_enabled()  # must re-read
        assert mock_load.call_count == 2

    def test_cache_reflects_new_value_after_expiry(self):
        """After expiry, a changed config value is picked up."""
        import core.auth as _auth
        from core.auth import auth_enabled
        calls = [_cfg(True, "hash"), _cfg(False)]
        mock_load = MagicMock(side_effect=calls)
        with patch("core.auth.load_web_config", mock_load):
            assert auth_enabled() is True
            with _auth._auth_cache_lock:
                _auth._auth_cache["ts"] = 0.0
            assert auth_enabled() is False


# ── invalidate_auth_cache() ───────────────────────────────────────────────────

class TestInvalidateAuthCache:
    def test_forces_re_read(self):
        from core.auth import auth_enabled, invalidate_auth_cache
        mock_load = MagicMock(return_value=_cfg(True, "hash"))
        with patch("core.auth.load_web_config", mock_load):
            auth_enabled()            # prime cache
            invalidate_auth_cache()   # bust cache
            auth_enabled()            # must re-read
        assert mock_load.call_count == 2

    def test_clears_cached_value(self):
        import core.auth as _auth
        from core.auth import auth_enabled, invalidate_auth_cache
        with patch("core.auth.load_web_config", return_value=_cfg(True, "h")):
            auth_enabled()
        with _auth._auth_cache_lock:
            assert _auth._auth_cache["value"] is not None
        invalidate_auth_cache()
        with _auth._auth_cache_lock:
            assert _auth._auth_cache["value"] is None


# ── _SERVER_EPOCH ─────────────────────────────────────────────────────────────

class TestServerEpoch:
    def test_is_non_empty_string(self):
        from core.auth import _SERVER_EPOCH
        assert isinstance(_SERVER_EPOCH, str) and _SERVER_EPOCH

    def test_is_valid_uuid(self):
        import uuid
        from core.auth import _SERVER_EPOCH
        uuid.UUID(_SERVER_EPOCH)  # must not raise


# ── _backoff_window() ─────────────────────────────────────────────────────────

class TestBackoffWindow:
    def _set_count(self, ip, count):
        import core.auth as _auth
        with _auth._fail_lock:
            _auth._fail_count[ip] = count

    def test_base_window_is_60(self):
        from core.auth import _backoff_window
        self._set_count("x", 1)
        assert _backoff_window("x") == 60.0

    def test_exactly_5_still_60(self):
        from core.auth import _backoff_window
        self._set_count("x", 5)
        assert _backoff_window("x") == 60.0

    def test_6_doubles_to_120(self):
        from core.auth import _backoff_window
        self._set_count("x", 6)
        assert _backoff_window("x") == 120.0

    def test_7_doubles_to_240(self):
        from core.auth import _backoff_window
        self._set_count("x", 7)
        assert _backoff_window("x") == 240.0

    def test_grows_monotonically(self):
        from core.auth import _backoff_window
        ip = "1.2.3.4"
        windows = []
        for n in range(5, 15):
            self._set_count(ip, n)
            windows.append(_backoff_window(ip))
        for a, b in zip(windows, windows[1:]):
            assert b >= a

    def test_capped_at_3600(self):
        from core.auth import _backoff_window
        self._set_count("x", 999)
        assert _backoff_window("x") == 3600.0


# ── is_locked_out() / record_failure() ───────────────────────────────────────

class TestBruteForceGuard:
    """These tests mock _get_ip() to avoid needing a Flask request context."""

    @pytest.fixture()
    def _ip(self):
        with patch("core.auth._get_ip", return_value="192.168.1.1"):
            yield "192.168.1.1"

    def test_not_locked_initially(self, _ip):
        from core.auth import is_locked_out
        assert is_locked_out() is False

    def test_not_locked_after_4_failures(self, _ip):
        from core.auth import is_locked_out, record_failure
        for _ in range(4):
            record_failure()
        assert is_locked_out() is False

    def test_locked_after_5_failures(self, _ip):
        from core.auth import is_locked_out, record_failure
        for _ in range(5):
            record_failure()
        assert is_locked_out() is True

    def test_clear_failures_unlocks(self, _ip):
        from core.auth import is_locked_out, record_failure, clear_failures
        for _ in range(5):
            record_failure()
        assert is_locked_out() is True
        clear_failures()
        assert is_locked_out() is False

    def test_old_failures_expire(self, _ip):
        """Failures older than the backoff window are ignored."""
        import core.auth as _auth
        from core.auth import is_locked_out
        past = time.time() - 120  # 2 minutes ago
        with _auth._fail_lock:
            _auth._fail_times["192.168.1.1"] = [past] * 5
            _auth._fail_count["192.168.1.1"] = 5
        # Default window is 60 s → 2-minute-old failures are outside the window
        assert is_locked_out() is False
