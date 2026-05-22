# -*- coding: utf-8 -*-

from pathlib import Path
import argparse
import os
import shutil
import time

from unit3dup.media import Media
from unit3dup.media_manager.ContentManager import ContentManager
from unit3dup.media_manager.TorrentManager import TorrentManager

from common.external_services.ftpx.core.models.list import FTPDirectory
from common.external_services.ftpx.core.menu import Menu
from common.external_services.ftpx.client import Client
from common.extractor import Extractor
from common.utility import ManageTitles

from view import custom_console


class Bot:
    """
    A Bot class that manages media files, including processing, torrent management,TMDB ,FTP

    Methods:
        run(): Starts the media processing and torrent handling tasks
        watcher(duration: int, watcher_path: str): Monitors a folder for changes and processes files
        ftp(): Connects to a remote FTP server and processes files
    """

    # Bot Manager
    def __init__(self, path: str, cli: argparse.Namespace, trackers_name_list: list, mode="man",
                 torrent_archive_path = None):
        """
        Initializes the Bot instance with path, command-line interface object, and mode

        Args:
            path (str): The path to the directory or file to be managed
            cli (argparse.Namespace): The command-line arguments object
            mode (str): The mode of operation, default is 'man'
        """
        self.trackers_name_list = trackers_name_list
        self.torrent_archive_path = torrent_archive_path
        self.content_manager = None
        self.path = path.strip()
        self.cli = cli
        self.mode = mode


    def contents(self) -> bool | list[Media]:
        """
        Start the process of analyzing and processing media files
        This method retrieves media files
        """
        custom_console.panel_message("Analyzing your media files... Please wait")

        if not os.path.exists(self.path):
            custom_console.bot_error_log("Path doesn't exist")
            return False

        # Get a Files list with basic attributes and create a content object for each
        self.content_manager: ContentManager = ContentManager(path=self.path, mode=self.mode, cli=self.cli)
        contents = self.content_manager.process()

        # -u requires a single file
        if not contents:
            custom_console.bot_error_log("There are no Media to process")
            return False

        # we got a handled exception
        if contents is None:
            exit(1)

        # -f requires at least one file
        if not contents:
            custom_console.bot_error_log(f"There are no Files to process. Try using -scan")
            exit(1)

        # Print the list of files being processed
        custom_console.bot_process_table_log(contents)

        return contents


    def run(self) -> bool:
        """
        processes the files using the TorrentManager and SeedManager
        """

        # Get the user content
        contents = self.contents()
        if not contents:
            return False

        # Instance a new run
        torrent_manager = TorrentManager(cli=self.cli, tracker_archive=self.torrent_archive_path)
        # Process the torrents content (files)
        torrent_manager.process(contents=contents)

        # We want to reseed
        if self.cli.reseed:
            torrent_manager.reseed(trackers_name_list=self.trackers_name_list)
            return True
        else:
            # otherwise run the torrents creations and the upload process
            upload_results = torrent_manager.run(trackers_name_list=self.trackers_name_list)

        if self.cli.noup:
            return False

        return self._uploads_succeeded(upload_results)


    def watcher(self, duration: int, watcher_path: str,  destination_path: str)-> bool:
        """
        Monitors the watcher path for new files/folders, uploads them one-by-one,
        then moves successfully processed items to the destination folder.

        Args:
            duration (int): The time duration in seconds for the watchdog to wait before checking again
            watcher_path (str): The path to the folder being monitored for new files
            destination_path: Where to move items AFTER a successful upload
        """
        try:
            # Return if the watcher path doesn't exist
            if not os.path.exists(watcher_path):
                custom_console.bot_error_log("Watcher path does not exist or is not configured\n")
                return False

            # Watchdog loop
            while True:
                # Traiter immédiatement les fichiers au démarrage et à chaque cycle
                watcher_root = Path(watcher_path)
                done_root = Path(destination_path)
                done_root.mkdir(parents=True, exist_ok=True)

                # Skip if there are no files in the watcher folder
                if not os.listdir(watcher_path):
                    custom_console.bot_log("The are no files in the Watcher folder\n")
                else:
                    # Process top-level entries one-by-one to avoid moving everything at once.
                    entries = sorted(
                        [p for p in watcher_root.iterdir() if p.name and not p.name.startswith(".")],
                        key=lambda p: p.name.lower(),
                    )

                    for src in entries:
                        if not src.exists():
                            continue

                        # Upload this single item (folder or file)
                        mode = "folder" if src.is_dir() else "man"
                        custom_console.bot_log(f"\n[Watcher] Processing -> {src}")

                        single_bot = Bot(
                            path=str(src),
                            cli=self.cli,
                            trackers_name_list=self.trackers_name_list,
                            mode=mode,
                            torrent_archive_path=self.torrent_archive_path,
                        )

                        ok = single_bot.run()
                        if not ok:
                            custom_console.bot_warning_log(
                                f"[Watcher] Upload failed/skipped, leaving in place -> {src}"
                            )
                            continue

                        moved_to = self._move_to_destination(src=src, done_root=done_root)
                        if moved_to:
                            custom_console.bot_log(f"[Watcher] Moved to destination -> {moved_to}")
                        else:
                            custom_console.bot_warning_log(
                                f"[Watcher] Upload succeeded but move failed, leaving in place -> {src}"
                            )
                
                # Nettoyer les fichiers .nfo isolés dans le dossier watcher après avoir traité tous les fichiers du cycle
                self._cleanup_orphaned_nfo_files(watcher_path)

                # Attendre avant le prochain cycle
                print()
                start_time = time.perf_counter()
                end_time = start_time + duration
                # Counter
                while time.perf_counter() < end_time:
                    remaining_time = end_time - time.perf_counter()
                    custom_console.bot_counter_log(
                        f"WATCHDOG: {remaining_time:.1f} seconds Ctrl-c to Exit "
                    )
                    time.sleep(0.01)
                print()

        except KeyboardInterrupt:
            custom_console.bot_log("Exiting...")
        return True

    @staticmethod
    def _uploads_succeeded(results) -> bool:
        if not results:
            return False

        return all(result.tracker_response and not result.tracker_message for result in results)

    @staticmethod
    def _next_available_destination(target: Path) -> Path:
        if not target.exists():
            return target

        counter = 1
        while True:
            candidate = target.with_name(f"{target.name}_{counter}")
            if not candidate.exists():
                return candidate
            counter += 1

    def _move_to_destination(self, src: Path, done_root: Path) -> Path | None:
        target = self._next_available_destination(done_root / src.name)

        try:
            shutil.move(str(src), str(target))
            return target
        except Exception as exc:
            custom_console.bot_warning_log(f"[Watcher] Failed to move '{src}' -> '{target}': {exc}")
            return None

    def _cleanup_orphaned_nfo_files(self, watcher_path: str) -> None:
        """
        Supprime uniquement les fichiers .nfo isolés à la RACINE du dossier watcher.
        Ces fichiers sont ceux générés par le script lors de l'upload.
        NE supprime PAS les fichiers .nfo dans les sous-dossiers (ceux qui étaient là avant).
        """
        try:
            if not os.path.exists(watcher_path) or not os.path.isdir(watcher_path):
                return
            
            # Vérifier UNIQUEMENT à la racine du watcher_path, pas récursivement
            files = os.listdir(watcher_path)
            
            # Chercher les fichiers .nfo à la racine
            nfo_files = [f for f in files if f.lower().endswith('.nfo') and os.path.isfile(os.path.join(watcher_path, f))]
            
            if not nfo_files:
                return
            
            # Vérifier s'il y a des fichiers vidéo à la racine
            video_files = [f for f in files if ManageTitles.filter_ext(f) and os.path.isfile(os.path.join(watcher_path, f))]
            
            # Si pas de fichiers vidéo à la racine mais des fichiers .nfo, supprimer les .nfo
            # (ce sont ceux générés par le script)
            if not video_files and nfo_files:
                for nfo_file in nfo_files:
                    nfo_path = os.path.join(watcher_path, nfo_file)
                    try:
                        os.remove(nfo_path)
                        custom_console.bot_log(
                            f"[Watcher] Deleted orphaned NFO file at root: {nfo_path}"
                        )
                    except Exception as e:
                        custom_console.bot_warning_log(
                            f"[Watcher] Failed to delete orphaned NFO '{nfo_path}': {e}"
                        )
        
        except Exception as e:
            custom_console.bot_warning_log(f"[Watcher] Error cleaning up orphaned NFO files: {e}")

    def ftp(self)-> None:
        """
        Connects to a remote FTP server and interacts with files.

        This method handles FTP connection, navigation, and file download from the remote server
        """
        custom_console.bot_question_log("\nConnecting to the remote FTP...\n")

        # FTP service
        ftp_client = Client()
        custom_console.bot_question_log(f"Connected to {ftp_client.sys_info()}\n\n")

        menu = Menu()

        page = ftp_client.home_page()
        menu.show(table=page)

        while True:
            user_option = ftp_client.user_input()
            page = ftp_client.input_manager(user_option)
            if page == 0:
                ftp_client.quit()
                break
            if not page:
                continue

            # Display selected folder or file
            if isinstance(page, FTPDirectory):
                ftp_directory_name = page.name
                if page.type == "Folder":
                    page = ftp_client.change_path(selected_folder=ftp_directory_name)
                    menu.show(table=page)
                else:
                    ftp_client.select_file(one_file_selected=page)
                continue

            # Show page as table
            menu.show(table=page)

        if ftp_client.download_to_local_path:
            self.path = os.path.dirname(ftp_client.download_to_local_path)
            self.mode = 'folder'
            # Upload -f process
            # Decompress .rar files if the flags are set
            extractor = Extractor(compressed_media__path=self.path)
            result = extractor.unrar()
            if result is False:
                custom_console.bot_error_log("Unrar Exit")
                exit(1)

            self.run()
        return None
