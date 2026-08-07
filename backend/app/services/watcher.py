"""Filesystem watcher, plus the nightly full rescan that backs it up.

CAD and slicer tools write in bursts, so events are coalesced per project over a
debounce window. inotify also drops events under load and sees nothing that
happened while the container was down, hence the scheduled full rescan — the
watcher is an optimisation, the rescan is the guarantee.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from ..config import Settings
from .library import RESERVED_DIRS, LibraryService

log = logging.getLogger(__name__)


class _DebouncedHandler(FileSystemEventHandler):
    """Collects touched project slugs; a timer thread does the actual work."""

    def __init__(self, root: Path):
        self.root = root
        self._pending: set[str] = set()
        self._lock = threading.Lock()
        self._last_event = 0.0

    def on_any_event(self, event: FileSystemEvent) -> None:
        for raw in (event.src_path, getattr(event, "dest_path", None)):
            slug = self._slug_for(raw)
            if slug:
                with self._lock:
                    self._pending.add(slug)
                    self._last_event = time.monotonic()

    def _slug_for(self, raw_path: str | bytes | None) -> str | None:
        if not raw_path:
            return None
        path = Path(raw_path.decode() if isinstance(raw_path, bytes) else raw_path)
        try:
            relative = path.relative_to(self.root)
        except ValueError:
            return None
        if not relative.parts:
            return None
        slug = relative.parts[0]
        if slug.startswith(".") or slug in RESERVED_DIRS:
            return None
        # Ignore our own atomic-write temp files.
        if path.name.endswith(".tmp"):
            return None
        return slug

    def take_settled(self, window: float) -> set[str]:
        """Return the pending slugs once the write burst has been quiet for `window`."""
        with self._lock:
            if not self._pending or time.monotonic() - self._last_event < window:
                return set()
            pending, self._pending = self._pending, set()
        return pending


class LibraryWatcher:
    def __init__(self, settings: Settings, library: LibraryService):
        self.settings = settings
        self.library = library
        self._observer: Observer | None = None  # type: ignore[valid-type]
        self._handler: _DebouncedHandler | None = None
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        self.settings.library_path.mkdir(parents=True, exist_ok=True)

        if self.settings.watch_enabled:
            self._handler = _DebouncedHandler(self.settings.library_path)
            observer = Observer()
            observer.schedule(self._handler, str(self.settings.library_path), recursive=True)
            observer.start()
            self._observer = observer
            self._spawn(self._debounce_loop, "library-debounce")
            log.info("watching %s", self.settings.library_path)

        self._spawn(self._nightly_loop, "library-nightly")

    def _spawn(self, target, name: str) -> None:
        thread = threading.Thread(target=target, name=name, daemon=True)
        thread.start()
        self._threads.append(thread)

    def stop(self) -> None:
        self._stop.set()
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None
        for thread in self._threads:
            thread.join(timeout=5)
        self._threads.clear()

    def _rescan(self, slugs: set[str]) -> None:
        for slug in slugs:
            directory = self.library.project_dir(slug)
            try:
                if directory.is_dir():
                    self.library.scan_project_dir(directory)
                else:
                    # The folder is gone; a full rescan clears the cached rows.
                    self.library.scan_all()
                    return
            except Exception:
                log.exception("rescan of %s failed", slug)

    def _debounce_loop(self) -> None:
        handler = self._handler
        assert handler is not None
        window = self.settings.watch_debounce_seconds

        while not self._stop.wait(0.25):
            # Fires only once the burst has ended, not on its first event.
            pending = handler.take_settled(window)
            if pending:
                log.debug("rescanning after burst: %s", ", ".join(sorted(pending)))
                self._rescan(pending)

    def _nightly_loop(self) -> None:
        while not self._stop.is_set():
            delay = _seconds_until_hour(self.settings.full_rescan_hour)
            if self._stop.wait(timeout=delay):
                return
            log.info("running scheduled full rescan")
            try:
                self.library.scan_all()
            except Exception:
                log.exception("scheduled rescan failed")


def _seconds_until_hour(hour: int) -> float:
    now = datetime.now()
    target = now.replace(hour=hour % 24, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()
