#!/usr/bin/env bash
# Lance le dashboard web Unit3Dup (Flask + Socket.IO)
# Délègue à start_web.py.
#
# Exemples :
#   ./start_web.sh
#   ./start_web.sh --host 127.0.0.1 --port 8080
#   ./start_web.sh --daemon
#   ./start_web.sh --host 127.0.0.1 --port 8080 --daemon
#   HOST=10.0.0.2 PORT=5000 ./start_web.sh
set -u

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-5000}"

if [[ $# -eq 0 ]]; then
  exec python3 "$SCRIPT_DIR/start_web.py" --host "$HOST" --port "$PORT"
else
  exec python3 "$SCRIPT_DIR/start_web.py" "$@"
fi
