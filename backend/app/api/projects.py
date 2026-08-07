"""Projects, models and the unfiled list."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Query

from ..services.library import STATUS_SORT_ORDER, STATUSES
from .deps import State, require_project
from .schemas import AttachFiles, ProjectCreate, ProjectUpdate

router = APIRouter(prefix="/api/projects", tags=["projects"])


def _expand(row: dict) -> dict:
    row["tags"] = json.loads(row.get("tags") or "[]")
    row["remix_of"] = json.loads(row.get("remix_of") or "[]")
    return row


@router.get("")
def list_projects(
    state: State,
    q: str = "",
    status: str = "",
    tag: str = "",
    sort: str = Query("title", pattern="^(title|created|scanned_at|status)$"),
) -> list[dict]:
    clauses, params = [], []
    if q:
        clauses.append("(title LIKE ? OR notes LIKE ? OR tags LIKE ?)")
        params += [f"%{q}%"] * 3
    if status:
        clauses.append("status = ?")
        params.append(status)
    if tag:
        # tags is a JSON array; the quotes keep `desk` from matching `desktop`.
        clauses.append("tags LIKE ?")
        params.append(f'%"{tag}"%')
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    if sort == "status":
        # Alphabetical status makes no sense to a human — "published" should
        # lead, not sort after "designing". STATUS_SORT_ORDER is a fixed,
        # internal tuple, so inlining it is safe; title breaks ties.
        cases = " ".join(f"WHEN '{s}' THEN {i}" for i, s in enumerate(STATUS_SORT_ORDER))
        order_by = f"CASE status {cases} ELSE {len(STATUS_SORT_ORDER)} END, title COLLATE NOCASE"
    else:
        order_by = f"{sort} COLLATE NOCASE"

    rows = state.db.query(
        f"""
        SELECT p.*,
               (SELECT COUNT(*) FROM models  m WHERE m.project_id = p.id) AS model_count,
               (SELECT COUNT(*) FROM prints  r WHERE r.project_id = p.id) AS print_count,
               (SELECT COUNT(*) FROM files   f WHERE f.project_id = p.id AND f.filed = 0)
                   AS unfiled_count
        FROM projects p {where} ORDER BY {order_by}
        """,
        tuple(params),
    )
    return [_expand(dict(row)) for row in rows]


@router.get("/statuses")
def statuses() -> list[str]:
    return list(STATUSES)


@router.get("/tags")
def all_tags(state: State) -> list[dict]:
    counts: dict[str, int] = {}
    for row in state.db.query("SELECT tags FROM projects"):
        for tag in json.loads(row["tags"] or "[]"):
            counts[tag] = counts.get(tag, 0) + 1
    return [
        {"tag": tag, "count": count}
        for tag, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


@router.post("", status_code=201)
def create_project(state: State, body: ProjectCreate) -> dict:
    if not body.title.strip():
        raise HTTPException(status_code=422, detail="title is required")
    project_id = state.library.create_project(
        body.title, status=body.status, tags=body.tags, license=body.license
    )
    return _expand(require_project(state, project_id))


@router.get("/{project_id}")
def get_project(state: State, project_id: str) -> dict:
    project = _expand(require_project(state, project_id))

    project["models"] = [
        {"name": row["name"], "files": json.loads(row["files"])}
        for row in state.db.query(
            "SELECT name, files FROM models WHERE project_id = ? ORDER BY name", (project_id,)
        )
    ]
    project["files"] = [
        dict(row)
        for row in state.db.query(
            "SELECT rel_path, kind, size, mtime, filed FROM files WHERE project_id = ? "
            "ORDER BY rel_path",
            (project_id,),
        )
    ]
    project["unfiled"] = state.library.unfiled(project_id)
    project["images"] = state.images.list_images(project_id)
    project["documents"] = state.images.list_documents(project_id)
    project["versions"] = state.versions.list_versions(project_id)
    project["prints"] = state.prints.list_prints(project_id)
    project["makerworld_url"] = project.get("makerworld_url")
    return project


@router.patch("/{project_id}")
def update_project(state: State, project_id: str, body: ProjectUpdate) -> dict:
    require_project(state, project_id)
    try:
        changes = body.changes()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # A retitle also moves the folder, so the slug keeps matching the title.
    if "title" in changes:
        state.library.rename_project(project_id, changes.pop("title"))
    if changes:
        state.library.update_project(project_id, changes)
    return get_project(state, project_id)


@router.delete("/{project_id}")
def delete_project(state: State, project_id: str, purge: bool = False) -> dict:
    """Move the project folder to `_trash/`, or destroy it when `purge` is set.

    The UI only ever calls the trashing form. Emptying `_trash/` is left to the
    file manager, because that is the one step nothing here can undo.
    """
    project = require_project(state, project_id)
    trashed_to = state.library.delete_project(project_id, keep_files=not purge)
    return {
        "id": project_id,
        "title": project["title"],
        "purged": purge,
        "trashed_to": trashed_to,
    }


@router.post("/{project_id}/rescan")
def rescan_project(state: State, project_id: str) -> dict:
    project = require_project(state, project_id)
    state.library.scan_project(project["slug"])
    return {"ok": True, "slug": project["slug"]}


@router.post("/{project_id}/attach")
def attach_files(state: State, project_id: str, body: AttachFiles) -> dict:
    require_project(state, project_id)
    state.library.attach_files_to_model(project_id, body.model_name, body.files)
    return get_project(state, project_id)
