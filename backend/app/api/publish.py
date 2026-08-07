"""Publish workspace: templates, snippets, draft, export, and LLM polish."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .deps import State, require_project
from .schemas import MarkdownDoc, PolishRequest, PreviewRequest, PublishDraftIn

router = APIRouter(prefix="/api", tags=["publish"])


# --------------------------------------------------------- shared libraries


@router.get("/templates")
def list_templates(state: State) -> list[dict]:
    return state.publish.list_templates()


@router.put("/templates")
def save_template(state: State, body: MarkdownDoc) -> dict:
    return state.publish.save_template(body.name, body.body)


@router.delete("/templates/{name}", status_code=204)
def delete_template(state: State, name: str) -> None:
    state.publish.delete_template(name)


@router.get("/snippets")
def list_snippets(state: State) -> list[dict]:
    return state.publish.list_snippets()


@router.put("/snippets")
def save_snippet(state: State, body: MarkdownDoc) -> dict:
    return state.publish.save_snippet(body.name, body.body)


@router.delete("/snippets/{name}", status_code=204)
def delete_snippet(state: State, name: str) -> None:
    state.publish.delete_snippet(name)


@router.get("/publish/recent")
def recent_values(state: State) -> dict:
    return state.publish.recent_values()


# ---------------------------------------------------------------- per-project


@router.get("/projects/{project_id}/publish")
def load_draft(state: State, project_id: str) -> dict:
    require_project(state, project_id)
    draft = state.publish.load_draft(project_id)
    draft["profiles"] = state.publish.profile_table(project_id, [])
    draft["context"] = state.publish.build_context(project_id, draft.get("print_ids") or None)
    return draft


@router.put("/projects/{project_id}/publish")
def save_draft(state: State, project_id: str, body: PublishDraftIn) -> dict:
    require_project(state, project_id)
    return state.publish.save_draft(project_id, body.model_dump(exclude_unset=True))


@router.post("/projects/{project_id}/publish/preview")
def preview(state: State, project_id: str, body: PreviewRequest) -> dict:
    """Fill a template from the ingested print data."""
    require_project(state, project_id)
    return {"markdown": state.publish.preview(project_id, body.template, body.print_ids)}


@router.post("/projects/{project_id}/publish/export")
def export(state: State, project_id: str, preset: str = "") -> dict:
    """Assets are copied verbatim unless `preset` explicitly asks for resizing."""
    require_project(state, project_id)
    return state.publish.export(project_id, preset)


# ----------------------------------------------------------------- LLM polish


@router.get("/llm/status")
async def llm_status(state: State) -> dict:
    """Drives the greyed-out polish button. Never fails the page."""
    return await state.llm.status()


@router.post("/llm/polish", status_code=202)
async def start_polish(state: State, body: PolishRequest) -> dict:
    """Kick off a polish run and return immediately with its id.

    A local model routinely takes 15-60s, so the browser waits on a poll it can
    abandon rather than on one long request it cannot.
    """
    if not state.llm.configured:
        raise HTTPException(
            status_code=503, detail="No LLM server is configured — set one up in Settings"
        )
    if not body.text.strip():
        raise HTTPException(status_code=422, detail="nothing to polish")

    async def work() -> str:
        return await state.llm.polish(body.text, instructions=body.instructions, model=body.model)

    run = state.polish_runs.start(body.text, work)
    return run.as_dict()


@router.get("/llm/polish/{run_id}")
def poll_polish(state: State, run_id: str) -> dict:
    run = state.polish_runs.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="that polish run has expired")
    return run.as_dict()


@router.post("/llm/polish/{run_id}/cancel")
def cancel_polish(state: State, run_id: str) -> dict:
    """Abort the run, which closes the connection and stops the model generating."""
    run = state.polish_runs.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="that polish run has expired")
    state.polish_runs.cancel(run_id)
    return run.as_dict()
