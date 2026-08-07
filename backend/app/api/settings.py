"""Runtime settings, currently the LLM connection and its system prompt."""

from __future__ import annotations

from dataclasses import replace

from fastapi import APIRouter, HTTPException

from ..services.llm import DEFAULT_SYSTEM_PROMPT, PROVIDERS, LlmSettings, normalise_base_url
from .deps import State
from .schemas import LlmSettingsIn

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("/llm")
def get_llm_settings(state: State) -> dict:
    current = state.llm.load()
    return {
        **current.as_dict(),
        "providers": list(PROVIDERS),
        "default_system_prompt": DEFAULT_SYSTEM_PROMPT,
        "settings_path": str(state.llm.settings_path),
    }


@router.put("/llm")
def save_llm_settings(state: State, body: LlmSettingsIn) -> dict:
    try:
        state.llm.save(body.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return get_llm_settings(state)


@router.post("/llm/test")
async def test_llm_settings(state: State, body: LlmSettingsIn) -> dict:
    """Probe a server without saving, so a bad URL never overwrites a good one.

    The probe runs here in the backend, not the browser: on a NAS this container
    is what has to reach the LLM machine, and its view of the network is the one
    that matters.
    """
    current = state.llm.load()
    changes = body.model_dump(exclude_unset=True)
    provider = changes.get("provider") or current.provider
    if provider not in PROVIDERS:
        raise HTTPException(status_code=422, detail=f"unknown provider {provider!r}")

    candidate: LlmSettings = replace(
        current,
        provider=provider,
        base_url=normalise_base_url(changes.get("base_url", current.base_url) or "", provider),
        model=changes.get("model", current.model) or "",
    )
    return await state.llm.status(candidate)


@router.post("/llm/reload")
def reload_llm_settings(state: State) -> dict:
    """Re-read `_shared/llm.yaml` after editing it by hand."""
    state.llm.invalidate()
    return get_llm_settings(state)
