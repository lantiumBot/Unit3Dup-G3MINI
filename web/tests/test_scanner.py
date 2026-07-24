"""Tests for core/scanner.py — _scan_dir, scan_source, episode lookup."""
import sys
import os
import uuid
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import patch


# ── Helpers that don't need the filesystem ────────────────────────────────
from core.scanner import (
    _is_integrale, _is_season, _tag, _tag_ok,
    pack_is_season1, _season_number_from_dirname,
)


class TestNameParsing:
    def test_integrale_detected(self):
        assert _is_integrale("Breaking.Bad.INTEGRALE.BluRay-GROUP")

    def test_integrale_case_insensitive(self):
        assert _is_integrale("Game.of.Thrones.integrale.HDTV-GROUP")

    def test_not_integrale(self):
        assert not _is_integrale("Breaking.Bad.S01.BluRay-GROUP")

    def test_season_detected(self):
        assert _is_season("Breaking.Bad.S03.BluRay-GROUP")

    def test_season_detected_sxxexx(self):
        # SxxExx folders (single episode): _SEASON_RE fails because \bS23\b word-boundary
        # is blocked by the following 'E'; _SE_RE must catch them instead.
        assert _is_season("NCIS.S23E11.MULTi.1080p.WEB.H264-AMB3R")
        assert _is_season("Show.S01E01.HDTV-GROUP")
        assert _is_season("Series.S02E05.1080p.BluRay-TEAM")

    def test_not_season(self):
        assert not _is_season("Breaking.Bad.INTEGRALE.BluRay-GROUP")

    def test_tag_extraction(self):
        assert _tag("Breaking.Bad.S01.BluRay-GROUP") == "GROUP"

    def test_tag_no_dash(self):
        assert _tag("NoTagHere") == ""

    def test_tag_ok_no_filter(self):
        assert _tag_ok("ANYTHING", [])

    def test_tag_ok_match(self):
        assert _tag_ok("group", ["GROUP", "TEAM"])

    def test_tag_ok_no_match(self):
        assert not _tag_ok("UNKNOWN", ["GROUP", "TEAM"])

    def test_pack_is_season1(self):
        assert pack_is_season1("Breaking.Bad.S01-GROUP")

    def test_pack_is_not_season1(self):
        assert not pack_is_season1("Breaking.Bad.S02-GROUP")

    def test_season_number_from_dirname(self):
        assert _season_number_from_dirname("Saison 2") == 2
        assert _season_number_from_dirname("S03") == 3
        assert _season_number_from_dirname("no-match") is None


# ── Filesystem tests ──────────────────────────────────────────────────────
from core.scanner import scan_source, _scan_dir


def _write_file(path: Path, size: int = 1024):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


class TestScanDir:
    def test_empty_dir_returns_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            items, skipped = _scan_dir(p, [], {}, {}, include_history=False)
            assert items == []
            assert skipped == []

    def test_video_file_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            _write_file(p / "Movie.2023.BluRay-GROUP.mkv")
            items, _ = _scan_dir(p, [], {}, {}, include_history=False)
            assert len(items) == 1
            assert items[0]["type"] == "file"
            assert items[0]["status"] == "pending"

    def test_empty_video_file_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / "empty.mkv").write_bytes(b"")
            items, _ = _scan_dir(p, [], {}, {}, include_history=False)
            assert items == []

    def test_season_dir_detected(self):
        rules = {"complete_or_season": {"enabled": True, "require_valid_tag": False}}
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            season_dir = p / "Show.S01-GROUP"
            _write_file(season_dir / "ep01.mkv")
            items, _ = _scan_dir(p, [], rules, {}, include_history=False)
            assert any(i["type"] == "season" for i in items)

    def test_integrale_dir_detected(self):
        rules = {"integrale": {"enabled": True, "upload_seasons": False}}
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            int_dir = p / "Show.INTEGRALE-GROUP"
            _write_file(int_dir / "S01" / "ep01.mkv")
            items, _ = _scan_dir(p, [], rules, {}, include_history=False)
            assert any(i["type"] == "integrale" for i in items)

    def test_history_item_excluded_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            video = p / "Old.Movie.BluRay-GROUP.mkv"
            _write_file(video)
            hist = {str(video): {"name": "Old.Movie", "type": "file", "tag": "GROUP",
                                  "processed_at": "", "status": "done", "job_id": None}}
            items, _ = _scan_dir(p, [], {}, hist, include_history=False)
            assert items == []

    def test_history_item_included_when_requested(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            video = p / "Old.Movie.BluRay-GROUP.mkv"
            _write_file(video)
            hist = {str(video): {"name": "Old.Movie", "type": "file", "tag": "GROUP",
                                  "processed_at": "", "status": "done", "job_id": None}}
            items, _ = _scan_dir(p, [], {}, hist, include_history=True)
            assert len(items) == 1
            assert items[0]["status"] == "history"

    def test_tag_filter_marks_invalid_as_pending_no_tag(self):
        """Items with invalid/missing tag stay 'pending' (selectable) but tag_valid=False."""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            _write_file(p / "Movie.2023.BluRay-BADGROUP.mkv")
            items, _ = _scan_dir(p, ["ALLOWED"], {}, {}, include_history=False)
            # Still selectable — user decides whether to upload
            assert items[0]["status"] == "pending"
            assert not items[0]["tag_valid"]

    def test_permission_error_captured_in_skipped(self):
        """scan_dir tolerates PermissionError and records the path."""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            bad = p / "restricted"
            bad.mkdir()
            with patch("core.scanner._safe_iterdir", return_value=([], False)):
                items, skipped = _scan_dir(p, [], {}, {}, include_history=False)
                assert skipped  # at least the top-level failed path

    def test_collection_detected(self):
        """A dir with 2+ video files is tagged as type='collection'."""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            col_dir = p / "Best.Of.2023-GROUP"
            col_dir.mkdir()
            _write_file(col_dir / "movie1.mkv")
            _write_file(col_dir / "movie2.mkv")
            items, _ = _scan_dir(p, [], {}, {}, include_history=False)
            assert len(items) == 1
            assert items[0]["type"] == "collection"
            assert items[0]["status"] == "pending"
            assert len(items[0]["files"]) == 2

    def test_collection_single_video_is_file(self):
        """A dir with exactly 1 video file stays type='file'."""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            d = p / "Movie.2023-GROUP"
            d.mkdir()
            _write_file(d / "movie.mkv")
            items, _ = _scan_dir(p, [], {}, {}, include_history=False)
            assert len(items) == 1
            assert items[0]["type"] == "file"

    def test_collection_tag_filter_marks_pending_no_tag(self):
        """Collection with invalid tag stays 'pending' (selectable) with tag_valid=False."""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            col_dir = p / "Collection.2023-BADTAG"
            col_dir.mkdir()
            _write_file(col_dir / "a.mkv")
            _write_file(col_dir / "b.mkv")
            items, _ = _scan_dir(p, ["GOODTAG"], {}, {}, include_history=False)
            assert items[0]["type"] == "collection"
            assert items[0]["status"] == "pending"   # selectable but tag_valid=False
            assert not items[0]["tag_valid"]


class TestScanSource:
    def test_nonexistent_source_returns_error(self):
        items, err, skipped = scan_source("/no/such/path", [], {})
        assert err is not None
        assert items == []

    def test_recursive_expands_unknown(self):
        """An 'unknown' dir (no season/integrale/video) is expanded in recursive mode."""
        rules = {"complete_or_season": {"enabled": True, "require_valid_tag": False}}
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            # source/Category/Show.S01 — Category is unknown at depth 0
            category = p / "Films"
            season   = category / "Show.S01-GROUP"
            _write_file(season / "ep.mkv")
            items, err, _ = scan_source(str(p), [], rules, recursive=True)
            assert err is None
            types = {i["type"] for i in items}
            assert "season" in types
            # The category dir itself should have been replaced by its contents
            names = {i["name"] for i in items}
            assert "Films" not in names


# ── Helper functions ──────────────────────────────────────────────────────────

from core.scanner import (
    _item_size_gb, _parse_media_info, _filename_is_s01e01,
    _has_video_files, _list_video_files, _is_extra_file, _real_movie_files,
    _is_collection_by_rule, find_s01e01_upload_file, episode_upload_for_item,
    _safe_iterdir,
)


class TestItemSizeGb:
    """_item_size_gb — size calculation for files and directories."""

    def test_file_size(self, tmp_path):
        f = tmp_path / "movie.mkv"
        f.write_bytes(b"x" * (1024 ** 3))  # exactly 1 GB
        assert _item_size_gb(f, True) == pytest.approx(1.0, abs=0.01)

    def test_dir_size_is_sum_of_files(self, tmp_path):
        (tmp_path / "a.mkv").write_bytes(b"x" * (512 * 1024 * 1024))
        (tmp_path / "b.mkv").write_bytes(b"x" * (512 * 1024 * 1024))
        size = _item_size_gb(tmp_path, False)
        assert size == pytest.approx(1.0, abs=0.01)

    def test_nonexistent_returns_zero(self, tmp_path):
        assert _item_size_gb(tmp_path / "ghost.mkv", True) == 0.0

    def test_empty_file_returns_zero(self, tmp_path):
        f = tmp_path / "empty.mkv"
        f.write_bytes(b"")
        assert _item_size_gb(f, True) == 0.0


class TestParseMediaInfo:
    """_parse_media_info — extracts resolution / source / codec from name."""

    def test_returns_dict_with_required_keys(self):
        result = _parse_media_info("Movie.2023.BluRay.1080p.x264-GROUP")
        assert "resolution" in result
        assert "source_type" in result
        assert "encoding" in result

    def test_all_values_are_strings(self):
        result = _parse_media_info("Movie.2023.BluRay.1080p.x264-GROUP")
        assert all(isinstance(v, str) for v in result.values())

    def test_unknown_name_returns_empty_strings(self):
        result = _parse_media_info("SomethingTotallyUnknown")
        # All values must be strings (empty is fine)
        assert isinstance(result["resolution"], str)

    def test_graceful_on_import_error(self, monkeypatch):
        """If guessit is unavailable the function must return empty strings, not raise."""
        import builtins
        real_import = builtins.__import__
        def mock_import(name, *a, **kw):
            if name == "guessit":
                raise ImportError("guessit not installed")
            return real_import(name, *a, **kw)
        monkeypatch.setattr(builtins, "__import__", mock_import)
        result = _parse_media_info("Movie.2023.BluRay.1080p.x264-GROUP")
        assert result == {"resolution": "", "source_type": "", "encoding": ""}


class TestFilenameIsS01E01:
    """_filename_is_s01e01 — detects pilot/first-episode pattern."""

    def test_detects_s01e01(self):
        from core.scanner import _filename_is_s01e01
        assert _filename_is_s01e01("Show.S01E01.HDTV")

    def test_case_insensitive(self):
        from core.scanner import _filename_is_s01e01
        assert _filename_is_s01e01("show.s01e01.hdtv")

    def test_rejects_s01e02(self):
        from core.scanner import _filename_is_s01e01
        assert not _filename_is_s01e01("Show.S01E02.HDTV")

    def test_rejects_s02e01(self):
        from core.scanner import _filename_is_s01e01
        assert not _filename_is_s01e01("Show.S02E01.HDTV")


class TestVideoFileHelpers:
    """_has_video_files / _list_video_files."""

    def test_has_video_files_true(self, tmp_path):
        f = tmp_path / "movie.mkv"
        f.write_bytes(b"x" * 1024)
        children = list(tmp_path.iterdir())
        assert _has_video_files(children)

    def test_has_video_files_false_on_empty_file(self, tmp_path):
        f = tmp_path / "empty.mkv"
        f.write_bytes(b"")
        children = list(tmp_path.iterdir())
        assert not _has_video_files(children)

    def test_has_video_files_false_no_video(self, tmp_path):
        (tmp_path / "readme.txt").write_text("hi")
        children = list(tmp_path.iterdir())
        assert not _has_video_files(children)

    def test_list_video_files_sorted(self, tmp_path):
        for name in ("c.mkv", "a.mkv", "b.mkv"):
            (tmp_path / name).write_bytes(b"x" * 100)
        children = list(tmp_path.iterdir())
        result = _list_video_files(children)
        names = [Path(p).name for p in result]
        assert names == sorted(names)

    def test_list_video_files_excludes_empty(self, tmp_path):
        (tmp_path / "movie.mkv").write_bytes(b"x" * 100)
        (tmp_path / "empty.mkv").write_bytes(b"")
        children = list(tmp_path.iterdir())
        result = _list_video_files(children)
        assert all("empty" not in p for p in result)


class TestExtraFilters:
    """_is_extra_file / _real_movie_files."""

    def test_sample_is_extra(self):
        assert _is_extra_file("/media/Movie/sample.mkv")

    def test_trailer_is_extra(self):
        assert _is_extra_file("/media/Movie/trailer.mkv")

    def test_featurette_is_extra(self):
        assert _is_extra_file("/media/Movie/featurette.mkv")

    def test_regular_movie_not_extra(self):
        assert not _is_extra_file("/media/Movie.2023.BluRay-GROUP.mkv")

    def test_real_movie_files_removes_extras(self, tmp_path):
        main    = str(tmp_path / "Movie.2023.BluRay.mkv")
        sample  = str(tmp_path / "sample.mkv")
        trailer = str(tmp_path / "trailer.mkv")
        result  = _real_movie_files([main, sample, trailer])
        assert result == [main]

    def test_real_movie_files_all_extras_edge_case(self, tmp_path):
        """If filtering leaves an empty list, return the original (fail-safe)."""
        only_sample = [str(tmp_path / "sample.mkv")]
        result = _real_movie_files(only_sample)
        assert result == only_sample  # original returned unchanged


class TestIsCollectionByRule:
    """_is_collection_by_rule — three-tier priority logic."""

    def test_collection_tags_match(self):
        assert _is_collection_by_rule("Collection", True, {"collection_tags": ["Collection"]})

    def test_collection_tags_no_match(self):
        assert not _is_collection_by_rule("FHD", True, {"collection_tags": ["Collection"]})

    def test_collection_tags_case_insensitive(self):
        assert _is_collection_by_rule("collection", True, {"collection_tags": ["COLLECTION"]})

    def test_require_valid_tag_true_and_valid(self):
        assert _is_collection_by_rule("GROUP", True, {"require_valid_tag": True})

    def test_require_valid_tag_true_and_invalid(self):
        assert not _is_collection_by_rule("GROUP", False, {"require_valid_tag": True})

    def test_default_always_true(self):
        # no collection_tags, require_valid_tag=False (or absent)
        assert _is_collection_by_rule("ANY", False, {})


class TestFindS01E01:
    """find_s01e01_upload_file — locates S01E01 file in a directory."""

    def test_finds_s01e01_at_root(self, tmp_path):
        f = tmp_path / "Show.S01E01.HDTV.mkv"
        f.write_bytes(b"x" * 1024)
        result = find_s01e01_upload_file(str(tmp_path))
        assert result is not None
        assert "S01E01" in result

    def test_finds_s01e01_nested(self, tmp_path):
        nested = tmp_path / "Season 01"
        nested.mkdir()
        f = nested / "Show.S01E01.HDTV.mkv"
        f.write_bytes(b"x" * 1024)
        result = find_s01e01_upload_file(str(tmp_path))
        assert result is not None

    def test_returns_none_when_absent(self, tmp_path):
        (tmp_path / "Show.S01E02.HDTV.mkv").write_bytes(b"x" * 1024)
        assert find_s01e01_upload_file(str(tmp_path)) is None

    def test_returns_none_for_nonexistent_dir(self, tmp_path):
        assert find_s01e01_upload_file(str(tmp_path / "no_such")) is None

    def test_search_sibling_s01_finds_episode(self, tmp_path):
        """search_sibling_s01=True should scan a sibling directory named S01."""
        integrale = tmp_path / "Show.INTEGRALE-GROUP"
        integrale.mkdir()
        sibling_s01 = tmp_path / "Show.S01-GROUP"
        sibling_s01.mkdir()
        ep = sibling_s01 / "Show.S01E01.HDTV.mkv"
        ep.write_bytes(b"x" * 1024)
        result = find_s01e01_upload_file(str(integrale), search_sibling_s01=True)
        assert result is not None


class TestEpisodeUploadForItem:
    """episode_upload_for_item — delegates to find_s01e01_upload_file."""

    def test_integrale_item(self, tmp_path):
        f = tmp_path / "Show.S01E01.HDTV.mkv"
        f.write_bytes(b"x" * 1024)
        item = {"path": str(tmp_path), "name": "Show.INTEGRALE-GROUP", "type": "integrale"}
        # May return None if no S01E01 found — just must not raise
        result = episode_upload_for_item(item)
        assert result is not None  # S01E01 file is present

    def test_season1_item(self, tmp_path):
        f = tmp_path / "Show.S01E01.HDTV.mkv"
        f.write_bytes(b"x" * 1024)
        item = {"path": str(tmp_path), "name": "Show.S01-GROUP", "type": "season"}
        result = episode_upload_for_item(item)
        assert result is not None

    def test_season2_returns_none(self, tmp_path):
        f = tmp_path / "Show.S02E01.HDTV.mkv"
        f.write_bytes(b"x" * 1024)
        item = {"path": str(tmp_path), "name": "Show.S02-GROUP", "type": "season"}
        # S02 is not S01 → should not look for S01E01 → None
        assert episode_upload_for_item(item) is None

    def test_file_type_returns_none(self, tmp_path):
        item = {"path": str(tmp_path), "name": "Movie.2023.mkv", "type": "file"}
        assert episode_upload_for_item(item) is None


class TestScanDirItemFields:
    """Verify that all items produced by _scan_dir carry the expected fields."""

    def test_video_file_has_size_gb(self, tmp_path):
        _write_file(tmp_path / "Movie.2023.BluRay-GROUP.mkv")
        items, _ = _scan_dir(tmp_path, [], {}, set(), include_history=False)
        assert len(items) == 1
        assert "size_gb" in items[0]
        assert isinstance(items[0]["size_gb"], float)

    def test_video_file_has_media_info_fields(self, tmp_path):
        _write_file(tmp_path / "Movie.2023.1080p.BluRay.x264-GROUP.mkv")
        items, _ = _scan_dir(tmp_path, [], {}, set(), include_history=False)
        assert "resolution"  in items[0]
        assert "source_type" in items[0]
        assert "encoding"    in items[0]

    def test_season_dir_has_size_gb(self, tmp_path):
        rules = {"complete_or_season": {"enabled": True, "require_valid_tag": False}}
        season = tmp_path / "Show.S01-GROUP"
        _write_file(season / "ep01.mkv")
        items, _ = _scan_dir(tmp_path, [], rules, set(), include_history=False)
        season_items = [i for i in items if i["type"] == "season"]
        assert season_items
        assert "size_gb" in season_items[0]

    def test_collection_item_has_size_gb(self, tmp_path):
        col = tmp_path / "Best.Of.2023-GROUP"
        col.mkdir()
        _write_file(col / "movie1.mkv")
        _write_file(col / "movie2.mkv")
        items, _ = _scan_dir(tmp_path, [], {}, set(), include_history=False)
        col_items = [i for i in items if i["type"] == "collection"]
        assert col_items
        assert "size_gb" in col_items[0]


class TestScanDirEdgeCases:
    """Edge cases not covered by the base TestScanDir suite."""

    def test_dir_in_history_excluded_by_default(self, tmp_path):
        season = tmp_path / "Show.S01-GROUP"
        _write_file(season / "ep.mkv")
        hist = {str(season)}  # proper set[str]
        items, _ = _scan_dir(tmp_path, [], {}, hist, include_history=False)
        assert all(i["path"] != str(season) for i in items)

    def test_dir_in_history_included_when_requested(self, tmp_path):
        season = tmp_path / "Show.S01-GROUP"
        _write_file(season / "ep.mkv")
        hist = {str(season)}
        items, _ = _scan_dir(tmp_path, [], {}, hist, include_history=True)
        hist_items = [i for i in items if i["path"] == str(season)]
        assert hist_items
        assert hist_items[0]["status"] == "history"

    def test_integrale_upload_seasons_true(self, tmp_path):
        """With upload_seasons=True the seasons list must be populated."""
        rules = {"integrale": {"enabled": True, "upload_seasons": True}}
        intg = tmp_path / "Show.INTEGRALE-GROUP"
        s1 = intg / "S01"
        _write_file(s1 / "ep.mkv")
        items, _ = _scan_dir(tmp_path, [], rules, set(), include_history=False)
        intg_items = [i for i in items if i["type"] == "integrale"]
        assert intg_items
        assert len(intg_items[0]["seasons"]) >= 1

    def test_no_video_dir_is_skip(self, tmp_path):
        """A directory containing no video files must be type='unknown', status='skip'."""
        empty_dir = tmp_path / "EmptyDir-GROUP"
        empty_dir.mkdir()
        (empty_dir / "readme.txt").write_text("hi")
        items, _ = _scan_dir(tmp_path, [], {}, set(), include_history=False)
        skip_items = [i for i in items if i["name"] == "EmptyDir-GROUP"]
        assert skip_items
        assert skip_items[0]["status"] == "skip"
        assert skip_items[0]["type"]   == "unknown"

    def test_collection_with_only_extras_becomes_file(self, tmp_path):
        """A dir with 2 videos but only 1 real movie (rest are extras) → type='file'."""
        col = tmp_path / "Movie.2023-GROUP"
        col.mkdir()
        _write_file(col / "movie.mkv")
        _write_file(col / "sample.mkv")
        items, _ = _scan_dir(tmp_path, [], {}, set(), include_history=False)
        assert len(items) == 1
        # After filtering the sample, only 1 real movie remains → file, not collection
        assert items[0]["type"] == "file"

    def test_collection_rule_not_met_becomes_file(self, tmp_path):
        """When _is_collection_by_rule returns False, treat as file (not collection)."""
        rules = {
            "collection": {
                "enabled": True,
                "require_valid_tag": False,
                "collection_tags": ["Pack"],   # only "Pack" is a collection tag
            }
        }
        col = tmp_path / "Movie.2023-NOTPACK"
        col.mkdir()
        _write_file(col / "a.mkv")
        _write_file(col / "b.mkv")
        items, _ = _scan_dir(tmp_path, [], rules, set(), include_history=False)
        assert len(items) == 1
        assert items[0]["type"] == "file"  # tag "NOTPACK" ∉ collection_tags → file
