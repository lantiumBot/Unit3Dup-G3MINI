"""Tests for core/conf.py — atomic write, config helpers, rotate logs."""
import sys
import os
import json
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from pathlib import Path
from unittest.mock import patch


class TestAtomicWriteJson:
    def test_write_creates_file(self, tmp_path):
        from core.conf import _atomic_write_json
        p = tmp_path / "out.json"
        _atomic_write_json(p, {"key": "val"})
        assert p.exists()
        assert json.loads(p.read_text()) == {"key": "val"}

    def test_no_tmp_file_remains(self, tmp_path):
        from core.conf import _atomic_write_json
        p = tmp_path / "out.json"
        _atomic_write_json(p, {})
        assert not (tmp_path / "out.json.tmp").exists()

    def test_overwrites_existing(self, tmp_path):
        from core.conf import _atomic_write_json
        p = tmp_path / "out.json"
        _atomic_write_json(p, {"a": 1})
        _atomic_write_json(p, {"b": 2})
        assert json.loads(p.read_text()) == {"b": 2}


class TestSafeReadJson:
    def test_missing_file_returns_default(self, tmp_path):
        from core.conf import _safe_read_json
        p = tmp_path / "missing.json"
        assert _safe_read_json(p, dict) == {}

    def test_valid_json(self, tmp_path):
        from core.conf import _safe_read_json
        p = tmp_path / "data.json"
        p.write_text('{"x": 42}')
        assert _safe_read_json(p, dict) == {"x": 42}

    def test_corrupt_json_returns_default_and_renames(self, tmp_path):
        from core.conf import _safe_read_json
        p = tmp_path / "bad.json"
        p.write_text("NOT JSON {{{{")
        result = _safe_read_json(p, dict)
        assert result == {}
        # A .bak file should have been created
        bak_files = list(tmp_path.glob("*.bak"))
        assert bak_files


class TestDefaultTrackerName:
    def test_returns_first_entry_lower(self):
        from core.conf import default_tracker_name
        fake_cfg = {"TRACKER_CONFIG": {"MULTI_TRACKER": ["GEMINI", "OTHER"]}}
        with patch("core.conf.load_unit3dbot", return_value=fake_cfg):
            assert default_tracker_name() == "gemini"

    def test_returns_none_when_empty(self):
        from core.conf import default_tracker_name
        with patch("core.conf.load_unit3dbot", return_value={"TRACKER_CONFIG": {"MULTI_TRACKER": []}}):
            assert default_tracker_name() is None

    def test_returns_none_when_missing(self):
        from core.conf import default_tracker_name
        with patch("core.conf.load_unit3dbot", return_value={}):
            assert default_tracker_name() is None


class TestRotateUploadLogs:
    def test_deletes_oldest_when_over_limit(self, tmp_path, monkeypatch):
        from core import conf
        monkeypatch.setattr(conf, "LOGS_UPLOAD_DIR", tmp_path)

        # Create 5 files with different mtimes
        for i in range(5):
            f = tmp_path / f"job{i:03d}.json"
            f.write_text("{}")
            # Stagger mtimes
            os.utime(f, (time.time() + i, time.time() + i))

        conf._rotate_upload_logs(max_files=3)
        remaining = list(tmp_path.glob("*.json"))
        assert len(remaining) == 3
        # The newest 3 files should remain
        names = sorted(f.name for f in remaining)
        assert names == ["job002.json", "job003.json", "job004.json"]

    def test_no_delete_when_under_limit(self, tmp_path, monkeypatch):
        from core import conf
        monkeypatch.setattr(conf, "LOGS_UPLOAD_DIR", tmp_path)

        for i in range(2):
            (tmp_path / f"job{i}.json").write_text("{}")

        conf._rotate_upload_logs(max_files=5)
        assert len(list(tmp_path.glob("*.json"))) == 2

    def test_age_based_purge_removes_old_files(self, tmp_path, monkeypatch):
        """Files older than log_retention_days must be deleted before count purge."""
        from core import conf
        monkeypatch.setattr(conf, "LOGS_UPLOAD_DIR", tmp_path)

        now = time.time()
        old_file = tmp_path / "old.json"
        new_file = tmp_path / "new.json"
        old_file.write_text("{}")
        new_file.write_text("{}")

        # Make old_file 2 days old (retention = 1 day)
        os.utime(old_file, (now - 2 * 86400, now - 2 * 86400))
        os.utime(new_file, (now,              now))

        # Patch load_web_config to return retention = 1 day
        monkeypatch.setattr(
            conf, "load_web_config",
            lambda: {"log_retention_days": 1},
        )

        conf._rotate_upload_logs(max_files=500)
        remaining = {f.name for f in tmp_path.glob("*.json")}
        assert "new.json" in remaining
        assert "old.json" not in remaining

    def test_age_purge_disabled_when_zero(self, tmp_path, monkeypatch):
        """log_retention_days=0 must NOT delete any files regardless of age."""
        from core import conf
        monkeypatch.setattr(conf, "LOGS_UPLOAD_DIR", tmp_path)

        old_file = tmp_path / "ancient.json"
        old_file.write_text("{}")
        os.utime(old_file, (0, 0))  # epoch-old

        monkeypatch.setattr(
            conf, "load_web_config",
            lambda: {"log_retention_days": 0},
        )
        conf._rotate_upload_logs(max_files=500)
        assert old_file.exists()


class TestDeepMerge:
    """_deep_merge — recursive dict merging."""

    def test_simple_override(self):
        from core.conf import _deep_merge
        base     = {"a": 1, "b": 2}
        override = {"b": 99, "c": 3}
        result   = _deep_merge(base, override)
        assert result == {"a": 1, "b": 99, "c": 3}

    def test_base_not_mutated(self):
        from core.conf import _deep_merge
        base = {"a": {"x": 1, "y": 2}}
        _deep_merge(base, {"a": {"x": 99}})
        assert base["a"]["y"] == 2  # original unchanged

    def test_nested_dict_merged(self):
        from core.conf import _deep_merge
        base     = {"rules": {"integrale": {"enabled": True}, "collection": {"enabled": False}}}
        override = {"rules": {"integrale": {"enabled": False}}}
        result   = _deep_merge(base, override)
        # sibling key 'collection' preserved
        assert result["rules"]["collection"]["enabled"] is False
        # overridden key changed
        assert result["rules"]["integrale"]["enabled"] is False

    def test_non_dict_override_wins(self):
        from core.conf import _deep_merge
        base     = {"key": {"nested": 1}}
        override = {"key": 42}          # non-dict replaces dict
        result   = _deep_merge(base, override)
        assert result["key"] == 42

    def test_deeply_nested(self):
        from core.conf import _deep_merge
        base     = {"a": {"b": {"c": 1, "d": 2}}}
        override = {"a": {"b": {"c": 99}}}
        result   = _deep_merge(base, override)
        assert result["a"]["b"]["c"] == 99
        assert result["a"]["b"]["d"] == 2


class TestLoadWebConfig:
    """load_web_config — defaults + disk merge + TTL cache."""

    def test_returns_default_keys_when_no_file(self, tmp_path, monkeypatch):
        from core import conf
        monkeypatch.setattr(conf, "WEB_CONFIG_JSON", tmp_path / "missing.json")
        conf._invalidate_cfg_cache()
        cfg = conf.load_web_config()
        # Core defaults present
        assert "source_folder" in cfg
        assert "rules" in cfg
        assert "webhook_format" in cfg
        assert "log_retention_days" in cfg

    def test_disk_value_overrides_default(self, tmp_path, monkeypatch):
        from core import conf
        cfg_path = tmp_path / "web_config.json"
        cfg_path.write_text('{"source_folder": "/data/movies"}')
        monkeypatch.setattr(conf, "WEB_CONFIG_JSON", cfg_path)
        conf._invalidate_cfg_cache()
        cfg = conf.load_web_config()
        assert cfg["source_folder"] == "/data/movies"
        # Default not overridden by the partial file
        assert "log_retention_days" in cfg

    def test_partial_override_preserves_defaults(self, tmp_path, monkeypatch):
        """A file with only rules.integrale must preserve rules.collection."""
        from core import conf
        cfg_path = tmp_path / "web_config.json"
        cfg_path.write_text('{"rules": {"integrale": {"enabled": false}}}')
        monkeypatch.setattr(conf, "WEB_CONFIG_JSON", cfg_path)
        conf._invalidate_cfg_cache()
        cfg = conf.load_web_config()
        # Override applied
        assert cfg["rules"]["integrale"]["enabled"] is False
        # Sibling key preserved from defaults
        assert "collection" in cfg["rules"]

    def test_cache_returns_same_object(self, tmp_path, monkeypatch):
        """Two calls within the TTL window must return equal dicts without re-reading disk."""
        from core import conf
        monkeypatch.setattr(conf, "WEB_CONFIG_JSON", tmp_path / "absent.json")
        conf._invalidate_cfg_cache()
        first  = conf.load_web_config()
        second = conf.load_web_config()
        assert first == second
