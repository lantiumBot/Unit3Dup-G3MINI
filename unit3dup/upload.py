import argparse
import json
import re
import requests

from common.external_services.igdb.core.models.search import Game
from common.trackers.trackers import TRACKData
from common.trackers.data import build_tracker_announces, download_torrent_from_url
from common.trackers.gemini_categories import resolve_gemini_video_category

from unit3dup.pvtTracker import Unit3d
from unit3dup.pvtDocu import PdfImages
from unit3dup import config_settings, Load
from unit3dup.pvtVideo import Video
from unit3dup.media import Media
from unit3dup.release_normalizer import normalize_release_name as _normalize_release_name

from view import custom_console

class UploadBot:
    def __init__(self, content: Media, tracker_name: str, cli: argparse):
        self.cli = cli
        self.content = content
        self.tracker_name = tracker_name
        self.tracker_data = TRACKData.load_from_module(tracker_name=tracker_name)
        self.tracker = Unit3d(tracker_name=tracker_name, release_name=content.torrent_name)

    def normalize_release_name(self, release_name: str) -> str:
        mediainfo_text: str | None = None
        is_silent: bool = False

        if self.content.mediafile and hasattr(self.content.mediafile, 'info'):
            mediainfo_text = self.content.mediafile.info or None

        if self.content.mediafile and hasattr(self.content.mediafile, 'is_silent'):
            is_silent = self.content.mediafile.is_silent

        tv_year = None
        torrent_pack = False
        if self.content.category == "tv":
            tv_year = self.content.release_year
            torrent_pack = bool(self.content.torrent_pack)
        return _normalize_release_name(
            release_name,
            mediainfo_text,
            is_silent,
            tv_year=tv_year,
            torrent_pack=torrent_pack,
        )

    def _check_personal_release_by_tag(self, release_name: str) -> int:
        """
        Détermine la valeur de personal_release pour cette release.

        Logique :
          1. La valeur initiale vient de PERSONAL_RELEASE (config) ou du flag --personal (CLI).
          2. Si TAGS_TEAM est renseigné dans uploader_tag, on extrait le tag après le dernier '-'
             et on compare (insensible à la casse) :
               - Tag reconnu  → personal_release conservé
               - Tag inconnu  → personal_release forcé à 0
          3. Si TAGS_TEAM est vide, aucun filtrage — on conserve la valeur initiale.

        Fonctionne pour tous les modes : -u, -f, -watcher, -scan.
        """
        personal_release = int(config_settings.user_preferences.PERSONAL_RELEASE) or int(self.cli.personal)

        # Récupérer la liste des tags autorisés depuis la config (insensible à la casse)
        tags_team: list[str] = [
            t.upper()
            for t in getattr(config_settings.uploader_tag, 'TAGS_TEAM', [])
        ]

        if tags_team:
            # Extraire le tag après le dernier tiret (ex: "...-KFL" → "KFL")
            parts = release_name.rsplit('-', 1)
            if len(parts) == 2:
                tag = parts[1].strip().upper()
                if tag not in tags_team:
                    personal_release = 0
            else:
                # Pas de tag d'équipe dans le nom → pas une release personnelle
                personal_release = 0

        return personal_release

    def message(self,tracker_response: requests.Response, torrent_archive: str) -> (requests, dict):

        name_error = ''
        info_hash_error = ''
        try:
            _message = json.loads(tracker_response.text)
            if isinstance(_message, dict) and 'data' in _message:
                _message = _message['data']
        except (json.JSONDecodeError, TypeError):
            # Si le JSON ne peut pas être parsé, utiliser le texte brut
            _message = tracker_response.text

        if tracker_response.status_code == 200:
            tracker_response_body = json.loads(tracker_response.text)
            custom_console.bot_log(f"\n[RESPONSE]-> '{self.tracker_name}'.....{tracker_response_body['message'].upper()}\n\n")
            custom_console.rule()
            # https://github.com/HDInnovations/UNIT3D/pull/4910/files
            # 08/09/2025
            # We have to download the torrent file to get the new random info_hash generated
            self.download_file(url=tracker_response_body["data"], destination_path=torrent_archive)
            return tracker_response_body["data"], {}

        elif tracker_response.status_code == 401:
            if isinstance(_message, dict):
                custom_console.bot_error_log(_message)
                exit(_message.get('message', 'Unauthorized'))
            else:
                custom_console.bot_error_log(_message)
                exit(str(_message))

        elif tracker_response.status_code == 404:
            if isinstance(_message, dict):
                if _message.get("type_id",None):
                    name_error =  _message["type_id"]
                else:
                    name_error = _message.get("message", str(_message))
            else:
                name_error = str(_message)
            error_message = f"{self.__class__.__name__} - {name_error}"
        else:
            if isinstance(_message, dict):
                if _message.get("name",None):
                    name_error =  _message["name"][0] if isinstance(_message["name"], list) else _message["name"]
                if _message.get("info_hash",None):
                    info_hash_error = _message["info_hash"][0] if isinstance(_message["info_hash"], list) else _message["info_hash"]
            else:
                name_error = str(_message)
            error_message =f"{self.__class__.__name__} - {name_error} : {info_hash_error}"

        custom_console.bot_error_log(f"\n[RESPONSE]-> '{error_message}\n\n")
        custom_console.rule()
        return {}, error_message

    def data(
        self,
        show_id: int,
        imdb_id: int,
        show_keywords_list: str,
        video_info: Video,
        genre_ids: list[int] | None = None,
        tv_show_type: str | None = None,
    ) -> Unit3d | None:

        release_name = self.content.display_name.replace(" ", ".")
        release_name = self.normalize_release_name(release_name)
        self.tracker.data["name"] = release_name
        self.tracker.data["tmdb"] = show_id
        self.tracker.data["imdb"] = imdb_id if imdb_id else 0
        self.tracker.data["keywords"] = show_keywords_list
        tracker_cat = resolve_gemini_video_category(
            self.content.category,
            genre_ids,
            tv_show_type,
        )
        category_id = self.tracker_data.category.get(tracker_cat)
        if category_id is None:
            category_id = self.tracker_data.category.get(self.content.category)
        self.tracker.data["category_id"] = category_id
        if tracker_cat != self.content.category:
            custom_console.bot_log(
                f"'TRACKER CATEGORY'.. {tracker_cat} (category_id={category_id}) "
                f"[base={self.content.category}]"
            )
        self.tracker.data["anonymous"] = int(config_settings.user_preferences.ANON)
        self.tracker.data["resolution_id"] = self.tracker_data.resolution[self.content.screen_size]\
            if self.content.screen_size else self.tracker_data.resolution[self.content.resolution]
        self.tracker.data["mediainfo"] = video_info.mediainfo
        self.tracker.data["description"] = video_info.description
        self.tracker.data["sd"] = video_info.is_hd
        self.tracker.data["type_id"] = self.tracker_data.filter_type(self.content.file_name)
        self.tracker.data["season_number"] = self.content.guess_season
        self.tracker.data["episode_number"] = (self.content.guess_episode if not self.content.torrent_pack else 0)
        self.tracker.data["personal_release"] = self._check_personal_release_by_tag(release_name)
        return self.tracker

    def data_game(self,igdb: Game) -> Unit3d | None:

        igdb_platform = self.content.platform_list[0].lower() if self.content.platform_list else ''
        release_name = self.content.display_name.replace(" ", ".")
        release_name = self.normalize_release_name(release_name)
        self.tracker.data["name"] = release_name
        self.tracker.data["tmdb"] = 0
        self.tracker.data["category_id"] = self.tracker_data.category.get(self.content.category)
        self.tracker.data["anonymous"] = int(config_settings.user_preferences.ANON)
        self.tracker.data["description"] = igdb.description if igdb else "Sorry, there is no valid IGDB"
        self.tracker.data["type_id"] = self.tracker_data.type_id.get(igdb_platform) if igdb_platform else 1
        self.tracker.data["igdb"] = igdb.id if igdb else 1,  # need zero not one ( fix tracker)
        self.tracker.data["personal_release"] = self._check_personal_release_by_tag(release_name)
        return self.tracker

    def data_docu(self, document_info: PdfImages) -> Unit3d | None:

        release_name = self.content.display_name.replace(" ", ".")
        release_name = self.normalize_release_name(release_name)
        self.tracker.data["name"] = release_name
        self.tracker.data["tmdb"] = 0
        self.tracker.data["category_id"] = self.tracker_data.category.get(self.content.category)
        self.tracker.data["anonymous"] = int(config_settings.user_preferences.ANON)
        self.tracker.data["description"] = document_info.description
        self.tracker.data["type_id"] = self.tracker_data.filter_type(self.content.file_name)
        self.tracker.data["resolution_id"] = ""
        self.tracker.data["personal_release"] = self._check_personal_release_by_tag(release_name)
        return self.tracker

    def send(self, torrent_archive: str, nfo_path = None) -> (requests, dict):

        tracker_response=self.tracker.upload_t(data=self.tracker.data,torrent_archive_path = torrent_archive,
                                               nfo_path=nfo_path)
        return self.message(tracker_response=tracker_response, torrent_archive=torrent_archive)


    def download_file(self, url: str, destination_path: str) -> bool:
        """Télécharge le .torrent post-upload avec le même api_token que l'upload."""
        from unit3dup.media_manager.common import UserContent

        if not download_torrent_from_url(
            url,
            destination_path,
            api_token=self.tracker.api_token,
            headers=self.tracker.headers,
        ):
            return False

        trackers = [self.tracker_name]
        if UserContent.torrent_announces(
            torrent_path=destination_path,
            tracker_name_list=trackers,
            selected_tracker=self.tracker_name,
            release_name=self.content.torrent_name,
        ):
            expected = build_tracker_announces(trackers, self.content.torrent_name)
            UserContent._patch_torrent_announces(destination_path, expected)
        return True
