"""Reusable OpenSCAD snippet "tools" for the in-browser editor's toolbar.

Library-wide, not per-project — same `_shared/` idiom as
`PublishService`'s templates/snippets (`publish.py`), including its "the
slugified name *is* the identity, saving under a new name creates a new
entry" semantics. The only addition here is an optional square icon image,
validated and stored alongside the snippet.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PIL import Image

from ..utils import slugify
from .library import MODEL_SOURCES_DIR, LibraryService

log = logging.getLogger(__name__)

TOOLS_DIR = "_shared/tools"

MAX_ICON_SIZE = 512

DEFAULT_TOOLS = {
    "chamfer-cylinder": (
        "module chamfer_cylinder(h, r, chamfer=1) {\n"
        "  // TODO: replace with your own chamfer profile\n"
        "  cylinder(h, r, r);\n"
        "}\n"
    ),
}


class ToolsService:
    def __init__(self, library: LibraryService):
        self.library = library

    @property
    def tools_dir(self) -> Path:
        return self.library.root / TOOLS_DIR

    def ensure_defaults(self) -> None:
        """Seed the shared library on first run. Never overwrites your edits."""
        self.tools_dir.mkdir(parents=True, exist_ok=True)
        for stem, body in DEFAULT_TOOLS.items():
            target = self.tools_dir / f"{stem}.scad"
            if not target.exists():
                target.write_text(body, encoding="utf-8")

    def list_tools(self) -> list[dict[str, Any]]:
        if not self.tools_dir.is_dir():
            return []
        return [
            {
                "name": path.stem,
                "body": path.read_text(encoding="utf-8"),
                "has_icon": path.with_suffix(".png").exists(),
            }
            for path in sorted(self.tools_dir.glob("*.scad"))
        ]

    def save_tool(self, name: str, body: str, icon_bytes: bytes | None = None) -> dict[str, Any]:
        self.tools_dir.mkdir(parents=True, exist_ok=True)
        stem = slugify(name, fallback="tool")
        target = self.tools_dir / f"{stem}.scad"
        target.write_text(body, encoding="utf-8")

        icon_path = self.tools_dir / f"{stem}.png"
        if icon_bytes is not None:
            _save_icon(icon_path, icon_bytes)

        return {"name": stem, "body": body, "has_icon": icon_path.exists()}

    def delete_tool(self, name: str) -> None:
        stem = slugify(name, fallback="tool")
        (self.tools_dir / f"{stem}.scad").unlink(missing_ok=True)
        (self.tools_dir / f"{stem}.png").unlink(missing_ok=True)

    def icon_path(self, name: str) -> Path | None:
        stem = slugify(name, fallback="tool")
        path = self.tools_dir / f"{stem}.png"
        return path if path.is_file() else None

    # ------------------------------------------------------- per-project use

    def copy_into_project(self, project_id: str, name: str) -> str:
        """Physically copy a tool's .scad into <project>/models/sources/tools/.

        A real file, not just the worker's in-browser virtual FS injection
        (see ScadWorkspace.tsx) — so a saved source that `use <tools/...>;`s
        it still resolves outside this app. The tool's own entry in
        `_shared/tools/` is untouched; this is a copy, not a move.
        """
        directory = self.library.dir_for_id(project_id)
        if directory is None:
            raise KeyError(project_id)
        stem = slugify(name, fallback="tool")
        source = self.tools_dir / f"{stem}.scad"
        if not source.is_file():
            raise ValueError(f"no such tool: {stem}")

        target_dir = directory / MODEL_SOURCES_DIR / "tools"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{stem}.scad"
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

        self.library.scan_project_dir(directory)
        return target.relative_to(directory).as_posix()

    def remove_from_project(self, project_id: str, name: str) -> None:
        """The other half of copy_into_project. Leaves an empty tools/ dir
        behind if this was the last one — harmless, and nothing else in the
        app proactively cleans up empty directories either."""
        directory = self.library.dir_for_id(project_id)
        if directory is None:
            raise KeyError(project_id)
        stem = slugify(name, fallback="tool")
        (directory / MODEL_SOURCES_DIR / "tools" / f"{stem}.scad").unlink(missing_ok=True)
        self.library.scan_project_dir(directory)


def _save_icon(target: Path, data: bytes) -> None:
    """Validate (readable, square, within MAX_ICON_SIZE) then store as RGBA PNG.

    Written to a temp file first and probed with Image.verify(), the same
    "reject anything Pillow can't open rather than caching a broken file"
    approach ImageService.add_photo uses (services/images.py) — except here
    nothing is kept on disk at all if validation fails, since there is no
    "unlink the bad upload" step needed for a target that was never written.
    """
    import io

    try:
        with Image.open(io.BytesIO(data)) as probe:
            probe.verify()
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
            if width != height:
                raise ValueError(f"icon must be square (got {width}x{height})")
            if width > MAX_ICON_SIZE:
                raise ValueError(f"icon must be {MAX_ICON_SIZE}x{MAX_ICON_SIZE} or smaller")
            target.parent.mkdir(parents=True, exist_ok=True)
            image.convert("RGBA").save(target, "PNG")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"not a readable image: {exc}") from exc
