#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
preview_release.py — Prévisualisation des release names (mode brut + mode Unit3dup)

Usage:
  python preview_release.py "Mon.Release.Name.1080p.WEBRip.x265-KFL"
  python preview_release.py /path/to/video.mkv --unit3dup
  python preview_release.py /path/to/folder --unit3dup
  python preview_release.py --batch /path/to/list.txt --unit3dup
  ls /storage/Upload/Series/ | python preview_release.py --stdin --unit3dup
  python preview_release.py --interactive --unit3dup
"""

import sys
import os
import argparse
import re
from pathlib import Path

# ── Couleurs ANSI ─────────────────────────────────────────────────────────────
RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

USE_COLOR = sys.stdout.isatty()
DEFAULT_SERIES_DIR = Path("/storage/Upload/Series")


def c(color, text):
    return f"{color}{text}{RESET}" if USE_COLOR else text


# ── Imports projet ────────────────────────────────────────────────────────────
try:
    from release_normalizer import normalize_release_name
    from common.mediainfo import MediaFile
    from common.utility import ManageTitles
except ImportError:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(script_dir)
    candidates = [
        repo_root,
        script_dir,
        os.path.join(script_dir, "unit3dup"),
        "/home/KFL/unit3dup",
        "/home/KFL/unit3dup/unit3dup",
    ]
    for path in candidates:
        if os.path.isdir(path) and path not in sys.path:
            sys.path.insert(0, path)
        has_release_normalizer = os.path.isfile(os.path.join(path, "release_normalizer.py"))
        has_common_pkg = os.path.isdir(os.path.join(path, "common"))
        if has_release_normalizer or has_common_pkg:
            from release_normalizer import normalize_release_name
            from common.mediainfo import MediaFile
            from common.utility import ManageTitles
            break
    else:
        print(f"{RED}ERREUR : modules du projet introuvables.{RESET}")
        print("Place ce script dans le repo unit3dup, ou adapte la liste `candidates`.")
        sys.exit(1)


def _build_release_from_path(path_str: str) -> tuple[str, str | None, bool, bool]:
    """
    Simule la partie naming de Unit3dup :
    - display_name nettoyé depuis fichier/dossier
    - conversion espaces -> points
    - lecture MediaInfo + détection film muet
    """
    path = Path(path_str).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Chemin introuvable: {path}")

    media_file_path: Path | None = None

    if path.is_file():
        display_name = path.stem
        media_file_path = path
    else:
        display_name = path.name
        for candidate in sorted(path.rglob("*")):
            if candidate.is_file() and ManageTitles.filter_ext(candidate.name):
                media_file_path = candidate
                break

    if not media_file_path:
        raise RuntimeError(f"Aucun fichier média trouvé dans: {path}")

    display_name = ManageTitles.clean_text(display_name)
    display_name = re.sub(r"[\[\]()]", "", display_name)
    release_name = display_name.replace(" ", ".")

    media = MediaFile(str(media_file_path))
    mediainfo_text = media.info or None
    is_silent = bool(media.is_silent)
    torrent_pack = bool(
        re.search(r"(S\d+(?!.*E\d+))|(S\d+E\d+-E?\d+)", str(path), re.IGNORECASE)
    )
    return release_name, mediainfo_text, is_silent, torrent_pack


def _resolve_series_entry(raw: str) -> Path | None:
    """
    Résout une entrée issue de `ls /storage/Upload/Series/`.
    Accepte un chemin absolu, relatif, ou juste un nom de dossier.
    """
    candidate = Path(raw).expanduser()
    search_paths = [
        candidate,
        Path.cwd() / candidate,
        DEFAULT_SERIES_DIR / candidate,
    ]
    for p in search_paths:
        if p.exists():
            return p.resolve()
    return None


# ── Diff token par token ───────────────────────────────────────────────────────
def _tokenize(name: str) -> list[str]:
    return re.split(r"([-.])", name)


def diff_display(original: str, normalized: str) -> str:
    if original == normalized:
        return c(GREEN, normalized)

    orig_tokens = _tokenize(original)
    norm_tokens = _tokenize(normalized)

    orig_set = set(orig_tokens)
    norm_set = set(norm_tokens)

    result = []
    for tok in norm_tokens:
        if tok in ("-", "."):
            result.append(tok)
        elif tok not in orig_set:
            result.append(c(GREEN, tok))
        else:
            result.append(tok)

    removed = [t for t in orig_tokens if t not in norm_set and t not in ("-", ".")]

    line = "".join(result)
    if removed:
        line += c(DIM, f"  [-{','.join(removed)}]")
    return line


def preview_one(
    raw: str,
    idx: int = None,
    total: int = None,
    use_unit3dup_mode: bool = False,
    auto_resolve_series: bool = False,
) -> bool:
    raw = raw.strip()
    if not raw:
        return False

    try:
        if use_unit3dup_mode:
            source_path = raw
            raw, mediainfo_text, is_silent, torrent_pack = _build_release_from_path(raw)
            normalized = normalize_release_name(
                raw, mediainfo_text, is_silent, torrent_pack=torrent_pack
            )
        elif auto_resolve_series:
            resolved = _resolve_series_entry(raw)
            if resolved:
                source_path = str(resolved)
                raw, mediainfo_text, is_silent, torrent_pack = _build_release_from_path(source_path)
                normalized = normalize_release_name(
                    raw, mediainfo_text, is_silent, torrent_pack=torrent_pack
                )
            else:
                source_path = None
                normalized = normalize_release_name(raw)
        else:
            source_path = None
            normalized = normalize_release_name(raw)
    except Exception as e:
        prefix = f"[{idx}/{total}] " if idx else ""
        print(f"{prefix}{c(RED, 'ERREUR')} sur : {raw}")
        print(f"  {e}")
        return False

    changed = raw != normalized
    prefix = f"[{idx}/{total}] " if idx else ""

    if source_path:
        print(f"{prefix}{c(CYAN, 'PATH  ')}  {source_path}")

    if changed:
        print(f"{prefix}{c(YELLOW, 'BEFORE')}  {c(DIM, raw)}")
        print(f"{prefix}{c(GREEN, 'AFTER ')}  {diff_display(raw, normalized)}")
        print()
    else:
        print(f"{prefix}{c(CYAN, 'OK    ')}  {normalized}")

    return changed


def run_args(names: list[str], use_unit3dup_mode: bool):
    total = len(names)
    changed = sum(preview_one(n, i + 1, total, use_unit3dup_mode=use_unit3dup_mode) for i, n in enumerate(names))
    _summary(total, changed)


def run_batch(filepath: str, use_unit3dup_mode: bool):
    with open(filepath, encoding="utf-8") as f:
        names = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    total = len(names)
    changed = sum(preview_one(n, i + 1, total, use_unit3dup_mode=use_unit3dup_mode) for i, n in enumerate(names))
    _summary(total, changed)


def run_stdin(use_unit3dup_mode: bool):
    names = [l.strip() for l in sys.stdin if l.strip() and not l.startswith("#")]
    total = len(names)
    changed = sum(
        preview_one(
            n,
            i + 1,
            total,
            use_unit3dup_mode=use_unit3dup_mode,
            auto_resolve_series=not use_unit3dup_mode,
        )
        for i, n in enumerate(names)
    )
    _summary(total, changed)


def run_interactive(use_unit3dup_mode: bool):
    print(c(BOLD, "=== Preview normalizer interactif (Ctrl+C pour quitter) ===\n"))
    while True:
        try:
            raw = input(c(CYAN, "Release/Path > ")).strip()
        except (KeyboardInterrupt, EOFError):
            print()
            break
        if not raw:
            continue
        preview_one(raw, use_unit3dup_mode=use_unit3dup_mode)


def _summary(total: int, changed: int):
    unchanged = total - changed
    print(c(BOLD, "─" * 60))
    print(f"  Total    : {total}")
    print(f"  {c(GREEN, 'Modifiées')} : {changed}")
    print(f"  {c(CYAN, 'Identiques')}: {unchanged}")


def main():
    parser = argparse.ArgumentParser(
        description="Prévisualise la normalisation des release names (brut ou mode Unit3dup).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("names", nargs="*", help="Releases (mode brut) ou chemins (mode --unit3dup)")
    parser.add_argument("--batch", "-b", metavar="FILE", help="Fichier texte avec une entrée par ligne")
    parser.add_argument("--stdin", "-", action="store_true", help="Lire les entrées depuis stdin (pipe)")
    parser.add_argument("--interactive", "-i", action="store_true", help="Mode saisie interactive")
    parser.add_argument(
        "--unit3dup",
        action="store_true",
        help="Simule Unit3dup: entrée traitée comme chemin, lecture MediaInfo, puis normalisation réelle.",
    )

    args = parser.parse_args()

    if args.interactive:
        run_interactive(args.unit3dup)
    elif args.stdin or (not sys.stdin.isatty() and not args.names and not args.batch):
        run_stdin(args.unit3dup)
    elif args.batch:
        run_batch(args.batch, args.unit3dup)
    elif args.names:
        run_args(args.names, args.unit3dup)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
