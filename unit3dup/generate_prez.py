#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script pour générer une présentation BBCode d'un film depuis TMDB
Usage: python generate_prez.py <tmdb_id> [fichier_media]
"""

import sys
import os
import requests
import json
from datetime import datetime
from typing import Optional

# Import de la configuration du projet
try:
    from common.external_services.theMovieDB import config
    TMDB_API_KEY = config.TMDB_APIKEY
except ImportError:
    TMDB_API_KEY = None

# Import de MediaFile pour extraire les infos techniques
try:
    from common.mediainfo import MediaFile
    MEDIAINFO_AVAILABLE = True
except ImportError:
    MEDIAINFO_AVAILABLE = False

TMDB_BASE_URL = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE_URL = "https://image.tmdb.org/t/p"

# Images imgur pour les séparateurs
IMGUR_SEPARATORS = {
    "info": "https://i.imgur.com/C0INBR7.png",
    "synopsis": "https://i.imgur.com/QrFDf9j.png",
    "tech": "https://i.imgur.com/bKVSEME.png",
    "release": "https://i.imgur.com/a8H87Hd.png",
    "note": "https://zupimages.net/up/21/02/fi3f.png",
    "tmdb": "https://zupimages.net/up/21/03/mxao.png",
    "youtube": "https://www.zupimages.net/up/21/02/ogot.png",
}


def get_movie_details(tmdb_id: int) -> Optional[dict]:
    """Récupère les détails d'un film depuis TMDB"""
    url = f"{TMDB_BASE_URL}/movie/{tmdb_id}"
    params = {
        "api_key": TMDB_API_KEY,
        "language": "fr-FR",
        "append_to_response": "credits,videos,images"
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Erreur lors de la récupération des données TMDB: {e}")
        return None


def format_date(date_str: str) -> str:
    """Formate une date au format français"""
    if not date_str:
        return "N/A"
    
    try:
        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
        days = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
        months = ["", "janvier", "février", "mars", "avril", "mai", "juin",
                  "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
        
        day_name = days[date_obj.weekday()]
        month_name = months[date_obj.month]
        
        return f"{day_name} {date_obj.day} {month_name} {date_obj.year}"
    except:
        return date_str


def format_duration(minutes: int) -> str:
    """Formate la durée en heures et minutes"""
    if not minutes:
        return "N/A"
    
    hours = minutes // 60
    mins = minutes % 60
    
    if hours > 0 and mins > 0:
        return f"{hours}h et {mins}min"
    elif hours > 0:
        return f"{hours}h"
    else:
        return f"{mins}min"


def get_countries(movie: dict) -> str:
    """Récupère les pays d'origine"""
    production_countries = movie.get("production_countries", [])
    if not production_countries:
        return "N/A"
    
    countries = [country.get("name", "") for country in production_countries]
    return ", ".join(countries) if countries else "N/A"


def get_director(credits: dict) -> str:
    """Récupère le réalisateur"""
    crew = credits.get("crew", [])
    directors = [person["name"] for person in crew if person.get("job") == "Director"]
    return ", ".join(directors) if directors else "N/A"


def get_cast(credits: dict, max_actors: int = 6) -> str:
    """Récupère les acteurs principaux"""
    cast = credits.get("cast", [])
    actors = [actor["name"] for actor in cast[:max_actors]]
    return ", ".join(actors) if actors else "N/A"


def get_genres(movie: dict) -> str:
    """Récupère les genres"""
    genres = movie.get("genres", [])
    genre_names = [genre.get("name", "") for genre in genres]
    return ", ".join(genre_names) if genre_names else "N/A"


def get_trailer_url(videos: dict) -> Optional[str]:
    """Récupère l'URL de la bande-annonce YouTube (Trailer officiel → Teaser → Clip)."""
    from common.external_services.theMovieDB.core.trailer_select import pick_youtube_trailer_key

    key = pick_youtube_trailer_key(videos.get("results", []))
    return f"https://www.youtube.com/watch?v={key}" if key else None


def get_cast_images(credits: dict, max_images: int = 4) -> str:
    """Génère les images des acteurs principaux"""
    cast = credits.get("cast", [])
    images = []
    
    for actor in cast[:max_images]:
        profile_path = actor.get("profile_path")
        if profile_path:
            img_url = f"{TMDB_IMAGE_BASE_URL}/w138_and_h175_face{profile_path}"
            images.append(f"[img]{img_url}[/img]")
    
    return "  ".join(images) if images else ""


def get_technical_info(media_file_path: Optional[str]) -> dict:
    """Extrait les informations techniques depuis Mediainfo"""
    tech_info = {
        "qualite": "",
        "format": "",
        "codec_video": "",
        "debit_video": "",
        "langues": "",
        "sous_titres": ""
    }
    
    if not media_file_path or not os.path.exists(media_file_path):
        return tech_info
    
    if not MEDIAINFO_AVAILABLE:
        print("Attention: Mediainfo n'est pas disponible. Les infos techniques ne seront pas extraites.")
        return tech_info
    
    try:
        media_file = MediaFile(media_file_path)
        
        # Qualité (résolution)
        height = media_file.video_height
        if height:
            try:
                height_int = int(height)
                if height_int >= 2160:
                    tech_info["qualite"] = "2160p (4K)"
                elif height_int >= 1080:
                    tech_info["qualite"] = "1080p"
                elif height_int >= 720:
                    tech_info["qualite"] = "720p"
                elif height_int >= 480:
                    tech_info["qualite"] = "480p"
                else:
                    tech_info["qualite"] = f"{height_int}p"
            except (ValueError, TypeError):
                tech_info["qualite"] = f"{height}p"
        
        # Format (container) - utiliser l'extension du fichier si disponible
        general_track = media_file.general_track
        if general_track:
            format_name = general_track.get("format", "")
            if format_name:
                tech_info["format"] = format_name.upper()
        
        # Si pas de format dans general_track, utiliser l'extension
        if not tech_info["format"]:
            _, ext = os.path.splitext(media_file_path)
            if ext:
                tech_info["format"] = ext.upper().replace(".", "")
        
        # Codec Vidéo
        video_track = media_file.video_track
        if video_track and len(video_track) > 0:
            codec = video_track[0].get("format", "")
            codec_id = video_track[0].get("codec_id", "")
            if codec:
                tech_info["codec_video"] = codec
                if codec_id and codec_id != codec:
                    tech_info["codec_video"] += f" ({codec_id})"
            
            # Débit Vidéo
            bit_rate = video_track[0].get("bit_rate", "")
            if bit_rate:
                try:
                    bit_rate_int = int(bit_rate)
                    if bit_rate_int >= 1000000:
                        tech_info["debit_video"] = f"{bit_rate_int / 1000000:.2f} Mbps"
                    else:
                        tech_info["debit_video"] = f"{bit_rate_int / 1000:.2f} Kbps"
                except (ValueError, TypeError):
                    tech_info["debit_video"] = str(bit_rate)
        
        # Langues
        languages = media_file.available_languages
        if languages and languages != ["not found"]:
            tech_info["langues"] = ", ".join(languages)
        
        # Sous-titres
        subtitle_tracks = media_file.subtitle_track
        if subtitle_tracks:
            sub_langs = []
            for sub in subtitle_tracks:
                lang = sub.get("language", "")
                if lang:
                    sub_langs.append(lang)
            if sub_langs:
                tech_info["sous_titres"] = ", ".join(sub_langs)
            else:
                tech_info["sous_titres"] = "Aucun"
        else:
            tech_info["sous_titres"] = "Aucun"
            
    except Exception as e:
        print(f"Erreur lors de l'extraction des infos Mediainfo: {e}")
    
    return tech_info


def generate_prez(tmdb_id: int, media_file_path: Optional[str] = None) -> Optional[str]:
    """Génère la présentation BBCode complète"""
    movie = get_movie_details(tmdb_id)
    
    if not movie:
        return None
    
    # Extraction des infos techniques depuis Mediainfo
    tech_info = get_technical_info(media_file_path)
    
    # Récupération des données
    title = movie.get("title", "N/A")
    original_title = movie.get("original_title", "N/A")
    overview = movie.get("overview", "N/A")
    release_date = format_date(movie.get("release_date", ""))
    runtime = format_duration(movie.get("runtime", 0))
    countries = get_countries(movie)
    vote_average = movie.get("vote_average", 0)
    poster_path = movie.get("poster_path", "")
    poster_url = f"{TMDB_IMAGE_BASE_URL}/w500{poster_path}" if poster_path else ""
    
    credits = movie.get("credits", {})
    director = get_director(credits)
    cast = get_cast(credits)
    genres = get_genres(movie)
    cast_images = get_cast_images(credits)
    
    videos = movie.get("videos", {})
    trailer_url = get_trailer_url(videos)
    
    tmdb_url = f"https://www.themoviedb.org/movie/{tmdb_id}"
    
    # Génération du BBCode
    prez = f"""[center][size=200][color=#aa0000][b]{title}[/b][/color][/size]
 
 
[img]{poster_url}[/img]
 
 
 
[img]{IMGUR_SEPARATORS['info']}[/img]
 
[b]Origines :[/b] {countries}
[b]Sortie :[/b] {release_date}
[b]Titre original :[/b] {original_title}
[b]Durée :[/b] {runtime}
 
[b]Réalisateur :[/b] {director}
 
[b]Acteurs :[/b] 
{cast}
 
[b]Genre :[/b]
{genres}
 
[img]{IMGUR_SEPARATORS['note']}[/img] {vote_average:.2f}
 
[img]{IMGUR_SEPARATORS['tmdb']}[/img] [url={tmdb_url}]Fiche du film[/url]"""
    
    if trailer_url:
        prez += f"\n[img]{IMGUR_SEPARATORS['youtube']}[/img] [url={trailer_url}]Bande annonce[/url]"
    
    prez += f"""
 
 
[img]{IMGUR_SEPARATORS['synopsis']}[/img]
 
{overview}
"""
    
    if cast_images:
        prez += f"\n {cast_images}"
    
    prez += f"""
 
[img]{IMGUR_SEPARATORS['tech']}[/img]
 
[b]Qualité :[/b] {tech_info['qualite'] if tech_info['qualite'] else ''}
[b]Format :[/b] {tech_info['format'] if tech_info['format'] else ''}
[b]Codec Vidéo :[/b] {tech_info['codec_video'] if tech_info['codec_video'] else ''}
[b]Débit Vidéo :[/b] {tech_info['debit_video'] if tech_info['debit_video'] else ''}
 
[b]Langue(s) :[/b] {tech_info['langues'] if tech_info['langues'] else ''}

 
[b]Sous-titres :[/b] {tech_info['sous_titres'] if tech_info['sous_titres'] else ''}

 

 
 
[img]{IMGUR_SEPARATORS['release']}[/img]
 
[b]Source / Release :[/b] 
[b]Nombre de fichier(s) :[/b] 
[b]Poids Total :[/b]  Go"""
    
    return prez


def main():
    if len(sys.argv) < 2:
        print("Usage: python generate_prez.py <tmdb_id> [fichier_media]")
        print("Exemple: python generate_prez.py 16235 /path/to/movie.mkv")
        print("         python generate_prez.py 16235  (sans fichier média)")
        sys.exit(1)
    
    try:
        tmdb_id = int(sys.argv[1])
    except ValueError:
        print("Erreur: L'ID TMDB doit être un nombre entier")
        sys.exit(1)
    
    # Récupérer le chemin du fichier média si fourni
    media_file_path = sys.argv[2] if len(sys.argv) > 2 else None
    
    # Vérifier si la clé API est configurée
    if not TMDB_API_KEY:
        print("ERREUR: Clé API TMDB non trouvée")
        print("Veuillez configurer votre clé API TMDB dans la configuration du projet")
        sys.exit(1)
    
    if media_file_path:
        if not os.path.exists(media_file_path):
            print(f"Attention: Le fichier média '{media_file_path}' n'existe pas")
            print("Les informations techniques ne seront pas extraites")
            media_file_path = None
        else:
            print(f"Fichier média: {media_file_path}")
    
    print(f"Génération de la présentation pour le film TMDB ID: {tmdb_id}...")
    prez = generate_prez(tmdb_id, media_file_path)
    
    if prez:
        print("\n" + "="*80)
        print("PRÉSENTATION GÉNÉRÉE:")
        print("="*80 + "\n")
        print(prez)
        print("\n" + "="*80)
        
        # Option pour sauvegarder dans un fichier
        save = input("\nVoulez-vous sauvegarder dans un fichier? (o/n): ").lower()
        if save == 'o':
            filename = f"prez_{tmdb_id}.txt"
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(prez)
            print(f"Présentation sauvegardée dans {filename}")
    else:
        print("Erreur: Impossible de générer la présentation")
        sys.exit(1)


if __name__ == "__main__":
    main()
