# -*- coding: utf-8 -*-
import argparse
import os

from common.external_services.theMovieDB.core.api import DbOnline
from common.bittorrent import BittorrentData
from common.utility import ManageTitles

from unit3dup.media_manager.common import UserContent
from unit3dup.upload import UploadBot
from unit3dup import config_settings
from unit3dup.pvtVideo import Video
from unit3dup.media import Media

from view import custom_console

class VideoManager:

    def __init__(self, contents: list[Media], cli: argparse.Namespace):
        """
        Initialize the VideoManager with the given contents

        Args:
            contents (list): List of content media objects
            cli (argparse.Namespace): user flag Command line
        """

        self.torrent_found:bool = False
        self.contents: list[Media] = contents
        self.cli: argparse = cli

    def process(self, selected_tracker: str, tracker_name_list: list, tracker_archive: str) -> list[BittorrentData]:
        """
           Process the video contents to filter duplicates and create torrents

           Returns:
               list: List of Bittorrent objects created for each content
        """

        # -multi : no announce_list . One announce for multi tracker
        if self.cli.mt:
            tracker_name_list = [selected_tracker.upper()]

        #  Init the torrent list
        bittorrent_list = []
        for content in self.contents :

            # get the archive path
            archive = os.path.join(tracker_archive, selected_tracker)
            os.makedirs(archive, exist_ok=True)
            torrent_filepath = os.path.join(tracker_archive,selected_tracker, f"{content.torrent_name}.torrent")

            # Filter contents based on existing torrents or duplicates
            if UserContent.is_preferred_language(content=content):

                if self.cli.watcher:
                    if os.path.exists(torrent_filepath):
                        custom_console.bot_log(f"Watcher Active.. skip the old upload '{content.file_name}'")
                        continue

                torrent_response = UserContent.torrent(content=content, tracker_name_list=tracker_name_list,
                                                       selected_tracker=selected_tracker, this_path=torrent_filepath)

                # Skip(S) if it is a duplicate or let the user choose to continue (C)
                if (self.cli.duplicate or config_settings.user_preferences.DUPLICATE_ON
                        and UserContent.is_duplicate(content=content, tracker_name=selected_tracker,
                                                     cli=self.cli)):
                    continue

                # Search for VIDEO ID
                db_online = DbOnline(media=content,category=content.category, no_title=self.cli.notitle)
                db = db_online.media_result

                # If it is 'None' we skipped the imdb search (-notitle)
                if not db:
                    continue

                if content.category == "tv" and db.year:
                    content.release_year = db.year

                # Update display name with Serie Title when requested by the user (-notitle)
                if self.cli.notitle:
                    # Add generated metadata to the display_title
                    if self.cli.gentitle:
                        content.display_name = (f"{db_online.media_result.result.get_title()} "
                                                f"{db_online.media_result.year} ")
                        content.display_name+= " " + content.generate_title
                    else:
                        # otherwise keep the old meta_data and add the new display_title to it
                         print()
                         content.display_name = (f"{db_online.media_result.result.get_title()}"
                                                 f" {db_online.media_result.year} {content.guess_title}")

                # Get meta from the media video
                video_info = Video(media=content, tmdb_id=db.video_id, trailer_key=db.trailer_key)
                video_info.build_info()
                # print the title will be shown on the torrent page
                custom_console.bot_log(f"'DISPLAYNAME'...{{{content.display_name}}}\n")

                genre_ids, tv_show_type = db_online.get_classification_hints(db)

                # Tracker instance
                unit3d_up = UploadBot(content=content, tracker_name=selected_tracker, cli = self.cli)

                # Get the data
                unit3d_up.data(
                    show_id=db.video_id,
                    imdb_id=db.imdb_id,
                    show_keywords_list=db.keywords_list,
                    video_info=video_info,
                    genre_ids=genre_ids,
                    tv_show_type=tv_show_type,
                )

                # ── Confirmation interactive (-confirm) ───────────────────────
                if getattr(self.cli, 'confirm', False):
                    release_name = unit3d_up.tracker.data.get("name", content.display_name)
                    custom_console.rule("[bold cyan]Validation release[/bold cyan]")
                    custom_console.bot_log(f"  Fichier      : {content.display_name}")
                    custom_console.bot_question_log(
                        f"\n  Release name : {release_name}\n\n"
                        f"  Confirmer l'upload ? [o/N] : "
                    )
                    try:
                        answer = input().strip().lower()
                    except KeyboardInterrupt:
                        custom_console.bot_error_log("\nOpération annulée par l'utilisateur.")
                        break
                    if answer not in ("o", "oui", "y", "yes"):
                        custom_console.bot_warning_log(f"  ✗ Upload annulé → {release_name}\n")
                        custom_console.rule()
                        continue
                    custom_console.bot_log(f"  ✓ Upload confirmé → {release_name}\n")
                    custom_console.rule()

                # Don't upload if -noup is set to True
                if self.cli.noup:
                    custom_console.bot_warning_log(f"No Upload active. Done.")
                    continue

                # ── Recherche / génération du NFO ────────────────────────────
                nfo_path = None
                nfo_generated = False   # True si c'est nous qui l'avons créé → à supprimer après

                # Déterminer le chemin du fichier média et son répertoire
                # Pour un torrent pack, utiliser directement le dossier pour trouver le NFO
                if content.torrent_pack:
                    media_file_path = content.torrent_path
                else:
                    media_file_path = content.file_name if content.file_name else content.torrent_path
                                
                if not media_file_path:
                    custom_console.bot_warning_log(f"[NFO] Aucun chemin média disponible pour générer le NFO")
                else:
                    # Convertir en chemin absolu
                    media_file_path = os.path.abspath(media_file_path)
                    
                    # Déterminer si c'est un fichier ou un dossier
                    if os.path.isdir(media_file_path):
                        # Cas d'un dossier (release pack)
                        media_dir = media_file_path
                        
                        # Chercher un fichier .nfo dans le dossier (n'importe quel .nfo)
                        nfo_files = [f for f in os.listdir(media_dir) if f.lower().endswith('.nfo')]
                        
                        if nfo_files:
                            # Utiliser le premier .nfo trouvé dans le dossier
                            nfo_path = os.path.join(media_dir, nfo_files[0])
                            custom_console.bot_log(f"[NFO] Fichier NFO existant trouvé et utilisé: {nfo_path}")
                            # nfo_generated reste False → on ne supprimera pas ce fichier
                        else:
                            # Aucun NFO trouvé → en générer un temporaire
                            nfo_filename = f"{content.torrent_name}.nfo"
                            nfo_path = os.path.join(media_dir, nfo_filename)
                            custom_console.bot_log(f"[NFO] Aucun NFO trouvé, génération du fichier NFO temporaire: {nfo_path}")
                            from common.mediainfo import MediaFile
                            try:
                                # Chercher le premier fichier vidéo dans le dossier
                                video_files = [f for f in os.listdir(media_dir) 
                                             if ManageTitles.filter_ext(f)]
                                if video_files:
                                    video_file_path = os.path.join(media_dir, video_files[0])
                                    media_file = MediaFile(video_file_path)
                                    if Video.generate_nfo_file(media_file, nfo_path):
                                        custom_console.bot_log(f"[NFO] Fichier NFO temporaire généré avec succès")
                                        nfo_generated = True  # Marquer comme temporaire → à supprimer après
                                    else:
                                        custom_console.bot_warning_log(f"[NFO] Échec de la génération du NFO")
                                        nfo_path = None
                                else:
                                    custom_console.bot_warning_log(f"[NFO] Aucun fichier vidéo trouvé dans le dossier pour générer le NFO")
                                    nfo_path = None
                            except Exception as e:
                                custom_console.bot_warning_log(f"[NFO] Erreur lors de la génération du NFO: {e}")
                                nfo_path = None
                    elif os.path.isfile(media_file_path):
                        # Cas d'un fichier unique
                        file_dir = os.path.dirname(media_file_path)
                        file_base = os.path.splitext(os.path.basename(media_file_path))[0]
                        nfo_candidate = os.path.join(file_dir, f"{file_base}.nfo")
                        
                        if os.path.isfile(nfo_candidate):
                            nfo_path = nfo_candidate
                            custom_console.bot_log(f"[NFO] Fichier NFO existant trouvé et utilisé: {nfo_path}")
                        else:
                            # Générer un NFO temporaire
                            nfo_path = nfo_candidate
                            custom_console.bot_log(f"[NFO] Aucun NFO trouvé, génération du fichier NFO temporaire: {nfo_path}")
                            from common.mediainfo import MediaFile
                            try:
                                media_file = MediaFile(media_file_path)
                                if Video.generate_nfo_file(media_file, nfo_path):
                                    custom_console.bot_log(f"[NFO] Fichier NFO temporaire généré avec succès")
                                    nfo_generated = True
                                else:
                                    custom_console.bot_warning_log(f"[NFO] Échec de la génération du NFO")
                                    nfo_path = None
                            except Exception as e:
                                custom_console.bot_warning_log(f"[NFO] Erreur lors de la génération du NFO: {e}")
                                nfo_path = None
                    else:
                        custom_console.bot_warning_log(f"[NFO] Chemin média invalide (ni fichier ni dossier): {media_file_path}")

                # Send to the tracker
                # Vérifier que le NFO existe avant l'envoi
                if nfo_path:
                    if os.path.isfile(nfo_path):
                        custom_console.bot_log(f"[NFO] Envoi du fichier NFO au tracker: {nfo_path}")
                    else:
                        custom_console.bot_warning_log(f"[NFO] Fichier NFO introuvable avant l'envoi: {nfo_path}")
                        nfo_path = None
                else:
                    custom_console.bot_warning_log(f"[NFO] Aucun fichier NFO à envoyer")
                
                tracker_response, tracker_message = unit3d_up.send(torrent_archive=torrent_filepath, nfo_path=nfo_path)

                # Supprimer UNIQUEMENT le NFO temporaire généré par le script (ne pas supprimer les .nfo existants)
                if nfo_generated and nfo_path and os.path.isfile(nfo_path):
                    try:
                        os.remove(nfo_path)
                        custom_console.bot_log(f"[NFO] Fichier NFO temporaire supprimé: {nfo_path}")
                    except Exception as e:
                        custom_console.bot_warning_log(f"[NFO] Impossible de supprimer le NFO temporaire: {e}")


                # Store response for the torrent clients
                bittorrent_list.append(
                    BittorrentData(
                        tracker_response=tracker_response,
                        torrent_response=torrent_response,
                        content=content,
                        tracker_message = tracker_message,
                        archive_path=torrent_filepath,
                    ))

        # // end content
        return bittorrent_list
