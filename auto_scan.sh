#!/usr/bin/env bash
# auto_scan.sh - Scan automatique d'un dossier de séries avec filtrage par tag
#
# Logique par dossier enfant du répertoire racine :
#   - Nom contient INTEGRALE → upload intégrale + chaque sous-dossier saison
#   - Nom contient COMPLETE ou Sxx → upload uniquement si le tag de fin est dans la liste valide
#   - Sinon → ignoré
#
# Usage:
#   ./auto_scan.sh <chemin_racine> [--tags <fichier>] [--confirm] [--dry-run]
#
# Exemples:
#   ./auto_scan.sh /mnt/Serie
#   ./auto_scan.sh /mnt/Serie --tags /etc/valid_tags.json --confirm
#   ./auto_scan.sh /mnt/Serie --dry-run

set -u

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
UNIT3DUP=""

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

TAGS_FILE="$SCRIPT_DIR/valid_tags.json"
ROOT=""
CONFIRM_FLAG=""
DRY_RUN=0
SKIP_DIRS=("http_cache")
VALID_TAGS=()

usage() {
  cat <<'EOF'
Usage: ./auto_scan.sh <chemin_racine> [options]

  <chemin_racine>     Dossier racine contenant les releases (ex: /mnt/Serie)

Options:
  --tags <fichier>    Fichier JSON ou TXT des tags valides (defaut: valid_tags.json)
  --confirm           Demande confirmation avant chaque upload
  --dry-run           Affiche les actions sans les executer
  -h, --help          Affiche cette aide

Logique de tri par sous-dossier:
  INTEGRALE           -> upload integrale + chaque sous-dossier saison
  COMPLETE ou Sxx     -> upload uniquement si le tag de fin est valide
  Autre               -> ignore

Format du fichier de tags:
  JSON  ->  {"tags": ["FW", "TyHD"]}  ou  ["FW", "TyHD"]
  TXT   ->  un tag par ligne (les lignes commencant par # sont ignorees)

Exemples:
  ./auto_scan.sh /mnt/Serie
  ./auto_scan.sh /mnt/Serie --tags ~/valid_tags.json --confirm
  ./auto_scan.sh /mnt/Serie --dry-run
EOF
}

# Charge les tags valides depuis un fichier JSON ou TXT dans VALID_TAGS[]
load_tags() {
  local file="$1"
  VALID_TAGS=()

  if [[ ! -f "$file" ]]; then
    echo "[ERREUR] Fichier de tags introuvable: $file"
    echo "         Creez-le ou utilisez --tags pour specifier un autre fichier."
    exit 1
  fi

  case "${file,,}" in
    *.json)
      if ! command -v python3 &>/dev/null; then
        echo "[ERREUR] python3 requis pour lire le fichier JSON."
        exit 1
      fi
      mapfile -t VALID_TAGS < <(python3 - "$file" <<'PYEOF'
import json, sys
try:
    with open(sys.argv[1]) as f:
        data = json.load(f)
    tags = data if isinstance(data, list) else data.get("tags", [])
    for t in tags:
        print(str(t).strip())
except Exception as e:
    print(f"[ERREUR JSON] {e}", file=sys.stderr)
    sys.exit(1)
PYEOF
      )
      ;;
    *)
      mapfile -t VALID_TAGS < <(grep -v '^\s*#' "$file" | grep -v '^\s*$' | sed 's/[[:space:]]//g')
      ;;
  esac

  if [[ ${#VALID_TAGS[@]} -eq 0 ]]; then
    echo "[AVERTISSEMENT] Aucun tag valide charge depuis: $file"
  fi
}

# Extrait le tag de release : partie apres le dernier '-'
get_release_tag() {
  local base="${1##*/}"
  base="${base%/}"
  # Rien apres un '-' → tag vide
  if [[ "$base" != *-* ]]; then
    echo ""
    return
  fi
  echo "${base##*-}"
}

# Verifie si le tag est dans VALID_TAGS (insensible à la casse)
is_valid_tag() {
  local tag="${1,,}"
  [[ -z "$tag" ]] && return 1
  for t in "${VALID_TAGS[@]}"; do
    [[ "${t,,}" == "$tag" ]] && return 0
  done
  return 1
}

# Verifie si le nom contient INTEGRALE (separateurs . _ - ou espace)
is_integrale() {
  local norm
  norm="${1##*/}"
  norm="${norm//./ }"; norm="${norm//_/ }"; norm="${norm//-/ }"
  [[ "$norm" =~ (^|[[:space:]])[Ii][Nn][Tt][Ee][Gg][Rr][Aa][Ll][Ee]([[:space:]]|$) ]]
}

# Verifie si le nom contient COMPLETE ou un pattern Sxx (S01..S99)
is_season_or_complete() {
  local norm
  norm="${1##*/}"
  norm="${norm//./ }"; norm="${norm//_/ }"; norm="${norm//-/ }"
  [[ "$norm" =~ (^|[[:space:]])[Cc][Oo][Mm][Pp][Ll][Ee][Tt][Ee]([[:space:]]|$) ]] && return 0
  [[ "$norm" =~ (^|[[:space:]])S[0-9]{2}([[:space:]]|$) ]] && return 0
  return 1
}

dir_is_empty() {
  local entries
  shopt -s dotglob
  entries=("$1"/*)
  shopt -u dotglob
  [[ ${#entries[@]} -eq 0 ]]
}

is_skipped_dir() {
  local base="$1"
  for s in "${SKIP_DIRS[@]}"; do
    [[ "$base" == "$s" ]] && return 0
  done
  return 1
}

do_upload() {
  local mode="$1"
  local path="$2"

  if [[ $DRY_RUN -eq 1 ]]; then
    echo "[DRY-RUN] unit3dup $mode \"$path\"${CONFIRM_FLAG:+ $CONFIRM_FLAG}"
    return 0
  fi

  "$UNIT3DUP" $mode "$path" $CONFIRM_FLAG
}

# --- Parse des arguments ---
while [[ $# -gt 0 ]]; do
  case "$1" in
    --tags)
      shift
      [[ -z "${1:-}" ]] && { echo "[ERREUR] --tags requiert un argument."; exit 1; }
      TAGS_FILE="$1"
      shift
      ;;
    --confirm|-confirm)
      CONFIRM_FLAG="-confirm"
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
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
  echo "[ERREUR] Chemin racine obligatoire."
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
load_tags "$TAGS_FILE"

echo "====================================================="
echo "  Racine    : $ROOT"
echo "  Tags      : $TAGS_FILE (${#VALID_TAGS[@]} tags charges)"
echo "  Confirm   : ${CONFIRM_FLAG:-desactive}"
echo "  Dry-run   : $([[ $DRY_RUN -eq 1 ]] && echo 'OUI' || echo 'non')"
echo "====================================================="
echo ""

shopt -s nullglob

ok=0
skipped=0
errors=0

upload_item() {
  local mode="$1"
  local path="$2"
  local label="$3"

  echo "[$label] $path"
  do_upload "$mode" "$path"
  local rc=$?
  if [[ $rc -ne 0 ]]; then
    echo "[ERREUR] $path (exit $rc)"
    (( errors++ )) || true
  else
    (( ok++ )) || true
  fi
}

for item in "$ROOT"/*/; do
  [[ -d "$item" ]] || continue
  base="$(basename "$item")"

  if is_skipped_dir "$base"; then
    echo "[SKIP EXCLU] $item"
    (( skipped++ )) || true
    continue
  fi

  if dir_is_empty "$item"; then
    echo "[SKIP VIDE] $item"
    (( skipped++ )) || true
    continue
  fi

  # --- CAS 1: INTEGRALE ---
  if is_integrale "$base"; then
    upload_item -f "$item" "INTEGRALE"

    # Upload aussi chaque sous-dossier saison
    for season_dir in "$item"/*/; do
      [[ -d "$season_dir" ]] || continue
      season_base="$(basename "$season_dir")"
      is_skipped_dir "$season_base" && continue
      dir_is_empty "$season_dir" && continue
      upload_item -f "$season_dir" "INTEGRALE SAISON"
    done
    continue
  fi

  # --- CAS 2: COMPLETE ou Sxx ---
  if is_season_or_complete "$base"; then
    tag="$(get_release_tag "$base")"
    if is_valid_tag "$tag"; then
      upload_item -f "$item" "SAISON/COMPLETE"
    else
      echo "[SKIP TAG INVALIDE] $item"
      echo "                    tag detecte: '${tag:-<aucun>}' — non present dans $TAGS_FILE"
      (( skipped++ )) || true
    fi
    continue
  fi

  # --- CAS 3: Non reconnu ---
  echo "[SKIP] $item (pas INTEGRALE, pas COMPLETE/Sxx)"
  (( skipped++ )) || true
done

shopt -u nullglob

echo ""
echo "====================================================="
echo "  Termine - OK: $ok | Skips: $skipped | Erreurs: $errors"
echo "====================================================="
[[ $errors -eq 0 ]]
exit $?
