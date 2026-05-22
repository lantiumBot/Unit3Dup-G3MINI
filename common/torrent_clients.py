# -*- coding: utf-8 -*-
import hashlib
import os
import stat
import time
import bencode2
import requests
from abc import ABC, abstractmethod

import qbittorrentapi
import transmission_rpc
from rtorrent_rpc import RTorrent

from unit3dup.pvtTorrent import Mytorrent
from unit3dup import config_settings
from unit3dup.media import Media

from view import custom_console


def _qbittorrent_api_client() -> qbittorrentapi.Client:
    return qbittorrentapi.Client(
        host=config_settings.torrent_client_config.QBIT_HOST,
        port=int(config_settings.torrent_client_config.QBIT_PORT),
        username=config_settings.torrent_client_config.QBIT_USER,
        password=config_settings.torrent_client_config.QBIT_PASS,
        REQUESTS_ARGS={"timeout": 10},
    )


class TorrClient(ABC):
    def __init__(self):
        self.client = None

    @abstractmethod
    def connect(self):
        raise NotImplementedError

    @abstractmethod
    def send_to_client(self, tracker_data_response: str, torrent: Mytorrent | None, content: Media, archive_path: str):
        raise NotImplementedError

    @staticmethod
    def download(tracker_torrent_url: requests.Response, full_path_archive: str):
        with open(full_path_archive, "wb") as file:
            file.write(tracker_torrent_url.content)
        return open(full_path_archive, "rb")


class TransmissionClient(TorrClient):
    def __init__(self) -> None:
        super().__init__()

    def connect(self) -> transmission_rpc.Client | None:
        try:
            self.client = transmission_rpc.Client(
                host=config_settings.torrent_client_config.TRASM_HOST,
                port=config_settings.torrent_client_config.TRASM_PORT,
                username=config_settings.torrent_client_config.TRASM_USER,
                password=config_settings.torrent_client_config.TRASM_PASS,
                timeout=10,
            )
            return self.client
        except requests.exceptions.HTTPError:
            custom_console.bot_error_log(f"{self.__class__.__name__} HTTP Error. Check IP/port or run Transmission")
        except requests.exceptions.ConnectionError:
            custom_console.bot_error_log(f"{self.__class__.__name__} Connection Error. Check IP/port or run Transmission")
        except transmission_rpc.TransmissionError:
            custom_console.bot_error_log(f"{self.__class__.__name__} Login required. Check your username and password")
        except Exception as e:
            custom_console.bot_error_log(f"{self.__class__.__name__} Unexpected error: {e}")
            custom_console.bot_error_log(f"{self.__class__.__name__} Please verify your configuration")
        return None

    def send_to_client(self, tracker_data_response: str, torrent: Mytorrent | None, content: Media, archive_path: str):
        # Transmission "shared path"
        if config_settings.torrent_client_config.SHARED_QBIT_PATH:
            torr_location = config_settings.torrent_client_config.SHARED_QBIT_PATH
        else:
            # content.torrent_path can be file or folder; use parent directory for both
            base = content.torrent_path
            torr_location = os.path.dirname(base)

        torr_location = os.path.normpath(torr_location)

        with open(archive_path, "rb") as file_buffer:
            self.client.add_torrent(torrent=file_buffer, download_dir=str(torr_location))

    def send_file_to_client(self, torrent_path: str):
        self.client.add_torrent(
            torrent=open(torrent_path, "rb"),
            download_dir=str(os.path.dirname(torrent_path)),
        )


class QbittorrentClient(TorrClient):
    def __init__(self):
        super().__init__()

    def connect(self) -> qbittorrentapi.Client | None:
        try:
            self.client = _qbittorrent_api_client()

            login_count = 0
            while True:
                try:
                    self.client.auth_log_in()
                    return self.client
                except qbittorrentapi.LoginFailed:
                    login_count += 1
                    if login_count > 5:
                        custom_console.bot_error_log("Failed to login.")
                        exit(1)
                    custom_console.bot_warning_log("Qbittorrent failed to login. Retry...Please wait")
                    time.sleep(2)

        except qbittorrentapi.APIConnectionError:
            custom_console.bot_error_log(f"{self.__class__.__name__} Connection Error. Check IP/port or run qBittorrent")
        except qbittorrentapi.Forbidden403Error:
            custom_console.bot_error_log(f"{self.__class__.__name__} Login required. Check your username and password")
        except requests.exceptions.HTTPError:
            custom_console.bot_error_log(f"{self.__class__.__name__} HTTP Error. Check IP/port or run qBittorrent")
        except requests.exceptions.ConnectionError:
            custom_console.bot_error_log(f"{self.__class__.__name__} Connection Error. Check IP/port or run qBittorrent")
        except Exception as e:
            custom_console.bot_error_log(f"{self.__class__.__name__} Unexpected error: {e}")
            custom_console.bot_error_log(f"{self.__class__.__name__} Please verify your configuration")
        return None

    @staticmethod
    def _resolve_savepath(content: Media) -> str:
        if config_settings.torrent_client_config.SHARED_QBIT_PATH:
            return os.path.normpath(config_settings.torrent_client_config.SHARED_QBIT_PATH)

        base = os.path.abspath(content.torrent_path) if content.torrent_path else None
        if not base:
            base = os.path.abspath(content.file_name) if content.file_name else None

        if base:
            if os.path.isfile(base) or os.path.isdir(base):
                torr_location = os.path.dirname(base)
            else:
                torr_location = os.path.dirname(base)
        else:
            torr_location = os.getcwd()

        return os.path.normpath(torr_location)

    def send_to_client(self, tracker_data_response: str, torrent: Mytorrent | None, content: Media, archive_path: str):
        torr_location = self._resolve_savepath(content)
        custom_console.bot_warning_log(f"[QbittorrentClient] Forced savepath: {torr_location}")

        tag = config_settings.torrent_client_config.TAG

        with open(archive_path, "rb") as file_buffer:
            torrent_data = file_buffer.read()
            info = bencode2.bdecode(torrent_data)[b"info"]
            info_hash = hashlib.sha1(bencode2.bencode(info)).hexdigest()
            file_buffer.seek(0)

            self.client.torrents_add(
                torrent_files=file_buffer,
                save_path=str(torr_location),
                tags=tag,
                use_auto_torrent_management=False,
            )

        try:
            self.client.torrents_add_tags(tags=tag, torrent_hashes=info_hash)
        except Exception:
            pass

    def send_file_to_client(self, torrent_path: str, media_location: str):
        with open(torrent_path, "rb") as fb:
            self.client.torrents_add(
                torrent_files=fb,
                save_path=str(os.path.normpath(media_location)),
                tags=config_settings.torrent_client_config.TAG,
                use_auto_torrent_management=False,
            )


class RTorrentClient(TorrClient):
    def __init__(self):
        super().__init__()

    def connect(self) -> RTorrent | None:
        # Build the socket string for rTorrent: TCP or Unix socket
        if os.path.exists(config_settings.torrent_client_config.RTORR_HOST):
            socket_type = os.stat(config_settings.torrent_client_config.RTORR_HOST).st_mode
            if stat.S_ISSOCK(socket_type):
                socket = f"scgi:///{config_settings.torrent_client_config.RTORR_HOST}"
            else:
                custom_console.bot_error_log("Invalid RTorrent host")
                exit(1)
        else:
            socket = (
                f"scgi://{config_settings.torrent_client_config.RTORR_HOST}:"
                f"{config_settings.torrent_client_config.RTORR_PORT}"
            )

        login_count = 0
        while True:
            try:
                self.client = RTorrent(address=socket, timeout=10)
                self.client.system_list_methods()
                return self.client
            except requests.exceptions.HTTPError:
                custom_console.bot_warning_log("Rtorrent failed to login. Retry...Please wait")
                time.sleep(2)
                login_count += 1
                if login_count > 5:
                    custom_console.bot_error_log("Rtorrent failed to login.")
                    exit()
            except (requests.exceptions.ConnectionError, TimeoutError, ConnectionRefusedError):
                custom_console.bot_error_log(f"{self.__class__.__name__} Connection Error. Check host/port or run rTorrent")
                exit()
            except AttributeError:
                custom_console.bot_error_log(f"{self.__class__.__name__} Socket connection error or wrong OS platform")
                exit()

    def send_to_client(self, tracker_data_response: str, torrent: Mytorrent | None, content: Media, archive_path: str):
        if config_settings.torrent_client_config.SHARED_RTORR_PATH:
            torr_location = config_settings.torrent_client_config.SHARED_RTORR_PATH
        else:
            base = content.torrent_path
            torr_location = os.path.dirname(base) if os.path.isfile(base) else base

        # Normalize for rTorrent
        torr_location = torr_location.replace("\\", "/")

        with open(archive_path, "rb") as file:
            self.client.add_torrent_by_file(
                content=file.read(),
                directory_base=str(torr_location),
                tags=[config_settings.torrent_client_config.TAG],
            )

    def send_file_to_client(self, torrent_path: str, media_location: str):
        with open(torrent_path, "rb") as file:
            self.client.add_torrent_by_file(
                content=file.read(),
                directory_base=str(media_location).replace("\\", "/"),
                tags=[config_settings.torrent_client_config.TAG],
            )
