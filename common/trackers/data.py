# -*- coding: utf-8 -*-

import os

import requests

from common import config_settings


trackers_api_data = {
    'GEMINI':
        {
            "url": config_settings.tracker_config.Gemini_URL,
            "api_key": config_settings.tracker_config.Gemini_APIKEY,
            "pass_key": config_settings.tracker_config.Gemini_PID,
            "announce": f"{config_settings.tracker_config.Gemini_URL}/announce/{config_settings.tracker_config.Gemini_PID}",
            "source": "Gemini",
        }
}


_VIDEO_EXTS = {
    ".mkv", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v",
    ".mpg", ".mpeg", ".ts", ".m2ts", ".vob", ".iso", ".divx",
}


def get_credentials_for_release(release_name: str) -> tuple[str, str]:
    """
    Retourne (pid, api_key) selon le tag d'équipe de la release.

    - Tag dans TAGS_TEAM → Gemini_PID + Gemini_APIKEY (compte KFL)
    - Tag absent ou inconnu → PID_OTHER + APIKEY_OTHER si configurés, sinon fallback sur les valeurs principales
    """
    tags_team: list[str] = [
        t.upper()
        for t in getattr(config_settings.uploader_tag, 'TAGS_TEAM', [])
    ]

    # Le mode -u passe un nom de fichier (ex: "...-KFL.mkv"), alors que -f passe
    # un nom de dossier ("...-KFL"). On retire UNIQUEMENT une vraie extension
    # vidéo connue : os.path.splitext() considérerait à tort un suffixe comme
    # ".x265-KFL" comme une extension et bouferait le tag d'équipe.
    release_base = os.path.basename(release_name or "")
    release_stem, ext = os.path.splitext(release_base)
    if ext.lower() in _VIDEO_EXTS:
        normalized_release = release_stem
    else:
        normalized_release = release_base

    is_kfl_release = False
    parts = normalized_release.rsplit('-', 1)
    if len(parts) == 2 and parts[1].strip().upper() in tags_team:
        is_kfl_release = True

    if is_kfl_release:
        return (
            config_settings.tracker_config.Gemini_PID or '',
            config_settings.tracker_config.Gemini_APIKEY or '',
        )

    # Release non-KFL
    pid_other = getattr(config_settings.uploader_tag, 'PID_OTHER', None)
    apikey_other = getattr(config_settings.uploader_tag, 'APIKEY_OTHER', None)

    return (
        pid_other or config_settings.tracker_config.Gemini_PID or '',
        apikey_other or config_settings.tracker_config.Gemini_APIKEY or '',
    )


def download_torrent_from_url(
    url: str,
    destination_path: str,
    *,
    api_token: str | None = None,
    headers: dict | None = None,
) -> bool:
    """Télécharge un .torrent depuis le tracker (api_token = compte utilisé à l'upload)."""
    params: dict[str, str] = {}
    if api_token:
        params["api_token"] = api_token
    try:
        response = requests.get(
            url,
            params=params or None,
            headers=headers,
            timeout=30,
        )
    except requests.RequestException:
        return False
    if response.status_code == 200:
        with open(destination_path, "wb") as file:
            file.write(response.content)
        return True
    return False


def build_tracker_announces(tracker_name_list: list[str], release_name: str) -> list[list[str]]:
    """URLs d'annonce (str pour torf ; encodage bytes au patch bencode)."""
    announces: list[list[str]] = []
    for tracker in tracker_name_list:
        if not tracker:
            continue
        api_data = trackers_api_data[tracker.upper()]
        pid, _ = get_credentials_for_release(release_name)
        if not pid:
            pid = api_data.get("pass_key") or ""
        base_url = (api_data["url"] or "").rstrip("/")
        if not base_url or not pid:
            continue
        announces.append([f"{base_url}/announce/{pid}"])
    return announces