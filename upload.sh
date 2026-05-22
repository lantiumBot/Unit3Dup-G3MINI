#!/usr/bin/env bash
set -u

# ============================================================
# upload.sh - Upload mixte depuis une racine unique
# - fichier video -> unit3dup -u
# - dossier       -> unit3dup -f
#
# Usage:
#   ./upload.sh [--confirm] [racine]
#   ./upload.sh --root /chemin/vers/upload --confirm
# ============================================================

ROOT="/storage/Upload"
CONFIRM_FLAG=""

# Dossiers a ignorer (modifiable si besoin)
SKIP_DIRS=("http_cache")

while [[ $# -gt 0 ]]; do
  case "$1" in
    --confirm|-confirm)
      CONFIRM_FLAG="-confirm"
      shift
      ;;
    --root)
      if [[ $# -lt 2 ]]; then
        echo "[ERREUR] --root attend un chemin."
        exit 1
      fi
      ROOT="$2"
      shift 2
      ;;
    -*)
      echo "[ERREUR] Option inconnue: $1"
      echo "Usage: ./upload.sh [--confirm] [--root chemin] [racine]"
      exit 1
      ;;
    *)
      ROOT="$1"
      shift
      ;;
  esac
done

ROOT="${ROOT%/}"

if [[ ! -d "$ROOT" ]]; then
  echo "[ERREUR] Dossier introuvable: $ROOT"
  exit 1
fi

echo "====================================================="
echo "  Racine   : $ROOT"
echo "  Confirm  : ${CONFIRM_FLAG:-desactive}"
echo "====================================================="
echo ""

shopt -s nullglob

ok=0
skipped=0
errors=0

for item in "$ROOT"/*; do
  # 1) Fichier video -> -u
  if [[ -f "$item" ]]; then
    case "${item,,}" in
      *.mkv|*.mp4|*.avi|*.m2ts) ;;
      *)
        echo "[SKIP NON-VIDEO] $item"
        (( skipped++ ))
        continue
        ;;
    esac

    if [[ ! -s "$item" ]]; then
      echo "[SKIP EMPTY FILE] $item"
      (( skipped++ ))
      continue
    fi

    echo "[PROCESS FILE] $item"
    unit3dup -u "$item" $CONFIRM_FLAG
    rc=$?

    if [[ $rc -ne 0 ]]; then
      echo "[SKIP ERROR] $item (exit code $rc)"
      (( errors++ ))
      continue
    fi

    echo "[OK] $item"
    (( ok++ ))
    continue
  fi

  # 2) Dossier -> -f
  if [[ -d "$item" ]]; then
    base="$(basename "$item")"

    skip=0
    for s in "${SKIP_DIRS[@]}"; do
      if [[ "$base" == "$s" ]]; then
        echo "[SKIP EXCLUDED DIR] $item"
        skip=1
        break
      fi
    done
    [[ $skip -eq 1 ]] && { (( skipped++ )); continue; }

    shopt -s dotglob
    dir_entries=("$item"/*)
    shopt -u dotglob
    if [[ ${#dir_entries[@]} -eq 0 ]]; then
      echo "[SKIP EMPTY DIR] $item"
      (( skipped++ ))
      continue
    fi

    echo "[PROCESS DIR] $item"
    unit3dup -f "$item"
    rc=$?

    if [[ $rc -ne 0 ]]; then
      echo "[SKIP ERROR] $item (exit code $rc)"
      (( errors++ ))
      continue
    fi

    echo "[OK] $item"
    (( ok++ ))
    continue
  fi

  echo "[SKIP UNKNOWN] $item"
  (( skipped++ ))
done

echo ""
echo "====================================================="
echo "  Termine - OK: $ok | Skippes: $skipped | Erreurs: $errors"
echo "====================================================="
