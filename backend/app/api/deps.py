"""Request-scoped access to the shared AppState."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request

from ..state import AppState


def get_state(request: Request) -> AppState:
    state: AppState | None = getattr(request.app.state, "app_state", None)
    if state is None:  # pragma: no cover — only reachable if startup failed
        raise HTTPException(status_code=503, detail="application is still starting")
    return state


State = Annotated[AppState, Depends(get_state)]


def require_project(state: AppState, project_id: str) -> dict:
    row = state.db.query_one("SELECT * FROM projects WHERE id = ?", (project_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="project not found")
    return dict(row)
