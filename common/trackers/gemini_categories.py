# -*- coding: utf-8 -*-
"""Résolution des category_id Gemini / G3mini à partir des métadonnées TMDB."""

# IDs TMDB (identiques films / séries sauf mention)
TMDB_GENRE_ANIMATION = 16
TMDB_GENRE_DOCUMENTARY = 99


def resolve_gemini_video_category(
    base_category: str,
    genre_ids: list[int] | None,
    tv_show_type: str | None = None,
) -> str:
    """
    Détermine la clé CATEGORY du tracker (gemini_data) pour un film ou une série.

    Priorité : Animation > Documentaire > types TMDB Talk Show / News / Documentaire >
    catégorie de base (Films / Séries).

    Args:
        base_category: 'movie' ou 'tv' (depuis Media.category / Guessit).
        genre_ids: liste d'IDs de genres TMDB (endpoint détails ou recherche).
        tv_show_type: champ ``type`` TMDB pour les séries (ex. 'Talk Show', 'News').
    """
    if base_category not in ("movie", "tv"):
        return base_category

    ids = set(genre_ids or [])
    is_movie = base_category == "movie"

    # Animation avant tout (ex. série comique animée aussi taguée « Documentaire » ailleurs)
    if TMDB_GENRE_ANIMATION in ids:
        return "movie_animation" if is_movie else "tv_animation"

    if TMDB_GENRE_DOCUMENTARY in ids:
        return "movie_documentary" if is_movie else "tv_documentary"

    if not is_movie and tv_show_type:
        t = tv_show_type.strip().lower()
        if t in ("talk show", "news"):
            return "tv_emission"
        if t == "documentary":
            return "tv_documentary"

    return base_category
