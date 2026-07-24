# -*- coding: utf-8 -*-
"""Tests for unit3dup/release_normalizer.py — normalize_release_name().

The normalizer has no external dependencies (re, unicodedata, typing only).
It is imported directly via importlib to bypass unit3dup/__init__.py,
which requires pydantic and the full CLI stack.
"""
import importlib.util
import os

import pytest

# Load the module directly, bypassing the package __init__
_spec = importlib.util.spec_from_file_location(
    "release_normalizer",
    os.path.join(os.path.dirname(__file__), "..", "..", "unit3dup", "release_normalizer.py"),
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
normalize = _mod.normalize_release_name


# ── Episode subtitle stripping ────────────────────────────────────────────────

class TestEpisodeSubtitleStripping:
    """Text after SxxExx (episode subtitle) must not appear in the series title."""

    def test_reported_case(self):
        """Vintage.Mecanic.S10E03.Audi.S4.V8.Cabriolet — episode title stripped."""
        result = normalize(
            "Vintage.Mecanic.S10E03.Audi.S4.V8.Cabriolet.FRENCH.720p.WEB.H264",
            tv_year=2016,
        )
        assert result.startswith("Vintage.Mecanic.")
        assert "Audi" not in result
        assert "Cabriolet" not in result
        assert "S10E03" in result
        assert "2016" in result

    def test_episode_subtitle_not_in_title(self):
        """Generic case: anything after SxxExx must be dropped from the title."""
        result = normalize("Show.Name.S02E05.The.Episode.Title.FRENCH.1080p.WEB.H264")
        assert result.startswith("Show.Name.")
        assert "Episode" not in result
        assert "Title" not in result
        assert "S02E05" in result

    def test_series_name_preserved_fully(self):
        """Multi-word series name before SxxExx must be kept intact."""
        result = normalize("The.Walking.Dead.S11E03.Episode.Title.FRENCH.1080p.BluRay.H264")
        assert result.startswith("The.Walking.Dead.")
        assert "Episode" not in result

    def test_no_episode_subtitle_unaffected(self):
        """When there is no text between SxxExx and the technical tokens, nothing changes."""
        result = normalize("Breaking.Bad.S01E01.FRENCH.1080p.WEB.H264-GROUP")
        assert result.startswith("Breaking.Bad.")
        assert "S01E01" in result

    def test_tmdb_title_takes_priority_over_prefix(self):
        """When tmdb_title is provided it overrides _ep_title_prefix entirely."""
        result = normalize(
            "Vintage.Mecanic.S10E03.Audi.S4.V8.Cabriolet.FRENCH.720p.WEB.H264",
            tmdb_title="Vintage Mécanic",
            tv_year=2016,
        )
        assert result.startswith("Vintage.Mecanic.")
        assert "Audi" not in result

    def test_episode_prefix_empty_falls_back_gracefully(self):
        """If SxxExx is at the very start (no series name before it), no crash."""
        result = normalize("S01E01.FRENCH.1080p.WEB.H264")
        assert "S01E01" in result

    def test_multi_word_episode_title_all_stripped(self):
        """All words of the episode title (between SxxExx and first tech token) are removed."""
        result = normalize("Garage.Story.S03E07.Ferrari.Testarossa.FRENCH.720p.WEB.x264-TEAM")
        assert result.startswith("Garage.Story.")
        assert "Ferrari" not in result
        assert "Testarossa" not in result
        assert "S03E07" in result


# ── is_documentary flag ───────────────────────────────────────────────────────

class TestIsDocumentary:
    """is_documentary=True must inject .DOC into the normalized name."""

    def test_reported_case_episode(self):
        """Vintage.Mecanic episode — DOC absent from filename but injected via flag."""
        result = normalize(
            "Vintage.Mecanic.S10E02.Mini.John.Cooper.Works.Coupe.R58.FRENCH.720p.WEB.H264",
            tv_year=2016,
            is_documentary=True,
        )
        assert result.startswith("Vintage.Mecanic.")
        assert ".DOC." in result
        assert "S10E02" in result
        assert "Mini" not in result   # episode subtitle stripped
        assert "Coupe" not in result

    def test_doc_injected_for_movie(self):
        """Film documentaire — .DOC. ajouté via flag."""
        result = normalize(
            "My.Documentary.2022.FRENCH.1080p.WEB.H264",
            is_documentary=True,
        )
        assert ".DOC." in result

    def test_doc_not_duplicated_when_already_in_name(self):
        """If 'DOC' is already in the filename, must not appear twice."""
        result = normalize(
            "My.Documentary.2022.DOC.FRENCH.1080p.WEB.H264",
            is_documentary=True,
        )
        assert result.count(".DOC.") == 1

    def test_no_doc_without_flag(self):
        """Without is_documentary=True, .DOC. must not appear."""
        result = normalize(
            "Vintage.Mecanic.S10E02.FRENCH.720p.WEB.H264",
            tv_year=2016,
        )
        assert ".DOC." not in result

    def test_doc_position_after_year_before_episode(self):
        """Convention G3MINI: .DOC. appears right after the year, before SxxExx."""
        result = normalize(
            "Vintage.Mecanic.S10E02.FRENCH.720p.WEB.H264",
            tv_year=2016,
            is_documentary=True,
        )
        # Expected shape: Vintage.Mecanic.2016.DOC.S10E02.VOF.720p.WEB.H264-NoTag
        year_pos = result.find("2016")
        doc_pos  = result.find(".DOC.")
        ep_pos   = result.find("S10E02")
        assert year_pos < doc_pos < ep_pos


# ── Season packs (no stripping) ───────────────────────────────────────────────

class TestSeasonPack:
    """Season packs (Sxx without Exx) must keep the full residual name as title."""

    def test_season_pack_title_intact(self):
        result = normalize(
            "Breaking.Bad.S01.FRENCH.1080p.BluRay.H264",
            torrent_pack=True,
        )
        assert result.startswith("Breaking.Bad.")
        assert "S01" in result

    def test_season_pack_no_episode_strip(self):
        """No SxxExx in a season pack → _ep_title_prefix is None → normal behavior."""
        result = normalize("The.Wire.S04.COMPLETE.FRENCH.720p.WEB.H264", torrent_pack=True)
        assert "The.Wire" in result
        assert "S04" in result


# ── Basic normalization regression tests ──────────────────────────────────────

class TestBasicNormalization:
    """Smoke tests to guard against regressions in core normalization behavior."""

    def test_french_becomes_vff(self):
        # FRENCH alone (no EN/ENG track) → VFF (Version Française)
        result = normalize("Movie.Name.2020.FRENCH.1080p.WEB.H264")
        assert "VFF" in result

    def test_notag_appended_when_no_team(self):
        result = normalize("Movie.Name.2020.FRENCH.1080p.WEB.H264")
        assert result.endswith("-NoTag")

    def test_team_tag_preserved(self):
        result = normalize("Movie.Name.2020.FRENCH.1080p.WEB.H264-YIFY")
        assert result.endswith("-YIFY")

    def test_resolution_present(self):
        result = normalize("Movie.2021.FRENCH.720p.WEB.H264")
        assert "720p" in result

    def test_h264_and_web_present(self):
        result = normalize("Show.S01E01.FRENCH.1080p.WEB.H264-TEAM")
        assert "H264" in result
        assert "WEB" in result

    def test_year_from_tv_year_param(self):
        result = normalize("Show.Name.S05E02.Episode.Title.FRENCH.1080p.WEB.H264", tv_year=2019)
        assert "2019" in result

    def test_no_double_dots(self):
        result = normalize("Movie.2022.FRENCH.1080p.WEB.H264")
        assert ".." not in result

    def test_mkv_extension_stripped(self):
        result = normalize("Movie.2022.FRENCH.1080p.WEB.H264.mkv")
        assert not result.endswith(".mkv")

    def test_truefrench_becomes_vff(self):
        result = normalize("Movie.2020.TRUEFRENCH.1080p.BluRay.H264-TEAM")
        assert "VFF" in result

    def test_multi_vff_preserved(self):
        result = normalize("Movie.2020.MULTi.VFF.1080p.WEB.H264-TEAM")
        assert "MULTi" in result
        assert "VFF" in result

    def test_vostfr_preserved(self):
        result = normalize("Movie.2023.VOSTFR.1080p.WEB.H264-TEAM")
        assert "VOSTFR" in result

    def test_french_preposition_de_not_mistaken_for_german(self):
        """'de' (lowercase preposition in French title) must not trigger MULTi.

        Regression: _SECONDARY_LANG_RE had re.IGNORECASE, causing 'de' in
        'Sagrada Familia le reve acheve de Gaudi' to match the German code DE.
        """
        result = normalize(
            "Sagrada.Familia.le.reve.acheve.de.Gaudi.2026.DOC.FRENCH.AD.1080p.WEB.x265-THESYNDiCATE.mkv"
        )
        assert "MULTi" not in result

    def test_uppercase_de_in_release_triggers_multi(self):
        """An explicit uppercase DE lang token alongside FRENCH must still give MULTi."""
        result = normalize("Film.FRENCH.DE.1080p.WEB.H264-TEAM")
        assert "MULTi" in result
