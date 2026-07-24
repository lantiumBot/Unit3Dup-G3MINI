# -*- coding: utf-8 -*-
import os
import hashlib
import inspect
from datetime import datetime

import diskcache
from typing import TypeVar

from common.external_services.theMovieDB.core.models.tvshow.alternative import Alternative
from common.external_services.theMovieDB.core.models.tvshow.details import TVShowDetails
from common.external_services.theMovieDB.core.models.movie.details import MovieDetails
from common.external_services.theMovieDB.core.models.movie.nowplaying import NowPlaying
from common.external_services.theMovieDB.core.models.tvshow.on_the_air import OnTheAir
from common.external_services.theMovieDB.core.models.tvshow.tvshow import TvShow
from common.external_services.theMovieDB.core.models.movie.movie import Movie
from common.external_services.theMovieDB.core.keywords import Keyword
from common.external_services.theMovieDB.core.videos import Videos
from common.external_services.theMovieDB.core.trailer_select import pick_youtube_trailer_key
from common.external_services.mediaresult import MediaResult
from common.external_services.sessions.session import MyHttp
from common.external_services.trailers.api import YtTrailer
from common.external_services.sessions.agents import Agent
from common.external_services.theMovieDB import config
from common.external_services.imdb import IMDB
from common.utility import ManageTitles

from unit3dup.media import Media
from view import custom_console


from unit3dup import config_settings

base_url = "https://api.themoviedb.org/3"
ENABLE_LOG = True
T = TypeVar('T')
_USE_DEFAULT_LANG = object()
TMDB_TRAILER_LANG_FALLBACK = ("fr-FR", "en-US", None)
# Sentinel pour détecter une clé nulle/vide
_NO_KEY_VALUES = frozenset({"no_key", "", None})

class MovieEndpoint:
    @staticmethod
    def search(query: str)-> dict:
        return {'url': f'{base_url}/search/movie', 'datatype': Movie, 'query': query, 'results': 'results' }


    @staticmethod
    def playing()-> dict:
        return {'url': f'{base_url}/movie/now_playing', 'datatype': NowPlaying, 'query': '', 'results': 'results'}


    @staticmethod
    def alternative(movie_id: int)-> dict:
        return {'url': f'{base_url}/movie/{movie_id}/alternative_titles', 'datatype': Alternative, 'query': '',
                'results': 'titles'}


    @staticmethod
    def videos(movie_id: int)-> dict:
        return {'url': f'{base_url}/movie/{movie_id}/videos', 'datatype': Videos, 'query': '',
                'results': 'results'}

    @staticmethod
    def details(movie_id: int)-> dict:
        return {'url': f'{base_url}/movie/{movie_id}', 'datatype': MovieDetails, 'query': ''}


    @staticmethod
    def keywords(movie_id: int)-> dict:
        return {'url': f'{base_url}/movie/{movie_id}/keywords', 'datatype': Keyword, 'query': '',
                'results': 'keywords'}


class TvEndpoint:
    @staticmethod
    def search(query: str):
        return {'url': f'{base_url}/search/tv', 'datatype': TvShow, 'query': query, 'results': 'results'}


    @staticmethod
    def playing():
        return {'url': f'{base_url}/tv/on_the_air', 'datatype': OnTheAir, 'query': '', 'results': 'results'}


    @staticmethod
    def alternative(serie_id: int) -> dict:
        return {'url': f'{base_url}/tv/{serie_id}/alternative_titles', 'datatype': Alternative, 'query': '',
                'results': 'results'}


    @staticmethod
    def videos(serie_id: int)-> dict:
        return {'url': f'{base_url}/tv/{serie_id}/videos', 'datatype': Videos, 'query': '',
                'results': 'results'}

    @staticmethod
    def details(serie_id: int)-> dict:
        return {'url': f'{base_url}/tv/{serie_id}', 'datatype': TVShowDetails, 'query': ''}



    @staticmethod
    def keywords(serie_id: int)-> dict:
        return {'url': f'{base_url}/tv/{serie_id}/keywords', 'datatype': Keyword, 'query': '',
                'results': 'results'}



class TmdbAPI(MyHttp):

    # Langue par défaut pour les requêtes TMDB (BCP-47 : fr-FR, en-US, it-IT…)
    _TMDB_LANGUAGE = "fr-FR"

    # Mappatura automatica degli endpoint
    ENDPOINTS = {
        'movie': MovieEndpoint,
        'tv': TvEndpoint,
    }

    def __init__(self):
        """
        Initialize the Api instance with an HTTP client.

        Authentication strategy (priorité décroissante) :
        1. TMDB_ACCESS_TOKEN configuré → Authorization: Bearer <token>  (recommandé TMDB v3/v4)
        2. Fallback → api_key en query parameter  (supporté mais méthode legacy)
        """
        headers = Agent.headers()

        access_token = getattr(config, 'TMDB_ACCESS_TOKEN', None)
        _use_bearer = (
            isinstance(access_token, str)
            and access_token.strip().lower() not in _NO_KEY_VALUES
        )

        if _use_bearer:
            # Méthode recommandée : Authorization header — aucun api_key dans les params
            headers["Authorization"] = f"Bearer {access_token.strip()}"
            self._auth_params: dict = {}
        else:
            # Méthode legacy : api_key en query param
            self._auth_params = {"api_key": config.TMDB_APIKEY}

        # Paramètres communs à toutes les requêtes (auth + langue)
        self.params: dict = {**self._auth_params, "language": self._TMDB_LANGUAGE}

        super().__init__(headers)
        self.http_client = self.session

    def _search(self, query: str, category: str, year: int | None = None) -> list[T] | None:
        """
        Searches for data based on a query and category.
        :param query: search query
        :param category: category of the search query, e.g., 'movie' or 'tv'
        :param year: optional release year to narrow results
        :return: list of T or None
        """
        # Only tv and movie
        if category not in ['movie', 'tv']:
            custom_console.bot_warning_log("Check the category of the search query")
            return []
        extra_params: dict | None = None
        if year:
            # TV   : first_air_date_year (restreint à la date de première diffusion)
            # Movie: primary_release_year (plus précis que le champ générique year)
            year_key = "first_air_date_year" if category == "tv" else "primary_release_year"
            extra_params = {year_key: year}
        if endpoint_class := self.ENDPOINTS.get(category):
            request = endpoint_class.search(query)
            return self.request(endpoint=request, extra_params=extra_params)
        else:
            print(f"Endpoint for category '{category}' not found.")
            return []

    def nowplaying(self, category: str) -> list[T] | None:
        """
        Searches for now playing content based on a query and category.
        :param category: category of the now playing content, e.g., 'movie' or 'tv'
        :return: list of T or None
        """

        if endpoint_class:=self.ENDPOINTS.get(category):
            request = endpoint_class.playing()
            return self.request(endpoint=request)
        else:
            print(f"Endpoint for category '{category}' not found.")
            return []

    def alternative(self, media_id: int, category: str) -> list[T] | None:
        """
        Searches for data based on a query and category.
        :param media_id: id of the media to search for
        :param category: category of the search query, e.g., 'movie' or 'tv'
        :return: list of T or None
        """
        if endpoint_class:=self.ENDPOINTS.get(category):
            request = endpoint_class.alternative(media_id)
            return self.request(endpoint=request)
        else:
            print(f"Endpoint for category '{category}' not found.")
            return []

    def _videos(
        self,
        video_id: int,
        category: str,
        language: str | None | object = _USE_DEFAULT_LANG,
    ) -> list[T] | None:
        if endpoint_class:=self.ENDPOINTS.get(category):
            request = endpoint_class.videos(video_id)
            return self.request(endpoint=request, language=language)
        else:
            print(f"Endpoint for category '{category}' not found.")
            return []

    def details(self, video_id: int, category: str) -> list[T] | None:
        if endpoint_class:=self.ENDPOINTS.get(category):
            request = endpoint_class.details(video_id)
            return self.request(endpoint=request)
        else:
            print(f"Endpoint for category '{category}' not found.")
            return []


    def _keywords(self, video_id: int, category: str) -> list[T] | None:
        if endpoint_class:=self.ENDPOINTS.get(category):
            request = endpoint_class.keywords(video_id)
            return self.request(endpoint=request)
        else:
            print(f"Endpoint for category '{category}' not found.")
            return []


    def request(
        self,
        endpoint: dict,
        language: str | None | object = _USE_DEFAULT_LANG,
        extra_params: dict | None = None,
    ) -> list[T] | None:
        """
        Sends a request to the API and returns a list of instances of the specified 'datatype'.
        :param endpoint: request endpoint
        :param language: langue TMDB (fr-FR, en-US), None = sans paramètre language
        :param extra_params: paramètres additionnels (ex: year, first_air_date_year)
        :return: list of T or None
        """
        params = {**self.params, "query": endpoint['query']}
        if extra_params:
            params.update(extra_params)
        if language is _USE_DEFAULT_LANG:
            pass
        elif language is None:
            params.pop("language", None)
        else:
            params["language"] = language
        response = self.get_url(endpoint['url'], params=params)

        if response:
            if response.status_code == 200:
                if not endpoint.get('results', None):
                    response_data = [response.json()]
                else:
                    response_data = response.json().get(endpoint['results'], [])

                datatype = endpoint['datatype']
                return [self._build_datatype(datatype, attribute) for attribute in response_data]
            else:
                return []
        return None

    @staticmethod
    def _build_datatype(datatype, attribute: dict):
        """
        Build endpoint datatypes while ignoring unknown API fields.
        TMDB may add fields (e.g. `softcore`) not present in our dataclasses.
        """
        if not isinstance(attribute, dict):
            return datatype(**attribute)

        try:
            return datatype(**attribute)
        except TypeError as exc:
            # Only fallback for unexpected keyword fields; re-raise other TypeErrors.
            if "unexpected keyword argument" not in str(exc):
                raise

            allowed = set(inspect.signature(datatype).parameters.keys())
            filtered = {key: value for key, value in attribute.items() if key in allowed}
            return datatype(**filtered)

class DbOnline(TmdbAPI):
    def __init__(self, media: Media, category: str, no_title: str, year: int | None = None) -> None:
        super().__init__()
        self.media = media
        self.query = media.guess_title
        self.category = category
        self.year = year

        # Load the cache file
        if config_settings.user_preferences.CACHE_DBONLINE:
            self.cache = diskcache.Cache(str(os.path.join(config_settings.user_preferences.CACHE_PATH, "tmdb.cache")))

        if media.tmdb_id or media.imdb_id:
            # Skip cache if there is a tmdb id or imdb in the title string
            self.media_result = self. results_in_string(tmdb_id=int(media.tmdb_id), imdb_id=int(media.imdb_id))
        else:
            # Load from the cache or search online for a tmdb id or imdb
            # Search for a video based on the filename or the title from the -notitle flag in the CLI
            if no_title:
                self.query = no_title
            self.media_result = self.search()

    @staticmethod
    def hash_key(key: str) -> str:
        """ Generate a hashkey for the cache index """
        return hashlib.md5(key.encode('utf-8')).hexdigest()

    def is_like(self, results: list[T]) -> T | bool:
        if results:
            # Search for in the tile or original_name
            for result in results:
                # check date
                if result.get_date() and self.media.guess_filename.guessit_year:
                    if not datetime.strptime(result.get_date(), '%Y-%m-%d').year == self.media.guess_filename.guessit_year:
                       continue

                # Search for title
                if ManageTitles.fuzzyit(str1=self.query, str2=ManageTitles.clean_text(result.get_title())) > 95:
                    return result

                if ManageTitles.fuzzyit(str1=self.query, str2=ManageTitles.clean_text(result.get_original())) > 95:
                    return result

            # Search for alternative title
            for result in results:
                alternative = self.alternative(media_id=result.id, category=self.category)
                for alt in alternative:
                    if ManageTitles.fuzzyit(str1=self.query, str2=alt.title) > 95:
                        return result
        return False

    def results_in_string(self, tmdb_id:int, imdb_id:int)-> MediaResult:
        """
        Use id from the string filename or name folder
        Cache disabled
        """
        keywords_list = ''
        trailer_key = ''

        if tmdb_id:
            if tmdb_id > 0:
                # Request trailer and keywords
                trailer_key = self.trailer(tmdb_id)
                keywords_list = self.keywords(tmdb_id) if trailer_key else ''
        else:
            tmdb_id = 0

        search_results = MediaResult(video_id=tmdb_id, imdb_id=imdb_id, trailer_key=trailer_key,
                                     keywords_list=keywords_list)
        self.print_results(results=search_results)
        return search_results


    def search(self) -> MediaResult | None:
        """
        Search for results based on a tmdb query
        season: True = search for a season. Title unknown
        """

        # Search in the cache first if cache is enabled
        if config_settings.user_preferences.CACHE_DBONLINE:
            search_results = self.load_cache(self.hash_key(self.query))
            if search_results:
                self.print_results(results=search_results)
                return search_results

        # or start an on-line search
        results = self._search(self.query, self.category, year=self.year)
        # Use imdb_id when tmdb_id is not available
        imdb_id = 0
        if results:
            if result:=self.is_like(results):
                # Get the trailer
                trailer_key = self.trailer(result.id)
                keywords_list = self.keywords(result.id)
                search_results = MediaResult(result, video_id=result.id, imdb_id=imdb_id,
                                             trailer_key=trailer_key, keywords_list=keywords_list)

                self.print_results(results=search_results)
                # Write to the cache if it is enabled
                if config_settings.user_preferences.CACHE_DBONLINE:
                    self.cache[self.hash_key(self.query)] = search_results
                # return the result
                return search_results

        # No response from TMDB
        if results is None:
            custom_console.bot_error_log(
                f"TMDB - No response from the remote host or the API key is invalid. Retry or update your key")
            exit(1)

        # not results found so try to initialize imdb
        custom_console.bot_warning_log(f"Title not found.What the bot has understood:")

        custom_console.bot_warning_log(f"Title:   '{self.query}'\ncategory:'{self.category}'")
        if self.category in 'tv':
            serie = f"S{str(self.media.guess_season).zfill(2)}" if self.media.guess_season else ''
            if not self.media.torrent_pack:
                serie += f"E{str(self.media.guess_episode).zfill(2)}"
            custom_console.bot_warning_log(f"details: '{serie}' Pack: '{self.media.torrent_pack}'")

        # Ask User TMDB ID
        search_results = self.manual_search()
        self.cache[self.hash_key(self.query)] = search_results
        return search_results


    def manual_search(self) -> MediaResult | None:
        """
                 User digits valid TMDB id or wait
                 User digits 0 if he wants to search on IMDB
                 Returns:
                     MediaResult
        """
        imdb = IMDB()
        imdb_id = 0
        user_id = 0
        while True:
            # Check if user wants to skip
            if not config_settings.user_preferences.SKIP_TMDB:
                try:
                    user_id = custom_console.user_input(message=f"Please digit a valid TMDB ID (0=skip)->")
                except KeyboardInterrupt:
                    custom_console.bot_error_log("\nOperation cancelled")
                    exit(0)
            else:
                custom_console.bot_warning_log(f"\n ** Auto skip TMDB ID **\n")

            # Try to add IMDB ID if tmdb is not available
            if user_id==0:
                imdb_id = imdb.search(query=self.query)
                # try searching for a YouTube video anyway
                trailer_key = self.youtube_trailer()
                search_results = MediaResult(video_id=user_id, imdb_id=imdb_id, trailer_key=trailer_key,
                                             keywords_list='not available')
                self.print_results(results=search_results)
                return search_results
            else:
                result = self.search_id(video_id=user_id)
                if result:
                    # Request trailer and keywords
                    trailer_key = self.trailer(user_id)
                    keywords_list = self.keywords(user_id) if trailer_key else ''
                    search_results = MediaResult(result=result, video_id=user_id, imdb_id=imdb_id, trailer_key=trailer_key,
                                                 keywords_list=keywords_list)
                    self.print_results(results=search_results)
                    return search_results


    def youtube_trailer(self) -> str | None:
        # Search trailer on YouTube
        if 'no_key'  in config_settings.tracker_config.YOUTUBE_KEY:
            return None

        yt_trailer = YtTrailer(self.query)
        result = yt_trailer.get_trailer_link()
        if result:
            # choose the first in the list
            # todo compare against the media title especially for the favorite channel
            return result[0].items[0].id.videoId
        else:
            if not config_settings.user_preferences.SKIP_YOUTUBE:
                user_youtube_id = custom_console.user_input_str(message="Title not found."
                                                                        " Please digit a valid Youtube ID (0=skip)->")
                if user_youtube_id==0:
                    return None
                return user_youtube_id
            return None

    def trailer(self, video_id: int) -> str | None:
        """Bande-annonce YouTube via /movie|tv/{id}/videos (fr-FR → en-US → sans langue)."""
        for lang in TMDB_TRAILER_LANG_FALLBACK:
            videos = self._videos(video_id, self.category, language=lang)
            if not videos:
                continue
            key = pick_youtube_trailer_key(videos)
            if key:
                return key
        return None

    def keywords(self, video_id: int) -> str | None:
        keywords_list = self._keywords(video_id, self.category)
        if keywords_list:
            return ",".join([key.name for key in keywords_list])
        else:
            return "not available"


    def print_results(self,results: MediaResult) -> None:
            custom_console.bot_log(f"'TMDB TITLE'..... {self.query}")
            custom_console.bot_log(f"'TMDB ID'........ {results.video_id}")
            if results.imdb_id:
                custom_console.bot_warning_log(f"_'IMDB ID'_........ '{results.imdb_id}'")
            custom_console.bot_log(f"'TMDB KEYWORDS'.. {results.keywords_list}")
            custom_console.bot_log(f"'TRAILER CODE' .. {results.trailer_key}")

    @staticmethod
    def _genre_ids_from(genres) -> list[int]:
        """Extrait les IDs TMDB (dict JSON ou dataclass Genre)."""
        ids: list[int] = []
        for g in genres:
            if isinstance(g, dict):
                ids.append(int(g["id"]))
            else:
                ids.append(int(g.id))
        return ids

    def get_classification_hints(self, media_result: MediaResult) -> tuple[list[int], str | None]:
        """
        Genres TMDB (IDs) et type d'émission pour les séries (champ ``type`` des détails TMDB).
        Un appel ``details`` complète les infos si la recherche ne les fournit pas.
        """
        vid = media_result.video_id
        if not vid:
            return [], None

        genre_ids: list[int] = []
        tv_type: str | None = None
        r = media_result.result

        if r is not None:
            if getattr(r, "genres", None):
                genre_ids = self._genre_ids_from(r.genres)
            elif getattr(r, "genre_ids", None):
                genre_ids = list(r.genre_ids)
            if self.category == "tv":
                tv_type = getattr(r, "type", None) or None

        needs_details = not genre_ids or (self.category == "tv" and not tv_type)
        if needs_details:
            det = self.details(vid, self.category)
            if det:
                d0 = det[0]
                if not genre_ids and getattr(d0, "genres", None):
                    genre_ids = self._genre_ids_from(d0.genres)
                if self.category == "tv" and not tv_type:
                    tv_type = getattr(d0, "type", None) or None

        return genre_ids, tv_type


    def load_cache(self, query: str)-> MediaResult | None:
        # Check if the item is in the cache
        if query not in self.cache:
            return None

        custom_console.bot_warning_log(f"<> Using cached results")
        try:
            # Try to get the video from the cache
            return self.cache[query]
        except KeyError:
            # Handle the case where the video is missing or the cache is corrupted
            custom_console.bot_error_log("Cached frame not found or cache file corrupted")
            custom_console.bot_error_log("Proceed to extract the screenshot again. Please wait..")
            return None


    def search_id(self, video_id: int) -> list[T] | None:
        """
                 Search Movie or Tvshow based on ID
                 Build a DataClass for the MediaResult
                 Returns:
                     list[T]: generic Movie or Tvshow results
        """
        result = self.details(video_id=video_id, category=self.category)
        if result:
            return result[0]
        return None