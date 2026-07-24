# -*- coding: utf-8 -*-
"""
release_normalizer.py — Normalisation des noms de release pour G3MINI Tracker

Portage Python du script g3mini_rename.sh
Ne renomme aucun fichier — agit uniquement sur le champ release_name.
"""

import re
import unicodedata
from typing import Optional


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _extract_french_en_lang(name: str) -> tuple[str, str]:
    """FRENCH/VFF + EN/ENG → VFF.VO ; retire EN du nom.
    La détection est volontairement case-sensitive : les prépositions minuscules
    (« en », « et », « de »…) ne doivent pas être confondues avec le code langue EN."""
    has_en = bool(re.search(r'(?:^|\s)(?:ENG|EN)(?:\s|$)', name))
    if not has_en:
        return name, ""
    if re.search(r'(?:^|\s)FRENCH(?:\s|$)', name, re.IGNORECASE):
        name = _remove_token(name, "FRENCH")
        name = _remove_token(name, "ENG")
        name = _remove_token(name, "EN")
        return _ws(name), "VFF.VO"
    if re.search(r'(?:^|\s)VFF(?:\s|$)', name, re.IGNORECASE):
        name = _remove_token(name, "VFF")
        name = _remove_token(name, "ENG")
        name = _remove_token(name, "EN")
        return _ws(name), "VFF.VO"
    return name, ""


_LANG_NORM_MAP: dict[str, str] = {
    "MULTI": "MULTi", "MULTIC": "MULTi",
    "MULTI.VFF": "MULTi.VFF", "MULTI-VFF": "MULTi.VFF",
    "MULTI.VFQ": "MULTi.VFQ", "MULTI-VFQ": "MULTi.VFQ",
    "MULTI.VF2": "MULTi.VF2", "MULTI-VF2": "MULTi.VF2",
    "MULTI.VFB": "MULTi.VFB", "MULTI-VFB": "MULTi.VFB",
    "FRENCH": "VFF", "VF": "VFF", "VFF": "VFF",
    "VFQ": "VFQ", "VF2": "VF2", "VFB": "VFB",
    "VOF": "VOF", "VOQ": "VOQ", "VOB": "VOB",
    "VOSTFR": "VOSTFR", "SUBFRENCH": "SUBFRENCH",
}


def _normalize_lang(raw: str) -> str:
    r = raw.upper()
    for prefix in ("VFF-", "VFQ-", "VF2-", "VFB-"):
        if r.startswith(prefix):
            return f"MULTi.{prefix[:3]}"
    return _LANG_NORM_MAP.get(r, raw)


_SOURCE_NORM_MAP: dict[str, str] = {
    "BLURAY": "BluRay", "BLU-RAY": "BluRay",
    "BDRIP": "BDRip",
    "4KLIGHT": "4KLight",
    "HDLIGHT": "HDLight", "MHD": "BluRay",
    "WEBRIP": "WEBRip",
    "WEB-DL": "WEB", "WEBDL": "WEB", "WEB": "WEB",
    "HDRIP": "HDRip",
    "HDTV": "HDTV",
    "TVRIP": "TVRip", "TVHDRIP": "TVRip",
    "DVDRIP": "DVDRip", "DVD": "DVDRip",
    "REMUX": "REMUX",
}


def _normalize_source(raw: str) -> str:
    return _SOURCE_NORM_MAP.get(raw.upper(), raw)


def _clean_title(t: str) -> str:
    t = t.strip()
    # Transliterate accented chars before stripping non-ASCII (é→e, à→a, ç→c…)
    t = unicodedata.normalize('NFD', t)
    t = ''.join(c for c in t if unicodedata.category(c) != 'Mn')
    t = t.replace("&", "and")
    t = re.sub(r'\s+-\s+', ' ', t)  # " - " subtitle separator → space
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
    m = re.match(r'S(\d{1,2})E(\d{1,4})', raw, re.IGNORECASE)
    if not m:
        return raw.upper()
    return f"S{int(m.group(1)):02d}E{int(m.group(2)):02d}"


def _extract_tv_season_episode(name: str) -> tuple[str, str, str]:
    """
    Extrait SxxEyy ou Sxx du nom (espaces). Retourne (nom_restant, saison, épisode).
    """
    episode = ""
    season = ""

    m = re.search(r'(?:^|\s)(S\d{1,2}E\d{1,4})(?:\s|$)', name, re.IGNORECASE)
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
        elif re.search(r'Language\s*:\s*French(?!\s*\()', line, re.IGNORECASE):                             vff = True
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
    "HE-AAC": 12,
    "HE-AAC2.0": 22,
    "HE-AAC5.1": 27,
    "AC3": 30,
    "AC32.0": 35,
    "AC35.1": 38,
    "DD": 28,
    "DD2.0": 35,
    "DD5.1": 38,
    "DD7.1": 45,
    "DDP": 40,
    "DDP2.0": 50,
    "DDP5.1": 60,
    "DDP7.1": 70,
    "DTS": 80,
    "DTS-HD": 90,
    "DTS-HD.MA": 95,
    "Atmos": 100,
    "DDP.Atmos":    105,
    "DDP2.0.Atmos": 107,
    "DDP5.1.Atmos": 112,
    "DDP7.1.Atmos": 117,
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
    # Atmos + DDP* → keep both as compound "Atmos.DDP5.1" etc.
    atmos = next((t for t in tags if t.upper() == "ATMOS"), None)
    ddp   = next((t for t in tags if t.upper().startswith("DDP")), None)
    if atmos and ddp:
        return f"{ddp}.Atmos"
    return max(tags, key=_audio_rank)


def _audio_has_channel_suffix(tag: str) -> bool:
    if not tag:
        return False
    # Match channel suffix at end OR followed by a dot (e.g. DDP5.1.Atmos)
    return bool(re.search(
        r'(?:HE-AAC|DDP|DD|AC3|AAC)(?:2\.0|5\.1|7\.1)(?:\.|$)|\.(?:2\.0|5\.1|7\.1)(?:\.|$)',
        tag,
        re.IGNORECASE,
    ))


def _audio_codec_family(tag: str) -> str:
    t = tag.upper()
    if t.startswith("HE-AAC"):
        return "HE-AAC"
    if t.startswith("DDP"):
        return "DDP"
    if t.startswith("DD"):
        return "DD"
    if t.startswith("AC3"):
        return "AC3"
    if t.startswith("AAC"):
        return "AAC"
    return tag.split(".", 1)[0].upper()


def _append_channel_to_audio_tag(tag: str, channel: str) -> str:
    return f"{tag}{channel}"


def _resolve_audio_channel_tag(audio: str, mi: Optional[str]) -> str:
    """Ajoute 2.0/5.1/7.1 à DD/DDP/AC3/AAC nu via MediaInfo.

    Préfère les canaux de la piste française ; repli sur toutes les pistes.
    """
    if not audio or not mi or _audio_has_channel_suffix(audio):
        return audio
    family = _audio_codec_family(audio)
    french_tags, all_tags = _get_audio_tags_from_mediainfo_lang(mi)
    # French tracks first, then all tracks
    for tag in (french_tags + [t for t in all_tags if t not in french_tags]):
        if not _audio_has_channel_suffix(tag):
            continue
        tf = _audio_codec_family(tag)
        if tf != family and not (family == "AC3" and tf == "DD"):
            continue
        ch_m = re.search(r'(2\.0|5\.1|7\.1)', tag)
        if ch_m:
            base = "AC3" if family == "AC3" else family
            return _append_channel_to_audio_tag(base, ch_m.group(1))
    return audio


def _attach_orphan_channels_to_audio_tags(found: list[str], name: str) -> str:
    """Associe un 2.0/5.1/7.1 orphelin au dernier tag DD/DDP/AC3/AAC sans canaux."""
    channel_bases = frozenset({"DDP", "DD", "AC3", "AAC"})
    mo = re.search(r'(?:^|\s)(2\.0|5\.1|7\.1)(?:\s|$)', name)
    if not mo:
        return name
    ch = mo.group(1)
    for i in range(len(found) - 1, -1, -1):
        if found[i] not in channel_bases:
            continue
        if _audio_has_channel_suffix(found[i]):
            continue
        found[i] = _append_channel_to_audio_tag(found[i], ch)
        name = re.sub(rf'(?:^|\s){re.escape(ch)}(?:\s|$)', ' ', name)
        return _ws(name)
    return name


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
        if re.search(r'E-AC-3|EAC3|DD\+|ENHANCED AC-3', blob):
            return f"DDP{ch_s}.Atmos" if ch_s else "DDP.Atmos"
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
        return f"DD{ch_s}" if ch_s else "DD"
    if re.search(r'HE-AAC', blob):
        return f"HE-AAC{ch_s}" if ch_s else "HE-AAC"
    if re.search(r'\bAAC\b', fmt, re.IGNORECASE):
        return f"AAC{ch_s}" if ch_s else "AAC"
    return ""


_FRENCH_AUDIO_RE = re.compile(
    r'Language\s*:\s*French\b'
    r'|Language\s*:\s*fr\b'
    r'|Title\s*:.*\b(?:VFF|VFI|TrueFrench|VFQ|VOQ|VFB|VOB|VOF|VF2|VF)\b',
    re.IGNORECASE,
)

_AUDIO_BLOCK_RE = re.compile(
    r'^Audio(?:\s+#\d+)?\s*\n(.*?)(?=^(?:Audio|Video|Text|Menu|General)\b|\Z)',
    re.MULTILINE | re.DOTALL | re.IGNORECASE,
)


def _get_audio_tags_from_mediainfo(mi: str) -> list[str]:
    if not mi:
        return []
    tags: list[str] = []
    for m in _AUDIO_BLOCK_RE.finditer(mi):
        tag = _audio_tag_from_mediainfo_block(m.group(0))
        if tag:
            tags.append(tag)
    return tags


def _get_audio_tags_from_mediainfo_lang(mi: str) -> tuple[list[str], list[str]]:
    """Return (french_tags, all_tags) from MediaInfo audio blocks.

    French tracks are identified by Language: French/fr or a French title tag.
    """
    if not mi:
        return [], []
    french_tags: list[str] = []
    all_tags: list[str] = []
    for m in _AUDIO_BLOCK_RE.finditer(mi):
        block = m.group(0)
        tag = _audio_tag_from_mediainfo_block(block)
        if not tag:
            continue
        all_tags.append(tag)
        if _FRENCH_AUDIO_RE.search(block):
            french_tags.append(tag)
    return french_tags, all_tags


def _get_preferred_audio_from_mediainfo(mi: str) -> str:
    """Return best French audio tag; falls back to best overall if no French track."""
    if not mi:
        return ""
    french_tags, all_tags = _get_audio_tags_from_mediainfo_lang(mi)
    if french_tags:
        return _pick_best_audio(french_tags)
    return _pick_best_audio(all_tags) if all_tags else ""


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
        (r'(?:^|\s)DDP(?:\s|$)', "DDP"),
    ):
        if re.search(pat, name, re.IGNORECASE):
            found.append(tag)
            name = re.sub(pat, ' ', name, flags=re.IGNORECASE)
            name = _ws(name)

    for pat, tag in (
        (r'(?:^|\s)DD(?!P)\s*7\.1(?:\s|$)', "DD7.1"),
        (r'(?:^|\s)DD(?!P)\s*5\.1(?:\s|$)', "DD5.1"),
        (r'(?:^|\s)DD(?!P)\s*2\.0(?:\s|$)', "DD2.0"),
        (r'(?:^|\s)DD(?!P)(?:\s|$)', "DD"),
    ):
        if re.search(pat, name, re.IGNORECASE):
            found.append(tag)
            name = re.sub(pat, ' ', name, flags=re.IGNORECASE)
            name = _ws(name)

    m = re.search(r'(?:^|\s)AC3[-. ]?(2\.0|5\.1|7\.1)(?:\s|$)', name, re.IGNORECASE)
    if m:
        found.append(_append_channel_to_audio_tag("DD", m.group(1)))
        name = re.sub(
            r'(?:^|\s)AC3[-. ]?' + re.escape(m.group(1)) + r'(?:\s|$)',
            ' ',
            name,
            flags=re.IGNORECASE,
        )
        name = _ws(name)
    elif re.search(r'(?:^|\s)AC3(?:\s|$)', name, re.IGNORECASE):
        found.append("DD")
        name = _remove_token(name, "AC3")

    for pat, tag in (
        (r'(?:^|\s)HE-AAC\s*5\.1(?:\s|$)', "HE-AAC5.1"),
        (r'(?:^|\s)HE-AAC\s*2\.0(?:\s|$)', "HE-AAC2.0"),
        (r'(?:^|\s)HE-AAC(?:\s|$)', "HE-AAC"),
    ):
        if re.search(pat, name, re.IGNORECASE):
            found.append(tag)
            name = re.sub(pat, ' ', name, flags=re.IGNORECASE)
            name = _ws(name)
            break

    for pat, tag in (
        (r'(?:^|\s)AAC\s*5\.1(?:\s|$)', "AAC5.1"),
        (r'(?:^|\s)AAC\s*2\.0(?:\s|$)', "AAC2.0"),
        (r'(?:^|\s)AAC(?:\s|$)', "AAC"),
    ):
        if re.search(pat, name, re.IGNORECASE):
            found.append(tag)
            name = re.sub(pat, ' ', name, flags=re.IGNORECASE)
            name = _ws(name)

    name = _attach_orphan_channels_to_audio_tags(found, name)
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
    r'|HMAX'
    r'|x265|x264|H265|H264|HEVC|AVC|AV1|VP9|VC1'
    r'|DTS-HDMA|DTS-HD|DTS|AC3|DDP|DD|TrueHD|Atmos|AAC'
    r'|MULTi|VFF|VFQ|VF2|VFB|VOSTFR|SUBFRENCH|VOF|VOQ|VOB|FRENCH'
    r'|EXTENDED|PROPER|REPACK|UNRATED|UNCUT|REMASTERED|RESTORED|INTERNAL|NoTAG|iNTEGRALE|CUSTOM'
    r'|8bit|10bit|12bit'
)
_RE_TAGS_SPLIT = re.compile(f'({_TAGS})({_TAGS})', re.IGNORECASE)

# Du plus spécifique au moins spécifique
_LANG_PATTERNS = [
    r'VFF-[A-Za-z]+(?:-[A-Za-z]+)*',
    r'VFQ-[A-Za-z]+(?:-[A-Za-z]+)*',
    r'VFB-[A-Za-z]+(?:-[A-Za-z]+)*',
    r'VF2-[A-Za-z]+(?:-[A-Za-z]+)*',
    r'MULTi\.VFF', r'MULTi\.VFQ', r'MULTi\.VF2', r'MULTi\.VFB',
    r'MULTi',
    r'FRENCH', r'VFF', r'VFQ', r'VF2', r'VFB', r'VF',
    r'VOF', r'VOQ', r'VOB',
    r'VOSTFR', r'SUBFRENCH',
]

# Non-French language codes that indicate a secondary audio track.
# Standalone token (surrounded by whitespace) alongside a French lang → MULTi.
# EN/ENG are excluded here — handled separately by _extract_french_en_lang.
_SECONDARY_LANG_RE = re.compile(
    r'(?:^|\s)(ES|SP|IT|DE|PT|BR|RU|PL|NL|SV|DA|FI|HU|CS|TR|RO|SK|HR|BG|EL|HE|TH|UK|AR|ZH|JA|KO)(?:\s|$)',
)
# French mono-language variants that can be promoted to MULTi when a secondary code is found.
_FRENCH_MONO_LANGS = frozenset({"VFF", "VFQ", "VF2", "VFB", "VOF", "VOQ", "VOB"})

_EXTRAS_MAP = {
    'EXTENDED':      'EXTENDED',
    'THEATRICAL':    'THEATRICAL',
    'PROPER':        'PROPER',
    'REPACK':        'REPACK',
    'UNRATED':       'UNRATED',
    'UNCUT':         'UNCUT',
    'REMASTERED':    'REMASTERED',
    'RESTORED':      'RESTORED',
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
    "BluRay", "Blu-Ray", "mHD",
    "BDRip",
    "WEB-DL", "WEBRip",
    "HDTV", "HDRip", "TVRip",
    "WEB", "DVDRip", "DVD",
]

# Qualificatifs de source : peuvent coexister avec une source principale.
# Ex: "4KLight BluRay" → source = "4KLight.BluRay"
_SOURCE_QUAL_LIST = ["4KLight", "HDLight"]

# Platforms
_PLATFORM_LIST = ["AMZN", "NF", "DSNP", "HULU", "ATVP", "PCOK", "MAX", "HBO", "CR", "ADN"]

# Teams whose name contains a hyphen — would otherwise be split at the last hyphen.
# Add any new hyphenated team names here (case-insensitive comparison).
_HYPHENATED_TEAMS: frozenset[str] = frozenset({
    "Tsundere-Raws",
})

_NON_TEAM_SUFFIXES = {
    # Extensions / contenants
    "MKV", "MP4", "AVI", "M2TS", "TS", "ISO",
    # Resolution / video tags
    "2160P", "1080P", "1080I", "720P", "576P", "480P", "4K", "UHD", "HMAX",
    "X264", "X265", "H264", "H265", "AVC", "HEVC", "AV1", "VP9", "VC1", "MPEG2",
    # Sources
    "WEB", "WEBRIP", "WEBDL", "BLURAY", "BDRIP", "HDRIP", "HDTV", "TVRIP", "DVDRIP", "REMUX",
    # Langues
    "FRENCH", "MULTI", "MULTIC", "VFF", "VFQ", "VF2", "VFB", "VOSTFR", "SUBFRENCH", "VOF", "VOQ", "VOB",
    "EN", "ENG",
    # Audio
    "DTS", "DTSHD", "DTSHDMA", "AC3", "DD", "DDP", "TRUEHD", "ATMOS", "AAC", "AD",
    # HDR / extras courants
    "HDR", "HDR10", "HDR10P", "DV", "HLG", "SDR", "NOTAG",
    # Qualificatifs de contenu
    "DOC", "CUSTOM",
}


def _is_team_suffix_candidate(suffix: str) -> bool:
    s = suffix.upper()
    # Tokens épisode/saison et autres suffixes techniques ne sont pas des teams.
    if re.fullmatch(r'S\d{1,2}E\d{1,4}', s):
        return False
    if re.fullmatch(r'S\d{1,2}', s):
        return False
    return s not in _NON_TEAM_SUFFIXES


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS HAUT NIVEAU
# ══════════════════════════════════════════════════════════════════════════════

def _apply_lang(
    name: str,
    mi: Optional[str],
    original: str,
    is_silent: bool,
) -> tuple[str, str]:
    """Steps 8a-9c : normalise MULTi, extrait la langue depuis le nom puis MediaInfo."""
    # 8a. Normalisation casse MULTi
    name = re.sub(r'\b[Mm][Uu][Ll][Tt][Ii][Cc]?\b', 'MULTi', name)

    # 8b. Compound "MULTi VFF" → "MULTi.VFF" etc.
    name = re.sub(r'MULTi\s+FRENCH',                        'MULTi.VFF', name, flags=re.IGNORECASE)
    name = re.sub(r'MULTi\s+VFF-[A-Za-z]+(?:-[A-Za-z]+)*', 'MULTi.VFF', name)
    name = re.sub(r'MULTi\s+VFQ-[A-Za-z]+(?:-[A-Za-z]+)*', 'MULTi.VFQ', name)
    name = re.sub(r'MULTi\s+VF2-[A-Za-z]+(?:-[A-Za-z]+)*', 'MULTi.VF2', name)
    name = re.sub(r'MULTi\s+VFB-[A-Za-z]+(?:-[A-Za-z]+)*', 'MULTi.VFB', name)
    name = re.sub(r'MULTi\s+(VFF)(\s|$)',                   r'MULTi.VFF\2', name)
    name = re.sub(r'MULTi\s+(VFQ)(\s|$)',                   r'MULTi.VFQ\2', name)
    name = re.sub(r'MULTi\s+(VF2)(\s|$)',                   r'MULTi.VF2\2', name)
    name = re.sub(r'MULTi\s+(VFB)(\s|$)',                   r'MULTi.VFB\2', name)

    # 9. Langue
    lang = ""
    lang_compound = False
    lang_from_french = False
    name, lang_french_en = _extract_french_en_lang(name)
    if lang_french_en:
        lang = lang_french_en
        # VFF.VO → MULTi.VFF si des sous-titres français sont présents dans le MediaInfo.
        # VFF.VO ne s'applique qu'en l'absence de ST français.
        if lang == "VFF.VO" and mi and _get_subfr_from_mediainfo(mi) == "yes":
            lang = "MULTi.VFF"
    for p in _LANG_PATTERNS:
        if lang:
            break
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

    # 9b. VFF-ENG composé : MULTi.VFF seulement si ST français présents
    if lang == "MULTi.VFF" and lang_compound and mi:
        subfr = _get_subfr_from_mediainfo(mi)
        if subfr == "no":
            orig_upper = original.upper().replace('.', ' ')
            mo = re.search(r'VFF-[A-Z]+(?:-[A-Z]+)*', orig_upper)
            lang = mo.group(0) if mo else "VFF"

    # 9b2. FRENCH + MediaInfo : VFF (FR) ou VFQ (CA)
    if lang_from_french and mi:
        mi_lang = _get_lang_from_mediainfo(mi)
        if mi_lang:
            lang = mi_lang

    # 9c. Fallback mediainfo : lang vide ou MULTi plain
    if (not lang or lang == "MULTi") and (is_silent or (mi and _is_silent_from_mediainfo(mi))):
        lang = "MUET"
    elif (not lang or lang == "MULTi") and mi:
        mi_lang = _get_lang_from_mediainfo(mi)
        lang = f"MULTi.{mi_lang}" if mi_lang else "MULTi.VFF"
    elif lang == "MULTi":
        # Aucun MediaInfo disponible : convention française par défaut
        lang = "MULTi.VFF"

    # 9d. Codes langue secondaires → MULTi
    # Si la langue est un mono-français (VFF, VFQ…) et qu'un code langue non-français
    # standalone est trouvé dans le nom (ex : SP, IT, DE…), on passe à MULTi.VFF.
    if lang in _FRENCH_MONO_LANGS:
        sm = _SECONDARY_LANG_RE.search(name)
        if sm:
            lang = f"MULTi.{lang}"
            # Retire tous les codes secondaires du nom pour qu'ils n'atterrissent pas dans le titre.
            name = _SECONDARY_LANG_RE.sub(' ', name)
            name = _ws(name)

    # 9e. Nettoyage des tokens EN/ENG résiduels (ex: VOSTFR EN → VOSTFR)
    # Case-sensitive : ne jamais supprimer "en" minuscule (préposition française).
    if lang:
        name = _ws(re.sub(r'(?:^|\s)ENG(?:\s|$)', ' ', name))
        name = _ws(re.sub(r'(?:^|\s)EN(?:\s|$)',  ' ', name))

    return name, lang


# ══════════════════════════════════════════════════════════════════════════════
#  EXTRACTION DOSSIER PARENT
# ══════════════════════════════════════════════════════════════════════════════

def _extract_parent_metadata(parent_name: str) -> dict:
    """
    Extrait depuis un nom de dossier parent les tokens manquants :
    team, lang, audio, res, source, hdr, codec, title.
    Utilisé comme fallback quand le fichier épisode ne contient pas toutes les infos.
    """
    name = parent_name
    result: dict = {}

    # Extension éventuelle (dossier sans extension normalement)
    name = re.sub(r'\.(mkv|mp4|avi|ts|m2ts|iso)$', '', name, flags=re.IGNORECASE)

    # Team (dernier tiret)
    m = re.search(r'-([A-Za-z0-9@_]+)$', name)
    if m and _is_team_suffix_candidate(m.group(1)):
        result['team'] = re.sub(r'[^a-zA-Z0-9]', '', m.group(1))
        name = name[:m.start()]

    # Séparateurs → espaces
    name = name.replace('.', ' ').replace('_', ' ')
    name = re.sub(r'\s+-\s+', ' ', name)
    name = _ws(name)

    # Recoller les tokens de canaux audio fragmentés (5 1 → 5.1, etc.)
    name = re.sub(r'(?<!\d)(7) (1)(?!\d)', '7.1', name)
    name = re.sub(r'(?<!\d)(5) (1)(?!\d)', '5.1', name)
    name = re.sub(r'(?<!\d)(2) (0)(?!\d)', '2.0', name)

    # Pré-normalisation (aliases symboliques)
    name = re.sub(r'HDR10\+',                               'HDR10P',   name, flags=re.IGNORECASE)
    name = re.sub(r'(?<!\w)WEB-DL(?!\w)',                  'WEB',      name, flags=re.IGNORECASE)
    name = re.sub(r'(?<!\w)WEBDL(?!\w)',                    'WEB',      name, flags=re.IGNORECASE)
    name = re.sub(r'E-?AC-?3',                              'DDP',      name, flags=re.IGNORECASE)
    name = re.sub(r'DD\+',                                  'DDP',      name, flags=re.IGNORECASE)
    name = re.sub(r'TRUE[\s._-]*HD',                        'TrueHD',   name, flags=re.IGNORECASE)
    name = re.sub(r'DTS[\s_-]*HD[\s_-]*MA',                'DTS-HDMA', name, flags=re.IGNORECASE)
    name = re.sub(r'TRUEFRENCH',                            'VFF',      name, flags=re.IGNORECASE)
    name = re.sub(r'(?<!\w)VFI(?!\w)',                      'VFF',      name, flags=re.IGNORECASE)
    name = re.sub(r'MULTi-(VFF|VFQ|VF2|VFB)',
                  lambda mo: f'MULTi.{mo.group(1).upper()}', name, flags=re.IGNORECASE)
    # MULTi + lang séparés par espace (après dots→espaces) → MULTi.LANG
    name = re.sub(r'MULTi\s+FRENCH',       'MULTi.VFF',    name, flags=re.IGNORECASE)
    name = re.sub(r'MULTi\s+(VFF)(\s|$)',  r'MULTi.VFF\2', name)
    name = re.sub(r'MULTi\s+(VFQ)(\s|$)',  r'MULTi.VFQ\2', name)
    name = re.sub(r'MULTi\s+(VF2)(\s|$)',  r'MULTi.VF2\2', name)
    name = re.sub(r'MULTi\s+(VFB)(\s|$)',  r'MULTi.VFB\2', name)
    name = re.sub(
        r'(?i)(BluRay|BDRip|WEBRip|WEB|HDTV|HDRip|TVRip|DVDRip)-(2160p|1080p|1080i|720p|576p|480p)',
        r'\1 \2', name,
    )

    # Séparation des tokens collés
    prev = None
    while name != prev:
        prev = name
        name = _RE_TAGS_SPLIT.sub(r'\1 \2', name)
    name = _ws(name)

    # Bit depth — capturer 10bit avant de retirer
    if re.search(r'(?:^|\s)10[- ]?bits?(?:\s|$)', name, re.IGNORECASE):
        result['bit_depth'] = '10bit'
    name = re.sub(r'(?:^|\s)10[- ]?bits?(?:\s|$)', ' ', name, flags=re.IGNORECASE)
    name = re.sub(r'(?:^|\s)(?:8|12)[- ]?bits?(?:\s|$)', ' ', name, flags=re.IGNORECASE)
    name = _ws(name)

    # CUSTOM — capturer avant extras
    if re.search(r'(?:^|\s)CUSTOM(?:\s|$)', name, re.IGNORECASE):
        result['custom'] = 'CUSTOM'
    name = _remove_token(name, 'CUSTOM')

    # Saison / épisode (retirer, inutiles pour le titre)
    name = re.sub(r'(?:^|\s)S\d{1,2}E\d{1,4}(?:\s|$)', ' ', name, flags=re.IGNORECASE)
    name = re.sub(r'(?:^|\s)S\d{1,2}(?:\s|$)', ' ', name, flags=re.IGNORECASE)
    name = _ws(name)

    # Année (retirer)
    name = re.sub(r'(?:^|\s)[12][0-9]{3}(?:\s|$)', ' ', name)
    name = _ws(name)

    # Extras (retirer)
    for kw in _EXTRAS_MAP:
        name = re.sub(rf'(?:^|\s){kw}(?:\s|$)', ' ', name, flags=re.IGNORECASE)
    name = _ws(name)

    # Langue
    name, lang_fe = _extract_french_en_lang(name)
    if lang_fe:
        result['lang'] = lang_fe
    else:
        for p in _LANG_PATTERNS:
            m = re.search(r'(?:^|\s)(' + p + r')(?:\s|$)', name, re.IGNORECASE)
            if m:
                result['lang'] = _normalize_lang(m.group(1))
                name = _remove_token(name, m.group(1))
                break
    name = _remove_token(name, "ENG")
    name = _remove_token(name, "EN")

    # HDR
    hdr_parts = []
    for h in ("HDR10P", "HDR10", "SDR", "DV", "HLG", "PQ10", "HDR"):
        if re.search(rf'(?:^|\s){re.escape(h)}(?:\s|$)', name, re.IGNORECASE):
            hdr_parts.append(h)
            name = _remove_token(name, h)
    if hdr_parts:
        result['hdr'] = '.'.join(hdr_parts)

    # HMAX (retirer)
    name = _remove_token(name, "HMAX")

    # Résolution
    for r in ("2160p", "4K", "1080p", "1080i", "720p", "576p", "480p"):
        if re.search(rf'(?:^|\s){re.escape(r)}(?:\s|$)', name, re.IGNORECASE):
            result['res'] = r
            name = _remove_token(name, r)
            break
    if result.get('res') in ("2160p", "4K"):
        name = _remove_token(name, "UHD")

    # Source
    source_qual = ""
    for sq in _SOURCE_QUAL_LIST:
        if re.search(rf'(?:^|\s){re.escape(sq)}(?:\s|$)', name, re.IGNORECASE):
            source_qual = _normalize_source(sq)
            name = _remove_token(name, sq)
            break
    for s in _SOURCE_LIST:
        pat = re.escape(s).replace(r'\ ', r'\s+')
        if re.search(rf'(?:^|\s){pat}(?:\s|$)', name, re.IGNORECASE):
            src = "BluRay" if s.upper() in ("UHD BLURAY", "UHD.BLURAY") else _normalize_source(s)
            result['source'] = f"{source_qual}.{src}" if source_qual else src
            name = re.sub(rf'(?:^|\s){pat}(?:\s|$)', ' ', name, flags=re.IGNORECASE)
            name = _ws(name)
            break
    else:
        if source_qual:
            result['source'] = source_qual

    if re.search(r'(?:^|\s)REMUX(?:\s|$)', name, re.IGNORECASE):
        result['remux'] = "REMUX"
        name = _remove_token(name, "REMUX")

    # Plateformes (retirer)
    for p in _PLATFORM_LIST:
        name = _remove_token(name, p)

    # Audio
    name, audio_tags = _collect_audio_tags_from_name(name)
    if audio_tags:
        result['audio'] = _pick_best_audio(audio_tags)

    # Codec
    for c_pat, c_norm in _CODEC_LIST:
        if re.search(rf'(?:^|\s){c_pat}(?:\s|$)', name, re.IGNORECASE):
            result['codec'] = c_norm
            name = re.sub(rf'(?:^|\s){c_pat}(?:\s|$)', ' ', name, flags=re.IGNORECASE)
            name = _ws(name)
            break

    # Titre résiduel (nom de la série/film dans le dossier parent)
    name = re.sub(r'\([^)]*\)', '', name)
    name = _remove_token(name, "COMPLETE")
    name = _remove_token(name, "INTEGRALE")
    title = _clean_title(name)
    if title:
        result['title'] = title

    return result


# ══════════════════════════════════════════════════════════════════════════════
#  PARSER PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

def _parse_release(
    original: str,
    mi: Optional[str] = None,
    is_silent: bool = False,
    tv_year: Optional[int] = None,
    torrent_pack: bool = False,
    parent_names: Optional[list[str]] = None,
    tmdb_title: Optional[str] = None,
    is_documentary: bool = False,
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

    # Whitelist pour les teams dont le nom contient un tiret (ex: Tsundere-Raws).
    # Prioritaire sur la regex standard qui s'arrêterait au dernier tiret.
    for _ht in _HYPHENATED_TEAMS:
        _suffix = f'-{_ht}'
        if name_team.lower().endswith(_suffix.lower()):
            team = _ht
            cut = len(name_team) - len(_suffix)
            name = name_team[:cut] + name[cut + len(_suffix):]
            break

    if not team:
        m = re.search(r'-([A-Za-z0-9@_]+)$', name_team)
    else:
        m = None  # team already found via whitelist
    if m and _is_team_suffix_candidate(m.group(1)):
        raw_team = m.group(1)
        team = re.sub(r'[^a-zA-Z0-9]', '', raw_team)  # préserve la casse
        # Suppression globale via replace (couvre le cas avec parens après le tag)
        name = name.replace(f'-{raw_team}', '')
    else:
        # Cas spécial : "-NoTag" explicite → retirer du nom pour éviter le double
        # (reconstruction forcera -NoTag car team=="", mais ne doit apparaître qu'une fois)
        if m and m.group(1).upper() == "NOTAG":
            name = name.replace(f'-{m.group(1)}', '')
        m2 = re.search(r'\.([A-Za-z0-9]{2,12})$', name_team)
        if m2:
            suffix = m2.group(1)
            if _is_team_suffix_candidate(suffix):
                team = suffix
                # Coupe au niveau de name_team (avant les éventuelles parens de fin)
                cut_pos = name_team.rfind(f'.{suffix}')
                if cut_pos >= 0:
                    name = name[:cut_pos] + name[cut_pos + len(suffix) + 1:]

    # ── 3. Normalisation séparateurs : points, underscores & " - " → espaces ───
    name = name.replace('.', ' ').replace('_', ' ')
    name = re.sub(r'\s+-\s+', ' ', name)  # " - " (séparateur TV) → espace
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
    name = re.sub(r'(?<!\w)WEB-DL(?!\w)',                  'WEB',      name, flags=re.IGNORECASE)
    name = re.sub(r'(?<!\w)WEBDL(?!\w)',                    'WEB',      name, flags=re.IGNORECASE)
    # SOURCE-RESOLUTION collés par tiret (ex: WEBDL-1080p → WEB 1080p)
    name = re.sub(
        r'(?i)(BluRay|BDRip|WEBRip|WEB|HDTV|HDRip|TVRip|DVDRip)-(2160p|1080p|1080i|720p|576p|480p)',
        r'\1 \2', name,
    )
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
    # VF seul → FRENCH (VFF/VFQ/VF2/VFB non affectés grâce à (?!\w))
    name = re.sub(r'(?<!\w)VF(?!\w)',                       'FRENCH',      name, flags=re.IGNORECASE)
    # FRENCH.EN / FRENCH-EN (avant séparation 5b) → FRENCH EN
    name = re.sub(r'(?:^|\s)FRENCH[.\s_-]+EN(?:\s|$)',      r' FRENCH EN ', name, flags=re.IGNORECASE)
    name = _ws(name)

    # ── 5a. Tirets séparateurs de champs (vieilles releases sans points) ───────
    # À ce stade, tous les tokens composés avec tiret intentionnel (DTS-HDMA,
    # VC-1, MPEG-2…) ont déjà été normalisés. Les tirets restants entre
    # caractères alphanumériques sont des séparateurs de champs (ex: -2001-VFF-).
    _HYPHEN_COMPOUNDS = ['DTS-HDMA', 'VC-1', 'MPEG-2', 'CBR-CBZ', 'HE-AAC']
    _ph_save = {t: f'\x00{i}\x00' for i, t in enumerate(_HYPHEN_COMPOUNDS)}
    for token, ph in _ph_save.items():
        name = re.sub(re.escape(token), ph, name, flags=re.IGNORECASE)
    name = re.sub(r'(?<=[A-Za-z0-9])-(?=[A-Za-z0-9])', ' ', name)
    for token, ph in _ph_save.items():
        name = name.replace(ph, token)
    name = _ws(name)

    # ── 5b. Séparation des tokens collés (boucle jusqu'à stabilité) ───────────
    prev = None
    while name != prev:
        prev = name
        name = _RE_TAGS_SPLIT.sub(r'\1 \2', name)
    name = _ws(name)

    # ── 5d. Bit depth ────────────────────────────────────────────────────────
    # 10bit : conservé, placé après la résolution dans le nom final.
    # 8bit / 12bit : retirés (pas de convention G3MINI pour ces valeurs).
    bit_depth = ""
    if re.search(r'(?:^|\s)10[- ]?bits?(?:\s|$)', name, re.IGNORECASE):
        bit_depth = "10bit"
        name = re.sub(r'(?:^|\s)10[- ]?bits?(?:\s|$)', ' ', name, flags=re.IGNORECASE)
    name = re.sub(r'(?:^|\s)(?:8|12)[- ]?bits?(?:\s|$)', ' ', name, flags=re.IGNORECASE)
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

    # Fallback année sur les dossiers parents (noms bruts, points non convertis)
    if not year and parent_names:
        _PARENT_YEAR_RE = re.compile(r'(?:^|[._\s])([12][0-9]{3})(?:[._\s]|$)')
        for pname in parent_names:
            m = _PARENT_YEAR_RE.search(pname)
            if m:
                year = m.group(1)
                break

    # ── 6c. DOC ───────────────────────────────────────────────────────────────
    doc = is_documentary  # pre-set from TMDB genre detection (genre_id 99)
    if re.search(r'(?:^|\s)DOC(?:\s|$)', name, re.IGNORECASE):
        doc = True
        name = _remove_token(name, 'DOC')
        name = _ws(name)

    # ── 6b. Saison / épisode (séries) ─────────────────────────────────────────
    if re.search(r'(?:^|\s)COMPLETE(?:\s|$)', name, re.IGNORECASE):
        name = _remove_token(name, "COMPLETE")
        name = _ws(name)
    # Capture text before SxxExx as the series title prefix.
    # Everything after SxxExx is the episode subtitle and must not appear in the title.
    _m_ep = re.search(r'(?:^|\s)(S\d{1,2}E\d{1,4})(?:\s|$)', name, re.IGNORECASE)
    _ep_title_prefix: str | None = _ws(name[:_m_ep.start(1)]) if _m_ep else None
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

    # Tokens épisode-spécifiques redondants avec le numéro d'épisode → supprimés
    name = _remove_token(name, "FINAL")

    # ── 7b. CUSTOM ────────────────────────────────────────────────────────────
    custom = ""
    if re.search(r'(?:^|\s)CUSTOM(?:\s|$)', name, re.IGNORECASE):
        custom = "CUSTOM"
        name = _remove_token(name, "CUSTOM")

    # ── 8a-9c. MULTi + Langue ─────────────────────────────────────────────────
    name, lang = _apply_lang(name, mi, original, is_silent)

    # ── 9d. AD (Audio Description) ───────────────────────────────────────────
    ad = ""
    if re.search(r'(?:^|\s)AD(?:\s|$)', name):
        ad = "AD"
        name = _remove_token(name, "AD")

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

    # ── 10c. HMAX (juste avant résolution dans le nom final) ─────────────────
    hmax = ""
    if re.search(r'(?:^|\s)HMAX(?:\s|$)', name, re.IGNORECASE):
        hmax = "HMAX"
        name = _remove_token(name, "HMAX")

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
        # Match standalone token OR parenthesized notation: CR or (CR)
        if re.search(rf'(?:^|[\s(]){re.escape(p)}(?:[\s)]|$)', name, re.IGNORECASE):
            platform = p
            name = re.sub(rf'\(\s*{re.escape(p)}\s*\)', ' ', name, flags=re.IGNORECASE)
            name = _remove_token(name, p)
            name = _ws(name)
            break

    for leftover in ("Netflix", "Disney", "AppleTV", "Paramount"):
        name = _remove_token(name, leftover)

    # ── 13. Audio — un seul tag (le plus prioritaire) ─────────────────────────
    name, audio_tags = _collect_audio_tags_from_name(name)
    audio = _pick_best_audio(audio_tags)

    if not audio and mi:
        audio = _get_preferred_audio_from_mediainfo(mi)
    elif audio and mi:
        audio = _resolve_audio_channel_tag(audio, mi)

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

    # x265 + HEVC dans le même nom : HEVC est redondant, on le retire du résidu.
    if codec and re.fullmatch(r'x265', codec, re.IGNORECASE):
        if re.search(r'(?:^|\s)HEVC(?:\s|$)', name, re.IGNORECASE):
            name = re.sub(r'(?:^|\s)HEVC(?:\s|$)', ' ', name, flags=re.IGNORECASE)
            name = _ws(name)

    # ── 15. Fallback mediainfo si codec manquant ──────────────────────────────
    if not codec and mi:
        codec = _get_codec_from_mediainfo(mi)

    # ── Fallback dossier parent ───────────────────────────────────────────────
    # Utilisé UNIQUEMENT pour les champs techniques absents du nom de fichier :
    # lang, source, résolution, codec vidéo, team.
    # L'audio vient exclusivement de MediaInfo ; le titre et l'année viennent
    # du fichier lui-même ou de TMDB — jamais du dossier parent.
    parent_meta: dict = {}
    if parent_names:
        parent_meta = _extract_parent_metadata(parent_names[0])
        if not team:      team      = parent_meta.get('team',      '')
        if not lang:      lang      = parent_meta.get('lang',      '')
        if not audio:     audio     = parent_meta.get('audio',     '')
        if not res:       res       = parent_meta.get('res',       '')
        if not source:    source    = parent_meta.get('source',    '')
        if not hdr:       hdr       = parent_meta.get('hdr',       '')
        if not codec:     codec     = parent_meta.get('codec',     '')
        if not bit_depth: bit_depth = parent_meta.get('bit_depth', '')
        if not custom:    custom    = parent_meta.get('custom',    '')

    # ── 16. Adaptation codec selon source (convention G3MINI) ─────────────────
    #   REMUX        → HEVC / AVC
    #   WEB          → H265 / H264  (sauf si MI ou nom original confirme x264/x265)
    #   WEBRip/BDRip/TVRip → x265 / x264
    #   BluRay/HDLight/DVDRip → inchangé
    #
    # Exception prioritaire : si le nom ORIGINAL contient explicitement x264 ou
    # x265, on conserve la notation telle quelle sans aucune conversion.
    _orig_has_x26x = bool(re.search(r'(?:^|[.\s_\-])x26[45](?:[.\s_\-]|$)', original, re.IGNORECASE))
    if codec and not _orig_has_x26x:
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

    # ── 17. Titre = résidu (ou titre TMDB international si fourni) ───────────
    name = re.sub(r'\([^)]*\)', '', name)
    if tmdb_title:
        title = _clean_title(tmdb_title)
    elif episode and _ep_title_prefix:
        # For single episodes, restrict title to text before SxxExx to avoid
        # including the episode subtitle in the series title.
        title = _clean_title(_ep_title_prefix)
    else:
        title = _clean_title(name)

    # ── Reconstruction ────────────────────────────────────────────────────────
    new = title
    if year:
        new += f".{year}"
    if doc:
        new += ".DOC"
    if is_tv:
        if episode:
            new += f".{episode}"
        elif season:
            new += f".{season}"
            if is_season_pack:
                new += ".COMPLETE"
    if extras:
        new += extras        # commence déjà par '.'
    if custom:
        new += f".{custom}"
    if lang:
        new += f".{lang}"
    if ad:
        new += ".AD"
    if hmax:
        new += f".{hmax}"
    if res:         new += f".{res}"
    if bit_depth:   new += f".{bit_depth}"
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

    new = re.sub(r'\.{2,}', '.', new)
    # Strip any residual video extension (should never appear in a release name)
    new = re.sub(r'\.(mkv|mp4|avi|ts|m2ts|iso)$', '', new, flags=re.IGNORECASE)
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
    parent_names: Optional[list[str]] = None,
    tmdb_title: Optional[str] = None,
    is_documentary: bool = False,
) -> str:
    return _parse_release(
        release_name,
        mediainfo_text,
        is_silent,
        tv_year=tv_year,
        torrent_pack=torrent_pack,
        parent_names=parent_names,
        tmdb_title=tmdb_title,
        is_documentary=is_documentary,
    )