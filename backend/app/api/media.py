"""Images, file serving, and version snapshots."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from ..services.images import EXPORT_PRESETS, render_backend_available
from ..services.library import PRINTS_DIR
from ..utils import StaleWriteError, safe_join, slugify, today
from .deps import State, require_project
from .schemas import (
    CoverImage,
    ImageOrder,
    ImageSourceLink,
    ImageVariant,
    RenderRequest,
    VersionCreate,
)

router = APIRouter(prefix="/api/projects/{project_id}", tags=["media"])


# ------------------------------------------------------------------- images


@router.get("/images")
def list_images(state: State, project_id: str) -> list[dict]:
    require_project(state, project_id)
    return state.images.list_images(project_id)


@router.get("/sources")
def list_sources(state: State, project_id: str) -> dict:
    """Editable originals: image sources plus the CAD files in models/sources/."""
    require_project(state, project_id)
    return state.images.list_sources(project_id)


@router.post("/images", status_code=201)
async def upload_image(
    state: State,
    project_id: str,
    file: UploadFile = File(...),
    variant: str = Form(""),
) -> dict:
    require_project(state, project_id)
    try:
        rel_path = state.images.add_photo(
            project_id, file.filename or "photo.png", await file.read(), variant
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"rel_path": rel_path, "variant": variant}


@router.post("/images/sources", status_code=201)
async def upload_image_source(
    state: State,
    project_id: str,
    file: UploadFile = File(...),
    for_image: str = Form(""),
) -> dict:
    """Upload a .psd/.pxd/… and optionally link it to an image already there."""
    require_project(state, project_id)
    try:
        rel_path = state.images.add_image_source(
            project_id, file.filename or "source.psd", await file.read(), for_image
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"rel_path": rel_path, "linked_to": for_image}


@router.post("/models/sources", status_code=201)
async def upload_model_source(state: State, project_id: str, file: UploadFile = File(...)) -> dict:
    """Upload a .step/.scad/… into models/sources/."""
    require_project(state, project_id)
    try:
        rel_path = state.images.add_model_source(
            project_id, file.filename or "model.step", await file.read()
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"rel_path": rel_path}


@router.put("/models/sources/content", status_code=204)
async def write_model_source(
    state: State, project_id: str, rel_path: str, request: Request, base_hash: str | None = None
) -> None:
    """Save from the SCAD editor — overwrites an existing file in place.

    `base_hash` (R10, optional) guards against clobbering a concurrent
    write to the same file — see ImageService.write_model_source.
    """
    require_project(state, project_id)
    try:
        state.images.write_model_source(
            project_id,
            rel_path,
            (await request.body()).decode("utf-8"),
            base_hash=base_hash,
        )
    except StaleWriteError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/models/sources/export", status_code=201)
async def export_model_source_stl(
    state: State, project_id: str, rel_path: str, request: Request
) -> dict:
    """Export the editor's compiled STL from a .scad source, same name, overwriting."""
    require_project(state, project_id)
    try:
        exported = state.images.export_model_stl(project_id, rel_path, await request.body())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"rel_path": exported}


@router.delete("/models/sources", status_code=204)
def delete_model_source(state: State, project_id: str, rel_path: str) -> None:
    """Delete a file from models/sources/ — the explorer's and Sources tab's ✕."""
    require_project(state, project_id)
    try:
        state.images.delete_model_source(project_id, rel_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/documents")
def list_documents(state: State, project_id: str) -> list[dict]:
    """PDFs and other attachments kept alongside the project."""
    require_project(state, project_id)
    return state.images.list_documents(project_id)


@router.post("/documents", status_code=201)
async def upload_document(state: State, project_id: str, file: UploadFile = File(...)) -> dict:
    require_project(state, project_id)
    try:
        rel_path = state.images.add_document(
            project_id, file.filename or "document.pdf", await file.read()
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"rel_path": rel_path}


@router.delete("/documents", status_code=204)
def delete_document(state: State, project_id: str, rel_path: str) -> None:
    require_project(state, project_id)
    try:
        state.images.delete_document(project_id, rel_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/images/variant")
def set_variant(state: State, project_id: str, body: ImageVariant) -> dict:
    require_project(state, project_id)
    try:
        state.images.set_variant(project_id, body.rel_path, body.variant)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"rel_path": body.rel_path, "variant": body.variant}


@router.put("/images/source")
def link_source(state: State, project_id: str, body: ImageSourceLink) -> dict:
    require_project(state, project_id)
    state.images.link_source(project_id, body.rel_path, body.source_path)
    return {"rel_path": body.rel_path, "source_path": body.source_path}


@router.post("/upload", status_code=201)
async def upload_anything(
    state: State,
    project_id: str,
    files: list[UploadFile] = File(...),
    variant: str = Form(""),
    for_image: str = Form(""),
) -> dict:
    """One drop zone for everything: images, originals, CAD sources, sliced 3MFs.

    A mixed drop is the normal case, so each file reports its own outcome rather
    than one bad file failing the whole batch.
    """
    project = require_project(state, project_id)
    directory = state.library.project_dir(project["slug"])

    accepted, rejected = [], []
    for upload in files:
        name = (upload.filename or "file").strip()
        payload = await upload.read()
        try:
            if _is_sliced_upload(name):
                accepted.append(_ingest_sliced(state, project_id, directory, name, payload))
            else:
                accepted.append(
                    state.images.route_upload(
                        project_id, name, payload, variant=variant, for_image=for_image
                    )
                )
        except Exception as exc:  # noqa: BLE001 — per-file reporting, keep going
            rejected.append({"filename": name, "error": str(exc)})

    return {"accepted": accepted, "rejected": rejected}


def _is_sliced_upload(name: str) -> bool:
    """`.gcode.3mf` and `.gcode` are sliced; a bare `.3mf` is just a mesh."""
    lowered = name.lower()
    return lowered.endswith(".gcode.3mf") or lowered.endswith(".gcode")


def _ingest_sliced(state: State, project_id: str, directory: Path, name: str, payload: bytes):
    prints_dir = directory / PRINTS_DIR
    prints_dir.mkdir(parents=True, exist_ok=True)

    lowered = name.lower()
    suffix = ".gcode" if lowered.endswith(".gcode") else ".gcode.3mf"
    bare = name[: -len(suffix)] if lowered.endswith(suffix) else name
    stem = slugify(bare, "print")

    target = prints_dir / f"{today()}_{stem}{suffix}"
    counter = 2
    while target.exists():
        target = prints_dir / f"{today()}_{stem}-{counter}{suffix}"
        counter += 1

    target.write_bytes(payload)
    try:
        print_id = state.prints.ingest_file(project_id, target)
    except Exception:
        target.unlink(missing_ok=True)  # never keep a file we cannot read
        raise
    state.library.scan_project_dir(directory)
    return {"kind": "print", "rel_path": target.relative_to(directory).as_posix(), "id": print_id}


@router.put("/images/cover")
def set_cover(state: State, project_id: str, body: CoverImage) -> dict:
    require_project(state, project_id)
    state.images.set_cover(project_id, body.rel_path)
    return {"cover_image": body.rel_path}


@router.put("/images/order")
def reorder_images(state: State, project_id: str, body: ImageOrder) -> dict:
    require_project(state, project_id)
    state.images.reorder(project_id, body.rel_paths)
    return {"image_order": body.rel_paths}


@router.delete("/images", status_code=204)
def delete_image(state: State, project_id: str, rel_path: str, with_source: bool = False) -> None:
    require_project(state, project_id)
    try:
        state.images.delete_image(project_id, rel_path, with_source=with_source)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/thumb")
def thumbnail(state: State, project_id: str, rel_path: str) -> FileResponse:
    require_project(state, project_id)
    try:
        cached = state.images.thumbnail(project_id, rel_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if cached is None:
        raise HTTPException(status_code=404, detail="no thumbnail")
    return FileResponse(cached, media_type="image/webp")


@router.get("/file")
def project_file(state: State, project_id: str, rel_path: str) -> FileResponse:
    """Serve any file inside the project folder, for previews and downloads."""
    project = require_project(state, project_id)
    directory = state.library.project_dir(project["slug"])
    try:
        target = safe_join(directory, rel_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not target.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(target, filename=target.name)


# ------------------------------------------------------------------ renders


@router.post("/render", status_code=202)
def queue_render(state: State, project_id: str, body: RenderRequest) -> dict:
    """Queued, never synchronous — a 24-frame turntable is minutes of CPU."""
    require_project(state, project_id)
    if not render_backend_available():
        raise HTTPException(
            status_code=503,
            detail="rendering extras are not installed on this server (pip install '.[render]')",
        )
    job_id = state.jobs.enqueue(
        "render.turntable",
        {
            "project_id": project_id,
            "rel_path": body.rel_path,
            "frames": body.frames,
            "size": body.size,
        },
    )
    return {"job_id": job_id}


@router.get("/render/presets")
def render_presets() -> dict:
    return {"available": render_backend_available(), "export_presets": EXPORT_PRESETS}


# ----------------------------------------------------------------- versions


@router.get("/versions")
def list_versions(state: State, project_id: str) -> list[dict]:
    require_project(state, project_id)
    return state.versions.list_versions(project_id)


@router.post("/versions", status_code=201)
def create_version(state: State, project_id: str, body: VersionCreate) -> dict:
    require_project(state, project_id)
    try:
        return state.versions.create(project_id, body.label, body.note)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/versions/{folder}")
def version_contents(state: State, project_id: str, folder: str) -> list[dict]:
    require_project(state, project_id)
    try:
        return state.versions.contents(project_id, folder)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="version not found") from exc


@router.post("/versions/{folder}/restore")
def restore_version(state: State, project_id: str, folder: str) -> dict:
    require_project(state, project_id)
    try:
        return state.versions.restore(project_id, folder)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/versions/{folder}", status_code=204)
def delete_version(state: State, project_id: str, folder: str) -> None:
    require_project(state, project_id)
    try:
        state.versions.delete(project_id, folder)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
