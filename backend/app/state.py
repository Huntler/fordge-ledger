"""Wiring. One place that knows how the services fit together."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

import httpx

from .config import Settings, get_settings
from .db import Database
from .services.images import ImageService
from .services.jobs import JobContext, JobQueue
from .services.library import LibraryService
from .services.llm import LlmService
from .services.polish_runs import PolishRunner
from .services.prints import PrintService
from .services.publish import PublishService
from .services.tools import ToolsService
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
        self.tools = ToolsService(self.library)
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

        # Editor availability probe cache (§2.4, §3c) — a 60s TTL, 2s-timeout
        # check of FORGE_EDITOR_URL, not the presence of the env var itself.
        # That's what makes leaving FORGE_EDITOR_URL set after removing the
        # editor's compose service degrade to "tab hidden" instead of "tab
        # links to a dead page".
        self._editor_status_cache: dict[str, Any] | None = None
        self._editor_status_checked_at: float = 0.0
        self._editor_last_available: bool | None = None

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

    # --------------------------------------------------- editor availability

    # Bump on ANY change to the seven host-contract endpoints in §1.4 of the
    # extraction plan (the .../sources, .../file, .../models/sources* family)
    # or their request/response shapes. Mirrored on the forge-scad-editor
    # side as HOST_CONTRACT_VERSION in api/system.py — the two must agree.
    HOST_CONTRACT_VERSION = 1

    _EDITOR_STATUS_TTL = 60.0
    _EDITOR_PROBE_TIMEOUT = 2.0

    def library_marker(self, *, create: bool = False) -> str:
        """UUID identifying *this* library, at `<library>/_shared/.forge-instance`.

        Create-if-missing, never overwrite — called with create=True once,
        at startup. The editor's own /api/health echoes back whatever it
        reads at the same path (it never writes it — see forge-scad-editor's
        state.py), so comparing the two catches a mount pointing at the
        wrong directory, an empty mount, or two containers that both mount
        `/library` but land on genuinely different filesystems (R4) — which
        comparing `library_path` strings alone cannot, and which
        docker-compose.test.yml's tmpfs mounts would otherwise trigger as a
        false negative (§Phase 6).
        """
        marker = self.settings.library_path / "_shared" / ".forge-instance"
        if marker.is_file():
            return marker.read_text(encoding="utf-8").strip()
        if not create:
            return ""
        marker.parent.mkdir(parents=True, exist_ok=True)
        value = str(uuid.uuid4())
        marker.write_text(value, encoding="utf-8")
        return value

    def editor_status(self) -> dict[str, Any]:
        """Cached (60s TTL) probe of FORGE_EDITOR_URL — the authority behind
        the Editor tab's visibility (§2.4), not the env var's mere presence.
        A 2s timeout keeps a hung or unreachable editor from ever blocking
        `/api/health` for the rest of the app.
        """
        now = time.monotonic()
        cache_age = now - self._editor_status_checked_at
        if self._editor_status_cache is not None and cache_age < self._EDITOR_STATUS_TTL:
            return self._editor_status_cache

        result = self._probe_editor()
        self._editor_status_checked_at = now
        self._editor_status_cache = result

        # Log only on a state transition — a 60s poll that logs every
        # failure buries the one line that actually matters.
        available = result["available"]
        if available != self._editor_last_available:
            if available:
                log.info("editor available at %s", self.settings.editor_url)
            else:
                log.warning("editor unavailable: %s", result.get("reason"))
        self._editor_last_available = available
        return result

    def _probe_editor(self) -> dict[str, Any]:
        if not self.settings.editor_url:
            return {"available": False, "reason": "not configured"}
        try:
            response = httpx.get(
                f"{self.settings.editor_url.rstrip('/')}/editor/api/health",
                timeout=self._EDITOR_PROBE_TIMEOUT,
            )
            response.raise_for_status()
            body = response.json()
        except Exception as exc:  # noqa: BLE001 — any failure means "unavailable", not a 500
            return {"available": False, "reason": f"unreachable: {exc}"}

        if body.get("host_contract") != self.HOST_CONTRACT_VERSION:
            return {
                "available": False,
                "reason": (
                    f"contract mismatch: editor wants v{body.get('host_contract')}, "
                    f"this server speaks v{self.HOST_CONTRACT_VERSION}"
                ),
            }
        if body.get("library_marker") != self.library_marker():
            return {
                "available": False,
                "reason": "library mismatch: the editor is not mounted on this library",
            }

        return {"available": True, "path": "/editor/", "reason": None, **body}

    # ----------------------------------------------------------- lifecycle

    def startup(self) -> None:
        if self.settings.demo_seed:
            self.seed_demo()
        self.library_marker(create=True)
        self.publish.ensure_defaults()
        self.tools.ensure_defaults()
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
