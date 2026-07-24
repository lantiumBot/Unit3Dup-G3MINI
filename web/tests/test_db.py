"""Tests for core/db.py — SQLite history and transcript operations."""
import sys
import os
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import patch
from pathlib import Path


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    """Each test gets its own isolated database.

    _db_existed is patched to False so init_db() treats it as a fresh DB.
    That's the normal scenario for a new install or test isolation.
    """
    db_path = tmp_path / "history.db"
    monkeypatch.setattr("core.db.DB_PATH", db_path)
    monkeypatch.setattr("core.db._db_existed", False)
    # Also patch the JSON migration source paths so no real files are read
    monkeypatch.setattr("core.db.HISTORY_JSON", tmp_path / "history.json")
    monkeypatch.setattr("core.db.HISTORY_TRANSCRIPTS_JSON", tmp_path / "history_transcripts.json")
    import core.db as db
    db.init_db()
    return db


class TestHistoryCRUD:
    def test_empty_history(self, fresh_db):
        assert fresh_db.db_load_history() == {}

    def test_upsert_and_load(self, fresh_db):
        proto = {
            "type": "file", "tag": "GROUP",
            "processed_at": "2024-01-01T00:00:00",
            "status": "done", "job_id": "j1",
            "source": None, "tracker_id": None,
        }
        fresh_db.db_upsert_history_paths(["/media/movie.mkv"], proto)
        hist = fresh_db.db_load_history()
        assert "/media/movie.mkv" in hist
        assert hist["/media/movie.mkv"]["status"] == "done"

    def test_delete_path(self, fresh_db):
        proto = {
            "type": "file", "tag": "GRP",
            "processed_at": "2024-01-01T00:00:00",
            "status": "done", "job_id": "jx",
            "source": None, "tracker_id": None,
        }
        fresh_db.db_upsert_history_paths(["/media/x.mkv"], proto)
        job_id = fresh_db.db_delete_history_path("/media/x.mkv")
        assert job_id == "jx"
        assert "/media/x.mkv" not in fresh_db.db_load_history()

    def test_save_history_replaces_all(self, fresh_db):
        fresh_db.db_save_history({
            "/a": {"name": "A", "type": "file", "tag": "", "processed_at": "",
                   "status": "done", "job_id": None},
        })
        fresh_db.db_save_history({})
        assert fresh_db.db_load_history() == {}

    def test_path_in_history(self, fresh_db):
        proto = {
            "type": "file", "tag": "", "processed_at": "",
            "status": "done", "job_id": None,
            "source": None, "tracker_id": None,
        }
        fresh_db.db_upsert_history_paths(["/foo/bar.mkv"], proto)
        assert fresh_db.db_path_in_history("/foo/bar.mkv")
        assert not fresh_db.db_path_in_history("/not/there.mkv")

    def test_history_query_pagination(self, fresh_db):
        proto_base = {
            "type": "file", "tag": "GRP", "processed_at": "2024-01-01T00:00:00",
            "status": "done", "job_id": None, "source": None, "tracker_id": None,
        }
        paths = [f"/media/movie{i}.mkv" for i in range(10)]
        fresh_db.db_upsert_history_paths(paths, proto_base)

        rows, total = fresh_db.db_history_query(page=1, limit=3)
        assert total == 10
        assert len(rows) == 3

        rows2, _ = fresh_db.db_history_query(page=2, limit=3)
        assert len(rows2) == 3
        # No overlap between pages
        ids1 = {r["path"] for r in rows}
        ids2 = {r["path"] for r in rows2}
        assert ids1.isdisjoint(ids2)

    def test_history_query_filter_type(self, fresh_db):
        for path, t in [("/a.mkv", "file"), ("/b", "season")]:
            proto = {
                "type": t, "tag": "", "processed_at": "",
                "status": "done", "job_id": None, "source": None, "tracker_id": None,
            }
            fresh_db.db_upsert_history_paths([path], proto)

        rows, total = fresh_db.db_history_query(ftype="season")
        assert total == 1
        assert rows[0]["path"] == "/b"

    def test_history_query_search(self, fresh_db):
        proto = {
            "type": "file", "tag": "", "processed_at": "",
            "status": "done", "job_id": None, "source": None, "tracker_id": None,
        }
        fresh_db.db_upsert_history_paths(["/media/Breaking.Bad.mkv"], proto)
        fresh_db.db_upsert_history_paths(["/media/Narcos.mkv"], proto)
        rows, total = fresh_db.db_history_query(search="breaking")
        assert total == 1
        assert "Breaking" in rows[0]["name"]


class TestAddHistoryEntries:
    """db_add_history_entries — INSERT OR IGNORE, never overwrites."""

    def test_add_new_entries(self, fresh_db):
        entries = {
            "/media/movie.mkv": {
                "name": "movie", "type": "file", "tag": "GRP",
                "processed_at": "2024-01-01", "status": "done",
                "job_id": None, "source": "gemini_inventory", "tracker_id": "42",
            }
        }
        fresh_db.db_add_history_entries(entries)
        assert fresh_db.db_path_in_history("/media/movie.mkv")

    def test_does_not_overwrite_existing(self, fresh_db):
        """INSERT OR IGNORE: an existing path must not be modified."""
        proto = {
            "type": "file", "tag": "OLD", "processed_at": "2024-01-01",
            "status": "done", "job_id": "j1", "source": None, "tracker_id": None,
        }
        fresh_db.db_upsert_history_paths(["/media/x.mkv"], proto)

        # Try to add the same path with different data — should be ignored
        entries = {
            "/media/x.mkv": {
                "name": "x", "type": "season", "tag": "NEW",
                "processed_at": "2025-01-01", "status": "error",
                "job_id": None, "source": "inventory", "tracker_id": "99",
            }
        }
        fresh_db.db_add_history_entries(entries)
        hist = fresh_db.db_load_history()
        # Original data must be preserved
        assert hist["/media/x.mkv"]["status"] == "done"
        assert hist["/media/x.mkv"]["tag"] == "OLD"

    def test_empty_dict_is_noop(self, fresh_db):
        fresh_db.db_add_history_entries({})
        assert fresh_db.db_load_history() == {}


class TestUpdateTrackerIdIfMissing:
    """db_update_tracker_id_if_missing — updates only when NULL."""

    def _insert(self, db, path, tracker_id=None):
        proto = {
            "type": "file", "tag": "", "processed_at": "",
            "status": "done", "job_id": None, "source": None,
            "tracker_id": tracker_id,
        }
        db.db_upsert_history_paths([path], proto)

    def test_sets_when_null(self, fresh_db):
        self._insert(fresh_db, "/a.mkv", tracker_id=None)
        fresh_db.db_update_tracker_id_if_missing("/a.mkv", "123")
        hist = fresh_db.db_load_history()
        assert hist["/a.mkv"].get("tracker_id") == "123"

    def test_does_not_overwrite_existing(self, fresh_db):
        self._insert(fresh_db, "/b.mkv", tracker_id="existing-42")
        fresh_db.db_update_tracker_id_if_missing("/b.mkv", "new-99")
        hist = fresh_db.db_load_history()
        assert hist["/b.mkv"].get("tracker_id") == "existing-42"

    def test_noop_for_missing_path(self, fresh_db):
        """Should not raise for a path that doesn't exist."""
        fresh_db.db_update_tracker_id_if_missing("/no/such/path.mkv", "42")


class TestMigrationGuard:
    """Migration must run only when the DB was freshly created (_db_existed=False)."""

    def test_migration_runs_on_fresh_db(self, tmp_path, monkeypatch):
        import json
        import core.db as db

        db_path = tmp_path / "history.db"
        hist_json = tmp_path / "history.json"
        hist_json.write_text(json.dumps({
            "/migrated/path.mkv": {
                "name": "path", "type": "file", "tag": "",
                "processed_at": "2024-01-01", "status": "done",
                "job_id": None,
            }
        }))

        monkeypatch.setattr("core.db.DB_PATH",           db_path)
        monkeypatch.setattr("core.db._db_existed",       False)   # fresh DB
        monkeypatch.setattr("core.db.HISTORY_JSON",      hist_json)
        monkeypatch.setattr("core.db.HISTORY_TRANSCRIPTS_JSON", tmp_path / "ht.json")

        db.init_db()
        assert db.db_path_in_history("/migrated/path.mkv")

    def test_migration_skipped_when_db_existed(self, tmp_path, monkeypatch):
        import json
        import core.db as db

        db_path = tmp_path / "history.db"
        hist_json = tmp_path / "history.json"
        hist_json.write_text(json.dumps({
            "/old/legacy.mkv": {
                "name": "legacy", "type": "file", "tag": "",
                "processed_at": "2024-01-01", "status": "done",
                "job_id": None,
            }
        }))

        monkeypatch.setattr("core.db.DB_PATH",           db_path)
        monkeypatch.setattr("core.db._db_existed",       True)    # DB existed → skip
        monkeypatch.setattr("core.db.HISTORY_JSON",      hist_json)
        monkeypatch.setattr("core.db.HISTORY_TRANSCRIPTS_JSON", tmp_path / "ht.json")

        db.init_db()
        # JSON data must NOT have been imported
        assert not db.db_path_in_history("/old/legacy.mkv")

    def test_migration_does_not_overwrite_existing_rows(self, tmp_path, monkeypatch):
        """Even on a fresh DB, INSERT OR IGNORE must not wipe existing rows."""
        import json
        import core.db as db

        db_path = tmp_path / "history2.db"
        hist_json = tmp_path / "h2.json"
        hist_json.write_text(json.dumps({
            "/common/path.mkv": {
                "name": "orig", "type": "file", "tag": "ORIG",
                "processed_at": "2024-01-01", "status": "done",
                "job_id": "j-orig",
            }
        }))

        monkeypatch.setattr("core.db.DB_PATH",           db_path)
        monkeypatch.setattr("core.db._db_existed",       False)
        monkeypatch.setattr("core.db.HISTORY_JSON",      hist_json)
        monkeypatch.setattr("core.db.HISTORY_TRANSCRIPTS_JSON", tmp_path / "ht2.json")

        db.init_db()

        # Pre-insert different data for the same path (simulating a row added before migration)
        db.db_upsert_history_paths(
            ["/common/path.mkv"],
            {"type": "season", "tag": "NEW", "processed_at": "2025-01-01",
             "status": "error", "job_id": "j-new", "source": None, "tracker_id": None},
        )

        # Run migration again (simulates double-import scenario)
        conn = db._connect(write=True)
        db._migrate_json(conn)
        conn.close()

        # The pre-existing row must be untouched (INSERT OR IGNORE)
        hist = db.db_load_history()
        assert hist["/common/path.mkv"]["tag"] == "NEW"
        assert hist["/common/path.mkv"]["status"] == "error"


class TestTranscriptCRUD:
    def test_save_and_get(self, fresh_db):
        entry = {
            "path": "/x.mkv", "name": "x", "type": "file", "tag": "",
            "status": "done", "started_at": None, "ended_at": None,
            "processed_at": "2024-01-01", "item": {}, "result": {},
            "transcript": "hello",
        }
        fresh_db.db_save_transcript("job-1", entry)
        out = fresh_db.db_get_transcript("job-1")
        assert out is not None
        assert out["transcript"] == "hello"

    def test_get_missing_returns_none(self, fresh_db):
        assert fresh_db.db_get_transcript("no-such-id") is None

    def test_delete_transcript(self, fresh_db):
        entry = {
            "path": "/y.mkv", "name": "y", "type": "file", "tag": "",
            "status": "done", "started_at": None, "ended_at": None,
            "processed_at": "2024-01-01", "item": {}, "result": {}, "transcript": "",
        }
        fresh_db.db_save_transcript("job-del", entry)
        fresh_db.db_delete_transcript("job-del")
        assert fresh_db.db_get_transcript("job-del") is None

    def test_clear_transcripts(self, fresh_db):
        entry = {
            "path": "/z.mkv", "name": "z", "type": "file", "tag": "",
            "status": "done", "started_at": None, "ended_at": None,
            "processed_at": "2024-01-01", "item": {}, "result": {}, "transcript": "",
        }
        fresh_db.db_save_transcript("j1", entry)
        fresh_db.db_save_transcript("j2", entry)
        fresh_db.db_clear_transcripts()
        assert fresh_db.db_load_transcripts() == {}

    def test_transcript_exists(self, fresh_db):
        assert not fresh_db.db_transcript_exists("nobody")
        entry = {
            "path": "/w.mkv", "name": "w", "type": "file", "tag": "",
            "status": "done", "started_at": None, "ended_at": None,
            "processed_at": "2024-01-01", "item": {}, "result": {}, "transcript": "",
        }
        fresh_db.db_save_transcript("j-exist", entry)
        assert fresh_db.db_transcript_exists("j-exist")


# ── Scan cache ────────────────────────────────────────────────────────────────

class TestScanCache:
    """db_scan_cache_get / db_scan_cache_set / db_scan_cache_clear."""

    def test_miss_on_unknown_folder(self, fresh_db):
        assert fresh_db.db_scan_cache_get("/no/such/folder") is None

    def test_set_then_get(self, fresh_db):
        items = [{"id": "abc", "name": "Movie", "type": "file", "status": "pending"}]
        fresh_db.db_scan_cache_set("/media/films", items)
        result = fresh_db.db_scan_cache_get("/media/films")
        assert result is not None
        assert len(result) == 1
        assert result[0]["name"] == "Movie"

    def test_overwrite_existing(self, fresh_db):
        fresh_db.db_scan_cache_set("/media/films", [{"id": "old"}])
        fresh_db.db_scan_cache_set("/media/films", [{"id": "new1"}, {"id": "new2"}])
        result = fresh_db.db_scan_cache_get("/media/films")
        assert result is not None
        assert len(result) == 2

    def test_clear_removes_entry(self, fresh_db):
        fresh_db.db_scan_cache_set("/media/films", [{"id": "x"}])
        fresh_db.db_scan_cache_clear("/media/films")
        assert fresh_db.db_scan_cache_get("/media/films") is None

    def test_clear_nonexistent_is_safe(self, fresh_db):
        fresh_db.db_scan_cache_clear("/no/such/path")  # must not raise

    def test_empty_folder_key_ignored(self, fresh_db):
        fresh_db.db_scan_cache_set("", [{"id": "x"}])  # blank key → no-op
        assert fresh_db.db_scan_cache_get("") is None

    def test_ttl_expiry_returns_none(self, fresh_db, monkeypatch):
        """A cached entry whose saved_at is older than TTL should be ignored."""
        import sqlite3
        import core.db as db_mod
        fresh_db.db_scan_cache_set("/media/old", [{"id": "stale"}])
        # Force saved_at to be 48 h ago (TTL = 24 h)
        conn = sqlite3.connect(str(db_mod.DB_PATH))
        conn.execute(
            "UPDATE scan_cache SET saved_at=? WHERE folder_key=?",
            ("2000-01-01T00:00:00", "/media/old"),
        )
        conn.commit()
        conn.close()
        assert fresh_db.db_scan_cache_get("/media/old") is None

    def test_whitespace_stripped_from_key(self, fresh_db):
        fresh_db.db_scan_cache_set("  /media/films  ", [{"id": "ws"}])
        # Lookup with the stripped key should work
        assert fresh_db.db_scan_cache_get("/media/films") is not None


# ── Chart data ────────────────────────────────────────────────────────────────

class TestChartData:
    """db_history_chart_data — daily aggregation."""

    def test_returns_correct_structure(self, fresh_db):
        result = fresh_db.db_history_chart_data(7)
        assert "labels" in result
        assert "done" in result
        assert "error" in result

    def test_labels_length_matches_days(self, fresh_db):
        for days in (7, 30, 90):
            result = fresh_db.db_history_chart_data(days)
            assert len(result["labels"]) == days
            assert len(result["done"])   == days
            assert len(result["error"])  == days

    def test_empty_history_all_zeros(self, fresh_db):
        result = fresh_db.db_history_chart_data(7)
        assert all(v == 0 for v in result["done"])
        assert all(v == 0 for v in result["error"])

    def test_counts_match_inserted_data(self, fresh_db):
        from datetime import date
        today = date.today().isoformat()
        # Insert 2 done + 1 error for today
        proto_done  = {"type": "file", "tag": "", "processed_at": f"{today}T10:00:00",
                       "status": "done", "job_id": None, "source": None, "tracker_id": None}
        proto_error = {"type": "file", "tag": "", "processed_at": f"{today}T11:00:00",
                       "status": "error", "job_id": None, "source": None, "tracker_id": None}
        fresh_db.db_upsert_history_paths(["/a.mkv", "/b.mkv"], proto_done)
        fresh_db.db_upsert_history_paths(["/c.mkv"],            proto_error)

        result = fresh_db.db_history_chart_data(7)
        # today is the last label
        assert result["labels"][-1] == today
        assert result["done"][-1]   == 2
        assert result["error"][-1]  == 1


# ── History query — date filter ───────────────────────────────────────────────

class TestHistoryQueryDateFilter:
    """db_history_query with date_from / date_to parameters."""

    @pytest.fixture(autouse=True)
    def _populate(self, fresh_db):
        """Insert 3 entries on distinct dates."""
        self.db = fresh_db
        for path, date_str in [
            ("/a.mkv", "2024-01-10"),
            ("/b.mkv", "2024-06-15"),
            ("/c.mkv", "2024-12-31"),
        ]:
            proto = {
                "type": "file", "tag": "", "status": "done",
                "processed_at": f"{date_str}T00:00:00",
                "job_id": None, "source": None, "tracker_id": None,
            }
            fresh_db.db_upsert_history_paths([path], proto)

    def test_date_from_filters_older(self):
        rows, total = self.db.db_history_query(date_from="2024-06-01")
        assert total == 2
        paths = {r["path"] for r in rows}
        assert "/b.mkv" in paths
        assert "/c.mkv" in paths
        assert "/a.mkv" not in paths

    def test_date_to_filters_newer(self):
        rows, total = self.db.db_history_query(date_to="2024-06-30")
        assert total == 2
        paths = {r["path"] for r in rows}
        assert "/a.mkv" in paths
        assert "/b.mkv" in paths
        assert "/c.mkv" not in paths

    def test_date_range_exact(self):
        rows, total = self.db.db_history_query(
            date_from="2024-06-01", date_to="2024-06-30"
        )
        assert total == 1
        assert rows[0]["path"] == "/b.mkv"

    def test_no_date_filter_returns_all(self):
        _, total = self.db.db_history_query()
        assert total == 3


# ── History paths helpers ─────────────────────────────────────────────────────

class TestHistoryPathsSet:
    """db_history_paths_set — returns set of all paths."""

    def test_empty_db_returns_empty_set(self, fresh_db):
        assert fresh_db.db_history_paths_set() == set()

    def test_all_paths_present(self, fresh_db):
        proto = {"type": "file", "tag": "", "processed_at": "", "status": "done",
                 "job_id": None, "source": None, "tracker_id": None}
        fresh_db.db_upsert_history_paths(["/a.mkv", "/b.mkv"], proto)
        paths = fresh_db.db_history_paths_set()
        assert {"/a.mkv", "/b.mkv"}.issubset(paths)

    def test_returns_set_type(self, fresh_db):
        assert isinstance(fresh_db.db_history_paths_set(), set)


class TestHistoryPathsBySource:
    """db_history_paths_by_source — filtered by source column."""

    def test_filters_by_source(self, fresh_db):
        inv_proto = {"type": "file", "tag": "", "processed_at": "", "status": "done",
                     "job_id": None, "source": "gemini_inventory", "tracker_id": None}
        job_proto = {"type": "file", "tag": "", "processed_at": "", "status": "done",
                     "job_id": "j1", "source": None, "tracker_id": None}
        fresh_db.db_upsert_history_paths(["/inv.mkv"], inv_proto)
        fresh_db.db_upsert_history_paths(["/job.mkv"], job_proto)

        inv_paths = fresh_db.db_history_paths_by_source("gemini_inventory")
        assert "/inv.mkv" in inv_paths
        assert "/job.mkv" not in inv_paths

    def test_unknown_source_returns_empty(self, fresh_db):
        assert fresh_db.db_history_paths_by_source("nonexistent_source") == set()


# ── History stats ─────────────────────────────────────────────────────────────

class TestHistoryStats:
    """db_history_stats — SQL aggregation without loading all rows."""

    def test_empty_returns_zeros(self, fresh_db):
        stats = fresh_db.db_history_stats()
        assert stats["total"] == 0
        assert stats["done"]  == 0
        assert stats["error"] == 0

    def test_counts_correct(self, fresh_db):
        # status='error' takes priority in the CASE statement over type='integrale',
        # so use status='done' for the integrale row to land in its own bucket.
        season_proto   = {"type": "season",    "tag": "", "processed_at": "",
                          "status": "done",  "job_id": None, "source": None, "tracker_id": None}
        integrale_proto = {"type": "integrale", "tag": "", "processed_at": "",
                           "status": "done",  "job_id": None, "source": None, "tracker_id": None}
        error_proto    = {"type": "file",       "tag": "", "processed_at": "",
                          "status": "error", "job_id": None, "source": None, "tracker_id": None}
        fresh_db.db_upsert_history_paths(["/s1", "/s2"], season_proto)
        fresh_db.db_upsert_history_paths(["/i1"],         integrale_proto)
        fresh_db.db_upsert_history_paths(["/e1"],         error_proto)

        stats = fresh_db.db_history_stats()
        assert stats["total"] == 4
        assert stats["done"]  == 3
        assert stats["error"] == 1
        assert stats["counts"]["season"]      == 2
        assert stats["counts"]["integrale"]   == 1
        assert stats["counts"]["error_upload"] == 1
