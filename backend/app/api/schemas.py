"""Request bodies. Responses are plain dicts assembled from the cache."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..services.library import STATUSES
from ..services.prints import STATUSES as PRINT_STATUSES


class RemixSourceIn(BaseModel):
    url: str
    title: str = ""
    author: str = ""
    license: str = ""


class ModelIn(BaseModel):
    name: str
    files: list[str] = Field(default_factory=list)


class ProjectCreate(BaseModel):
    title: str
    status: str = "idea"
    tags: list[str] = Field(default_factory=list)
    license: str = ""


class ProjectUpdate(BaseModel):
    title: str | None = None
    status: str | None = None
    tags: list[str] | None = None
    license: str | None = None
    notes: str | None = None
    cover_image: str | None = None
    image_order: list[str] | None = None
    remix_of: list[RemixSourceIn] | None = None
    makerworld_url: str | None = None
    models: list[ModelIn] | None = None

    def changes(self) -> dict[str, Any]:
        data = self.model_dump(exclude_none=True)
        if data.get("status") and data["status"] not in STATUSES:
            raise ValueError(f"status must be one of {', '.join(STATUSES)}")
        return data


class AttachFiles(BaseModel):
    model_name: str
    files: list[str]


class PrintUpdate(BaseModel):
    status: str | None = None
    model_name: str | None = None
    notes: str | None = None
    actual_s: int | None = None
    failure_reason: str | None = None
    failure_fix: str | None = None
    cost: float | None = None

    def changes(self) -> dict[str, Any]:
        data = self.model_dump(exclude_unset=True)
        if data.get("status") and data["status"] not in PRINT_STATUSES:
            raise ValueError(f"status must be one of {', '.join(PRINT_STATUSES)}")
        return data


class VersionCreate(BaseModel):
    label: str = ""
    note: str = ""


class ImageOrder(BaseModel):
    rel_paths: list[str]


class CoverImage(BaseModel):
    rel_path: str


class ImageVariant(BaseModel):
    rel_path: str
    # "" clears the tag, so an image can serve both listings.
    variant: str = ""


class ImageSourceLink(BaseModel):
    rel_path: str
    source_path: str = ""


class RenderRequest(BaseModel):
    rel_path: str
    frames: int = Field(default=24, ge=4, le=120)
    size: int = Field(default=1200, ge=200, le=2400)


class MarkdownDoc(BaseModel):
    name: str
    body: str


class PublishDraftIn(BaseModel):
    title: str | None = None
    summary: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    category: str | None = None
    license: str | None = None
    template: str | None = None
    print_ids: list[str] | None = None
    assets: list[str] | None = None
    checklist: dict[str, bool] | None = None


class PreviewRequest(BaseModel):
    template: str | None = None
    print_ids: list[str] | None = None


class PolishRequest(BaseModel):
    text: str
    instructions: str = ""
    model: str | None = None


class LlmSettingsIn(BaseModel):
    provider: str | None = None
    # Accepts a bare IP, host:port, or a full URL; normalised on save.
    base_url: str | None = None
    model: str | None = None
    system_prompt: str | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    timeout_seconds: float | None = Field(default=None, ge=5.0, le=900.0)
