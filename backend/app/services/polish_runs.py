"""Cancellable polish runs.

A local model can take tens of seconds, so polish cannot be a request that the
browser simply waits on: the user needs to see it working and be able to give
up. Each run is an asyncio task the UI can poll and cancel.

Cancelling matters more than it looks. Aborting the task closes the HTTP
connection to Ollama or LM Studio, and both stop generating when that happens —
so "Cancel" actually frees the GPU on the other machine rather than just hiding
a request that keeps running.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from ..utils import new_ulid

log = logging.getLogger(__name__)

# Finished runs are kept briefly so the UI can collect a result even if it polls
# a moment late, then pruned so this cannot grow without bound.
RETAIN_SECONDS = 600


@dataclass
class PolishRun:
    id: str
    original: str
    status: str = "running"  # running | done | failed | cancelled
    polished: str = ""
    error: str = ""
    started: float = field(default_factory=time.monotonic)
    finished: float | None = None
    task: asyncio.Task | None = None

    @property
    def elapsed_seconds(self) -> float:
        end = self.finished if self.finished is not None else time.monotonic()
        return round(end - self.started, 1)

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "status": self.status,
            "elapsed_seconds": self.elapsed_seconds,
        }
        if self.status == "done":
            payload["original"] = self.original
            payload["polished"] = self.polished
        if self.status == "failed":
            payload["error"] = self.error
        return payload


class PolishRunner:
    def __init__(self) -> None:
        self._runs: dict[str, PolishRun] = {}

    def start(self, original: str, work: Callable[[], Awaitable[str]]) -> PolishRun:
        self._prune()
        run = PolishRun(id=new_ulid(), original=original)
        self._runs[run.id] = run
        run.task = asyncio.create_task(self._execute(run, work))
        return run

    async def _execute(self, run: PolishRun, work: Callable[[], Awaitable[str]]) -> None:
        try:
            run.polished = await work()
            run.status = "done"
        except asyncio.CancelledError:
            run.status = "cancelled"
            run.finished = time.monotonic()
            raise
        except Exception as exc:  # noqa: BLE001 — surfaced to the UI as `error`
            log.warning("polish run %s failed: %s", run.id, exc)
            run.status = "failed"
            run.error = str(exc)
        finally:
            if run.finished is None:
                run.finished = time.monotonic()

    def get(self, run_id: str) -> PolishRun | None:
        return self._runs.get(run_id)

    def cancel(self, run_id: str) -> bool:
        run = self._runs.get(run_id)
        if run is None or run.status != "running":
            return False
        if run.task is not None:
            run.task.cancel()
        # Marked here as well as in the task, so an immediate poll is truthful
        # rather than racing the cancellation.
        run.status = "cancelled"
        run.finished = time.monotonic()
        return True

    def cancel_all(self) -> None:
        for run_id in list(self._runs):
            self.cancel(run_id)

    def _prune(self) -> None:
        now = time.monotonic()
        for run_id, run in list(self._runs.items()):
            if run.finished is not None and now - run.finished > RETAIN_SECONDS:
                del self._runs[run_id]
