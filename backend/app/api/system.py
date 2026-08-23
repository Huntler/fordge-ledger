"""Health, jobs, rescan, and the SSE stream the UI listens on."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..services.events import bus
from ..services.images import render_backend_available
from ..services.versions import reflink_probe
from .deps import State

router = APIRouter(prefix="/api", tags=["system"])

HEARTBEAT_SECONDS = 20.0


@router.get("/health")
def health(state: State) -> dict:
    counts = {
        table: state.db.count(table) for table in ("projects", "prints", "versions", "images")
    }
    return {
        "status": "ok",
        "library_path": str(state.settings.library_path),
        "data_path": str(state.settings.data_path),
        "counts": counts,
        "last_full_scan": state.library.last_full_scan(),
        "watcher": state.settings.watch_enabled,
        "render_available": render_backend_available(),
        # True on the throwaway instance, whose library does not survive a restart.
        "demo_instance": state.settings.demo_seed,
        "llm_configured": state.llm.configured,
        "llm_provider": state.llm.load().provider,
        # False just means snapshots use full copies instead of clones.
        "reflink_available": reflink_probe(state.settings.library_path),
        # Drives the Editor tab's visibility (§2.4) — the probe result, not
        # FORGE_EDITOR_URL's mere presence. See AppState.editor_status.
        "editor": state.editor_status(),
    }


@router.get("/stats")
def stats(state: State) -> dict:
    by_status = {
        row["status"]: row["n"]
        for row in state.db.query("SELECT status, COUNT(*) AS n FROM projects GROUP BY status")
    }
    return {"projects_by_status": by_status, "prints": state.prints.stats()}


@router.post("/rescan", status_code=202)
def rescan(state: State) -> dict:
    """The manual escape hatch for when the watcher has missed something."""
    return {"job_id": state.jobs.enqueue("library.rescan", unique=True)}


@router.get("/jobs")
def list_jobs(state: State, limit: int = 50) -> list[dict]:
    return state.jobs.recent(limit)


@router.post("/jobs/{job_id}/cancel")
def cancel_job(state: State, job_id: int) -> dict:
    if not state.jobs.cancel(job_id):
        raise HTTPException(status_code=409, detail="job is not cancellable")
    return {"ok": True}


@router.get("/events")
async def events(request: Request, state: State) -> StreamingResponse:
    """Server-sent events: job progress, library changes, print updates."""
    queue = bus.subscribe()

    async def stream():
        try:
            yield _sse({"event": "hello", "data": {"ok": True}})
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                except TimeoutError:
                    # Keeps proxies from closing an idle connection.
                    yield ": ping\n\n"
                    continue
                yield f"data: {payload}\n\n"
        finally:
            bus.unsubscribe(queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"
