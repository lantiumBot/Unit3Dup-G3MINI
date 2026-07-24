"""Source-folder scanning: media type detection, episode lookup, scan_source."""
import re
import uuid
from pathlib import Path

from core.db import db_history_paths_set


def _item_size_gb(path: Path, is_file: bool) -> float:
    """Compute local size in GB. Returns 0.0 on error."""
    try:
        if is_file:
            return round(path.stat().st_size / (1024**3), 3)
        total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
        return round(total / (1024**3), 3)
    except Exception:
        return 0.0

_norm = lambda n: n.replace(".", " ").replace("_", " ").replace("-", " ")

_TECH_TOKENS = {
    "FRENCH", "MULTI", "AD", "VFF", "VFQ", "VF", "VO", "VOST", "VOSTFR",
    "1080P", "720P", "4K", "2160P", "480P",
    "WEB", "WEBRIP", "WEBDL", "BLURAY", "HDTV", "DVDRIP", "BDRIP",
    "H264", "H265", "X264", "X265", "HEVC", "AVC", "XVID",
    "AAC", "AC3", "DDP", "DTS", "TRUEHD", "FLAC", "MP3",
    "REPACK", "PROPER", "EXTENDED", "UNRATED", "REMUX",
}

def _series_tokens(name: str) -> list[str]:
    """Extract the first meaningful title tokens from a folder name, ignoring technical tags."""
    tokens = re.split(r'[\.\s_\-]+', name.upper())
    result = []
    for tok in tokens:
        if not tok.isalpha() or len(tok) <= 1:
            break
        if tok in _TECH_TOKENS:
            break
        result.append(tok)
        if len(result) == 3:
            break
    return result

def _same_series(name_a: str, name_b: str) -> bool:
    """Return True if two folder names share the same series title prefix (≥ 2 tokens)."""
    ta, tb = _series_tokens(name_a), _series_tokens(name_b)
    if len(ta) < 1 or len(tb) < 1:
        return False
    n = min(len(ta), len(tb), 2)
    return ta[:n] == tb[:n]
_VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".m2ts"}

_SE_RE     = re.compile(r'S(\d{1,2})E(\d{1,4})', re.IGNORECASE)
_S_RE      = re.compile(r'(?:^|[.\s_\-])S(\d{1,2})(?:[.\s_\-]|$)', re.IGNORECASE)
# Matches standalone Exx without a preceding Sxx — absolute episode numbering (anime)
_ABS_EP_RE = re.compile(r'(?:^|[.\s_\-])E(\d{1,4})(?:[.\s_\-]|$)', re.IGNORECASE)

# Text-based season markers: "Second Season", "Season 2", "Saison 2"
_ORDINAL_SEASON_RE = re.compile(
    r'(?:^|[.\s_\-])'
    r'(?P<ord>First|Second|Third|Fourth|Fifth|Sixth|Seventh|Eighth|Ninth|Tenth)'
    r'[.\s_\-]+Season'
    r'(?:[.\s_\-]|$)',
    re.IGNORECASE,
)
_NUMERIC_SEASON_RE = re.compile(
    r'(?:^|[.\s_\-])S(?:ea|ai)son[.\s_\-]*(\d{1,2})(?:[.\s_\-]|$)',
    re.IGNORECASE,
)
_ORDINAL_MAP = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
}


def _parse_season_from_text(name: str) -> int | None:
    """Detect text-based season indicators not caught by the SxxExx regex.

    Handles:
    - "Second Season" / "Third Season" … (ordinal + word)
    - "Season 2" / "Saison 2" (keyword + digit)
    Returns the season number as int, or None if not found.
    """
    m = _ORDINAL_SEASON_RE.search(name)
    if m:
        return _ORDINAL_MAP.get(m.group("ord").lower())
    m = _NUMERIC_SEASON_RE.search(name)
    return int(m.group(1)) if m else None


def _parse_season_episode(name: str) -> tuple[int | None, int | None]:
    """Extract (season_num, episode_num) from a release name.

    Returns (season, None) for season packs (SxxEyy absent),
    (None, None) when no season/episode marker found.
    """
    m = _SE_RE.search(name)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = _S_RE.search(name)
    if m:
        return int(m.group(1)), None
    # Text-based: "Second Season", "Season 2", "Saison 2"
    s = _parse_season_from_text(name)
    return (s, None) if s is not None else (None, None)


def _absolute_episode_number(name: str) -> int | None:
    """Return the absolute episode number when name has Exx but no Sxx.

    Returns None when a SxxExx / Sxx marker is already present (standard notation).
    Common in anime releases where teams use overall episode count instead of
    per-season numbering (e.g. Hachimitsu.to.Clover.E18 instead of S01E18).
    """
    if _SE_RE.search(name) or _S_RE.search(name):
        return None
    m = _ABS_EP_RE.search(name)
    return int(m.group(1)) if m else None


def _parse_media_info(name: str) -> dict:
    """Parse resolution, source type, and video codec from a release name using guessit.

    Returns a dict with keys: ``resolution``, ``source_type``, ``encoding``.
    All values are strings (empty string when unknown/not detected).
    Gracefully handles missing guessit dependency.
    """
    try:
        from guessit import guessit as _guessit  # type: ignore[import-untyped]
        g = _guessit(name)
        return {
            "resolution":  str(g.get("screen_size") or ""),
            "source_type": str(g.get("source") or ""),
            "encoding":    str(g.get("video_codec") or ""),
        }
    except Exception:
        return {"resolution": "", "source_type": "", "encoding": ""}

# Patterns for files that should NOT count as "real" movies when deciding
# whether a folder is a multi-movie collection.  A folder with only one
# "real" movie + a sample / trailer is NOT a collection.
_EXTRA_RE = re.compile(
    r"\b(sample|trailer|teaser|featurette|extra|bonus|"
    r"behind.the.scenes|deleted.scenes|interview|short|clip)\b",
    re.I,
)

_INTEGRALE_RE  = re.compile(r"\bINTEGRALE\b", re.I)
_SEASON_RE     = re.compile(r"\bS\d{2}\b", re.I)
_S01E01_RE     = re.compile(r"[Ss]01[Ee]01", re.I)
_SEASON_DIR_RE = re.compile(r"[Ss](?:aison\s*)?(\d+)", re.I)


def _is_integrale(name: str) -> bool:
    return bool(_INTEGRALE_RE.search(name))


def _is_season(name: str) -> bool:
    # _SEASON_RE catches season packs (bare Sxx without episode number).
    # _SE_RE catches single-episode folders (SxxExx) — _SEASON_RE fails on them
    # because the word-boundary \b after the digits is blocked by the following 'E'.
    return bool(_SE_RE.search(name)) or bool(_SEASON_RE.search(name)) or _parse_season_from_text(name) is not None


def _filename_is_s01e01(stem: str) -> bool:
    return bool(_S01E01_RE.search(stem))


def _season_number_from_dirname(name: str) -> int | None:
    m = _SEASON_DIR_RE.search(name)
    return int(m.group(1)) if m else None


def pack_is_season1(name: str) -> bool:
    return _season_number_from_dirname(name) == 1


def find_s01e01_upload_file(directory: str, *, search_sibling_s01: bool = False) -> str | None:
    root = Path(directory)
    if not root.is_dir():
        return None
    skip = {"http_cache"}
    found: list[Path] = []

    def scan_dir(d: Path, depth: int = 0) -> None:
        if depth > 2:
            return
        try:
            children = sorted(d.iterdir())
        except PermissionError:
            return
        for child in children:
            if child.name in skip:
                continue
            try:
                if child.is_file() and child.suffix.lower() in _VIDEO_EXTS and child.stat().st_size > 0:
                    if _filename_is_s01e01(child.stem):
                        found.append(child)
                elif child.is_dir() and depth < 2:
                    scan_dir(child, depth + 1)
            except PermissionError:
                continue

    scan_dir(root)
    if not found and search_sibling_s01 and root.parent.is_dir():
        try:
            siblings = sorted(root.parent.iterdir())
        except PermissionError:
            siblings = []
        for sib in siblings:
            if not sib.is_dir() or sib == root or sib.name in skip:
                continue
            if pack_is_season1(sib.name) and _same_series(root.name, sib.name):
                scan_dir(sib)
                if found:
                    break
    return str(found[0]) if found else None


def episode_upload_for_item(item: dict) -> str | None:
    path = item.get("path") or ""
    name = item.get("name") or Path(path).name
    t    = item.get("type")
    if t == "integrale":
        return find_s01e01_upload_file(path, search_sibling_s01=True)
    if t == "season" and pack_is_season1(name):
        return find_s01e01_upload_file(path, search_sibling_s01=False)
    return None


def _tag(n: str) -> str:
    return n.rsplit("-", 1)[-1] if "-" in n else ""


def _safe_iterdir(path: Path) -> tuple[list, bool]:
    """Return (children, permission_ok). On PermissionError returns ([], False)."""
    try:
        return list(path.iterdir()), True
    except PermissionError:
        return [], False


def _tag_ok(tag: str, valid_tags: list) -> bool:
    """True if tag matches valid_tags (case-insensitive), or if no filtering is configured."""
    if not valid_tags:
        return True
    return tag.upper() in {t.upper() for t in valid_tags}


def _has_video_files(children: list) -> bool:
    """True if the list of paths contains at least one non-empty video file."""
    for c in children:
        try:
            if c.is_file() and c.suffix.lower() in _VIDEO_EXTS and c.stat().st_size > 0:
                return True
        except (PermissionError, OSError):
            continue
    return False


def _list_video_files(children: list) -> list[str]:
    """Return sorted list of non-empty video file paths from the list of directory entries."""
    files: list[str] = []
    for c in children:
        try:
            if c.is_file() and c.suffix.lower() in _VIDEO_EXTS and c.stat().st_size > 0:
                files.append(str(c))
        except (PermissionError, OSError):
            continue
    return sorted(files)


def _is_extra_file(path_str: str) -> bool:
    """True if a video file looks like a trailer / sample / featurette (not a main movie)."""
    return bool(_EXTRA_RE.search(Path(path_str).stem))


def _real_movie_files(video_files: list[str]) -> list[str]:
    """Filter out extras/samples from a list of video file paths.

    Keeps files that do NOT match the _EXTRA_RE pattern.  If filtering would
    leave an empty list, returns the original list unchanged so a folder with
    only a sample is still treated as a movie (edge case).
    """
    real = [f for f in video_files if not _is_extra_file(f)]
    return real if real else video_files


def _is_collection_by_rule(tag: str, tag_valid: bool, col_rule: dict) -> bool:
    """Return True if the folder should be classified as a collection.

    Logic (in priority order):
      1. If collection_tags is non-empty → only True when tag matches one of them.
         This is the primary fix for the reported bug: folders whose tag is NOT
         in collection_tags (e.g., FHD, 4K) are NOT classified as collections.
      2. If require_valid_tag=True  → only True when tag is in valid_tags.
      3. Default → True (any multi-video folder is a collection, legacy behaviour).
    """
    col_tags = [t.strip().upper() for t in (col_rule.get("collection_tags") or []) if t.strip()]
    if col_tags:
        return tag.upper() in col_tags
    if col_rule.get("require_valid_tag", False):
        return tag_valid
    return True


_SKIP_NAMES = {"http_cache"}


def _scan_dir(
    p: Path,
    valid_tags: list,
    rules: dict,
    hist: set[str],
    *,
    include_history: bool,
) -> tuple[list, list]:
    """Scan one directory level. Returns (items, skipped_paths).

    *hist* is a set of paths already in history (path column only) — lighter
    than loading full history rows just to check membership.
    """
    items:   list[dict] = []
    skipped: list[str]  = []

    children, ok = _safe_iterdir(p)
    if not ok:
        skipped.append(str(p))
        return items, skipped

    for child in sorted(children):
        if child.name in _SKIP_NAMES:
            continue
        name     = child.name
        path_str = str(child)

        try:
            is_file = child.is_file()
            is_dir  = child.is_dir()
        except PermissionError:
            skipped.append(path_str)
            continue

        # ── Video file ────────────────────────────────────────────────────
        if is_file and child.suffix.lower() in _VIDEO_EXTS:
            try:
                if child.stat().st_size == 0:
                    continue
            except PermissionError:
                skipped.append(path_str)
                continue
            tag       = _tag(child.stem)
            tag_valid = _tag_ok(tag, valid_tags)
            mi        = _parse_media_info(name)
            if path_str in hist:
                if not include_history:
                    continue
                items.append({
                    "id": str(uuid.uuid4()), "path": path_str, "name": name,
                    "type": "file", "seasons": [],
                    "tag": tag, "tag_valid": True,
                    "status": "history", "history": {},
                    "size_gb": _item_size_gb(child, True),
                    **mi,
                })
            else:
                # Tag invalide/absent → toujours "pending" (sélectionnable) mais non coché
                # dans l'UI (tag_valid=False indique à l'UI de laisser décoché par défaut).
                items.append({
                    "id": str(uuid.uuid4()), "path": path_str, "name": name,
                    "type": "file", "seasons": [],
                    "tag": tag, "tag_valid": tag_valid,
                    "status": "pending",
                    "size_gb": _item_size_gb(child, True),
                    **mi,
                })
            continue

        if not is_dir:
            continue

        # ── Directory ────────────────────────────────────────────────────
        sub_children, sub_ok = _safe_iterdir(child)
        if not sub_ok:
            skipped.append(path_str)
            continue
        if not sub_children:
            continue

        tag = _tag(name)
        mi  = _parse_media_info(name)

        if path_str in hist:
            if not include_history:
                continue
            items.append({
                "id": str(uuid.uuid4()), "path": path_str, "name": name,
                "type": "unknown", "seasons": [],
                "tag": tag, "tag_valid": True,
                "status": "history", "history": {},
                "size_gb": _item_size_gb(child, False),
                **mi,
            })
            continue

        if rules.get("integrale", {}).get("enabled") and _is_integrale(name):
            tag_valid = _tag_ok(tag, valid_tags)
            seasons = []
            if rules["integrale"].get("upload_seasons"):
                sub_list, _ = _safe_iterdir(child)
                for sub in sorted(sub_list):
                    if not sub.is_dir() or sub.name in _SKIP_NAMES:
                        continue
                    sub2, _ = _safe_iterdir(sub)
                    if sub2:
                        seasons.append(str(sub))
            # Chercher S01E01 indépendamment de la validité du tag
            ep = find_s01e01_upload_file(path_str, search_sibling_s01=True)
            items.append({
                "id": str(uuid.uuid4()), "path": path_str, "name": name,
                "type": "integrale", "seasons": seasons,
                "episode_upload": ep,
                "tag": tag, "tag_valid": tag_valid,
                "status": "pending",   # toujours sélectionnable, tag_valid guide l'UI
                "size_gb": _item_size_gb(child, False),
                **mi,
            })

        elif rules.get("complete_or_season", {}).get("enabled") and _is_season(name):
            req       = rules["complete_or_season"].get("require_valid_tag", True)
            tag_valid = not req or _tag_ok(tag, valid_tags)
            ep        = find_s01e01_upload_file(path_str) if pack_is_season1(name) else None
            items.append({
                "id": str(uuid.uuid4()), "path": path_str, "name": name,
                "type": "season", "seasons": [],
                "episode_upload": ep,
                "tag": tag, "tag_valid": tag_valid,
                "status": "pending",   # toujours sélectionnable
                "size_gb": _item_size_gb(child, False),
                **mi,
            })
        else:
            video_files = _list_video_files(sub_children)
            n_videos    = len(video_files)
            if n_videos == 0:
                # Dossier sans vidéo — peut être un sous-dossier source en mode récursif
                items.append({
                    "id": str(uuid.uuid4()), "path": path_str, "name": name,
                    "type": "unknown", "seasons": [],
                    "tag": tag, "tag_valid": False, "status": "skip",
                    "size_gb": _item_size_gb(child, False),
                    **mi,
                })
            elif n_videos == 1:
                # Un seul fichier vidéo dans le dossier — traité comme film simple
                tag_valid = _tag_ok(tag, valid_tags)
                items.append({
                    "id": str(uuid.uuid4()), "path": path_str, "name": name,
                    "type": "file", "seasons": [],
                    "tag": tag, "tag_valid": tag_valid,
                    "status": "pending",   # toujours sélectionnable
                    "size_gb": _item_size_gb(child, False),
                    **mi,
                })
            else:
                # 2+ fichiers vidéo : peut être une collection ou un film avec extras.
                # 1) Filtrer les extras/samples pour ne compter que les "vrais" films.
                real_files = _real_movie_files(video_files)
                tag_valid  = _tag_ok(tag, valid_tags)
                col_rule   = rules.get("collection", {})

                if len(real_files) <= 1:
                    # Après filtrage des extras il reste ≤ 1 film → pas une collection
                    items.append({
                        "id": str(uuid.uuid4()), "path": path_str, "name": name,
                        "type": "file", "seasons": [],
                        "tag": tag, "tag_valid": tag_valid,
                        "status": "pending",
                        "size_gb": _item_size_gb(child, False),
                        **mi,
                    })
                elif col_rule.get("enabled", True) and _is_collection_by_rule(tag, tag_valid, col_rule):
                    # Plusieurs films réels ET la règle collection le confirme
                    items.append({
                        "id": str(uuid.uuid4()), "path": path_str, "name": name,
                        "type": "collection", "seasons": [],
                        "files": real_files,   # liste ordonnée des films (sans extras)
                        "tag": tag, "tag_valid": tag_valid,
                        "status": "pending",
                        "size_gb": _item_size_gb(child, False),
                        **mi,
                    })
                else:
                    # Règle collection non satisfaite (tag non reconnu comme collection)
                    # → traiter comme film simple (le dossier sera uploadé tel quel)
                    items.append({
                        "id": str(uuid.uuid4()), "path": path_str, "name": name,
                        "type": "file", "seasons": [],
                        "tag": tag, "tag_valid": tag_valid,
                        "status": "pending",
                        "size_gb": _item_size_gb(child, False),
                        **mi,
                    })

    return items, skipped


def scan_source(
    source: str,
    valid_tags: list,
    rules: dict,
    *,
    include_history: bool = False,
    recursive: bool = False,
) -> tuple[list, str | None, list[str]]:
    """Scan source folder. Returns (items, error, skipped_paths).

    If recursive=True, unknown subdirectories are expanded one level deeper
    (useful when source contains category sub-folders like Movies/, Series/).
    """
    p = Path(source)
    if not p.exists():
        return [], f"Dossier introuvable: {source}", []

    hist = db_history_paths_set()
    items, skipped = _scan_dir(p, valid_tags, rules, hist, include_history=include_history)

    if recursive:
        expanded: list[dict] = []
        remove_ids: set[str] = set()
        for item in items:
            if item["type"] != "unknown":
                continue
            sub_path = Path(item["path"])
            if not sub_path.is_dir():
                continue
            sub_items, sub_skipped = _scan_dir(
                sub_path, valid_tags, rules, hist, include_history=include_history
            )
            if sub_items:
                remove_ids.add(item["id"])
                expanded.extend(sub_items)
                skipped.extend(sub_skipped)
        if remove_ids:
            items = [i for i in items if i["id"] not in remove_ids] + expanded

    for item in items:
        s, e = _parse_season_episode(item.get("name", ""))
        item["season_num"]  = s
        item["episode_num"] = e
        # Absolute episode (Exx without Sxx) — season TBD via TMDB lookup
        if s is None and e is None:
            abs_ep = _absolute_episode_number(item.get("name", ""))
            if abs_ep is not None:
                item["episode_num"]      = abs_ep
                item["absolute_episode"] = True

    return items, None, skipped
