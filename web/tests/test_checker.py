"""Tests for core/checker.py — unit checks and TTL cache.

Only the pure-Python helpers that don't require live network/process access
are tested here.  Network-dependent checks (_check_tracker, _check_torrent,
_check_tmdb) and the binary runner (_check_binary) are validated only for
the "not configured" path that short-circuits before any I/O.
"""
import sys
import os
import time
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path


# ── _ms ──────────────────────────────────────────────────────────────────────

class TestMs:
    """_ms — millisecond elapsed-time helper."""

    def test_returns_non_negative_int(self):
        from core.checker import _ms
        t0 = time.time()
        result = _ms(t0)
        assert isinstance(result, int)
        assert result >= 0

    def test_roughly_one_second(self):
        from core.checker import _ms
        t0 = time.time() - 1.0
        result = _ms(t0)
        assert 900 <= result <= 1200  # 1 s ± 300 ms tolerance


# ── _check_source ────────────────────────────────────────────────────────────

class TestCheckSource:
    """_check_source — validates source_folder from web config."""

    def test_no_source_folder_returns_warn(self):
        from core.checker import _check_source
        result = _check_source({"source_folder": ""})
        assert result["id"]     == "source"
        assert result["status"] == "warn"
        assert result["ms"]     is None

    def test_existing_folder_returns_ok(self, tmp_path):
        from core.checker import _check_source
        result = _check_source({"source_folder": str(tmp_path)})
        assert result["id"]     == "source"
        assert result["status"] == "ok"
        assert isinstance(result["ms"], int)

    def test_missing_folder_returns_error(self):
        from core.checker import _check_source
        result = _check_source({"source_folder": "/no/such/folder/at/all"})
        assert result["id"]     == "source"
        assert result["status"] == "error"

    def test_result_contains_detail(self, tmp_path):
        from core.checker import _check_source
        result = _check_source({"source_folder": str(tmp_path)})
        assert "detail" in result
        assert str(tmp_path) in result["detail"]


# ── _check_config ────────────────────────────────────────────────────────────

class TestCheckConfig:
    """_check_config — checks that Unit3Dbot.json exists."""

    def test_existing_config_returns_ok(self, tmp_path):
        from core import checker
        fake_cfg = tmp_path / "Unit3Dbot.json"
        fake_cfg.write_text("{}")
        with patch.object(checker, "UNIT3DBOT_JSON", fake_cfg):
            result = checker._check_config()
        assert result["id"]     == "config"
        assert result["status"] == "ok"

    def test_missing_config_returns_error(self, tmp_path):
        from core import checker
        missing = tmp_path / "NoFile.json"
        with patch.object(checker, "UNIT3DBOT_JSON", missing):
            result = checker._check_config()
        assert result["id"]     == "config"
        assert result["status"] == "error"

    def test_contains_detail_with_path(self, tmp_path):
        from core import checker
        fake_cfg = tmp_path / "Unit3Dbot.json"
        fake_cfg.write_text("{}")
        with patch.object(checker, "UNIT3DBOT_JSON", fake_cfg):
            result = checker._check_config()
        assert fake_cfg.name in result["detail"]


# ── _check_torrent — no config branch ────────────────────────────────────────

class TestCheckTorrentNoConfig:
    """_check_torrent returns warn when no host is configured."""

    def test_no_host_returns_warn(self):
        from core.checker import _check_torrent
        u3d = {"TORRENT_CLIENT_CONFIG": {"TORRENT_CLIENT": "qbittorrent", "QBIT_HOST": ""}}
        result = _check_torrent(u3d)
        assert result["status"] == "warn"

    def test_missing_torrent_config_returns_warn(self):
        from core.checker import _check_torrent
        result = _check_torrent({})
        # No host configured → warn
        assert result["status"] == "warn"


# ── _check_tracker — no config branch ────────────────────────────────────────

class TestCheckTrackerNoConfig:
    """_check_tracker returns warn when no URL is configured."""

    def test_no_url_returns_warn(self):
        from core.checker import _check_tracker
        result = _check_tracker({"Gemini_URL": ""})
        assert result["status"] == "warn"

    def test_empty_config_returns_warn(self):
        from core.checker import _check_tracker
        result = _check_tracker({})
        assert result["status"] == "warn"


# ── _check_tmdb — no config branch ───────────────────────────────────────────

class TestCheckTmdbNoConfig:
    """_check_tmdb returns warn when no credentials are configured."""

    def test_no_token_no_key_returns_warn(self):
        from core.checker import _check_tmdb
        result = _check_tmdb({"TMDB_ACCESS_TOKEN": "", "TMDB_APIKEY": ""})
        assert result["status"] == "warn"

    def test_missing_keys_returns_warn(self):
        from core.checker import _check_tmdb
        result = _check_tmdb({})
        assert result["status"] == "warn"


# ── get_status_checks — TTL cache ────────────────────────────────────────────

class TestGetStatusChecksCache:
    """get_status_checks — 30-second TTL cache prevents double-fetching."""

    @pytest.fixture(autouse=True)
    def _reset_cache(self):
        """Clear the module-level cache before each test."""
        from core import checker
        checker._STATUS_CACHE["ts"]     = 0.0
        checker._STATUS_CACHE["result"] = None
        yield
        checker._STATUS_CACHE["ts"]     = 0.0
        checker._STATUS_CACHE["result"] = None

    def _mock_checks(self):
        """Return a fake results list."""
        return [{"id": "source", "status": "ok", "detail": "", "ms": 1}]

    def test_second_call_within_ttl_uses_cache(self):
        from core import checker

        call_count = [0]
        original   = checker.get_status_checks

        def _patched_checks(*, force=False):
            # We patch the internal _fetch step indirectly by patching load_unit3dbot
            # and load_web_config so no real I/O happens.
            pass

        # Directly set the cache to a known value and check it's reused
        checker._STATUS_CACHE["result"] = self._mock_checks()
        checker._STATUS_CACHE["ts"]     = time.time()  # fresh

        # Second call should return cached value without computing
        with patch("core.checker.load_unit3dbot", return_value={}) as mock_u3d, \
             patch("core.checker.load_web_config", return_value={}):
            result = checker.get_status_checks()
            # load_unit3dbot should NOT have been called (cache hit)
            mock_u3d.assert_not_called()

        assert result == self._mock_checks()

    def test_force_bypasses_cache(self):
        from core import checker

        # Pre-populate cache
        checker._STATUS_CACHE["result"] = self._mock_checks()
        checker._STATUS_CACHE["ts"]     = time.time()

        with patch("core.checker.load_unit3dbot", return_value={}) as mock_u3d, \
             patch("core.checker.load_web_config", return_value={}):
            checker.get_status_checks(force=True)
            # force=True → must re-read even if cache is fresh
            mock_u3d.assert_called_once()

    def test_expired_cache_triggers_refresh(self):
        from core import checker

        # Pre-populate cache but make it stale (ts = 0 → always expired)
        checker._STATUS_CACHE["result"] = self._mock_checks()
        checker._STATUS_CACHE["ts"]     = 0.0

        with patch("core.checker.load_unit3dbot", return_value={}) as mock_u3d, \
             patch("core.checker.load_web_config", return_value={}):
            checker.get_status_checks()
            mock_u3d.assert_called_once()
