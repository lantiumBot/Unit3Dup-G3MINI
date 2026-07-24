#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
source "$SCRIPT_DIR/.venv/bin/activate"
"$SCRIPT_DIR/.venv/bin/unit3dup" "$@"
