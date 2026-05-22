# -*- coding: utf-8 -*-
"""Sélection d'une bande-annonce YouTube à partir des vidéos TMDB."""

from typing import Any

_TRAILER_TYPE_ORDER = ("trailer", "teaser", "clip")


def _field(video: Any, name: str, default: Any = "") -> Any:
    if isinstance(video, dict):
        return video.get(name, default) if video.get(name) is not None else default
    return getattr(video, name, default) if getattr(video, name, None) is not None else default


def pick_youtube_trailer_key(videos: list[Any] | None) -> str | None:
    """
    Retourne la clé YouTube d'embed pour la première vidéo pertinente.

    Priorité : Trailer officiel → Trailer → Teaser → Clip (site YouTube uniquement).
    """
    if not videos:
        return None

    candidates = []
    for video in videos:
        if str(_field(video, "site", "")).lower() != "youtube":
            continue
        vtype = str(_field(video, "type", "")).lower()
        if vtype not in _TRAILER_TYPE_ORDER:
            continue
        key = str(_field(video, "key", "")).strip()
        if key:
            candidates.append(video)

    if not candidates:
        return None

    def _sort_key(video: Any) -> tuple[int, int]:
        vtype = str(_field(video, "type", "")).lower()
        official = bool(_field(video, "official", False))
        return (_TRAILER_TYPE_ORDER.index(vtype), 0 if official else 1)

    best = min(candidates, key=_sort_key)
    key = str(_field(best, "key", "")).strip()
    return key or None
