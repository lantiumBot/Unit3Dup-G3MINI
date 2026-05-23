# -*- coding: utf-8 -*-
import hashlib
import os.path
import re

import diskcache

from common.external_services.imageHost import Build
from common.mediainfo import MediaFile
from common.frames import VideoFrame

from view import custom_console
from unit3dup import config_settings
from unit3dup.media import Media

_YOUTUBE_TRAILER_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{6,}$")


def _is_valid_youtube_trailer_key(key) -> bool:
    if not key or key == "not available":
        return False
    return bool(_YOUTUBE_TRAILER_KEY_RE.match(str(key).strip()))


class Video:
    """ Build a description for the torrent page: screenshots, mediainfo, trailers, metadata """

    def __init__(self, media: Media,  tmdb_id: int, trailer_key=None):
        self.file_name: str = media.file_name
        self.display_name: str = media.display_name

        self.tmdb_id: int = tmdb_id
        self.trailer_key: int = trailer_key
        self.cache = diskcache.Cache(str(config_settings.user_preferences.CACHE_PATH))

        # Create a cache key for tmdb_id
        self.key = f"{self.tmdb_id}.{self.display_name}"
        self.cache_key = self.hash_key(self.key)

        # Load the video frames (sauf si screenshots désactivés)
        self.video_frames: VideoFrame | None = None
        if not config_settings.user_preferences.SKIP_SCREENSHOTS:
            # if web_enabled is off set the number of screenshots to an even number
            if not config_settings.user_preferences.WEBP_ENABLED:
                if config_settings.user_preferences.NUMBER_OF_SCREENSHOTS % 2 != 0:
                    config_settings.user_preferences.NUMBER_OF_SCREENSHOTS += 1

            samples_n = max(2, min(config_settings.user_preferences.NUMBER_OF_SCREENSHOTS, 10))
            self.video_frames = VideoFrame(self.file_name, num_screenshots=samples_n)

        # Init
        self.is_hd: int = 0
        self.description: str = ''
        self.mediainfo: str = ''

    @staticmethod
    def hash_key(key: str) -> str:
        """ Generate a hashkey for the cache index """
        return hashlib.md5(key.encode('utf-8')).hexdigest()

    @staticmethod
    def generate_nfo_file(media_info: MediaFile, output_path: str) -> bool:
        """Génère un fichier NFO avec la sortie brute de Mediainfo.

        - Utilise `media_info.info` (texte brut de Mediainfo)
        - Remplace le chemin complet par juste le nom du fichier dans "Complete name"
        """
        try:
            mediainfo_output = media_info.info

            # Pattern pour trouver "Complete name" suivi du chemin complet
            pattern = r'(Complete name\s+:\s+)(.+[/\\])([^/\\]+\.\w+)'

            def replace_path(match: re.Match) -> str:
                # Garder seulement le nom de fichier
                return match.group(1) + match.group(3)

            # Appliquer le remplacement
            mediainfo_output = re.sub(pattern, replace_path, mediainfo_output)

            # Écrire le fichier NFO avec la sortie modifiée
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(mediainfo_output)

            return True
        except Exception as e:
            custom_console.bot_warning_log(f"[NFO] Erreur lors de la génération du NFO: {e}")
            return False

    def _apply_tmdb_trailer_description(self) -> None:
        """TMDB (fr-FR → en-US → sans langue) : [youtube] ou « . » si aucune vidéo."""
        if _is_valid_youtube_trailer_key(self.trailer_key):
            self.description += (
                f"[b][spoiler=Spoiler: PLAY TRAILER][center][youtube]{self.trailer_key}[/youtube]"
                f"[/center][/spoiler][/b]"
            )
        else:
            self.description = "."

    def build_info(self):
        """Build the information to send to the tracker"""

        # media_info
        media_info = MediaFile(self.file_name)
        self.mediainfo = media_info.info

        if config_settings.user_preferences.SKIP_SCREENSHOTS:
            custom_console.bot_log("\n[SCREENSHOTS] Ignorés (SKIP_SCREENSHOTS activé)")
            self.description = ""
            self._apply_tmdb_trailer_description()
            return

        if config_settings.user_preferences.CACHE_SCR:
            description = self.cache.get(self.cache_key)
            if description:
                custom_console.bot_warning_log(f"\n<> Using cached images for '{self.key}'")
                self.is_hd = description.get('is_hd', 0)
                if not _is_valid_youtube_trailer_key(self.trailer_key):
                    self.description = "."
                    return
                cached_desc = description.get('description', '')
                if cached_desc:
                    self.description = cached_desc
                    return

        if not self.description:
            # If no description found generate it
            custom_console.bot_log(f"\n[GENERATING IMAGES..] [HD {'ON' if self.is_hd == 0 else 'OFF'}]")
            # Extract the frames
            extracted_frames, is_hd = self.video_frames.create()
            # Create a webp file if it's enabled in the config json
            extracted_frames_webp = []
            if config_settings.user_preferences.WEBP_ENABLED:
                extracted_frames_webp = self.video_frames.create_webp_from_video(
                    video_path=self.file_name,
                    start_time=90,
                    duration=10,
                    output_path=os.path.join(config_settings.user_preferences.CACHE_PATH, "file.webp"),
                )
            custom_console.bot_log("Done.")

            # Build the description
            build_description = Build(extracted_frames=extracted_frames_webp + extracted_frames, filename=self.display_name)
            self.description = build_description.description()
            self._apply_tmdb_trailer_description()
            self.is_hd = is_hd

        # Caching
        if config_settings.user_preferences.CACHE_SCR:
            self.cache[self.cache_key] = {'tmdb_id': self.tmdb_id, 'description': self.description, 'is_hd': self.is_hd}