# -*- coding: utf-8 -*-

gemini_data = {
    # Tableau officiel G3mini / gemini-tracker.org (Film TMDB / Série TMDB)
    "CATEGORY": {
        "movie": 1,
        "tv": 2,
        "movie_documentary": 13,
        "tv_documentary": 14,
        "movie_animation": 7,
        "tv_animation": 6,
        "tv_emission": 15,
        "edicola": 12,
        "game": 3,
    },

    "FREELECH":{ "size20": 100,
                 "size15": 75,
                 "size10": 50,
                 "size5": 25},

    "TYPE_ID": {
                # ── Vidéo ───────────────────────────────────────────────────
                "full-disc":    1,   # FULL DISC
                "remux":        2,   # REMUX
                "bdremux":      2,
                "untouched":    2,
                "bd-untouched": 2,
                "bdrip":        3,   # BDRip
                "bluray":       3,
                "mhd":          3,
                "web-dl":       4,   # WEB
                "webdl":        4,
                "web":          4,
                "web-dlmux":    4,
                "webrip":       5,   # WEBRip
                "hdtv":         6,   # HDTV
                "iso":          7,   # ISO
                "dvd":          11,  # DVD
                "tvrip":        12,  # TVRip
                "dvdrip":       13,  # DVDRip
                "vhsrip":       38,  # VHSRip
                "vh":           38,
                "vu":           38,
                # ── Jeux ────────────────────────────────────────────────────
                "pc":           16,  # Jeux\Windows
                "xbox":         20,  # Jeux\Xbox
                "nintendo":     21,  # Jeux\Nintendo
                "nsw":          21,
                "ps4":          23,  # Jeux\Sony
                "ps5":          23,
                "psn":          23,
                "psvr":         23,
                # ── Livres ──────────────────────────────────────────────────
                "epub":         27,  # Livres
                "pdf":          27,
                "cbr-cbz":      29,  # Livres\Comics
                # ── Logiciels ───────────────────────────────────────────────
                "windows":      34,  # Logiciels\Windows
                "mac":          35,  # Logiciels\MacOS
                "macos":        35,
                "linux":        36,  # Logiciels\Linux
                "altro":        37,  # Logiciels\Autres
    },
    "TYPE_ID_AUDIO":{ "flac": 7,
                      "alac": 8,
                      "ac3": 9,
                      "aac": 10,
                      "mp3": 11,
    },
    "TAGS":{
             "SD": 1,
             "HD": 0,
    },
    "RESOLUTION": {
                    "4320p": 1,
                    "2160p": 2,
                    "1080p": 3,
                    "1080i": 4,
                    "720p": 5,
                    "576p": 6,
                    "576i": 7,
                    "480p": 8,
                    "480i": 9,
                    "altro": 10,
    },
    "CODEC": [
                "h261",
                "h262",
                "h263",
                "h264",
                "x264",
                "x265",
                "avc",
                "h265",
                "hevc",
                "vp8",
                "vp9",
                "av1",
                "mpeg-1",
                "mpeg-4",
                "wmv",
                "theora",
                "divx",
                "xvid",
                "prores",
                "dnxhd",
                "cinepak",
                "indeo",
                "dv",
                "ffv1",
                "sorenson",
                "rv40",
                "cineform",
                "huffyuv",
                "mjpeg",
                "lagarith",
                "msu",
                "rle",
                "dirac",
                "wmv3",
                "vorbis",
                "smpte",
                "mjpeg",
                "ffvhuff",
                "v210",
                "yuv4:2:2",
                "yuv4:4:4",
                "hap",
                "sheervideo",
                "ut",
                "quicktime",
                "rududu",
                "h.266",
                "vvc",
                "mjpeg 4:2:0",
                "h.263+",
                "h.263++",
                "vp4",
                "vp5",
                "vp6",
                "vp7",
                "vp8",
                "vp9",
                "vp10",
                "vp11",
                "vp12",
                "vp3",
                "vp2",
                "vp1",
                "amv",
                "daala",
                "gecko",
                "nvenc",
                "bluray",
            ],
}
