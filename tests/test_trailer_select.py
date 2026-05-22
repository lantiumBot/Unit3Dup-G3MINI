# -*- coding: utf-8 -*-
from common.external_services.theMovieDB.core.trailer_select import pick_youtube_trailer_key
from common.external_services.theMovieDB.core.videos import Videos


def _v(**kwargs) -> Videos:
    defaults = dict(
        id="1",
        iso_3166_1="US",
        iso_639_1="en",
        key="abc123",
        name="Test",
        official=False,
        published_at="2020-01-01",
        site="YouTube",
        size=1080,
        type="Trailer",
    )
    defaults.update(kwargs)
    return Videos(**defaults)


def test_prefers_official_trailer_over_teaser():
    videos = [
        _v(type="Teaser", key="teaser1", official=True),
        _v(type="Trailer", key="trailer1", official=True),
        _v(type="Clip", key="clip1"),
    ]
    assert pick_youtube_trailer_key(videos) == "trailer1"


def test_fallback_teaser_then_clip():
    videos = [
        _v(type="Clip", key="clip1"),
        _v(type="Teaser", key="teaser1"),
    ]
    assert pick_youtube_trailer_key(videos) == "teaser1"


def test_ignores_non_youtube():
    videos = [_v(site="Vimeo", key="vimeo1")]
    assert pick_youtube_trailer_key(videos) is None


def test_empty_list():
    assert pick_youtube_trailer_key([]) is None
