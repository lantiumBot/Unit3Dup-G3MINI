#!/usr/bin/env bash
set -u

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
UNIT3DUP=""

# Cree .venv + pip install -e . si besoin, sinon utilise le binaire du venv (comme unit3dup-wrapper.sh)
ensure_unit3dup() {
  local bin="$VENV_DIR/bin/unit3dup"

  if [[ -x "$bin" ]]; then
    UNIT3DUP="$bin"
    return 0
  fi

  if [[ ! -d "$VENV_DIR" ]]; then
    echo "[SETUP] Environnement virtuel absent, creation de .venv ..."
    if ! command -v python3 &>/dev/null; then
      echo "[ERREUR] python3 introuvable. Installez Python 3 puis relancez."
      exit 1
    fi
    python3 -m venv "$VENV_DIR" || exit 1
  fi

  echo "[SETUP] Installation de unit3dup dans le venv ..."
  # shellcheck source=/dev/null
  source "$VENV_DIR/bin/activate"
  pip install -e "$SCRIPT_DIR" || exit 1

  if [[ ! -x "$bin" ]]; then
    echo "[ERREUR] Installation echouee : $bin introuvable."
    exit 1
  fi
  UNIT3DUP="$bin"
}

# ============================================================
# upload.sh - Parcours un dossier et envoie vers unit3dup
#   - fichier video -> unit3dup -u
#   - sous-dossier   -> unit3dup -f
#
# Usage:
#   ./upload.sh <chemin> [--confirm]
#   ./upload.sh --confirm <chemin>
#
# Exemples:
#   ./upload.sh /chemin/vers/mes/releases
#   ./upload.sh ~/Downloads/upload --confirm
#
# Si .venv est absent, il est cree automatiquement (pip install -e .).
# Sinon, utilise .venv/bin/unit3dup comme unit3dup-wrapper.sh.
# ============================================================

CONFIRM_FLAG=""
ROOT=""

usage() {
  cat <<'EOF'
Usage: ./upload.sh <chemin> [options]

  <chemin>    Dossier a parcourir (obligatoire)

Options:
  --confirm, -confirm   Demande confirmation avant chaque upload (-u)

Exemples:
  ./upload.sh /data/uploads
  ./upload.sh ./releases --confirm
EOF
}

# Dossiers a ignorer (modifiable si besoin)
SKIP_DIRS=("http_cache")

while [[ $# -gt 0 ]]; do
  case "$1" in
    --confirm|-confirm)
      CONFIRM_FLAG="-confirm"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --root)
      echo "[ERREUR] L'option --root n'existe plus : passez le chemin en argument."
      echo ""
      usage
      exit 1
      ;;
    -*)
      echo "[ERREUR] Option inconnue: $1"
      echo ""
      usage
      exit 1
      ;;
    *)
      if [[ -n "$ROOT" ]]; then
        echo "[ERREUR] Un seul chemin est attendu (recu en trop: $1)"
        echo ""
        usage
        exit 1
      fi
      ROOT="$1"
      shift
      ;;
  esac
done

if [[ -z "$ROOT" ]]; then
  echo "[ERREUR] Chemin obligatoire."
  echo ""
  usage
  exit 1
fi

ROOT="${ROOT%/}"

if [[ ! -d "$ROOT" ]]; then
  echo "[ERREUR] Dossier introuvable: $ROOT"
  exit 1
fi

ensure_unit3dup

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
    "$UNIT3DUP" -u "$item" $CONFIRM_FLAG
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
    "$UNIT3DUP" -f "$item"
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
