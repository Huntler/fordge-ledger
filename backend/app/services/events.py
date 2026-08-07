"""In-process event bus feeding the SSE stream.

Publishers are mostly worker *threads* (scans, renders) while subscribers are
*asyncio* consumers in the request loop, so every hand-off goes through
``call_soon_threadsafe``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

log = logging.getLogger(__name__)

# Slow or dead clients must never block a worker thread; past this depth the
# oldest events are dropped and the client is told to refetch.
MAX_QUEUE_DEPTH = 256


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[str]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Called once at startup, so threads know where to deliver."""
        self._loop = loop

    def subscribe(self) -> asyncio.Queue[str]:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=MAX_QUEUE_DEPTH)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[str]) -> None:
        self._subscribers.discard(queue)

    def publish(self, event: str, data: dict[str, Any] | None = None) -> None:
        """Fan out to every subscriber. Safe to call from any thread."""
        payload = json.dumps({"event": event, "data": data or {}})
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        # RuntimeError here means the loop is shutting down; the event is moot.
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(self._deliver, payload)

    def _deliver(self, payload: str) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                # Drop the oldest so the newest state still gets through.
                try:
                    queue.get_nowait()
                    queue.put_nowait(payload)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    log.debug("dropping event for a saturated subscriber")


bus = EventBus()
