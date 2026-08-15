"""SCAD snippet "tools" — the Editor page's toolbar, managed from Settings."""

from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .deps import State, require_project

router = APIRouter(prefix="/api/tools", tags=["tools"])


@router.get("")
def list_tools(state: State) -> list[dict]:
    return state.tools.list_tools()


@router.put("")
async def save_tool(
    state: State,
    name: str = Form(...),
    body: str = Form(...),
    icon: UploadFile | None = File(None),
) -> dict:
    icon_bytes = await icon.read() if icon is not None else None
    try:
        return state.tools.save_tool(name, body, icon_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/{name}", status_code=204)
def delete_tool(state: State, name: str) -> None:
    state.tools.delete_tool(name)


@router.get("/{name}/icon")
def tool_icon(state: State, name: str) -> FileResponse:
    path = state.tools.icon_path(name)
    if path is None:
        raise HTTPException(status_code=404, detail="no icon")
    return FileResponse(path, media_type="image/png")


# ------------------------------------------------------ per-project use


@router.post("/{name}/projects/{project_id}", status_code=201)
def add_tool_to_project(state: State, name: str, project_id: str) -> dict:
    require_project(state, project_id)
    try:
        rel_path = state.tools.copy_into_project(project_id, name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"rel_path": rel_path}


@router.delete("/{name}/projects/{project_id}", status_code=204)
def remove_tool_from_project(state: State, name: str, project_id: str) -> None:
    require_project(state, project_id)
    state.tools.remove_from_project(project_id, name)
