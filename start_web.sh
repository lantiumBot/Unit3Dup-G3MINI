#!/usr/bin/env bash
# Lance le dashboard web Unit3Dup (Flask + Socket.IO)
# Délègue à start_web.py — crée le .venv et installe les dépendances au premier lancement.
#
# Exemples :
#   ./start_web.sh                                  # interactif, 0.0.0.0:5000
#   ./start_web.sh --host 127.0.0.1 --port 8080    # interface et port personnalisés
#   ./start_web.sh --daemon                         # arrière-plan (PID /tmp/u3dup-web.pid)
#   ./start_web.sh --host 0.0.0.0 --port 5000 --daemon
#   ./start_web.sh --stop                           # arrêt du daemon
#   ./start_web.sh --setup                          # assistant : mot de passe + certificat TLS
#   ./start_web.sh --tls                            # HTTPS (cert auto-signé dans web/.ssl/)
#   ./start_web.sh --tls --cert /path/cert.pem --key /path/key.pem
#   HOST=10.0.0.2 PORT=5000 ./start_web.sh          # via variables d'environnement
#
# Variables d'environnement :
#   HOST               Interface d'écoute (défaut : 0.0.0.0)
#   PORT               Port TCP (défaut : 5000)
#   U3D_WEB_HOST       Alias pour HOST (prioritaire si passé en argument)
#   U3D_WEB_PORT       Alias pour PORT (prioritaire si passé en argument)
#   U3D_SECRET_KEY     Clé secrète Flask (généré et stocké dans web/.secret_key si absent)
#   UNIT3DUP_CONFIG_ROOT  Chemin alternatif vers le dossier de configuration
#
# Logs :
#   Daemon stdout/stderr : /tmp/u3dup-web.log  (ou --log <fichier>)
#   Application Flask   : web/logs/app/app.log (rotation 5 Mo × 3)
#   Upload par job      : web/logs/upload/<job_id>.json  (purge auto par count max 500
#                         et par âge configuré via web_config.json log_retention_days)
#
# Fichiers d'état persistants (web/) :
#   history.db                Base SQLite WAL — 6 tables :
#                               history     (chemins uploadés)
#                               transcripts (job_id → résultat console)
#                               dup_cache   (cache TTL vérifications doublons)
#                               job_queue   (jobs pending — survivent au redémarrage)
#                               app_state   (état inventory Gemini, clé "gemini_inventory")
#                               scan_cache  (dernier scan par dossier, TTL 24 h —
#                                           complète le cache localStorage navigateur)
#                             Migration auto depuis JSON legacy au 1er lancement.
#
# Fonctionnalités notables du dashboard :
#   Scan asynchrone       : /api/scan retourne immédiatement un task_id ; progression
#                           via Socket.IO scan_progress + poll /api/scan/status/<id>
#   Cache scan serveur    : GET/POST /api/scan/cache — survit aux recharges navigateur
#   Tri colonnes scan     : clic sur en-tête Nom / Taille pour trier la table de scan
#   Sélection rapide      : boutons Pending / Films / Séries / Collections / Inverser
#   ETA file de jobs      : durée estimée globale visible au-dessus des cards de jobs
#   Graphiques stats      : Chart.js — uploads/erreurs par jour (7j/30j/90j)
#   Webhook Discord       : webhook_format="discord" → embed coloré compatible Discord/Slack
#   Filtre date historique: inputs from/to dans la page Historique
#   Transcript inline     : clic sur ligne historique → transcript affiché sans modale
#   Auto-logout restart   : Socket.IO connect_error + ping 15 s → redirect /login immédiat
#   Page Inventaire       : /inventory — tous les uploads Gemini, tri/filtre/recherche,
#                           statut qBit via champ comment (ID exact), re-téléchargement direct
#
# TLS: add --tls to enable HTTPS (auto-generates a self-signed cert in web/.ssl/)
#      Use --cert and --key to provide your own certificate.
set -eu

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-5000}"

# ── Migration JSON → SQLite (idempotent, ignorée si déjà migrée) ─────────────
# Lance la migration avant le serveur afin que l'historique soit disponible dès
# le premier démarrage même si Flask n'a pas encore été lancé.
# Le script quitte avec code 0 (succès) ou 1 (erreur non bloquante).
if python3 "$SCRIPT_DIR/web/migrate_to_sqlite.py" 2>&1; then
    : # migration OK
else
    echo "[start_web.sh] Avertissement : migration SQLite a échoué (non bloquant)" >&2
fi

# --host/--port passés en premier comme valeurs par défaut ; tout argument
# explicite dans $@ (ex: --host 127.0.0.1) les surcharge car argparse retient
# la dernière occurrence d'une option.
exec python3 "$SCRIPT_DIR/start_web.py" --host "$HOST" --port "$PORT" "$@"