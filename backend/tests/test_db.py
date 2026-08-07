"""Cache behaviour that is easy to get wrong and invisible when it breaks."""

from __future__ import annotations

import threading
from pathlib import Path

from app.db import Database


def _db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "forge.db")
    db.initialise()
    return db


def _insert(db: Database, project_id: str) -> None:
    db.execute(
        "INSERT INTO projects(id, slug, path, title) VALUES(?, ?, ?, ?)",
        (project_id, project_id, f"/library/{project_id}", project_id.title()),
    )


def test_reads_see_writes_made_after_an_earlier_single_row_read(tmp_path: Path):
    """A partly-consumed cursor must not pin the connection's snapshot.

    `fetchone()` leaves the statement active, which holds SQLite's read
    transaction open. On a long-lived per-thread connection that freezes the
    snapshot for the life of the process, so the UI serves stale rows forever.
    """
    db = _db(tmp_path)
    _insert(db, "first")

    # Single-row read, of the kind every detail endpoint starts with.
    assert db.query_one("SELECT id FROM projects WHERE id = ?", ("first",)) is not None

    _insert(db, "second")

    assert len(db.query("SELECT id FROM projects")) == 2
    assert db.query_one("SELECT id FROM projects WHERE id = ?", ("second",)) is not None


def test_a_reused_reader_thread_sees_later_writes_from_another_thread(tmp_path: Path):
    """The real shape: worker threads write, and a pooled request thread reads twice.

    The reader's connection is thread-local and therefore long-lived, so a read
    transaction left open by the first request would freeze every later one.
    """
    db = _db(tmp_path)
    _insert(db, "first")

    first_done = threading.Event()
    write_done = threading.Event()
    seen: list[int] = []

    def reader() -> None:
        # Request one: a single-row lookup, as every detail endpoint does.
        db.query_one("SELECT id FROM projects LIMIT 1")
        seen.append(len(db.query("SELECT id FROM projects")))
        first_done.set()

        # Request two, on the same pooled thread and the same connection.
        assert write_done.wait(timeout=5)
        seen.append(len(db.query("SELECT id FROM projects")))

    thread = threading.Thread(target=reader)
    thread.start()

    assert first_done.wait(timeout=5)
    _insert(db, "second")  # a different thread, a different connection
    write_done.set()
    thread.join(timeout=5)

    assert seen == [1, 2]


def test_missing_row_read_does_not_pin_the_snapshot(tmp_path: Path):
    """A lookup that finds nothing still leaves a statement active."""
    db = _db(tmp_path)

    assert db.query_one("SELECT id FROM projects WHERE id = ?", ("later",)) is None

    _insert(db, "later")

    assert db.query_one("SELECT id FROM projects WHERE id = ?", ("later",)) is not None


def test_reset_rebuilds_an_empty_schema(tmp_path: Path):
    db = _db(tmp_path)
    _insert(db, "first")

    db.reset()

    assert db.query("SELECT id FROM projects") == []
    _insert(db, "again")
    assert len(db.query("SELECT id FROM projects")) == 1
