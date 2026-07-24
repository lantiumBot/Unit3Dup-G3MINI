"""SQLite backend — history, transcripts, dup_cache, job_queue, app_state.

Previously only history and transcripts were stored here.  The three JSON state
files (duplicate_check_cache.json, job_queue.json, inventory_state.json) have
been migrated to SQLite so that:

  - All mutable state lives in a single WAL database (atomic, concurrent-safe)
  - No more read-modify-write races on JSON files under concurrent requests
  - The _QUEUE_LOCK / _dup_cache_lock threading locks are no longer needed

Migration
---------
``init_db()`` is called automatically on import.

  * Legacy JSON history migration  : runs only if ``history.db`` did not exist
    when the process started (``_db_existed = False``).  Prevents re-importing
    stale JSON after a clear-history operation.

  * New-tables migration           : runs on every startup but only writes if
    the target table is currently empty AND the source JSON file exists.
    Idempotent — safe to call multiple times.

Thread safety
-------------
A single module-level ``_DB_LOCK`` serialises all writes.  Reads use
short-lived connections in WAL mode and are concurrent-safe without the lock.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator

from core.conf import (
    WEB_DIR,
    LOGS_UPLOAD_DIR,
    _safe_read_json,
)

# ── Legacy JSON migration paths ───────────────────────────────────────────────
# These paths are only used by _migrate_json() at startup to import data from
# files that pre-date the SQLite backend.  They live here (not in conf.py)
# because conf.py has no knowledge of the migration strategy.
HISTORY_JSON             = WEB_DIR / "history.json"
HISTORY_TRANSCRIPTS_JSON = WEB_DIR / "history_transcripts.json"
DUPLICATE_CACHE_JSON     = WEB_DIR / "duplicate_check_cache.json"
QUEUE_JSON               = WEB_DIR / "job_queue.json"
INVENTORY_STATE_JSON     = WEB_DIR / "inventory_state.json"

_log = logging.getLogger("core.db")

DB_PATH  = WEB_DIR / "history.db"
_DB_LOCK = threading.Lock()

# Capture BEFORE init_db() creates the file — used to decide whether migration
# from legacy JSON is needed.  If the file already exists on disk the DB was
# already bootstrapped on a previous run and we must never overwrite it.
_db_existed: bool = DB_PATH.exists()


# ── Connection helpers ────────────────────────────────────────────────────────

def _connect(*, write: bool = False) -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    if write:
        conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _drop_surrogates(obj: object) -> object:
    """Recursively replace lone surrogate characters in strings.

    Linux filesystems decoded with surrogateescape produce lone surrogates
    (e.g. \\udce9 for a raw 0xe9 byte).  json.dumps(ensure_ascii=False) and
    SQLite parameter binding cannot encode them; replace with the Unicode
    replacement character (U+FFFD) so writes never fail.  On-disk paths are
    unaffected.
    """
    if isinstance(obj, str):
        return obj.encode("utf-8", errors="replace").decode("utf-8")
    if isinstance(obj, dict):
        return {k: _drop_surrogates(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_drop_surrogates(v) for v in obj]
    return obj


# ── Schema ────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS history (
    path         TEXT PRIMARY KEY,
    name         TEXT NOT NULL DEFAULT '',
    type         TEXT NOT NULL DEFAULT '',
    tag          TEXT NOT NULL DEFAULT '',
    processed_at TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'done',
    job_id       TEXT,
    source       TEXT,
    tracker_id   TEXT
);

CREATE INDEX IF NOT EXISTS idx_history_processed_at ON history(processed_at DESC);
CREATE INDEX IF NOT EXISTS idx_history_status        ON history(status);
CREATE INDEX IF NOT EXISTS idx_history_type          ON history(type);

CREATE TABLE IF NOT EXISTS transcripts (
    job_id      TEXT PRIMARY KEY,
    path        TEXT NOT NULL DEFAULT '',
    name        TEXT NOT NULL DEFAULT '',
    type        TEXT NOT NULL DEFAULT '',
    tag         TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT '',
    started_at  TEXT,
    ended_at    TEXT,
    processed_at TEXT NOT NULL DEFAULT '',
    item_json   TEXT NOT NULL DEFAULT '{}',
    result_json TEXT NOT NULL DEFAULT '{}',
    transcript  TEXT NOT NULL DEFAULT ''
);

-- C: duplicate check results cache (replaces duplicate_check_cache.json)
CREATE TABLE IF NOT EXISTS dup_cache (
    cache_key    TEXT PRIMARY KEY,
    path         TEXT NOT NULL DEFAULT '',
    tracker      TEXT NOT NULL DEFAULT '',
    checked_at   TEXT NOT NULL,
    is_duplicate INTEGER NOT NULL DEFAULT 0,
    info_json    TEXT NOT NULL DEFAULT '{}',
    skip_th      REAL NOT NULL DEFAULT 0.0,
    status       TEXT
);
CREATE INDEX IF NOT EXISTS idx_dup_cache_checked_at ON dup_cache(checked_at);

-- D: pending job queue (replaces job_queue.json)
CREATE TABLE IF NOT EXISTS job_queue (
    job_id    TEXT PRIMARY KEY,
    item_json TEXT NOT NULL DEFAULT '{}'
);

-- E: generic app key-value state (replaces inventory_state.json and future singletons)
CREATE TABLE IF NOT EXISTS app_state (
    key        TEXT PRIMARY KEY,
    value_json TEXT NOT NULL DEFAULT '{}'
);

-- F: server-side scan cache (replaces per-browser localStorage)
CREATE TABLE IF NOT EXISTS scan_cache (
    folder_key  TEXT PRIMARY KEY,
    items_json  TEXT NOT NULL DEFAULT '[]',
    saved_at    TEXT NOT NULL
);
"""


def init_db() -> None:
    """Create schema and migrate legacy data (idempotent)."""
    with _DB_LOCK:
        conn = _connect(write=True)
        try:
            conn.executescript(_SCHEMA)
            conn.commit()
            _migrate_json(conn)
            _migrate_new_json_tables(conn)
        finally:
            conn.close()


# ── Migration — legacy history JSON (one-shot, skipped if DB pre-existed) ────

def _migrate_json(conn: sqlite3.Connection) -> None:
    """One-shot import from legacy JSON files — only if the DB was just created.

    Guard: if ``history.db`` already existed when this process started
    (``_db_existed = True``), we skip migration entirely.  This prevents:
    - re-importing a stale history.json after the user clears the DB
    - overwriting accumulated SQLite data on every restart

    The legacy JSON files are left untouched (read-only) so a rollback is
    always possible.
    """
    if _db_existed:
        _log.debug("DB: migration ignorée — history.db existait déjà au démarrage")
        return

    # Migrate history.json (INSERT OR IGNORE — never overwrites)
    if HISTORY_JSON.exists():
        hist = _safe_read_json(HISTORY_JSON, dict)
        if isinstance(hist, dict) and hist:
            _log.info("DB: migration history.json → SQLite (%d entrées)", len(hist))
            conn.executemany(
                """INSERT OR IGNORE INTO history
                   (path, name, type, tag, processed_at, status, job_id, source, tracker_id)
                   VALUES (:path,:name,:type,:tag,:processed_at,:status,:job_id,:source,:tracker_id)""",
                [
                    _drop_surrogates({
                        "path":         path,
                        "name":         e.get("name", Path(path).name),
                        "type":         e.get("type", ""),
                        "tag":          e.get("tag", ""),
                        "processed_at": e.get("processed_at", ""),
                        "status":       e.get("status", "done"),
                        "job_id":       e.get("job_id"),
                        "source":       e.get("source"),
                        "tracker_id":   str(e["tracker_id"]) if e.get("tracker_id") else None,
                    })
                    for path, e in hist.items()
                ],
            )
            conn.commit()

    # Migrate history_transcripts.json (INSERT OR IGNORE — never overwrites)
    if HISTORY_TRANSCRIPTS_JSON.exists():
        trs = _safe_read_json(HISTORY_TRANSCRIPTS_JSON, dict)
        if isinstance(trs, dict) and trs:
            _log.info("DB: migration history_transcripts.json → SQLite (%d entrées)", len(trs))
            rows = []
            for job_id, e in trs.items():
                rows.append(_drop_surrogates({
                    "job_id":       job_id,
                    "path":         e.get("path", ""),
                    "name":         e.get("name", ""),
                    "type":         e.get("type", ""),
                    "tag":          e.get("tag", ""),
                    "status":       e.get("status", ""),
                    "started_at":   e.get("started_at"),
                    "ended_at":     e.get("ended_at"),
                    "processed_at": e.get("processed_at", e.get("ended_at", "")),
                    "item_json":    json.dumps(_drop_surrogates(e.get("item") or {}), ensure_ascii=False),
                    "result_json":  json.dumps(_drop_surrogates(e.get("result") or {}), ensure_ascii=False),
                    "transcript":   e.get("transcript", ""),
                }))
            conn.executemany(
                """INSERT OR IGNORE INTO transcripts
                   (job_id,path,name,type,tag,status,started_at,ended_at,
                    processed_at,item_json,result_json,transcript)
                   VALUES
                   (:job_id,:path,:name,:type,:tag,:status,:started_at,:ended_at,
                    :processed_at,:item_json,:result_json,:transcript)""",
                rows,
            )
            conn.commit()


# ── Migration — new tables (runs every startup, writes only if table is empty) ─

def _migrate_new_json_tables(conn: sqlite3.Connection) -> None:
    """Migrate JSON state files to the new SQLite tables.

    Unlike _migrate_json() this migration runs even on pre-existing databases
    because the three new tables didn't exist before this change.  It is safe
    to run repeatedly: the INSERT OR IGNORE guard makes it idempotent, and the
    "table empty + file exists" check means it only runs once.
    """
    # D: job_queue.json → job_queue table
    queue_count = conn.execute("SELECT COUNT(*) FROM job_queue").fetchone()[0]
    if queue_count == 0 and QUEUE_JSON.exists():
        raw = _safe_read_json(QUEUE_JSON, dict)
        if isinstance(raw, dict) and raw:
            _log.info("DB: migration job_queue.json → SQLite (%d entrées)", len(raw))
            conn.executemany(
                "INSERT OR IGNORE INTO job_queue (job_id, item_json) VALUES (?, ?)",
                [(jid, json.dumps(item)) for jid, item in raw.items()],
            )
            conn.commit()

    # E: inventory_state.json → app_state table (key = "gemini_inventory")
    inv_count = conn.execute(
        "SELECT COUNT(*) FROM app_state WHERE key='gemini_inventory'"
    ).fetchone()[0]
    if inv_count == 0 and INVENTORY_STATE_JSON.exists():
        raw = _safe_read_json(INVENTORY_STATE_JSON, dict)
        if isinstance(raw, dict) and raw:
            _log.info("DB: migration inventory_state.json → SQLite")
            conn.execute(
                "INSERT OR IGNORE INTO app_state (key, value_json) VALUES (?, ?)",
                ("gemini_inventory", json.dumps(raw)),
            )
            conn.commit()

    # C: duplicate_check_cache.json → dup_cache table
    dup_count = conn.execute("SELECT COUNT(*) FROM dup_cache").fetchone()[0]
    if dup_count == 0 and DUPLICATE_CACHE_JSON.exists():
        raw = _safe_read_json(DUPLICATE_CACHE_JSON, dict)
        entries = (raw or {}).get("entries") if isinstance(raw, dict) else None
        if isinstance(entries, dict) and entries:
            _log.info(
                "DB: migration duplicate_check_cache.json → SQLite (%d entrées)", len(entries)
            )
            rows = []
            for key, e in entries.items():
                if not isinstance(e, dict):
                    continue
                rows.append((
                    key,
                    e.get("path", ""),
                    e.get("tracker", ""),
                    e.get("checked_at") or datetime.now().isoformat(),
                    int(bool(e.get("is_duplicate"))),
                    json.dumps(e.get("info") or {}),
                    float(e.get("skip_th", 0.0)),
                    e.get("status"),
                ))
            if rows:
                conn.executemany(
                    """INSERT OR IGNORE INTO dup_cache
                       (cache_key, path, tracker, checked_at, is_duplicate,
                        info_json, skip_th, status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    rows,
                )
                conn.commit()


# ── History CRUD ──────────────────────────────────────────────────────────────

def db_load_history() -> dict:
    """Return the full history dict keyed by path (mirrors legacy API)."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT path,name,type,tag,processed_at,status,job_id,source,tracker_id FROM history"
        ).fetchall()
    finally:
        conn.close()

    result = {}
    for r in rows:
        entry: dict = {
            "name":         r["name"],
            "type":         r["type"],
            "tag":          r["tag"],
            "processed_at": r["processed_at"],
            "status":       r["status"],
            "job_id":       r["job_id"],
        }
        if r["source"]:
            entry["source"] = r["source"]
        if r["tracker_id"]:
            entry["tracker_id"] = r["tracker_id"]
        result[r["path"]] = entry
    return result


def db_save_history(data: dict) -> None:
    """Replace the whole history (used by bulk operations like clear/delete)."""
    with _DB_LOCK:
        conn = _connect(write=True)
        try:
            conn.execute("DELETE FROM history")
            if data:
                conn.executemany(
                    """INSERT OR REPLACE INTO history
                       (path,name,type,tag,processed_at,status,job_id,source,tracker_id)
                       VALUES
                       (:path,:name,:type,:tag,:processed_at,:status,:job_id,:source,:tracker_id)""",
                    [
                        {
                            "path":         path,
                            "name":         e.get("name", Path(path).name),
                            "type":         e.get("type", ""),
                            "tag":          e.get("tag", ""),
                            "processed_at": e.get("processed_at", ""),
                            "status":       e.get("status", "done"),
                            "job_id":       e.get("job_id"),
                            "source":       e.get("source"),
                            "tracker_id":   str(e["tracker_id"]) if e.get("tracker_id") else None,
                        }
                        for path, e in data.items()
                    ],
                )
            conn.commit()
        finally:
            conn.close()


def db_upsert_history_paths(paths: list[str], entry_proto: dict) -> None:
    """Upsert multiple paths at once (used by add_to_history)."""
    with _DB_LOCK:
        conn = _connect(write=True)
        try:
            conn.executemany(
                """INSERT OR REPLACE INTO history
                   (path,name,type,tag,processed_at,status,job_id,source,tracker_id)
                   VALUES
                   (:path,:name,:type,:tag,:processed_at,:status,:job_id,:source,:tracker_id)""",
                [
                    {**entry_proto, "path": p, "name": Path(p).name}
                    for p in paths
                ],
            )
            conn.commit()
        finally:
            conn.close()


def db_add_history_entries(entries: dict) -> None:
    """Insert new history entries without touching existing rows (INSERT OR IGNORE).

    Unlike ``db_save_history`` this never deletes anything.  Use this for
    additive operations such as the Gemini inventory sync.
    """
    if not entries:
        return
    with _DB_LOCK:
        conn = _connect(write=True)
        try:
            conn.executemany(
                """INSERT OR IGNORE INTO history
                   (path,name,type,tag,processed_at,status,job_id,source,tracker_id)
                   VALUES
                   (:path,:name,:type,:tag,:processed_at,:status,:job_id,:source,:tracker_id)""",
                [
                    {
                        "path":         path,
                        "name":         e.get("name", Path(path).name),
                        "type":         e.get("type", ""),
                        "tag":          e.get("tag", ""),
                        "processed_at": e.get("processed_at", ""),
                        "status":       e.get("status", "done"),
                        "job_id":       e.get("job_id"),
                        "source":       e.get("source"),
                        "tracker_id":   str(e["tracker_id"]) if e.get("tracker_id") else None,
                    }
                    for path, e in entries.items()
                ],
            )
            conn.commit()
        finally:
            conn.close()


def db_update_tracker_id_if_missing(
    path: str, tracker_id: str, source: str = "gemini_inventory"
) -> None:
    """Set tracker_id on an existing history entry only if it is currently NULL or empty."""
    with _DB_LOCK:
        conn = _connect(write=True)
        try:
            conn.execute(
                """UPDATE history
                   SET tracker_id = ?, source = ?
                   WHERE path = ? AND (tracker_id IS NULL OR tracker_id = '')""",
                (str(tracker_id), source, path),
            )
            conn.commit()
        finally:
            conn.close()


def db_delete_history_path(path: str) -> str | None:
    """Delete one history entry; returns its job_id if any."""
    with _DB_LOCK:
        conn = _connect(write=True)
        try:
            row = conn.execute(
                "SELECT job_id FROM history WHERE path=?", (path,)
            ).fetchone()
            job_id = row["job_id"] if row else None
            conn.execute("DELETE FROM history WHERE path=?", (path,))
            conn.commit()
        finally:
            conn.close()
    return job_id


def db_path_in_history(path: str) -> bool:
    conn = _connect()
    try:
        return conn.execute(
            "SELECT 1 FROM history WHERE path=?", (path,)
        ).fetchone() is not None
    finally:
        conn.close()


def db_history_paths_set() -> set[str]:
    """Return the set of all paths present in history (path column only).

    Much lighter than db_load_history() — used by scanner and duplicate checker
    to quickly test membership without loading all columns into memory.
    """
    conn = _connect()
    try:
        rows = conn.execute("SELECT path FROM history").fetchall()
    finally:
        conn.close()
    return {r["path"] for r in rows}


def db_inventory_query(
    *,
    search: str = "",
    page:   int = 1,
    limit:  int = 50,
) -> tuple[list[dict], int]:
    """Paginated query of inventory entries (source='gemini_inventory').

    Returns (rows, total) where each row has keys:
      path, name, processed_at, tracker_id
    """
    params: list = ["gemini_inventory"]
    where_extra = ""
    if search:
        where_extra = " AND (LOWER(name) LIKE ? OR LOWER(path) LIKE ?)"
        like = f"%{search.lower()}%"
        params += [like, like]

    conn = _connect()
    try:
        total = conn.execute(
            f"SELECT COUNT(*) FROM history WHERE source=?{where_extra}", params
        ).fetchone()[0]

        offset = (page - 1) * limit
        rows = conn.execute(
            f"""SELECT path, name, processed_at, tracker_id
                FROM history
                WHERE source=?{where_extra}
                ORDER BY processed_at DESC
                LIMIT ? OFFSET ?""",
            params + [limit, offset],
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows], total


def db_history_paths_by_source(source: str) -> set[str]:
    """Return the set of history paths whose ``source`` column matches *source*.

    Used by the 4-phase duplicate checker to distinguish paths that were added
    by the Gemini inventory sync (source='gemini_inventory') from all other
    history entries (source=None/'ignored_duplicate'/…).
    """
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT path FROM history WHERE source=?", (source,)
        ).fetchall()
    finally:
        conn.close()
    return {r["path"] for r in rows}


def db_history_info_by_source(source: str) -> dict[str, dict]:
    """Return {path: {tracker_id, job_id}} for history entries matching *source*."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT path, tracker_id, job_id FROM history WHERE source=?", (source,)
        ).fetchall()
    finally:
        conn.close()
    return {
        r["path"]: {
            "tracker_id": r["tracker_id"] or "",
            "job_id":     r["job_id"]     or "",
        }
        for r in rows
    }


def db_transcript_job_ids(job_ids: list[str]) -> set[str]:
    """Return the subset of *job_ids* that have a transcript row.

    Used by api_history() to batch-check transcript existence instead of
    issuing one SELECT per history row.
    """
    if not job_ids:
        return set()
    placeholders = ",".join("?" * len(job_ids))
    conn = _connect()
    try:
        rows = conn.execute(
            f"SELECT job_id FROM transcripts WHERE job_id IN ({placeholders})",
            job_ids,
        ).fetchall()
    finally:
        conn.close()
    return {r["job_id"] for r in rows}


def db_history_query(
    *,
    search: str = "",
    ftype:  str = "",
    fstatus: str = "",
    date_from: str = "",
    date_to:   str = "",
    page:   int = 1,
    limit:  int = 50,
) -> tuple[list[dict], int]:
    """Filtered, paginated history query. Returns (rows, total)."""
    clauses: list[str] = []
    params:  list      = []

    if search:
        clauses.append("(LOWER(name) LIKE ? OR LOWER(tag) LIKE ?)")
        like = f"%{search.lower()}%"
        params += [like, like]
    if ftype:
        clauses.append("type=?")
        params.append(ftype)
    if fstatus:
        clauses.append("status=?")
        params.append(fstatus)
    if date_from:
        clauses.append("processed_at >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("processed_at <= ?")
        params.append(date_to + "T23:59:59")

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    conn = _connect()
    try:
        total_row = conn.execute(
            f"SELECT COUNT(*) FROM history {where}", params
        ).fetchone()
        total = total_row[0]

        offset = (page - 1) * limit
        rows = conn.execute(
            f"""SELECT path,name,type,tag,processed_at,status,job_id
                FROM history {where}
                ORDER BY processed_at DESC
                LIMIT ? OFFSET ?""",
            params + [limit, offset],
        ).fetchall()
    finally:
        conn.close()

    return [dict(r) for r in rows], total


# B: aggregate stats without loading all rows into memory
def db_history_stats() -> dict:
    """Return history counts using SQL aggregation — O(1) memory regardless of table size.

    Returns a dict with keys:
      counts  — {"integrale": n, "season": n, "skip": n, "error_upload": n}
      total   — total row count
      done    — count of status='done'
      error   — count of status='error'
    """
    conn = _connect()
    try:
        bucket_rows = conn.execute("""
            SELECT
                CASE
                    WHEN status = 'error'   THEN 'error_upload'
                    WHEN type   = 'integrale' THEN 'integrale'
                    WHEN type   = 'season'  THEN 'season'
                    ELSE 'skip'
                END AS bucket,
                COUNT(*) AS n
            FROM history
            GROUP BY bucket
        """).fetchall()

        status_rows = conn.execute("""
            SELECT status, COUNT(*) AS n FROM history GROUP BY status
        """).fetchall()
    finally:
        conn.close()

    counts: dict[str, int] = {"integrale": 0, "season": 0, "skip": 0, "error_upload": 0}
    for r in bucket_rows:
        bucket = r["bucket"]
        if bucket in counts:
            counts[bucket] = r["n"]

    total = done = error = 0
    for r in status_rows:
        n = r["n"]
        total += n
        s = r["status"]
        if s == "done":
            done = n
        elif s == "error":
            error = n

    return {"counts": counts, "total": total, "done": done, "error": error}


# A: cursor-based generator for streaming CSV export
def db_iter_history_csv_chunks(chunk_size: int = 500) -> Iterator[list[dict]]:
    """Yield successive chunks of history rows ordered by processed_at DESC.

    Each chunk is a list of plain dicts with keys:
      name, type, tag, processed_at, status, path

    Used by the streaming CSV export endpoint so the entire table never has to
    fit in memory — works correctly for 50 000+ entries.
    """
    conn = _connect()
    try:
        cursor = conn.execute(
            "SELECT name,type,tag,processed_at,status,path FROM history ORDER BY processed_at DESC"
        )
        while True:
            rows = cursor.fetchmany(chunk_size)
            if not rows:
                break
            yield [dict(r) for r in rows]
    finally:
        conn.close()


# ── Transcript CRUD ───────────────────────────────────────────────────────────

def db_save_transcript(job_id: str, entry: dict) -> None:
    with _DB_LOCK:
        conn = _connect(write=True)
        try:
            conn.execute(
                """INSERT OR REPLACE INTO transcripts
                   (job_id,path,name,type,tag,status,started_at,ended_at,
                    processed_at,item_json,result_json,transcript)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    job_id,
                    entry.get("path", ""),
                    entry.get("name", ""),
                    entry.get("type", ""),
                    entry.get("tag", ""),
                    entry.get("status", ""),
                    entry.get("started_at"),
                    entry.get("ended_at"),
                    entry.get("processed_at", entry.get("ended_at", "")),
                    json.dumps(entry.get("item") or {}),
                    json.dumps(entry.get("result") or {}),
                    entry.get("transcript", ""),
                ),
            )
            conn.commit()
        finally:
            conn.close()


def db_load_transcripts() -> dict:
    """Load all transcripts as a dict job_id → entry (mirrors legacy API)."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT job_id,path,name,type,tag,status,started_at,ended_at,"
            "processed_at,item_json,result_json,transcript FROM transcripts"
        ).fetchall()
    finally:
        conn.close()

    result = {}
    for r in rows:
        result[r["job_id"]] = _row_to_transcript(r)
    return result


def db_get_transcript(job_id: str) -> dict | None:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT job_id,path,name,type,tag,status,started_at,ended_at,"
            "processed_at,item_json,result_json,transcript "
            "FROM transcripts WHERE job_id=?",
            (job_id,),
        ).fetchone()
    finally:
        conn.close()
    return _row_to_transcript(row) if row else None


def db_delete_transcript(job_id: str) -> None:
    with _DB_LOCK:
        conn = _connect(write=True)
        try:
            conn.execute("DELETE FROM transcripts WHERE job_id=?", (job_id,))
            conn.commit()
        finally:
            conn.close()


def db_clear_transcripts() -> None:
    with _DB_LOCK:
        conn = _connect(write=True)
        try:
            conn.execute("DELETE FROM transcripts")
            conn.commit()
        finally:
            conn.close()


def db_transcript_exists(job_id: str) -> bool:
    conn = _connect()
    try:
        return conn.execute(
            "SELECT 1 FROM transcripts WHERE job_id=?", (job_id,)
        ).fetchone() is not None
    finally:
        conn.close()


def _row_to_transcript(r: sqlite3.Row) -> dict:
    try:
        item = json.loads(r["item_json"] or "{}")
    except Exception:
        item = {}
    try:
        result = json.loads(r["result_json"] or "{}")
    except Exception:
        result = {}
    return {
        "job_id":       r["job_id"],
        "path":         r["path"],
        "name":         r["name"],
        "type":         r["type"],
        "tag":          r["tag"],
        "status":       r["status"],
        "started_at":   r["started_at"],
        "ended_at":     r["ended_at"],
        "processed_at": r["processed_at"],
        "item":         item,
        "result":       result,
        "transcript":   r["transcript"],
    }


# ── C: Duplicate cache CRUD ───────────────────────────────────────────────────

def db_dup_cache_lookup(
    cache_key: str, skip_th: float, ttl_sec: int
) -> "tuple[bool, dict | None, str | None] | None":
    """Look up a cached duplicate-check result.

    Returns:
      None                          — cache miss (not found or expired)
      (False, None, None)           — cached "no duplicate"
      (True, info_dict, status_str) — cached duplicate

    The TTL is applied as a SQL filter (no Python-side pruning needed),
    so expired rows are simply invisible until the next prune pass.
    """
    if ttl_sec <= 0:
        return None
    cutoff = (datetime.now() - timedelta(seconds=ttl_sec)).isoformat()
    conn = _connect()
    try:
        row = conn.execute(
            """SELECT is_duplicate, info_json, status, skip_th
               FROM dup_cache
               WHERE cache_key = ? AND checked_at >= ?""",
            (cache_key, cutoff),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return None
    # Cache key must match the threshold used when the entry was written
    if float(row["skip_th"]) != skip_th:
        return None
    try:
        info = json.loads(row["info_json"] or "{}")
    except Exception:
        info = {}
    if not row["is_duplicate"]:
        return False, None, None
    return True, info, row["status"]


def db_dup_cache_store(
    cache_key: str,
    path: str,
    tracker: str,
    skip_th: float,
    is_dup: bool,
    info: "dict | None",
    status: "str | None",
) -> None:
    """Persist a duplicate-check result."""
    with _DB_LOCK:
        conn = _connect(write=True)
        try:
            conn.execute(
                """INSERT OR REPLACE INTO dup_cache
                   (cache_key, path, tracker, checked_at,
                    is_duplicate, info_json, skip_th, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    cache_key,
                    path,
                    tracker,
                    datetime.now().isoformat(),
                    int(bool(is_dup)),
                    json.dumps(info or {}),
                    skip_th,
                    status,
                ),
            )
            conn.commit()
        finally:
            conn.close()


def db_dup_cache_prune(ttl_sec: int) -> int:
    """Delete expired duplicate-cache entries.  Returns the number deleted.

    Called opportunistically; callers should not depend on the exact count.
    """
    if ttl_sec <= 0:
        return 0
    cutoff = (datetime.now() - timedelta(seconds=ttl_sec)).isoformat()
    with _DB_LOCK:
        conn = _connect(write=True)
        try:
            cur = conn.execute(
                "DELETE FROM dup_cache WHERE checked_at < ?", (cutoff,)
            )
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()


# ── D: Job queue CRUD ─────────────────────────────────────────────────────────

def db_queue_load() -> dict:
    """Return all pending queue entries as {job_id: item_dict}."""
    conn = _connect()
    try:
        rows = conn.execute("SELECT job_id, item_json FROM job_queue").fetchall()
    finally:
        conn.close()
    result: dict = {}
    for r in rows:
        try:
            result[r["job_id"]] = json.loads(r["item_json"])
        except Exception:
            pass
    return result


def db_queue_add(job_id: str, item: dict) -> None:
    """Persist a new pending job."""
    with _DB_LOCK:
        conn = _connect(write=True)
        try:
            conn.execute(
                "INSERT OR REPLACE INTO job_queue (job_id, item_json) VALUES (?, ?)",
                (job_id, json.dumps(item)),
            )
            conn.commit()
        finally:
            conn.close()


def db_queue_remove(job_id: str) -> None:
    """Remove a completed/cancelled job from the persistent queue."""
    with _DB_LOCK:
        conn = _connect(write=True)
        try:
            conn.execute("DELETE FROM job_queue WHERE job_id=?", (job_id,))
            conn.commit()
        finally:
            conn.close()


# ── E: App key-value state CRUD ───────────────────────────────────────────────

def db_state_get(key: str) -> dict:
    """Return the JSON state stored under *key*, or {} if absent."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT value_json FROM app_state WHERE key=?", (key,)
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return {}
    try:
        return json.loads(row["value_json"]) or {}
    except Exception:
        return {}


def db_state_set(key: str, state: dict) -> None:
    """Persist *state* under *key* (INSERT OR REPLACE)."""
    with _DB_LOCK:
        conn = _connect(write=True)
        try:
            conn.execute(
                "INSERT OR REPLACE INTO app_state (key, value_json) VALUES (?, ?)",
                (key, json.dumps(state)),
            )
            conn.commit()
        finally:
            conn.close()


def db_history_chart_data(days: int = 30) -> dict:
    """Return daily upload counts for the last *days* days.

    Returns:
      {"labels": ["2025-01-01", ...], "done": [N, ...], "error": [N, ...]}
    """
    from datetime import date, timedelta
    conn = _connect()
    try:
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        rows = conn.execute(
            """SELECT substr(processed_at, 1, 10) AS day, status, COUNT(*) AS n
               FROM history
               WHERE processed_at >= ?
               GROUP BY day, status
               ORDER BY day ASC""",
            (cutoff,),
        ).fetchall()
    finally:
        conn.close()

    # Build day range
    today = date.today()
    labels = [(today - timedelta(days=days - 1 - i)).isoformat() for i in range(days)]
    done_map:  dict[str, int] = {}
    error_map: dict[str, int] = {}
    for r in rows:
        day, status, n = r["day"], r["status"], r["n"]
        if status == "done":
            done_map[day]  = done_map.get(day, 0) + n
        elif status == "error":
            error_map[day] = error_map.get(day, 0) + n

    return {
        "labels": labels,
        "done":   [done_map.get(d, 0) for d in labels],
        "error":  [error_map.get(d, 0) for d in labels],
    }


# ── F: Server-side scan cache ─────────────────────────────────────────────────

_SCAN_CACHE_TTL_H = 24  # hours


def db_scan_cache_get(folder: str) -> list | None:
    """Return cached scan items for *folder*, or None if absent/expired."""
    key = folder.strip()
    if not key:
        return None
    cutoff = (datetime.now() - timedelta(hours=_SCAN_CACHE_TTL_H)).isoformat()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT items_json FROM scan_cache WHERE folder_key=? AND saved_at>=?",
            (key, cutoff),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    try:
        return json.loads(row["items_json"])
    except Exception:
        return None


def db_scan_cache_set(folder: str, items: list) -> None:
    """Persist scan items for *folder*."""
    key = folder.strip()
    if not key:
        return
    with _DB_LOCK:
        conn = _connect(write=True)
        try:
            items_json = json.dumps(_drop_surrogates(items), ensure_ascii=False)
            conn.execute(
                "INSERT OR REPLACE INTO scan_cache (folder_key, items_json, saved_at) VALUES (?, ?, ?)",
                (key, items_json, datetime.now().isoformat()),
            )
            conn.commit()
        finally:
            conn.close()


def db_scan_cache_clear(folder: str) -> None:
    """Delete cached scan for *folder*."""
    key = folder.strip()
    if not key:
        return
    with _DB_LOCK:
        conn = _connect(write=True)
        try:
            conn.execute("DELETE FROM scan_cache WHERE folder_key=?", (key,))
            conn.commit()
        finally:
            conn.close()


# ── Auto-initialise on import ─────────────────────────────────────────────────
init_db()
