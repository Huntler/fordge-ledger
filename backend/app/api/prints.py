"""Print jobs: drop in a sliced 3MF, then move it across the board."""

from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile

from ..services.library import PRINTS_DIR
from ..utils import slugify, today
from .deps import State, require_project
from .schemas import PrintUpdate

router = APIRouter(prefix="/api", tags=["prints"])


@router.get("/prints")
def list_prints(state: State, project_id: str = "", status: str = "") -> list[dict]:
    return state.prints.list_prints(project_id or None, status or None)


@router.get("/prints/stats")
def print_stats(state: State) -> dict:
    return state.prints.stats()


@router.get("/prints/failures")
def failure_log(state: State, limit: int = 100) -> list[dict]:
    """Why did this warp last time — as a query, not a memory."""
    return state.prints.failure_log(limit)


@router.get("/prints/{print_id}")
def get_print(state: State, print_id: str) -> dict:
    record = state.prints.get(print_id)
    if record is None:
        raise HTTPException(status_code=404, detail="print not found")
    return record


@router.patch("/prints/{print_id}")
def update_print(state: State, print_id: str, body: PrintUpdate) -> dict:
    try:
        return state.prints.update(print_id, body.changes())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="print not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/prints/{print_id}", status_code=204)
def delete_print(state: State, print_id: str, remove_files: bool = False) -> None:
    try:
        state.prints.delete(print_id, remove_files=remove_files)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="print not found") from exc


@router.post("/projects/{project_id}/prints", status_code=201)
async def upload_sliced_file(state: State, project_id: str, file: UploadFile = File(...)) -> dict:
    """Drop in a sliced 3MF; the app parses the settings and creates the job."""
    project = require_project(state, project_id)
    directory = state.library.project_dir(project["slug"])

    name = (file.filename or "print.3mf").strip()
    if not (name.lower().endswith(".3mf") or name.lower().endswith(".gcode")):
        raise HTTPException(status_code=422, detail="expected a .3mf or .gcode file")

    prints_dir = directory / PRINTS_DIR
    prints_dir.mkdir(parents=True, exist_ok=True)

    # Dated filenames keep prints/ chronological in a file manager. Sliced 3MFs
    # are always stored as `.gcode.3mf`, whatever the slicer named them.
    lowered = name.lower()
    for extension in (".gcode.3mf", ".3mf", ".gcode"):
        if lowered.endswith(extension):
            bare = name[: -len(extension)]
            break
    else:  # pragma: no cover — guarded by the check above
        bare = name
    suffix = ".gcode" if lowered.endswith(".gcode") else ".gcode.3mf"
    stem = slugify(bare, "print")
    target = prints_dir / f"{today()}_{stem}{suffix}"
    counter = 2
    while target.exists():
        target = prints_dir / f"{today()}_{stem}-{counter}{suffix}"
        counter += 1

    target.write_bytes(await file.read())

    try:
        print_id = state.prints.ingest_file(project_id, target)
    except Exception as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=f"could not parse: {exc}") from exc

    state.library.scan_project_dir(directory)
    return get_print(state, print_id)


@router.post("/projects/{project_id}/prints/ingest")
def ingest_existing(state: State, project_id: str) -> dict:
    """Pick up 3MFs copied into prints/ by hand."""
    require_project(state, project_id)
    job_id = state.jobs.enqueue("prints.ingest", {"project_id": project_id})
    return {"job_id": job_id}
