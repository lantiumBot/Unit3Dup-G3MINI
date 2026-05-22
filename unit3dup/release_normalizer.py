# -*- coding: utf-8 -*-
"""
release_normalizer.py — Normalisation des noms de release pour G3MINI Tracker

Portage Python du script g3mini_rename.sh
Ne renomme aucun fichier — agit uniquement sur le champ release_name.
"""

import re
from typing import Optional


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _normalize_lang(raw: str) -> str:
    r = raw.upper()
    if r == "TRUEFRENCH":                   return "VFF"
    if re.match(r'^VFF-', r):               return "MULTi.VFF"
    if re.match(r'^VFQ-', r):               return "MULTi.VFQ"
    if re.match(r'^VF2-', r):               return "MULTi.VF2"
    if re.match(r'^VFB-', r):               return "MULTi.VFB"
    if r in ("MULTI.VFF", "MULTI-VFF"):     return "MULTi.VFF"
    if r in ("MULTI.VFQ", "MULTI-VFQ"):     return "MULTi.VFQ"
    if r in ("MULTI.VF2", "MULTI-VF2"):     return "MULTi.VF2"
    if r in ("MULTI.VFB", "MULTI-VFB"):     return "MULTi.VFB"
    if r in ("MULTI", "MULTIC"):            return "MULTi"
    if r in ("FRENCH", "VFF", "VFI"):       return "VFF"
    if r == "VFQ":                          return "VFQ"
    if r == "VF2":                          return "VF2"
    if r == "VFB":                          return "VFB"
    if r == "VOF":                          return "VOF"
    if r == "VOQ":                          return "VOQ"
    if r == "VOB":                          return "VOB"
    if r == "VOSTFR":                       return "VOSTFR"
    if r == "SUBFRENCH":                    return "SUBFRENCH"
    return raw


def _normalize_source(raw: str) -> str:
    r = raw.upper()
    if r in ("BLURAY", "BLU-RAY"):          return "BluRay"
    if r == "BDRIP":                        return "BDRip"
    if r == "4KLIGHT":                      return "4KLight"
    if r in ("HDLIGHT", "MHD"):             return "HDLight"
    if r == "WEBRIP":                       return "WEBRip"
    if r in ("WEB-DL", "WEBDL", "WEB"):     return "WEB"
    if r == "HDRIP":                        return "HDRip"
    if r == "HDTV":                         return "HDTV"
    if r in ("TVRIP", "TVHDRIP"):           return "TVRip"
    if r in ("DVDRIP", "DVD"):              return "DVDRip"
    if r == "REMUX":                        return "REMUX"
    return raw


def _clean_title(t: str) -> str:
    t = t.strip()
    t = t.replace(" ", ".")
    t = re.sub(r'[^a-zA-Z0-9._-]', '', t)
    t = re.sub(r'\.{2,}', '.', t)
    t = t.strip('.')
    return t


def _remove_token(s: str, tok: str) -> str:
    """Supprime toutes les occurrences d'un token (insensible à la casse).
    Gère début, milieu et fin de chaîne. Compacte les espaces résiduels."""
    tok_esc = re.escape(tok)
    result = re.sub(r'(^|\s)' + tok_esc + r'(\s|$)', ' ', s, flags=re.IGNORECASE)
    return re.sub(r' {2,}', ' ', result).strip()


def _ws(s: str) -> str:
    """Compactage simple des espaces."""
    return re.sub(r' {2,}', ' ', s).strip()


def _format_season_token(raw: str) -> str:
    m = re.match(r'S(\d{1,2})', raw, re.IGNORECASE)
    return f"S{int(m.group(1)):02d}" if m else raw.upper()


def _format_episode_token(raw: str) -> str:
    m = re.match(r'S(\d{1,2})E(\d{1,3})', raw, re.IGNORECASE)
    if not m:
        return raw.upper()
    return f"S{int(m.group(1)):02d}E{int(m.group(2)):02d}"


def _extract_tv_season_episode(name: str) -> tuple[str, str, str]:
    """
    Extrait SxxEyy ou Sxx du nom (espaces). Retourne (nom_restant, saison, épisode).
    """
    episode = ""
    season = ""

    m = re.search(r'(?:^|\s)(S\d{1,2}E\d{1,3})(?:\s|$)', name, re.IGNORECASE)
    if m:
        episode = _format_episode_token(m.group(1))
        name = _remove_token(name, m.group(1))
        return _ws(name), season, episode

    m = re.search(r'(?:^|\s)(S\d{1,2})(?:\s|$)', name, re.IGNORECASE)
    if m:
        season = _format_season_token(m.group(1))
        name = _remove_token(name, m.group(1))

    return _ws(name), season, episode


# ══════════════════════════════════════════════════════════════════════════════
#  MEDIAINFO PARSERS
# ══════════════════════════════════════════════════════════════════════════════

def _get_codec_from_mediainfo(mi: str) -> str:
    """Détecte le codec vidéo depuis le texte brut MediaInfo."""
    if not mi:
        return ""
    # Isoler le bloc Video (jusqu'au prochain bloc ou fin)
    m = re.search(r'^Video.*?(?=\n(?:Audio|Text|Menu|General)|\Z)', mi,
                  re.MULTILINE | re.DOTALL)
    if not m:
        return ""
    block = m.group(0)

    # Encoded_Library_Name (underscore ou espace selon version MediaInfo)
    lib_m = re.search(r'Encoded[\s_]library[\s_]name\s*:\s*(.+)', block, re.IGNORECASE)
    if lib_m:
        lib = lib_m.group(1).strip().upper()
        if lib.startswith("X264"):  return "x264"
        if lib.startswith("X265"):  return "x265"

    # Format (première ligne du bloc)
    fmt_m = re.search(r'^Format\s*:\s*(.+)', block, re.MULTILINE | re.IGNORECASE)
    if fmt_m:
        fmt = fmt_m.group(1).strip().upper()
        return {
            "AVC":    "x264",
            "HEVC":   "x265",
            "AV1":    "AV1",
            "VP9":    "VP9",
            "MPEG-2": "MPEG-2",
            "MPEG2":  "MPEG-2",
            "MPEG":   "MPEG-2",
            "VC-1":   "VC-1",
            "VC1":    "VC-1",
        }.get(fmt, "")
    return ""


def _has_encode_library(mi: str) -> bool:
    """Retourne True uniquement si la writing library confirme un encode x264/x265.
    Ne se base PAS sur le champ Format — réservé aux vrais encodes (pas untouched)."""
    if not mi:
        return False
    m = re.search(r'^Video.*?(?=\n(?:Audio|Text|Menu|General)|\Z)', mi,
                  re.MULTILINE | re.DOTALL)
    if not m:
        return False
    block = m.group(0)
    # Writing library (ligne "Writing library" ou "Encoded library name")
    for pattern in (
        r'Writing\s+library\s*:\s*(.+)',
        r'Encoded[\s_]library[\s_]name\s*:\s*(.+)',
    ):
        lib_m = re.search(pattern, block, re.IGNORECASE)
        if lib_m:
            lib = lib_m.group(1).strip().upper()
            if lib.startswith("X264") or lib.startswith("X265"):
                return True
    return False


def _get_lang_from_mediainfo(mi: str) -> str:
    """Retourne le tag de langue dominant (sans préfixe MULTi) :
    VFF, VFQ, VF2, VFB, VOF, VOQ, VOB ou '' """
    if not mi:
        return ""
    vff = vfq = vfb = vof = voq = vob = False
    for line in mi.splitlines():
        if   re.search(r'Language\s*:\s*French\s*\(FR\)', line, re.IGNORECASE):                             vff = True
        elif re.search(r'Language\s*:\s*French\s*\(CA\)', line, re.IGNORECASE):                             vfq = True
        elif re.search(r'Title\s*:.*\b(VFF|VFI|TrueFrench|French\s*\(France\))\b', line, re.IGNORECASE):   vff = True
        elif re.search(r'Title\s*:.*\b(VFB|French\s*\(Belgique\))\b', line, re.IGNORECASE):                vfb = True
        elif re.search(r'Title\s*:.*\b(VOF)\b', line, re.IGNORECASE):                                      vof = True
        elif re.search(r'Title\s*:.*\b(VFQ|French\s*\(Canadien\))\b', line, re.IGNORECASE):                vfq = True
        elif re.search(r'Title\s*:.*\b(VOQ|French\s*\(Québec\))\b', line, re.IGNORECASE):                  voq = True
        elif re.search(r'Title\s*:.*\b(VOB|French\s*\(Belgique\s*VO\))\b', line, re.IGNORECASE):           vob = True

    if vff and vfq: return "VF2"
    if vff:         return "VFF"
    if vfq:         return "VFQ"
    if vfb:         return "VFB"
    if vof:         return "VOF"
    if voq:         return "VOQ"
    if vob:         return "VOB"
    return ""


def _get_subfr_from_mediainfo(mi: str) -> str:
    """Retourne 'yes', 'no' ou 'unknown' selon la présence de ST français."""
    if not mi:
        return "unknown"
    in_text = False
    for line in mi.splitlines():
        if re.match(r'^Text\s*$|^Text #', line):
            in_text = True
        elif re.match(r'^(Video|Audio|General|Menu)', line):
            in_text = False
        if in_text and re.search(r'Language\s*:\s*(French|fr)\b', line, re.IGNORECASE):
            return "yes"
    return "no"

def _is_silent_from_mediainfo(mi: str) -> bool:
    """Retourne True si toutes les pistes audio ont Language: zxx (film muet).
    zxx est le code ISO 639-2 pour 'No linguistic content'."""
    if not mi:
        return False
    in_audio = False
    audio_langs = []
    for line in mi.splitlines():
        if re.match(r'^Audio\s*$|^Audio #', line):
            in_audio = True
        elif re.match(r'^(Video|Text|Menu|General)', line):
            in_audio = False
        if in_audio:
            m = re.search(r'Language\s*:\s*(\S+)', line, re.IGNORECASE)
            if m:
                audio_langs.append(m.group(1).strip().lower())
    return bool(audio_langs) and all(l == 'zxx' for l in audio_langs)


# Priorité audio (plus haut = codec « plus gros », conservé si plusieurs pistes).
_AUDIO_RANK: dict[str, int] = {
    "AAC": 10,
    "AAC2.0": 20,
    "AAC5.1": 25,
    "AC3": 30,
    "DDP": 40,
    "DDP2.0": 50,
    "DDP5.1": 60,
    "DDP7.1": 70,
    "DTS": 80,
    "DTS-HD": 90,
    "DTS-HD.MA": 95,
    "Atmos": 100,
    "TrueHD": 110,
    "TrueHD.Atmos": 120,
}


def _audio_rank(tag: str) -> int:
    if not tag:
        return 0
    if tag in _AUDIO_RANK:
        return _AUDIO_RANK[tag]
    base = tag.split(".", 1)[0]
    rank = _AUDIO_RANK.get(base, _AUDIO_RANK.get(tag, 0))
    if re.search(r'7\.1|7ch', tag):
        rank += 8
    elif re.search(r'5\.1|6ch', tag):
        rank += 5
    elif re.search(r'2\.0|2ch', tag):
        rank += 2
    return rank


def _pick_best_audio(tags: list[str]) -> str:
    tags = [t for t in tags if t]
    if not tags:
        return ""
    return max(tags, key=_audio_rank)


def _channel_audio_suffix(ch: int) -> str:
    if ch <= 2:
        return "2.0"
    if ch == 6:
        return "5.1"
    if ch >= 8:
        return "7.1"
    if ch > 0:
        return f"{ch}ch"
    return ""


def _audio_tag_from_mediainfo_block(block: str) -> str:
    fmt = ""
    ch = 0
    title = ""
    for line in block.splitlines():
        m = re.match(r'\s*Format\s*:\s*(.+)', line, re.IGNORECASE)
        if m:
            fmt = m.group(1).strip()
        m = re.match(r'\s*Channel\(s\)\s*:\s*(\d+)', line, re.IGNORECASE)
        if m:
            ch = int(m.group(1))
        m = re.match(r'\s*Commercial name\s*:\s*(.+)', line, re.IGNORECASE)
        if m and not title:
            title = m.group(1).strip()
        m = re.match(r'\s*Title\s*:\s*(.+)', line, re.IGNORECASE)
        if m:
            title = m.group(1).strip()

    blob = f"{fmt} {title}".upper()
    ch_s = _channel_audio_suffix(ch)

    if re.search(r'ATMOS', blob):
        if re.search(r'TRUEHD|MLP', blob):
            return "TrueHD.Atmos"
        return "Atmos"
    if re.search(r'TRUEHD|MLP FRIENDLY', blob):
        return "TrueHD"
    if re.search(r'MASTER AUDIO|DTS-HD MA', blob):
        return f"DTS-HD.MA{('.' + ch_s) if ch_s else ''}"
    if re.search(r'DTS-HD', blob):
        return f"DTS-HD{('.' + ch_s) if ch_s else ''}"
    if re.search(r'\bDTS\b', fmt, re.IGNORECASE):
        return "DTS"
    if re.search(r'E-AC-3|EAC3|DD\+|ENHANCED AC-3', blob):
        return f"DDP{ch_s}" if ch_s else "DDP"
    if re.search(r'AC-3|AC3|DOLBY DIGITAL', blob):
        return "AC3"
    if re.search(r'\bAAC\b', fmt, re.IGNORECASE):
        return f"AAC{ch_s}" if ch_s else "AAC"
    return ""


def _get_audio_tags_from_mediainfo(mi: str) -> list[str]:
    if not mi:
        return []
    tags: list[str] = []
    for m in re.finditer(
        r'^Audio(?:\s+#\d+)?\s*\n(.*?)(?=^(?:Audio|Video|Text|Menu|General)\b|\Z)',
        mi,
        re.MULTILINE | re.DOTALL | re.IGNORECASE,
    ):
        tag = _audio_tag_from_mediainfo_block(m.group(0))
        if tag:
            tags.append(tag)
    return tags


def _get_best_audio_from_mediainfo(mi: str) -> str:
    return _pick_best_audio(_get_audio_tags_from_mediainfo(mi))


def _collect_audio_tags_from_name(name: str) -> tuple[str, list[str]]:
    """Détecte tous les tags audio dans le nom ; retourne (nom_restant, tags)."""
    found: list[str] = []

    if re.search(r'DTS-HDMA|DTS[-. ]?HD[-. ]?MA', name, re.IGNORECASE):
        name = re.sub(r'DTS-HDMA|DTS[-. ]?HD[-. ]?MA', ' ', name, flags=re.IGNORECASE)
        name = _ws(name)
        mo = re.search(r'(?:^|\s)([0-9][.][0-9])(?:\s|$)', name)
        dts_ch = f".{mo.group(1)}" if mo else ""
        if mo:
            name = re.sub(r'(?:^|\s)[0-9][.][0-9](?:\s|$)', ' ', name)
        found.append(f"DTS-HD.MA{dts_ch}")

    if re.search(r'(?:^|\s)DTS-HD(?:\s|$)', name, re.IGNORECASE):
        name = _remove_token(name, "DTS-HD")
        mo = re.search(r'(?:^|\s)([0-9][.][0-9])(?:\s|$)', name)
        dts_ch = f".{mo.group(1)}" if mo else ""
        if mo:
            name = re.sub(r'(?:^|\s)[0-9][.][0-9](?:\s|$)', ' ', name)
        found.append(f"DTS-HD{dts_ch}")

    if re.search(r'(?:^|\s)(?:AC3-DTS|DTS-AC3)(?:\s|$)', name, re.IGNORECASE):
        found.append("DTS")
        name = re.sub(r'(?:^|\s)(?:AC3-DTS|DTS-AC3)(?:\s|$)', ' ', name, flags=re.IGNORECASE)

    if re.search(r'(?:^|\s)DTS(?:\s|$)', name, re.IGNORECASE):
        found.append("DTS")
        name = _remove_token(name, "DTS")

    if re.search(r'(?:^|\s)TrueHD\s+Atmos(?:\s|$)', name, re.IGNORECASE):
        found.append("TrueHD.Atmos")
        name = re.sub(r'(?:^|\s)TrueHD\s+Atmos(?:\s|$)', ' ', name, flags=re.IGNORECASE)
    elif re.search(r'(?:^|\s)TrueHD(?:\s|$)', name, re.IGNORECASE):
        found.append("TrueHD")
        name = _remove_token(name, "TrueHD")

    if re.search(r'(?:^|\s)Atmos(?:\s|$)', name, re.IGNORECASE):
        found.append("Atmos")
        name = _remove_token(name, "Atmos")

    for pat, tag in (
        (r'(?:^|\s)DDP\s*7\.1(?:\s|$)', "DDP7.1"),
        (r'(?:^|\s)DDP\s*5\.1(?:\s|$)', "DDP5.1"),
        (r'(?:^|\s)DDP\s*2\.0(?:\s|$)', "DDP2.0"),
        (r'(?:^|\s)E-?AC-?3\s*7\.1(?:\s|$)', "DDP7.1"),
        (r'(?:^|\s)E-?AC-?3\s*5\.1(?:\s|$)', "DDP5.1"),
        (r'(?:^|\s)E-?AC-?3\s*2\.0(?:\s|$)', "DDP2.0"),
        (r'(?:^|\s)DDP(?:\s|$)', "DDP"),
        (r'(?:^|\s)E-?AC-?3(?:\s|$)', "DDP"),
    ):
        if re.search(pat, name, re.IGNORECASE):
            found.append(tag)
            name = re.sub(pat, ' ', name, flags=re.IGNORECASE)
            name = _ws(name)

    if re.search(r'(?:^|\s)AC3[-. ][0-9]', name, re.IGNORECASE):
        found.append("AC3")
        name = re.sub(r'(?:^|\s)AC3[-. ][0-9](?:[. ][0-9])?(?:\s|$)', ' ', name, flags=re.IGNORECASE)
    elif re.search(r'(?:^|\s)AC3(?:\s|$)', name, re.IGNORECASE):
        found.append("AC3")
        name = _remove_token(name, "AC3")

    for pat, tag in (
        (r'(?:^|\s)AAC\s*5\.1(?:\s|$)', "AAC5.1"),
        (r'(?:^|\s)AAC\s*2\.0(?:\s|$)', "AAC2.0"),
        (r'(?:^|\s)AAC(?:\s|$)', "AAC"),
    ):
        if re.search(pat, name, re.IGNORECASE):
            found.append(tag)
            name = re.sub(pat, ' ', name, flags=re.IGNORECASE)
            name = _ws(name)

    name = re.sub(r'(?:^|\s)[0-9][.][0-9](?:\s|$)', ' ', name)
    return _ws(name), found


# ══════════════════════════════════════════════════════════════════════════════
#  CONSTANTES DE PARSING
# ══════════════════════════════════════════════════════════════════════════════

# Séparateurs utilisés en step 5b pour décoller les tokens collés.
# Ordre important : plus long avant plus court dans chaque famille.
_TAGS = (
    r'BluRay|BDRip|WEBRip|WEB|4KLight|HDLight|HDRip|TVRip|DVDRip|HDTV|REMUX|CAM'
    r'|2160p|1080p|1080i|720p|576p|480p|4K|UHD'
    r'|HDR10P|HDR10|SDR|DV|HLG|PQ10|HDR'
    r'|x265|x264|H265|H264|HEVC|AVC|AV1|VP9|VC1'
    r'|DTS-HDMA|DTS-HD|DTS|AC3|DDP|TrueHD|Atmos|AAC'
    r'|MULTi|VFF|VFQ|VF2|VFB|VOSTFR|SUBFRENCH|VOF|VOQ|VOB|FRENCH'
    r'|EXTENDED|PROPER|REPACK|UNRATED|UNCUT|REMASTERED|INTERNAL|NoTAG|iNTEGRALE'
    r'|8bit|10bit|12bit'
)

# Du plus spécifique au moins spécifique
_LANG_PATTERNS = [
    r'VFF-[A-Za-z]+(?:-[A-Za-z]+)*',
    r'VFQ-[A-Za-z]+(?:-[A-Za-z]+)*',
    r'VFB-[A-Za-z]+(?:-[A-Za-z]+)*',
    r'VF2-[A-Za-z]+(?:-[A-Za-z]+)*',
    r'MULTi\.VFF', r'MULTi\.VFQ', r'MULTi\.VF2', r'MULTi\.VFB',
    r'MULTi',
    r'FRENCH', r'VFF', r'VFQ', r'VF2', r'VFB',
    r'VOF', r'VOQ', r'VOB',
    r'VOSTFR', r'SUBFRENCH',
]

_EXTRAS_MAP = {
    'EXTENDED':      'EXTENDED',
    'THEATRICAL':    'THEATRICAL',
    'PROPER':        'PROPER',
    'REPACK':        'REPACK',
    'UNRATED':       'UNRATED',
    'UNCUT':         'UNCUT',
    'REMASTERED':    'REMASTERED',
    'INTERNAL':      'INTERNAL',
    'NOTAG':         'NoTAG',
    'INTEGRALE':     'iNTEGRALE',
    'LIMITED':       'LIMITED',
    'IMAX EDITION':  'IMAX.EDITION',
    'IMAX':          'IMAX',
}

_CODEC_LIST = [
    ("x265",   "x265"),
    ("x264",   "x264"),
    ("H\\.265", "H.265"),
    ("H\\.264", "H.264"),
    ("H265",   "H.265"),
    ("H264",   "H.264"),
    ("HEVC",   "HEVC"),
    ("AVC",    "AVC"),
    ("AV1",    "AV1"),
    ("VP9",    "VP9"),
    ("MPEG-2", "MPEG-2"),
    ("VC-1",   "VC-1"),
]

# Sources testées du plus spécifique au moins spécifique.
# "UHD BluRay" (espace) car les points ont été convertis en step 3.
# NOTE: 4KLight et HDLight sont gérés SÉPARÉMENT (source_qual) avant cette liste.
_SOURCE_LIST = [
    "UHD BluRay",
    "BluRay", "Blu-Ray",
    "BDRip",
    "WEB-DL", "WEBRip",
    "HDTV", "HDRip", "TVRip",
    "WEB", "DVDRip", "DVD",
]

# Qualificatifs de source : peuvent coexister avec une source principale.
# Ex: "4KLight BluRay" → source = "4KLight.BluRay"
_SOURCE_QUAL_LIST = ["4KLight", "HDLight", "mHD"]

# Platforms
_PLATFORM_LIST = ["AMZN", "NF", "DSNP", "HULU", "ATVP", "PCOK", "MAX", "HBO"]

_NON_TEAM_SUFFIXES = {
    # Extensions / contenants
    "MKV", "MP4", "AVI", "M2TS", "TS", "ISO",
    # Resolution / video tags
    "2160P", "1080P", "1080I", "720P", "576P", "480P", "4K", "UHD",
    "X264", "X265", "H264", "H265", "AVC", "HEVC", "AV1", "VP9", "VC1", "MPEG2",
    # Sources
    "WEB", "WEBRIP", "WEBDL", "BLURAY", "BDRIP", "HDRIP", "HDTV", "TVRIP", "DVDRIP", "REMUX",
    # Langues
    "FRENCH", "MULTI", "MULTIC", "VFF", "VFQ", "VF2", "VFB", "VOSTFR", "SUBFRENCH", "VOF", "VOQ", "VOB",
    # Audio
    "DTS", "DTSHD", "DTSHDMA", "AC3", "DDP", "TRUEHD", "ATMOS", "AAC",
    # HDR / extras courants
    "HDR", "HDR10", "HDR10P", "DV", "HLG", "SDR", "NOTAG",
}


def _is_team_suffix_candidate(suffix: str) -> bool:
    s = suffix.upper()
    # Tokens épisode/saison et autres suffixes techniques ne sont pas des teams.
    if re.fullmatch(r'S\d{1,2}E\d{1,3}', s):
        return False
    if re.fullmatch(r'S\d{1,2}', s):
        return False
    return s not in _NON_TEAM_SUFFIXES


# ══════════════════════════════════════════════════════════════════════════════
#  PARSER PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

def _parse_release(
    original: str,
    mi: Optional[str] = None,
    is_silent: bool = False,
    tv_year: Optional[int] = None,
    torrent_pack: bool = False,
) -> str:
    name = original

    # ── 1. Extension ─────────────────────────────────────────────────────────
    ext = ""
    m = re.search(r'\.(mkv|mp4|avi|ts|m2ts|iso)$', name, re.IGNORECASE)
    if m:
        ext = "." + m.group(1)
        name = name[:-len(ext)]

    # ── 2. Team ───────────────────────────────────────────────────────────────
    # Extrait avant la normalisation des séparateurs.
    # name_team = nom sans les parens de fin (ex: "(58 minutes pour vivre)")
    team = ""
    name_team = re.sub(r'\s*\([^)]*\)\s*$', '', name)
    m = re.search(r'-([A-Za-z0-9@_]+)$', name_team)
    if m:
        raw_team = m.group(1)
        team = re.sub(r'[^a-zA-Z0-9]', '', raw_team)  # préserve la casse
        # Suppression globale via replace (couvre le cas avec parens après le tag)
        name = name.replace(f'-{raw_team}', '')
    else:
        m = re.search(r'\.([A-Za-z0-9]{2,12})$', name_team)
        if m:
            suffix = m.group(1)
            if _is_team_suffix_candidate(suffix):
                team = suffix
                # Coupe au niveau de name_team (avant les éventuelles parens de fin)
                cut_pos = name_team.rfind(f'.{suffix}')
                if cut_pos >= 0:
                    name = name[:cut_pos] + name[cut_pos + len(suffix) + 1:]

    # ── 3. Normalisation séparateurs : points & underscores → espaces ─────────
    name = name.replace('.', ' ').replace('_', ' ')
    name = _ws(name)

    # 3b. Recoller les channel tokens détruits (ex: "7 1" → "7.1")
    name = re.sub(r'(?<!\d)(7) (1)(?!\d)', '7.1', name)
    name = re.sub(r'(?<!\d)(5) (1)(?!\d)', '5.1', name)
    name = re.sub(r'(?<!\d)(2) (0)(?!\d)', '2.0', name)
    name = re.sub(r'(?<!\d)(1) (0)(?!\d)', '1.0', name)

    # ── 4. [Crochets] → contenu conservé, parenthèses de titre gardées ────────
    name = re.sub(r'\[([^\]]*)\]', r'\1', name)
    name = _ws(name)

    # ── 5. Pré-normalisation : aliases symboliques et textuels ────────────────
    name = re.sub(r'HDR10\+',                               'HDR10P',   name, flags=re.IGNORECASE)
    name = re.sub(r'HDR10PLUS',                             'HDR10P',   name, flags=re.IGNORECASE)
    name = re.sub(r'DOLBY[\s._-]*VISION',                   'DV',       name, flags=re.IGNORECASE)
    name = re.sub(r'DD\+',                                  'DDP',      name, flags=re.IGNORECASE)
    name = re.sub(r'E-?AC-?3',                              'DDP',      name, flags=re.IGNORECASE)
    name = re.sub(r'TRUE[\s._-]*HD',                        'TrueHD',   name, flags=re.IGNORECASE)
    name = re.sub(r'TRUEFRENCH',                            'VFF',      name, flags=re.IGNORECASE)
    name = re.sub(r'DTS[\s_-]*HD[\s_-]*MA',                'DTS-HDMA', name, flags=re.IGNORECASE)
    name = re.sub(r'DTS[\s_-]*HD[\s_-]*RA',                'DTS-HDMA', name, flags=re.IGNORECASE)
    name = re.sub(r'WEB-Rip',                               'WEBRip',   name, flags=re.IGNORECASE)
    name = re.sub(r'(?<!\w)H\s+264(?!\w)',                  'H264',     name, flags=re.IGNORECASE)
    name = re.sub(r'(?<!\w)H\s+265(?!\w)',                  'H265',     name, flags=re.IGNORECASE)
    # 4KLight (variable separators) → 4KLight
    name = re.sub(r'4K[\s._-]*LIGHT',                       '4KLight',  name, flags=re.IGNORECASE)
    # MULTi-VFF/VFQ/VF2/VFB (tiret) → MULTi.VFF etc.
    name = re.sub(r'MULTi-(VFF|VFQ|VF2|VFB)',
                  lambda mo: f'MULTi.{mo.group(1).upper()}',            name, flags=re.IGNORECASE)
    # VFI → VFF (case-insensitive, word-boundary)
    name = re.sub(r'(?<!\w)VFI(?!\w)',                      'VFF',      name, flags=re.IGNORECASE)
    # FR-ENG-... → MULTi.VFF
    name = re.sub(r'(^|\s)FR-[A-Za-z]+(?:-[A-Za-z]+)+(\s|$)',
                  r'\1MULTi.VFF\2',                                     name, flags=re.IGNORECASE)
    # FR seul → FRENCH
    name = re.sub(r'(^|\s)FR(\s|$)',                        r'\1FRENCH\2', name, flags=re.IGNORECASE)
    name = _ws(name)

    # ── 5b. Séparation des tokens collés (boucle jusqu'à stabilité) ───────────
    prev = None
    while name != prev:
        prev = name
        name = re.sub(f'({_TAGS})({_TAGS})', r'\1 \2', name, flags=re.IGNORECASE)
    name = _ws(name)

    # ── 5d. Bit depth (10bit/8bit/12bit) : on le retire du nom ───────────────
    # Conventions G3MINI: on ne garde pas "10bit" dans le release_name final.
    name = re.sub(r'(?:^|\s)(?:8|10|12)[- ]?bit(?:\s|$)', ' ', name, flags=re.IGNORECASE)
    name = _ws(name)

    # ── 5c. SAISON N → S0N ────────────────────────────────────────────────────
    while True:
        m = re.search(r'(?:^|\s)[Ss][Aa][Ii][Ss][Oo][Nn]\s*([0-9]+)(?:\s|$)', name)
        if not m:
            break
        snum = m.group(1)
        padded = f'S{int(snum):02d}'
        name = re.sub(rf'[Ss][Aa][Ii][Ss][Oo][Nn]\s*{re.escape(snum)}', padded, name, flags=re.IGNORECASE)

    # ── 6. Année ──────────────────────────────────────────────────────────────
    year = ""
    m = re.search(r'\(([12][0-9]{3})\)', name)
    if m:
        year = m.group(1)
        name = name.replace(f'({year})', '')
    else:
        m = re.search(r'(?:^|\s)([12][0-9]{3})(?:\s|$)', name)
        if m:
            year = m.group(1)
            name = re.sub(rf'(?:^|\s){re.escape(year)}(?:\s|$)', ' ', name)
    name = _ws(name)

    if not year and tv_year:
        year = str(tv_year)

    # ── 6b. Saison / épisode (séries) ─────────────────────────────────────────
    if re.search(r'(?:^|\s)COMPLETE(?:\s|$)', name, re.IGNORECASE):
        name = _remove_token(name, "COMPLETE")
        name = _ws(name)
    name, season, episode = _extract_tv_season_episode(name)
    is_integrale = bool(re.search(r"(?:^|\s)INTEGRALE(?:\s|$)", name, re.IGNORECASE))
    is_tv = bool(season or episode or is_integrale)
    is_season_pack = bool(season) and not episode and (torrent_pack or is_integrale)

    # ── 7. Extras ─────────────────────────────────────────────────────────────
    extras = ""
    for kw, display in _EXTRAS_MAP.items():
        if re.search(rf'(?:^|\s){kw}(?:\s|$)', name, re.IGNORECASE):
            extras += f'.{display}'
            name = _remove_token(name, kw)
    if re.search(r'(?:^|\s)VL(?:\s|$)', name):
        extras += '.EXTENDED'
        name = _remove_token(name, 'VL')
    # DC EXTREME → DIRECTORS.CUT.EXTREME (avant DC seul)
    if re.search(r'(?:^|\s)DC\s+EXTREME(?:\s|$)', name, re.IGNORECASE):
        extras += '.DIRECTORS.CUT.EXTREME'
        name = re.sub(r'(?:^|\s)DC\s+EXTREME(?:\s|$)', ' ', name, flags=re.IGNORECASE)
    elif re.search(r'(?:^|\s)DC(?:\s|$)', name):
        extras += '.DIRECTORS.CUT'
        name = _remove_token(name, 'DC')

    # ── 8a. Normalisation casse MULTi ─────────────────────────────────────────
    name = re.sub(
        r'(?:^|\s)[Mm][Uu][Ll][Tt][Ii][Cc]?(?:\s|$)',
        lambda mo: mo.group(0)[0] + 'MULTi' + mo.group(0)[-1],
        name,
    )

    # ── 8b. Compound "MULTi VFF" → "MULTi.VFF" etc. ──────────────────────────
    name = re.sub(r'MULTi\s+FRENCH',                        'MULTi.VFF', name, flags=re.IGNORECASE)
    name = re.sub(r'MULTi\s+VFF-[A-Za-z]+(?:-[A-Za-z]+)*', 'MULTi.VFF', name)
    name = re.sub(r'MULTi\s+VFQ-[A-Za-z]+(?:-[A-Za-z]+)*', 'MULTi.VFQ', name)
    name = re.sub(r'MULTi\s+VF2-[A-Za-z]+(?:-[A-Za-z]+)*', 'MULTi.VF2', name)
    name = re.sub(r'MULTi\s+VFB-[A-Za-z]+(?:-[A-Za-z]+)*', 'MULTi.VFB', name)
    name = re.sub(r'MULTi\s+(VFF)(\s|$)',                   r'MULTi.VFF\2', name)
    name = re.sub(r'MULTi\s+(VFQ)(\s|$)',                   r'MULTi.VFQ\2', name)
    name = re.sub(r'MULTi\s+(VF2)(\s|$)',                   r'MULTi.VF2\2', name)
    name = re.sub(r'MULTi\s+(VFB)(\s|$)',                   r'MULTi.VFB\2', name)

    # ── 9. Langue ─────────────────────────────────────────────────────────────
    lang = ""
    lang_compound = False
    lang_from_french = False  # True si le token source était "FRENCH" (pas VFF/VFI explicite)
    for p in _LANG_PATTERNS:
        m = re.search(r'(?:^|\s)(' + p + r')(?:\s|$)', name, re.IGNORECASE)
        if m:
            matched = m.group(1)
            lang = _normalize_lang(matched)
            name = _remove_token(name, matched)
            if '-' in matched:
                lang_compound = True
            if matched.upper() == "FRENCH":
                lang_from_french = True
            break

    # ── 9b. VFF-ENG composé : MULTi.VFF seulement si ST français présents ─────
    if lang == "MULTi.VFF" and lang_compound and mi:
        subfr = _get_subfr_from_mediainfo(mi)
        if subfr == "no":
            orig_upper = original.upper().replace('.', ' ')
            mo = re.search(r'VFF-[A-Z]+(?:-[A-Z]+)*', orig_upper)
            lang = mo.group(0) if mo else "VFF"
        # yes ou unknown → on garde MULTi.VFF

    # ── 9b2. FRENCH + MediaInfo : VFF (FR) ou VFQ (CA) ───────────────────────
    # Quand le token source est "FRENCH" (ambigu), on consulte le MI pour
    # distinguer French (FR) → VFF et French (CA) → VFQ.
    if lang_from_french and mi:
        mi_lang = _get_lang_from_mediainfo(mi)
        if mi_lang:
            lang = mi_lang  # VFF, VFQ, VF2, VFB...

    # ── 9c. Fallback mediainfo : lang vide ou MULTi plain ─────────────────────
    if (not lang or lang == "MULTi") and (is_silent or (mi and _is_silent_from_mediainfo(mi))):
        lang = "MUET"
    elif (not lang or lang == "MULTi") and mi:
        mi_lang = _get_lang_from_mediainfo(mi)
        lang = f"MULTi.{mi_lang}" if mi_lang else "MULTi.VFF"

    # ── 10. HDR / SDR — tous les tokens collectés ─────────────────────────────
    hdr_parts = []
    hybrid = ""
    if re.search(r'(?:^|\s)Hybrid(?:\s|$)', name, re.IGNORECASE):
        hybrid = "Hybrid"
        name = _remove_token(name, "Hybrid")
    for h in ("HDR10P", "HDR10", "SDR", "DV", "HLG", "PQ10", "HDR"):
        if re.search(rf'(?:^|\s){re.escape(h)}(?:\s|$)', name, re.IGNORECASE):
            hdr_parts.append(h)
            name = _remove_token(name, h)
    hdr = '.'.join(hdr_parts)
    if hybrid and hdr:
        hdr = f"Hybrid.{hdr}"
    elif hybrid:
        hdr = "Hybrid"

    # ── 11. Résolution ────────────────────────────────────────────────────────
    res = ""
    for r in ("2160p", "4K", "1080p", "1080i", "720p", "576p", "480p"):
        if re.search(rf'(?:^|\s){re.escape(r)}(?:\s|$)', name, re.IGNORECASE):
            res = r
            name = _remove_token(name, r)
            break
    # UHD est redondant si 2160p/4K déjà capturé
    if res in ("2160p", "4K"):
        name = _remove_token(name, "UHD")

    # ── 12. Source ────────────────────────────────────────────────────────────
    source = ""
    source_qual = ""   # qualificatif de source : 4KLight, HDLight (peut coexister avec BluRay)
    remux = ""
    full_disc = ""

    if re.search(r'(?:^|\s)FULL\s+DISC(?:\s|$)', name, re.IGNORECASE):
        full_disc = "FULL"
        name = re.sub(r'(?:^|\s)FULL\s+DISC(?:\s|$)', ' ', name, flags=re.IGNORECASE)
    elif re.search(r'(?:^|\s)FULL(?:\s|$)', name, re.IGNORECASE):
        full_disc = "FULL"
        name = _remove_token(name, "FULL")

    # Qualificatifs de source extraits en priorité : peuvent coexister avec
    # une source principale (ex: "4KLight BluRay" → "4KLight.BluRay").
    for q in _SOURCE_QUAL_LIST:
        if re.search(rf'(?:^|\s){re.escape(q)}(?:\s|$)', name, re.IGNORECASE):
            source_qual = _normalize_source(q)
            name = _remove_token(name, q)
            name = _ws(name)
            break

    # "UHD BluRay" avec espace car les points ont été convertis en step 3
    for s in _SOURCE_LIST:
        pat = re.escape(s).replace(r'\ ', r'\s+')  # espace → \s+ pour robustesse
        if re.search(rf'(?:^|\s){pat}(?:\s|$)', name, re.IGNORECASE):
            if s.upper() in ("UHD BLURAY", "UHD.BLURAY"):
                source = "BluRay"
            else:
                source = _normalize_source(s)
            name = re.sub(rf'(?:^|\s){pat}(?:\s|$)', ' ', name, flags=re.IGNORECASE)
            name = _ws(name)
            break

    # Combine qualificatif + source principale : ex. "4KLight.BluRay"
    if source_qual and source:
        source = f"{source_qual}.{source}"
    elif source_qual:
        source = source_qual

    if re.search(r'(?:^|\s)REMUX(?:\s|$)', name, re.IGNORECASE):
        remux = "REMUX"
        name = _remove_token(name, "REMUX")

    platform = ""
    for p in _PLATFORM_LIST:
        if re.search(rf'(?:^|\s){re.escape(p)}(?:\s|$)', name, re.IGNORECASE):
            platform = p
            name = _remove_token(name, p)
            break

    for leftover in ("Netflix", "Disney", "AppleTV", "Paramount"):
        name = _remove_token(name, leftover)

    # ── 13. Audio — un seul tag (le plus prioritaire) ─────────────────────────
    name, audio_tags = _collect_audio_tags_from_name(name)
    audio = _pick_best_audio(audio_tags)

    if not audio and mi:
        audio = _get_best_audio_from_mediainfo(mi)

    if lang == "MUET":
        audio = ""

    # ── 14. Codec vidéo ───────────────────────────────────────────────────────
    codec = ""
    for c_pat, c_norm in _CODEC_LIST:
        if re.search(rf'(?:^|\s){c_pat}(?:\s|$)', name, re.IGNORECASE):
            codec = c_norm
            # Suppression via le pattern (c_pat peut contenir des chars regex)
            name = re.sub(rf'(?:^|\s){c_pat}(?:\s|$)', ' ', name, flags=re.IGNORECASE)
            name = _ws(name)
            break

    # ── 15. Fallback mediainfo si codec manquant ──────────────────────────────
    if not codec and mi:
        codec = _get_codec_from_mediainfo(mi)

    # ── 16. Adaptation codec selon source (convention G3MINI) ─────────────────
    #   REMUX        → HEVC / AVC
    #   WEB          → H265 / H264  (sauf si MI confirme un vrai encode x264/x265)
    #   WEBRip/BDRip/TVRip → x265 / x264
    #   BluRay/HDLight/DVDRip → inchangé
    if codec:
        is265 = bool(re.fullmatch(r'x265|HEVC|H\.?265', codec, re.IGNORECASE))
        is264 = bool(re.fullmatch(r'x264|AVC|H\.?264',  codec, re.IGNORECASE))
        # Détermine la source "nue" pour la logique de codec (sans le qualificatif)
        base_source = source.split('.')[-1] if source else ""
        # Si le MI confirme un encode via writing library (x264/x265), on ne
        # remplace pas par H264/H265 même sur source WEB.
        mi_is_encode = _has_encode_library(mi) if mi else False
        if is265:
            if remux == "REMUX":                                codec = "HEVC"
            elif base_source == "WEB" and not mi_is_encode:    codec = "H265"
            elif base_source in ("WEBRip", "BDRip", "TVRip"):  codec = "x265"
        elif is264:
            if remux == "REMUX":                                codec = "AVC"
            elif base_source == "WEB" and not mi_is_encode:    codec = "H264"
            elif base_source in ("WEBRip", "BDRip", "TVRip"):  codec = "x264"

    # ── 17. Titre = résidu ────────────────────────────────────────────────────
    name = re.sub(r'\([^)]*\)', '', name)
    title = _clean_title(name)

    # ── Reconstruction ────────────────────────────────────────────────────────
    new = title
    if year:
        new += f".{year}"
    if is_tv:
        if episode:
            new += f".{episode}"
        elif season:
            new += f".{season}"
            if is_season_pack:
                new += ".COMPLETE"
    if extras:
        new += extras        # commence déjà par '.'
    if lang:
        new += f".{lang}"
    if res:         new += f".{res}"
    if hdr:         new += f".{hdr}"
    if platform:    new += f".{platform}"
    if source:      new += f".{source}"
    if full_disc:   new += f".{full_disc}"
    if remux:       new += f".{remux}"
    if audio:       new += f".{audio}"
    if codec:       new += f".{codec}"
    if team:
        new += f"-{team}"
    else:
        # Si aucun tag d'équipe n'est present en suffixe, forcer -NoTag
        # uniquement dans le release_name final (sans modifier le fichier source).
        new += "-NoTag"
    new += ext

    new = re.sub(r'\.{2,}', '.', new)
    return new


# ══════════════════════════════════════════════════════════════════════════════
#  POINT D'ENTRÉE PUBLIC
# ══════════════════════════════════════════════════════════════════════════════

def normalize_release_name(
    release_name: str,
    mediainfo_text: Optional[str] = None,
    is_silent: bool = False,
    tv_year: Optional[int] = None,
    torrent_pack: bool = False,
) -> str:
    return _parse_release(
        release_name,
        mediainfo_text,
        is_silent,
        tv_year=tv_year,
        torrent_pack=torrent_pack,
    )