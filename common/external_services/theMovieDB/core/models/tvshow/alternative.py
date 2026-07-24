# -*- coding: utf-8 -*-

from dataclasses import dataclass

@dataclass
class Alternative:
    iso_3166_1: str
    title: str
    type: str | None = None  # Peut être absent ou chaîne vide selon l'API TMDB

@dataclass
class DataResponse:
    id: int
    results: list[Alternative]
