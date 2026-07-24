"""Tests for core/job.py — queue persistence, restore, Job dict shape.

Avoids PTY / scheduler side effects by testing only the pure-Python helpers.
On Windows, pty/termios are absent; the scheduler thread is started at import
time but never processes jobs in tests (no items enqueued + no PTY).
"""
import sys
import os
import types
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ── Stub Unix-only modules so core.job imports on Windows ────────────────────
if sys.platform == "win32":
    for _name in ("pty", "termios", "tty", "fcntl"):
        if _name not in sys.modules:
            _mod = types.ModuleType(_name)
            if _name == "pty":
                _mod.openpty = lambda: (0, 0)       # type: ignore[attr-defined]
                _mod.STDOUT_FILENO = 1               # type: ignore[attr-defined]
            sys.modules[_name] = _mod

import pytest
from unittest.mock import patch, MagicMock


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def _db_queue(tmp_path, monkeypatch):
    """Redirect the SQLite DB to a temp file so tests don't touch history.db."""
    import core.db as db
    import core.conf as conf
    test_db = str(tmp_path / "test.db")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    # Re-run init_db() with the temp path
    db.init_db.__wrapped__(test_db) if hasattr(db.init_db, "__wrapped__") else None
    # Manually create the DB at the new path
    import sqlite3
    conn = sqlite3.connect(test_db)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS job_queue (
            job_id   TEXT PRIMARY KEY,
            item_json TEXT NOT NULL
        );
    """)
    conn.commit()
    conn.close()
    monkeypatch.setattr("core.db._DB_PATH", tmp_path / "test.db", raising=False)
    yield tmp_path / "test.db"


# ── Queue persistence ─────────────────────────────────────────────────────────

class TestQueueState:
    """Test add/load/remove via the conf.py façade (delegates to db.py)."""

    def test_empty_queue_returns_empty_dict(self, tmp_path, monkeypatch):
        """db_queue_load on an empty DB returns {}."""
        import sqlite3
        db_path = tmp_path / "q.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE job_queue (job_id TEXT PRIMARY KEY, item_json TEXT NOT NULL)"
        )
        conn.commit()
        conn.close()

        import core.db as db
        monkeypatch.setattr(db, "DB_PATH", db_path)
        assert db.db_queue_load() == {}

    def test_add_then_load(self, tmp_path, monkeypatch):
        import sqlite3
        db_path = tmp_path / "q.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE job_queue (job_id TEXT PRIMARY KEY, item_json TEXT NOT NULL)"
        )
        conn.commit()
        conn.close()

        import core.db as db
        monkeypatch.setattr(db, "DB_PATH", db_path)

        item = {"path": "/tmp/test", "name": "Test", "type": "file", "status": "pending"}
        db.db_queue_add("job-001", item)
        result = db.db_queue_load()

        assert "job-001" in result
        assert result["job-001"]["path"] == "/tmp/test"

    def test_remove_after_add(self, tmp_path, monkeypatch):
        import sqlite3
        db_path = tmp_path / "q.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE job_queue (job_id TEXT PRIMARY KEY, item_json TEXT NOT NULL)"
        )
        conn.commit()
        conn.close()

        import core.db as db
        monkeypatch.setattr(db, "DB_PATH", db_path)

        item = {"path": "/tmp/x", "name": "X", "type": "file", "status": "pending"}
        db.db_queue_add("job-002", item)
        assert "job-002" in db.db_queue_load()
        db.db_queue_remove("job-002")
        assert "job-002" not in db.db_queue_load()

    def test_remove_nonexistent_is_safe(self, tmp_path, monkeypatch):
        import sqlite3
        db_path = tmp_path / "q.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE job_queue (job_id TEXT PRIMARY KEY, item_json TEXT NOT NULL)"
        )
        conn.commit()
        conn.close()

        import core.db as db
        monkeypatch.setattr(db, "DB_PATH", db_path)
        db.db_queue_remove("nonexistent-id")  # must not raise

    def test_add_idempotent(self, tmp_path, monkeypatch):
        """Adding the same job_id twice updates the item (INSERT OR REPLACE)."""
        import sqlite3
        db_path = tmp_path / "q.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE job_queue (job_id TEXT PRIMARY KEY, item_json TEXT NOT NULL)"
        )
        conn.commit()
        conn.close()

        import core.db as db
        monkeypatch.setattr(db, "DB_PATH", db_path)

        db.db_queue_add("job-003", {"name": "first"})
        db.db_queue_add("job-003", {"name": "second"})
        result = db.db_queue_load()
        assert len(result) == 1
        assert result["job-003"]["name"] == "second"


# ── Job.to_dict() ─────────────────────────────────────────────────────────────

class TestJobToDict:
    """Verify the dict contract exposed by Job.to_dict()."""

    @pytest.fixture()
    def _job(self):
        try:
            from core.job import Job
        except ImportError:
            pytest.skip("core.job not importable on this platform")
        item = {
            "id":     "test-id-1",
            "path":   "/data/Movie (2023)",
            "name":   "Movie (2023)",
            "type":   "file",
            "tag":    "Bluray",
            "status": "pending",
        }
        with patch("core.job._restore_queue"), \
             patch("core.job.threading.Thread"):
            return Job(item, "unit3dup", False)

    def test_to_dict_has_id(self, _job):
        d = _job.to_dict()
        assert "id" in d

    def test_to_dict_has_status(self, _job):
        d = _job.to_dict()
        assert d["status"] in ("pending", "running", "done", "error", "cancelled")

    def test_to_dict_has_retry_count(self, _job):
        d = _job.to_dict()
        assert "retry_count" in d
        assert d["retry_count"] == 0

    def test_to_dict_has_item(self, _job):
        d = _job.to_dict()
        assert d["name"] == "Movie (2023)"
        assert d["type"] == "file"


# ── _restore_queue integration ────────────────────────────────────────────────

class TestRestoreQueue:
    def test_empty_queue_does_nothing(self, monkeypatch):
        """_restore_queue with an empty queue must not crash."""
        try:
            from core.job import _restore_queue
        except ImportError:
            pytest.skip("core.job not importable on this platform")

        monkeypatch.setattr("core.conf.load_queue_state", lambda: {})
        monkeypatch.setattr("core.conf.load_web_config", lambda: {"confirm_mode": False})

        import shared
        original_jobs = dict(shared._jobs)
        _restore_queue()
        # Queue was empty — _jobs should be unchanged
        assert shared._jobs == original_jobs
