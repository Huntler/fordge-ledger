"""SQLite cache.

Per the governing principle, nothing here is authoritative. Every column is
reconstructible from the library folders by a full rescan, which is what makes
`rm data/forge.db` a safe thing to do. Durable metadata belongs in
`project.yaml`, the print sidecar JSON, `version.yaml` and `publish/`.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id            TEXT PRIMARY KEY,
    slug          TEXT NOT NULL UNIQUE,
    path          TEXT NOT NULL,
    title         TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'idea',
    created       TEXT,
    tags          TEXT NOT NULL DEFAULT '[]',
    license       TEXT,
    remix_of      TEXT NOT NULL DEFAULT '[]',
    notes         TEXT NOT NULL DEFAULT '',
    cover_image   TEXT,
    scanned_at    TEXT
);

CREATE TABLE IF NOT EXISTS models (
    id            INTEGER PRIMARY KEY,
    project_id    TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    files         TEXT NOT NULL DEFAULT '[]',
    UNIQUE(project_id, name)
);

-- Every file found on disk. `filed` is 0 when project.yaml does not mention it,
-- which surfaces it as "unfiled" rather than silently absorbing it.
CREATE TABLE IF NOT EXISTS files (
    id            INTEGER PRIMARY KEY,
    project_id    TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    rel_path      TEXT NOT NULL,
    kind          TEXT NOT NULL,
    size          INTEGER NOT NULL DEFAULT 0,
    mtime         REAL NOT NULL DEFAULT 0,
    filed         INTEGER NOT NULL DEFAULT 0,
    UNIQUE(project_id, rel_path)
);

CREATE TABLE IF NOT EXISTS prints (
    id             TEXT PRIMARY KEY,
    project_id     TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    rel_path       TEXT NOT NULL,
    name           TEXT NOT NULL,
    model_name     TEXT,
    status         TEXT NOT NULL DEFAULT 'queued',
    printer        TEXT,
    nozzle         REAL,
    plate_count    INTEGER NOT NULL DEFAULT 1,
    filaments      TEXT NOT NULL DEFAULT '[]',
    estimated_s    INTEGER,
    actual_s       INTEGER,
    weight_g       REAL,
    cost           REAL,
    notes          TEXT NOT NULL DEFAULT '',
    failure_reason TEXT,
    failure_fix    TEXT,
    settings       TEXT NOT NULL DEFAULT '{}',
    parser_version INTEGER,
    created        TEXT,
    started        TEXT,
    finished       TEXT,
    UNIQUE(project_id, rel_path)
);

CREATE TABLE IF NOT EXISTS versions (
    id            INTEGER PRIMARY KEY,
    project_id    TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    number        INTEGER NOT NULL,
    folder        TEXT NOT NULL,
    label         TEXT NOT NULL DEFAULT '',
    note          TEXT NOT NULL DEFAULT '',
    created       TEXT,
    file_count    INTEGER NOT NULL DEFAULT 0,
    UNIQUE(project_id, folder)
);

CREATE TABLE IF NOT EXISTS images (
    id            INTEGER PRIMARY KEY,
    project_id    TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    rel_path      TEXT NOT NULL,
    category      TEXT NOT NULL DEFAULT 'photo',
    sort_order    INTEGER NOT NULL DEFAULT 0,
    -- '' | 'web' | 'mobile': which listing the image is cut for.
    variant       TEXT NOT NULL DEFAULT '',
    -- The editable original it was exported from, e.g. images/sources/cover.psd.
    source_path   TEXT NOT NULL DEFAULT '',
    UNIQUE(project_id, rel_path)
);

-- Background work: rescans and turntable renders. A table plus worker threads,
-- rather than a broker that would need its own container.
CREATE TABLE IF NOT EXISTS jobs (
    id            INTEGER PRIMARY KEY,
    kind          TEXT NOT NULL,
    payload       TEXT NOT NULL DEFAULT '{}',
    status        TEXT NOT NULL DEFAULT 'pending',
    progress      REAL NOT NULL DEFAULT 0,
    message       TEXT NOT NULL DEFAULT '',
    error         TEXT,
    created       TEXT,
    started       TEXT,
    finished      TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    key           TEXT PRIMARY KEY,
    value         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_files_project    ON files(project_id, kind);
CREATE INDEX IF NOT EXISTS idx_prints_status    ON prints(status);
CREATE INDEX IF NOT EXISTS idx_prints_project   ON prints(project_id);
CREATE INDEX IF NOT EXISTS idx_images_project   ON images(project_id, category, sort_order);
CREATE INDEX IF NOT EXISTS idx_jobs_status      ON jobs(status, id);
"""


class Database:
    """Thread-local SQLite connections over one WAL database file."""

    def __init__(self, path: Path):
        self.path = path
        self._local = threading.local()
        # Serialises multi-statement writes; SQLite handles the rest.
        self._write_lock = threading.Lock()

    @property
    def connection(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path, timeout=30.0, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=30000")
            self._local.conn = conn
        return conn

    def initialise(self) -> None:
        """Create the schema, rebuilding from scratch if the version moved on.

        There are no migrations here on purpose. Every column is derivable from
        the library folders, so an out-of-date cache is cheaper to throw away
        than to migrate — and a rescan repopulates it.
        """
        if self._stored_version() not in (None, SCHEMA_VERSION):
            log.info("cache schema is stale; rebuilding it from the library")
            self._drop_all()

        with self.write() as conn:
            conn.executescript(SCHEMA)
            conn.execute(
                "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEMA_VERSION),),
            )

    def _stored_version(self) -> int | None:
        try:
            row = self.connection.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchone()
        except sqlite3.OperationalError:
            return None  # fresh database, no meta table yet
        if row is None:
            return None
        try:
            return int(row["value"])
        except (TypeError, ValueError):
            return None

    def _drop_all(self) -> None:
        with self.write() as conn:
            tables = [
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            ]
            for table in tables:
                conn.execute(f"DROP TABLE IF EXISTS {table}")

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        """Transactional write. Commits on success, rolls back on failure."""
        with self._write_lock:
            conn = self.connection
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def query(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        cursor = self.connection.execute(sql, params)
        try:
            return cursor.fetchall()
        finally:
            cursor.close()

    def query_one(self, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
        # Closed explicitly rather than left to the garbage collector: an
        # unreset statement keeps SQLite's read transaction open, and these
        # connections are thread-local and live for the life of the process.
        cursor = self.connection.execute(sql, params)
        try:
            return cursor.fetchone()
        finally:
            cursor.close()

    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        """Write in its own transaction. The cursor is returned for `rowcount`."""
        with self.write() as conn:
            return conn.execute(sql, params)

    def count(self, table: str) -> int:
        row = self.query_one(f"SELECT COUNT(*) AS n FROM {table}")
        return int(row["n"]) if row else 0

    def reset(self) -> None:
        """Drop everything and recreate. The cache is disposable by design."""
        self._drop_all()
        self.initialise()

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None
