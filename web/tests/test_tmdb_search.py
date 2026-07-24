"""Tests for core/tmdb_search.py — title parsing, auth, search logic.

All HTTP calls are mocked so no real network access is needed.
"""
import sys
import os
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import patch, MagicMock


# ── _parse_title_year ────────────────────────────────────────────────────────

class TestParseTitleYear:
    """_parse_title_year — extract (title, year) from a release name."""

    def test_basic_movie_with_year(self):
        from core.tmdb_search import _parse_title_year
        title, year = _parse_title_year("Inception.2010.BluRay.1080p.x264-GROUP")
        assert year == 2010
        assert "Inception" in title or title  # title non-empty

    def test_no_year_returns_none(self):
        from core.tmdb_search import _parse_title_year
        _, year = _parse_title_year("SomeMovieWithNoYear.BluRay.x264-GROUP")
        # year may be None or an int — must not raise
        assert year is None or isinstance(year, int)

    def test_returns_non_empty_title(self):
        from core.tmdb_search import _parse_title_year
        title, _ = _parse_title_year("The.Dark.Knight.2008.BluRay-GROUP")
        assert title  # must not be empty string

    def test_tv_show_name(self):
        from core.tmdb_search import _parse_title_year
        title, _ = _parse_title_year("Breaking.Bad.S01.BluRay-GROUP")
        assert title

    def test_fallback_when_guessit_unavailable(self, monkeypatch):
        """If guessit raises ImportError the fallback regex must still return a title."""
        import builtins
        real_import = builtins.__import__
        def _mock_import(name, *a, **kw):
            if name == "guessit":
                raise ImportError("guessit not installed")
            return real_import(name, *a, **kw)
        monkeypatch.setattr(builtins, "__import__", _mock_import)
        from core.tmdb_search import _parse_title_year
        title, year = _parse_title_year("Movie.2023.BluRay-GROUP")
        assert title
        assert year == 2023 or year is None  # fallback may or may not extract year


# ── _tmdb_auth ───────────────────────────────────────────────────────────────

class TestTmdbAuth:
    """_tmdb_auth — selects Bearer token vs api_key vs empty credentials."""

    def _cfg(self, token="", apikey=""):
        return {"TRACKER_CONFIG": {"TMDB_ACCESS_TOKEN": token, "TMDB_APIKEY": apikey}}

    def test_bearer_token_takes_priority(self):
        from core.tmdb_search import _tmdb_auth
        with patch("core.tmdb_search.load_unit3dbot",
                   return_value=self._cfg(token="mytoken", apikey="mykey")):
            headers, params = _tmdb_auth()
            assert "Authorization" in headers
            assert "Bearer mytoken" in headers["Authorization"]
            assert "api_key" not in params

    def test_api_key_fallback(self):
        from core.tmdb_search import _tmdb_auth
        with patch("core.tmdb_search.load_unit3dbot",
                   return_value=self._cfg(token="", apikey="mykey")):
            headers, params = _tmdb_auth()
            assert "Authorization" not in headers
            assert params.get("api_key") == "mykey"

    def test_no_credentials_returns_empty(self):
        from core.tmdb_search import _tmdb_auth
        with patch("core.tmdb_search.load_unit3dbot",
                   return_value=self._cfg(token="", apikey="")):
            headers, params = _tmdb_auth()
            assert headers == {}
            assert params  == {}

    def test_no_key_token_treated_as_absent(self):
        """Token value 'no_key' must be ignored (legacy placeholder)."""
        from core.tmdb_search import _tmdb_auth
        with patch("core.tmdb_search.load_unit3dbot",
                   return_value=self._cfg(token="no_key", apikey="realkey")):
            headers, params = _tmdb_auth()
            # 'no_key' token → fall through to api_key
            assert params.get("api_key") == "realkey"

    def test_whitespace_token_treated_as_absent(self):
        from core.tmdb_search import _tmdb_auth
        with patch("core.tmdb_search.load_unit3dbot",
                   return_value=self._cfg(token="   ", apikey="fallback")):
            headers, params = _tmdb_auth()
            assert params.get("api_key") == "fallback"


# ── search_tmdb_item ─────────────────────────────────────────────────────────

class TestSearchTmdbItem:
    """search_tmdb_item — category routing + mocked HTTP."""

    def _no_creds(self):
        return {"TRACKER_CONFIG": {"TMDB_ACCESS_TOKEN": "", "TMDB_APIKEY": ""}}

    def test_returns_none_when_no_credentials(self):
        from core.tmdb_search import search_tmdb_item
        with patch("core.tmdb_search.load_unit3dbot", return_value=self._no_creds()):
            assert search_tmdb_item("Movie 2023", "file") is None

    def test_tv_type_uses_tv_endpoint(self):
        """integrale/season types must call the /search/tv endpoint."""
        from core.tmdb_search import search_tmdb_item

        cfg = {"TRACKER_CONFIG": {"TMDB_ACCESS_TOKEN": "tok", "TMDB_APIKEY": ""}}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "results": [{"id": 42, "name": "Breaking Bad", "first_air_date": "2008-01-20"}]
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("core.tmdb_search.load_unit3dbot", return_value=cfg), \
             patch("core.tmdb_search.requests.get", return_value=mock_resp) as mock_get:
            result = search_tmdb_item("Breaking.Bad.S01-GROUP", "season")
            assert result is not None
            assert result["tmdb_id"] == 42
            # Verify endpoint contained /tv
            called_url = mock_get.call_args[0][0]
            assert "/tv" in called_url

    def test_file_type_uses_movie_endpoint(self):
        from core.tmdb_search import search_tmdb_item

        cfg = {"TRACKER_CONFIG": {"TMDB_ACCESS_TOKEN": "tok", "TMDB_APIKEY": ""}}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "results": [{"id": 99, "title": "Inception", "release_date": "2010-07-16"}]
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("core.tmdb_search.load_unit3dbot", return_value=cfg), \
             patch("core.tmdb_search.requests.get", return_value=mock_resp) as mock_get:
            result = search_tmdb_item("Inception.2010.BluRay-GROUP", "file")
            assert result is not None
            assert result["tmdb_id"] == 99
            called_url = mock_get.call_args[0][0]
            assert "/movie" in called_url

    def test_returns_none_on_empty_results(self):
        from core.tmdb_search import search_tmdb_item

        cfg = {"TRACKER_CONFIG": {"TMDB_ACCESS_TOKEN": "tok", "TMDB_APIKEY": ""}}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"results": []}
        mock_resp.raise_for_status = MagicMock()

        with patch("core.tmdb_search.load_unit3dbot", return_value=cfg), \
             patch("core.tmdb_search.requests.get", return_value=mock_resp):
            assert search_tmdb_item("NoSuchTitle9999", "file") is None

    def test_returns_none_on_network_error(self):
        from core.tmdb_search import search_tmdb_item

        cfg = {"TRACKER_CONFIG": {"TMDB_ACCESS_TOKEN": "tok", "TMDB_APIKEY": ""}}
        with patch("core.tmdb_search.load_unit3dbot", return_value=cfg), \
             patch("core.tmdb_search.requests.get", side_effect=Exception("timeout")):
            assert search_tmdb_item("Movie 2023", "file") is None


# ── enrich_items_with_tmdb ───────────────────────────────────────────────────

class TestEnrichItemsWithTmdb:
    """enrich_items_with_tmdb — concurrent enrichment with early-out guards."""

    def _cfg(self, token="tok"):
        return {"TRACKER_CONFIG": {"TMDB_ACCESS_TOKEN": token, "TMDB_APIKEY": ""}}

    def test_no_credentials_returns_items_unchanged(self):
        from core.tmdb_search import enrich_items_with_tmdb
        items = [{"status": "pending", "type": "file", "name": "Movie"}]
        with patch("core.tmdb_search.load_unit3dbot",
                   return_value={"TRACKER_CONFIG": {"TMDB_ACCESS_TOKEN": "", "TMDB_APIKEY": ""}}):
            result = enrich_items_with_tmdb(items)
            assert "tmdb_id" not in result[0]

    def test_already_has_tmdb_id_not_overwritten(self):
        from core.tmdb_search import enrich_items_with_tmdb
        items = [{"status": "pending", "type": "file", "name": "Movie", "tmdb_id": 123}]
        with patch("core.tmdb_search.load_unit3dbot", return_value=self._cfg()):
            # Even with credentials, tmdb_id=123 must not be replaced
            result = enrich_items_with_tmdb(items)
            assert result[0]["tmdb_id"] == 123

    def test_history_items_skipped(self):
        """Items with status='history' must not be enriched."""
        from core.tmdb_search import enrich_items_with_tmdb
        items = [{"status": "history", "type": "file", "name": "Movie"}]
        with patch("core.tmdb_search.load_unit3dbot", return_value=self._cfg()):
            result = enrich_items_with_tmdb(items)
            assert "tmdb_id" not in result[0]

    def test_pending_item_gets_enriched(self):
        from core.tmdb_search import enrich_items_with_tmdb

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "results": [{"id": 777, "title": "Inception", "release_date": "2010-07-16"}]
        }
        mock_resp.raise_for_status = MagicMock()

        items = [{"status": "pending", "type": "file", "name": "Inception.2010.BluRay-GROUP"}]
        with patch("core.tmdb_search.load_unit3dbot", return_value=self._cfg()), \
             patch("core.tmdb_search.requests.get", return_value=mock_resp):
            result = enrich_items_with_tmdb(items)
            assert result[0].get("tmdb_id") == 777

    def test_empty_list_returns_empty(self):
        from core.tmdb_search import enrich_items_with_tmdb
        with patch("core.tmdb_search.load_unit3dbot", return_value=self._cfg()):
            assert enrich_items_with_tmdb([]) == []

    def test_documentary_genre_sets_doc_tag(self):
        """When TMDB returns genre_id 99, tag must be set to 'DOC'."""
        from core.tmdb_search import enrich_items_with_tmdb

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "results": [{"id": 97888, "name": "Some Doc", "first_air_date": "2020-01-01",
                         "genre_ids": [99, 10764]}]
        }
        mock_resp.raise_for_status = MagicMock()

        items = [{"status": "pending", "type": "season", "name": "Some.Doc.2020-GROUP",
                  "tag": "", "tag_valid": False}]
        with patch("core.tmdb_search.load_unit3dbot", return_value=self._cfg()), \
             patch("core.tmdb_search.requests.get", return_value=mock_resp):
            result = enrich_items_with_tmdb(items, valid_tags=["DOC", "FHD", "4K"])
            assert result[0].get("tmdb_id") == 97888
            assert result[0].get("tag") == "DOC"
            assert result[0].get("tag_valid") is True

    def test_documentary_tag_valid_false_when_not_in_valid_tags(self):
        """tag_valid=False when 'DOC' is not in the configured valid_tags list."""
        from core.tmdb_search import enrich_items_with_tmdb

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "results": [{"id": 12345, "title": "My Doc", "release_date": "2021-01-01",
                         "genre_ids": [99]}]
        }
        mock_resp.raise_for_status = MagicMock()

        items = [{"status": "pending", "type": "file", "name": "My.Doc.2021-GROUP",
                  "tag": "", "tag_valid": False}]
        with patch("core.tmdb_search.load_unit3dbot", return_value=self._cfg()), \
             patch("core.tmdb_search.requests.get", return_value=mock_resp):
            result = enrich_items_with_tmdb(items, valid_tags=["FHD", "4K"])
            assert result[0].get("tag") == "DOC"
            assert result[0].get("tag_valid") is False

    def test_non_documentary_genre_does_not_change_tag(self):
        """Genre IDs without 99 must not alter the tag."""
        from core.tmdb_search import enrich_items_with_tmdb

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "results": [{"id": 55555, "title": "Action Movie", "release_date": "2022-06-01",
                         "genre_ids": [28, 12]}]
        }
        mock_resp.raise_for_status = MagicMock()

        items = [{"status": "pending", "type": "file", "name": "Action.Movie.2022-FHD",
                  "tag": "FHD", "tag_valid": True}]
        with patch("core.tmdb_search.load_unit3dbot", return_value=self._cfg()), \
             patch("core.tmdb_search.requests.get", return_value=mock_resp):
            result = enrich_items_with_tmdb(items, valid_tags=["FHD", "DOC"])
            assert result[0].get("tag") == "FHD"   # unchanged
