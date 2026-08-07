"""Wiring. One place that knows how the services fit together."""

from __future__ import annotations

import logging

from .config import Settings, get_settings
from .db import Database
from .services.images import ImageService
from .services.jobs import JobContext, JobQueue
from .services.library import LibraryService
from .services.llm import LlmService
from .services.polish_runs import PolishRunner
from .services.prints import PrintService
from .services.publish import PublishService
from .services.versions import VersionService
from .services.watcher import LibraryWatcher

log = logging.getLogger(__name__)


class AppState:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.settings.ensure_directories()

        self.db = Database(self.settings.db_path)
        self.db.initialise()

        self.library = LibraryService(self.settings, self.db)
        self.prints = PrintService(self.settings, self.db, self.library)
        self.versions = VersionService(self.db, self.library)
        self.images = ImageService(self.settings, self.db, self.library)
        self.publish = PublishService(
            self.settings, self.db, self.library, self.prints, self.images
        )
        self.llm = LlmService(self.settings, self.library.root)
        self.polish_runs = PolishRunner()

        self.jobs = JobQueue(self.db, self.settings.worker_threads)
        self.jobs.register("library.rescan", self._job_rescan)
        self.jobs.register("prints.ingest", self._job_ingest)
        self.jobs.register("render.turntable", self._job_render)

        self.watcher = LibraryWatcher(self.settings, self.library)

        # A sliced 3MF copied into prints/ by hand becomes a print job on the
        # next scan, exactly like one dropped through the UI.
        self._ingesting: set[str] = set()
        self.library.after_scan = self._ingest_new_prints

    def _ingest_new_prints(self, project_id: str) -> bool:
        """Returns True when something was ingested, so the scan refreshes."""
        # ingest_file writes a sidecar, which the watcher sees as a change; the
        # guard stops that from bouncing back into another ingest.
        if project_id in self._ingesting:
            return False
        self._ingesting.add(project_id)
        try:
            return bool(self.prints.ingest_project(project_id, only_stale=True))
        finally:
            self._ingesting.discard(project_id)

    # ------------------------------------------------------------ job kinds

    def _job_rescan(self, payload: dict, ctx: JobContext) -> dict:
        return self.library.scan_all(progress=ctx.progress)

    def _job_ingest(self, payload: dict, ctx: JobContext) -> dict:
        project_id = payload["project_id"]
        ctx.progress(0.1, "reading prints/")
        ingested = self.prints.ingest_project(project_id)
        return {"ingested": len(ingested)}

    def _job_render(self, payload: dict, ctx: JobContext) -> dict:
        return self.images.render_turntable(
            payload["project_id"],
            payload["rel_path"],
            frames=int(payload.get("frames", 24)),
            size=int(payload.get("size", 1200)),
            progress=ctx.progress,
        )

    # ----------------------------------------------------------- lifecycle

    def startup(self) -> None:
        if self.settings.demo_seed:
            self.seed_demo()
        self.publish.ensure_defaults()
        self.jobs.start()
        # Boot scan is synchronous-ish via the queue, so the UI has data quickly
        # without blocking the server from accepting connections.
        self.jobs.enqueue("library.rescan", unique=True)
        self.watcher.start()
        log.info("library at %s, data at %s", self.settings.library_path, self.settings.data_path)

    def seed_demo(self) -> list[str]:
        """Populate an empty library with sample projects. Never overwrites."""
        from .demo import seed_library

        existing = self.library.project_dirs()
        if existing:
            log.info("skipping demo seed — library already has %d projects", len(existing))
            return []
        return seed_library(self.settings.library_path)

    def shutdown(self) -> None:
        # Drops the connections to the LLM host so it stops generating.
        self.polish_runs.cancel_all()
        self.watcher.stop()
        self.jobs.stop()
        self.db.close()
