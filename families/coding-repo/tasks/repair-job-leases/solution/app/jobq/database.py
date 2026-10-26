"""The SQLite file that holds a queue."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


SCHEMA_VERSION = 1
BUSY_TIMEOUT_SECONDS = 10.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    dedupe_key TEXT,
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'done', 'dead')),
    attempts INTEGER NOT NULL DEFAULT 0,
    run_at REAL NOT NULL,
    lease_owner TEXT,
    lease_token TEXT,
    lease_until REAL,
    result TEXT,
    last_error TEXT,
    created_at REAL NOT NULL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS jobs_ready ON jobs (status, run_at, id);
CREATE UNIQUE INDEX IF NOT EXISTS jobs_active_dedupe ON jobs (dedupe_key)
    WHERE dedupe_key IS NOT NULL AND status IN ('queued', 'running');
"""


def connect(path: Path) -> sqlite3.Connection:
    """Open the queue at `path` in autocommit mode, creating its schema on first use."""
    db = sqlite3.connect(
        path,
        timeout=BUSY_TIMEOUT_SECONDS,
        isolation_level=None,
        check_same_thread=False,
    )
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode = WAL')

    version = db.execute('PRAGMA user_version').fetchone()[0]
    if version == 0:
        db.executescript(
            f'BEGIN IMMEDIATE; {SCHEMA} PRAGMA user_version = {SCHEMA_VERSION}; COMMIT;'
        )
    elif version != SCHEMA_VERSION:
        db.close()
        raise RuntimeError(f'{path} has unsupported schema version {version}.')
    return db


@contextmanager
def transaction(db: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a write transaction that holds SQLite's write lock from its start."""
    db.execute('BEGIN IMMEDIATE')
    try:
        yield db
    except BaseException:
        db.execute('ROLLBACK')
        raise
    db.execute('COMMIT')
