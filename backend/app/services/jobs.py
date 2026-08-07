"""Background job queue: a SQLite table plus a small pool of worker threads.

Single user on a NAS. A broker would add a container to babysit and buy
nothing — but the jobs still need to survive a restart and report progress,
which the table gives us for free.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from typing import Any

from ..db import Database
from ..utils import utcnow
from .events import bus

log = logging.getLogger(__name__)

JobHandler = Callable[[dict[str, Any], "JobContext"], Any]


class JobContext:
    """Handed to a handler so it can report progress over SSE."""

    def __init__(self, queue: JobQueue, job_id: int, kind: str):
        self.queue = queue
        self.job_id = job_id
        self.kind = kind
        self._cancelled = threading.Event()

    def progress(self, fraction: float, message: str = "") -> None:
        fraction = max(0.0, min(1.0, fraction))
        self.queue.db.execute(
            "UPDATE jobs SET progress = ?, message = ? WHERE id = ?",
            (fraction, message, self.job_id),
        )
        bus.publish(
            "job.progress",
            {"id": self.job_id, "kind": self.kind, "progress": fraction, "message": message},
        )

    def cancel(self) -> None:
        self._cancelled.set()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()


class JobQueue:
    def __init__(self, db: Database, worker_count: int = 2):
        self.db = db
        self.worker_count = max(1, worker_count)
        self._handlers: dict[str, JobHandler] = {}
        self._workers: list[threading.Thread] = []
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._running: dict[int, JobContext] = {}
        self._lock = threading.Lock()

    def register(self, kind: str, handler: JobHandler) -> None:
        self._handlers[kind] = handler

    def enqueue(
        self, kind: str, payload: dict[str, Any] | None = None, *, unique: bool = False
    ) -> int:
        """Queue a job. `unique` skips it when one of the same kind is already waiting."""
        if kind not in self._handlers:
            raise ValueError(f"no handler registered for job kind {kind!r}")

        with self.db.write() as conn:
            if unique:
                existing = conn.execute(
                    "SELECT id FROM jobs WHERE kind = ? AND status IN ('pending','running') LIMIT 1",  # noqa: E501
                    (kind,),
                ).fetchone()
                if existing:
                    return int(existing["id"])
            cursor = conn.execute(
                "INSERT INTO jobs(kind, payload, status, created) VALUES(?, ?, 'pending', ?)",
                (kind, json.dumps(payload or {}), utcnow()),
            )
            job_id = int(cursor.lastrowid or 0)

        bus.publish("job.queued", {"id": job_id, "kind": kind})
        self._wake.set()
        return job_id

    def cancel(self, job_id: int) -> bool:
        with self._lock:
            context = self._running.get(job_id)
        if context is not None:
            context.cancel()
            return True
        cursor = self.db.execute(
            "UPDATE jobs SET status='cancelled', finished=? WHERE id=? AND status='pending'",
            (utcnow(), job_id),
        )
        return cursor.rowcount > 0

    def start(self) -> None:
        # Anything left 'running' belongs to a process that no longer exists.
        self.db.execute(
            "UPDATE jobs SET status='failed', error='interrupted by restart', finished=? "
            "WHERE status='running'",
            (utcnow(),),
        )
        for index in range(self.worker_count):
            thread = threading.Thread(target=self._run, name=f"job-worker-{index}", daemon=True)
            thread.start()
            self._workers.append(thread)
        log.info("started %d job workers", self.worker_count)

    def stop(self, timeout: float = 5.0) -> None:
        self._stopping.set()
        self._wake.set()
        with self._lock:
            for context in self._running.values():
                context.cancel()
        for thread in self._workers:
            thread.join(timeout=timeout)
        self._workers.clear()

    def _claim(self) -> tuple[int, str, dict[str, Any]] | None:
        """Atomically take the oldest pending job."""
        with self.db.write() as conn:
            row = conn.execute(
                "SELECT id, kind, payload FROM jobs WHERE status='pending' ORDER BY id LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            updated = conn.execute(
                "UPDATE jobs SET status='running', started=? WHERE id=? AND status='pending'",
                (utcnow(), row["id"]),
            )
            if updated.rowcount == 0:
                return None  # another worker won the race
        return int(row["id"]), str(row["kind"]), json.loads(row["payload"])

    def _run(self) -> None:
        while not self._stopping.is_set():
            claimed = self._claim()
            if claimed is None:
                self._wake.wait(timeout=2.0)
                self._wake.clear()
                continue

            job_id, kind, payload = claimed
            context = JobContext(self, job_id, kind)
            with self._lock:
                self._running[job_id] = context

            bus.publish("job.started", {"id": job_id, "kind": kind})
            try:
                handler = self._handlers.get(kind)
                if handler is None:
                    raise ValueError(f"no handler registered for {kind!r}")
                result = handler(payload, context)
                status = "cancelled" if context.cancelled else "done"
                self.db.execute(
                    "UPDATE jobs SET status=?, progress=1.0, finished=?, message=? WHERE id=?",
                    (status, utcnow(), _summarise(result), job_id),
                )
                bus.publish(f"job.{status}", {"id": job_id, "kind": kind, "result": result})
            except Exception as exc:
                log.exception("job %s (%s) failed", job_id, kind)
                self.db.execute(
                    "UPDATE jobs SET status='failed', error=?, finished=? WHERE id=?",
                    (str(exc), utcnow(), job_id),
                )
                bus.publish("job.failed", {"id": job_id, "kind": kind, "error": str(exc)})
            finally:
                with self._lock:
                    self._running.pop(job_id, None)

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(row) | {"payload": json.loads(row["payload"])} for row in rows]


def _summarise(result: Any) -> str:
    if result is None:
        return ""
    if isinstance(result, str):
        return result[:500]
    return json.dumps(result, default=str)[:500]
