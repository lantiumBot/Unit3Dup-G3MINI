"""Tests for core/duplicate.py — cache helpers and apply_duplicate_checks.

After migration C (duplicate_check_cache.json → SQLite):
  - _prune_duplicate_cache() is gone; replaced by db_dup_cache_prune() in db.py
  - Tests for prune logic now exercise the SQLite functions directly
  - apply_duplicate_checks uses db_history_paths_set() instead of load_history()
"""
import sys
import os
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fresh_db(tmp_path):
    """Return a db module pointing to a temporary database."""
    import importlib
    import core.db as db_mod
    db_mod.DB_PATH = tmp_path / "test.db"
    db_mod._db_existed = False
    db_mod._DB_LOCK    = __import__("threading").Lock()
    db_mod.init_db()
    return db_mod


# ── C: SQLite duplicate cache tests ──────────────────────────────────────────

class TestDupCacheSQLite:
    """db_dup_cache_store / db_dup_cache_lookup / db_dup_cache_prune."""

    @pytest.fixture(autouse=True)
    def _tmp_db(self, tmp_path, monkeypatch):
        import core.db as db_mod
        monkeypatch.setattr(db_mod, "DB_PATH",   tmp_path / "test.db")
        monkeypatch.setattr(db_mod, "_db_existed", False)
        db_mod.init_db()

    def test_store_and_lookup_hit(self):
        from core.db import db_dup_cache_store, db_dup_cache_lookup
        db_dup_cache_store("k1", "/a", "tracker", 5.0, True, {"delta_pct": 3.0}, "duplicate")
        result = db_dup_cache_lookup("k1", 5.0, ttl_sec=3600)
        assert result is not None
        is_dup, info, status = result
        assert is_dup is True
        assert info["delta_pct"] == 3.0
        assert status == "duplicate"

    def test_store_no_dup_and_lookup(self):
        from core.db import db_dup_cache_store, db_dup_cache_lookup
        db_dup_cache_store("k2", "/b", "tracker", 0.0, False, None, None)
        result = db_dup_cache_lookup("k2", 0.0, ttl_sec=3600)
        assert result is not None
        is_dup, info, status = result
        assert is_dup is False
        assert info is None
        assert status is None

    def test_cache_miss_unknown_key(self):
        from core.db import db_dup_cache_lookup
        assert db_dup_cache_lookup("unknown", 0.0, ttl_sec=3600) is None

    def test_skip_th_mismatch_returns_miss(self):
        from core.db import db_dup_cache_store, db_dup_cache_lookup
        db_dup_cache_store("k3", "/c", "t", 10.0, True, {}, "duplicate")
        # Different skip_th → cache miss
        assert db_dup_cache_lookup("k3", 5.0, ttl_sec=3600) is None

    def test_ttl_zero_always_miss(self):
        from core.db import db_dup_cache_store, db_dup_cache_lookup
        db_dup_cache_store("k4", "/d", "t", 0.0, False, None, None)
        # ttl=0 means cache disabled
        assert db_dup_cache_lookup("k4", 0.0, ttl_sec=0) is None

    def test_expired_entry_returns_miss(self):
        """Store an entry then look it up with a 1-second TTL; wait 2 s."""
        from core.db import db_dup_cache_store, db_dup_cache_lookup
        db_dup_cache_store("k5", "/e", "t", 0.0, False, None, None)
        # Simulate expiry by patching checked_at to be old
        import sqlite3
        import core.db as db_mod
        conn = sqlite3.connect(str(db_mod.DB_PATH))
        conn.execute(
            "UPDATE dup_cache SET checked_at=? WHERE cache_key=?",
            ("2000-01-01T00:00:00", "k5"),
        )
        conn.commit()
        conn.close()
        assert db_dup_cache_lookup("k5", 0.0, ttl_sec=3600) is None

    def test_prune_removes_stale(self):
        """db_dup_cache_prune deletes entries older than ttl."""
        from core.db import db_dup_cache_store, db_dup_cache_prune
        import sqlite3, core.db as db_mod
        db_dup_cache_store("fresh", "/f", "t", 0.0, False, None, None)
        # Force checked_at to be very old for a second entry
        conn = sqlite3.connect(str(db_mod.DB_PATH))
        conn.execute(
            """INSERT OR REPLACE INTO dup_cache
               (cache_key,path,tracker,checked_at,is_duplicate,info_json,skip_th,status)
               VALUES (?,?,?,?,?,?,?,?)""",
            ("stale", "/g", "t", "2000-01-01T00:00:00", 0, "{}", 0.0, None),
        )
        conn.commit()
        conn.close()
        deleted = db_dup_cache_prune(ttl_sec=3600)
        assert deleted == 1
        # fresh entry should still be there
        from core.db import db_dup_cache_lookup
        assert db_dup_cache_lookup("fresh", 0.0, ttl_sec=3600) is not None

    def test_prune_ttl_zero_returns_zero(self):
        from core.db import db_dup_cache_prune
        assert db_dup_cache_prune(ttl_sec=0) == 0


# ── apply_duplicate_checks guards ─────────────────────────────────────────────

class TestApplyDuplicateChecksGuards:
    """apply_duplicate_checks short-circuits and returns items unchanged when
    MULTI_TRACKER is missing, DUPLICATE_ON=False, or Load() fails."""

    def test_no_tracker_configured(self):
        from core.duplicate import apply_duplicate_checks
        with patch("core.duplicate.default_tracker_name", return_value=None):
            items = [{"path": "/a", "status": "pending"}]
            out, rate_limited, unchecked = apply_duplicate_checks(items)
            assert out == items
            assert not rate_limited
            assert unchecked == []

    def _make_sys_modules_patch(self, mock_prefs):
        """Build a sys.modules patch that provides common.settings.Load and
        unit3dup.config_settings for apply_duplicate_checks' local imports."""
        mock_cs = MagicMock()
        mock_cs.user_preferences = mock_prefs
        mock_common_settings = MagicMock()
        mock_common_settings.Load = MagicMock()
        mock_unit3dup = MagicMock()
        mock_unit3dup.config_settings = mock_cs
        return {
            "common": MagicMock(),
            "common.settings": mock_common_settings,
            "unit3dup": mock_unit3dup,
            "unit3dup.config_settings": mock_cs,
        }, mock_cs

    def test_duplicate_on_false(self):
        from core.duplicate import apply_duplicate_checks
        mock_prefs = MagicMock()
        mock_prefs.DUPLICATE_ON = False
        sys_mods, _ = self._make_sys_modules_patch(mock_prefs)
        with patch("core.duplicate.default_tracker_name", return_value="gemini"), \
             patch.dict("sys.modules", sys_mods):
            items = [{"path": "/a", "status": "pending"}]
            out, rate_limited, _ = apply_duplicate_checks(items)
            assert out == items
            assert not rate_limited

    def test_history_items_not_checked(self):
        """Items already in history should be marked 'history' and skipped."""
        from core.duplicate import apply_duplicate_checks
        mock_prefs = MagicMock()
        mock_prefs.DUPLICATE_ON = True
        sys_mods, _ = self._make_sys_modules_patch(mock_prefs)
        # Patch db_history_paths_set (the function called inside apply_duplicate_checks)
        with patch("core.duplicate.default_tracker_name", return_value="gemini"), \
             patch.dict("sys.modules", sys_mods), \
             patch("core.db.db_history_paths_set", return_value={"/a"}), \
             patch("core.duplicate._duplicate_skip_threshold_pct", return_value=0):
            items = [{"path": "/a", "status": "pending"}]
            out, _, _ = apply_duplicate_checks(items)
            assert out[0]["status"] == "history"
