#!/usr/bin/env python3
"""Migration standalone : history.json + history_transcripts.json → history.db (SQLite WAL).

Ce script est idempotent : il peut être exécuté plusieurs fois sans effet de bord.
La migration n'a lieu que si :
  - history.json et/ou history_transcripts.json existent dans web/
  - Les tables SQLite correspondantes sont encore vides

Usage :
    cd <projet>
    python3 web/migrate_to_sqlite.py

Le script configure sys.path pour que `core.*` soit importable sans avoir besoin
d'activer le virtualenv Python (la migration peut donc être lancée depuis start_web.sh
avant que l'application Flask ne démarre).
"""
from __future__ import annotations

import sys
import os
import logging

# Ajoute web/ au sys.path pour permettre les imports core.*
_WEB_DIR = os.path.dirname(os.path.abspath(__file__))
if _WEB_DIR not in sys.path:
    sys.path.insert(0, _WEB_DIR)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [migrate] %(message)s",
    datefmt="%H:%M:%S",
)
_log = logging.getLogger("migrate")

try:
    # L'import de core.db déclenche automatiquement init_db()
    # qui gère la migration JSON → SQLite si les tables sont vides
    import core.db as db  # noqa: F401

    conn = db._connect()
    hist_count = conn.execute("SELECT COUNT(*) FROM history").fetchone()[0]
    tr_count   = conn.execute("SELECT COUNT(*) FROM transcripts").fetchone()[0]
    conn.close()

    _log.info("Migration OK — history: %d entrée(s), transcripts: %d entrée(s)", hist_count, tr_count)
    sys.exit(0)

except Exception as exc:
    _log.error("Migration échouée : %s", exc)
    sys.exit(1)
