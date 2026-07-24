# -*- coding: utf-8 -*-
import argparse
import json
import os
import re

from common.utility import ManageTitles, System
from unit3dup.automode import Auto
from unit3dup.media import Media

class ContentManager:
    def __init__(self, path: str, mode: str, cli: argparse.Namespace):
        """
        Args:
            path (str): The path to the media files or directories
            mode (str):  mode 'manual' or 'automatic'
        """
        self.path = path
        self.mode = mode
        self.cli = cli

        self.languages: list[str] | None = None
        self.display_name: str | None = None
        self.meta_info_list: list[dict] = []
        self.game_nfo: str = ''
        self.category: int | None = None

        self.meta_info: str | None = None
        self.size: int | None = None
        self.torrent_name: str | None = None
        self.folder: str | None = None
        self.file_name: str | None = None
        self.torrent_path: str | None = None
        self.doc_description: str | None = None
        self.tmdb_id: int  = 0
        self.imdb_id: int  = 0
        self.igdb_id: int  = 0
        self.generate_title: str | None = None

        self.path: str = os.path.normpath(path)
        self.auto = Auto(path=self.path, mode=self.mode)
        self.media_list = self.auto.upload() if self.mode in ["man", "folder"] else self.auto.scan()

    def process(self)-> list[Media]:
        contents = []
        for media in self.media_list:
            # Utiliser media.torrent_path qui est déjà le chemin complet (fichier ou dossier)
            # Mais pour les dossiers, on doit utiliser le dossier lui-même, pas le chemin du fichier
            if os.path.isdir(media.torrent_path):
                # C'est un dossier, utiliser le chemin du dossier
                self.path = media.torrent_path
            else:
                # C'est un fichier, utiliser le chemin du fichier
                self.path = media.torrent_path
            media.category = media.category if not self.cli.force else self.cli.force
            self.category = media.category

            content = self.get_data(media=media)
            if content:
                contents.append(content)
        return contents

    def get_data(self, media: Media) -> Media | bool:
        """
        Process files or folders and create a `Contents` object if the metadata is valid
        """
        process = False
        # Convertir en chemin absolu pour éviter les problèmes de chemins relatifs
        self.path = os.path.abspath(self.path) if self.path else None
        
        if not self.path:
            return False
            
        if os.path.isdir(self.path):
            process = self.process_folder()
        elif os.path.isfile(self.path):
            process = self.process_file()

        if not self.meta_info:
            return False

        # add category filter to the regex result caused by a possible substring (e.g., S06) in the whole path
        # and not part of the title
        if media.category=='tv':
            # Standard SxxExx-range or bare Sxx (no episode) — original pattern
            # Extended: also match French "Saison.05", English "Season.2",
            # and ordinal "Second Season" / "Third Season" etc.
            torrent_pack = bool(re.search(
                r"(S\d+(?!.*E\d+))"            # S05 without episode
                r"|(S\d+E\d+-E?\d+)"           # S05E01-E05 range
                r"|(?:S(?:ea|ai)son[.\s_\-]*\d+)"  # Season 2 / Saison 05
                r"|(?:(?:Second|Third|Fourth|Fifth|Sixth|Seventh|Eighth|Ninth|Tenth)"
                r"[.\s_\-]+Season)",            # Second Season / Third Season
                self.path, re.IGNORECASE
            ))
            if not torrent_pack and ManageTitles.is_tv_integrale(media.title_sanitized):
                torrent_pack = True
        else:
            torrent_pack = False

        if process:
            media.file_name = self.file_name
            media.torrent_name = self.torrent_name
            media.size = self.size
            media.metainfo = self.meta_info
            media.torrent_pack = torrent_pack
            media.doc_description = self.doc_description
            media.game_nfo = self.game_nfo

            # Add ID if there are one in the title
            media.imdb_id = self.imdb_id
            media.tmdb_id = self.tmdb_id
            media.igdb_id = self.igdb_id
            media.display_name = self.display_name

            # Language addition removed - keep display_name as is
            return media
        else:
            return False


    def search_ids(self):
        _id = re.findall(r"\{(imdb-\d+|tmdb-\d+|igdb-\d+)}", self.file_name, re.IGNORECASE)
        if _id:
            # // Searching..
            for id in _id:
                if 'imdb-' in id:
                    self.imdb_id = id.replace('imdb-', '')
                    self.imdb_id = self.imdb_id if self.imdb_id.isdigit() else None
                    self.display_name = self.display_name.replace(f"imdb-{self.imdb_id}", '')
                elif 'tmdb-' in id:
                    self.tmdb_id = id.replace('tmdb-', '')
                    self.tmdb_id = self.tmdb_id if self.tmdb_id.isdigit() else None
                    self.display_name = self.display_name.replace(f"tmdb-{self.tmdb_id}", '')

                elif 'igdb-' in id:
                    self.igdb_id = id.replace('igdb-', '')
                    self.igdb_id = self.igdb_id if self.igdb_id.isdigit() else None
                    self.display_name = self.display_name.replace(f"igdb-{self.igdb_id}", '')

        else:
            self.imdb_id = 0
            self.tmdb_id = 0
            self.igdb_id = 0

    def process_file(self) -> bool:
        """Process individual files and gather metadata"""
        self.file_name = self.path
        # Display name on webpage
        self.display_name, _ = os.path.splitext(os.path.basename(self.file_name))
        self.display_name = ManageTitles.clean_text(self.display_name)
        self.display_name = re.sub(r'[\[\]()]', '', self.display_name)
        # current media path
        self.torrent_path = self.path
        # Try to get video ID from the string title
        self.search_ids()
        # Torrent name
        self.torrent_name =  os.path.basename(self.file_name)
        # test to check if it is a doc
        self.doc_description = self.file_name

        # Build meta_info
        self.size = os.path.getsize(self.path)
        self.meta_info = json.dumps([{"length": self.size, "path": [self.file_name]}], indent=4)
        return True

    def process_folder(self)-> bool:
        """Process folder and gather metadata for a torrent containing multiple files"""
        files_list = self.list_files_by_category()
        if not files_list:
            return False

        # Sample the first file in the list (utiliser le chemin complet pour le Mediainfo)
        first_file_rel = files_list[0]
        self.file_name = os.path.join(self.path, first_file_rel)
        # Display name on webpage
        self.display_name = ManageTitles.clean_text(os.path.basename(self.path))
        self.display_name = re.sub(r'[\[\]()]', '', self.display_name)
        # current media path
        self.torrent_path = self.path
        # Torrent name
        self.torrent_name = os.path.basename(self.path)
        # Document description
        self.doc_description = "\n".join(files_list)
        # Try to get video ID from the string title
        self.search_ids()
        # Build meta_info
        self.size = 0
        self.meta_info_list = []
        for file_rel in files_list:
            # Utiliser le chemin complet pour obtenir la taille
            file_full_path = os.path.join(self.path, file_rel)
            size = os.path.getsize(file_full_path)
            # Pour le torrent, utiliser le chemin relatif (séparé par /)
            file_path_parts = file_rel.replace('\\', '/').split('/')
            self.meta_info_list.append({"length": size, "path": file_path_parts})
            self.size += size
            if file_rel.lower().endswith(".nfo"):
                self.game_nfo = os.path.join(self.path, file_rel)

        self.meta_info = json.dumps(self.meta_info_list, indent=4)
        return True
    def list_files_by_category(self) -> list[str]:
        """List files based on the content category"""
        if self.category ==System.category_list.get(System.GAME):
            return self.list_game_files()
        return self.list_video_files()

    def list_video_files(self) -> list[str]:
        """List video files in the folder recursively"""
        video_files = []
        # Parcourir récursivement tous les fichiers dans le dossier
        for root, dirs, files in os.walk(self.path):
            for file in files:
                if ManageTitles.filter_ext(file):
                    # Obtenir le chemin relatif depuis self.path
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, self.path)
                    # Normaliser les séparateurs pour Windows/Unix
                    rel_path = rel_path.replace('\\', '/')
                    video_files.append(rel_path)
        return video_files

    def list_game_files(self) -> list[str]:
        """List all files in a game folder recursively"""
        all_files = []
        # Parcourir récursivement tous les fichiers dans le dossier
        for root, dirs, files in os.walk(self.path):
            for file in files:
                # Obtenir le chemin relatif depuis self.path
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, self.path)
                # Normaliser les séparateurs pour Windows/Unix
                rel_path = rel_path.replace('\\', '/')
                all_files.append(rel_path)
        return all_files