"""Publish workspace: templates, snippets, placeholder filling, export.

The point of this module is to delete the grind — writing "0.2mm layer height,
3 walls, 15% gyroid" by hand for the fortieth time. Print settings come from the
ingested 3MFs, so a template fills itself.

The draft is not hidden in a database row: `publish/makerworld/description.md`
and `fields.yaml` *are* the draft, editable in any text editor.
"""

from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ..config import Settings
from ..db import Database
from ..utils import safe_join, slugify, utcnow
from .events import bus
from .library import PUBLISH_DIR, LibraryService, read_project_doc
from .prints import PrintService
from .threemf import format_duration

log = logging.getLogger(__name__)

SHARED_DIR = "_shared"
TEMPLATES_DIR = f"{SHARED_DIR}/templates"
SNIPPETS_DIR = f"{SHARED_DIR}/snippets"

PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")

DEFAULT_TEMPLATE = """# {{project.title}}

{{project.summary}}

## Print settings

- **Layer height:** {{settings.layer_height}}
- **Walls:** {{settings.wall_count}}
- **Infill:** {{settings.infill_density}} {{settings.infill_pattern}}
- **Supports:** {{settings.supports}}
- **Filament:** {{print.filament}}
- **Print time:** {{print.time}}
- **Filament used:** {{print.weight}}

Printed on a {{print.printer}} with a {{print.nozzle}} nozzle.

## Notes

{{project.notes}}

{{snippet.license}}
"""

DEFAULT_SNIPPETS = {
    "license": "## Licence\n\nThis model is released under {{project.license}}. "
    "Please respect the terms if you remix it.\n",
    "supports": "## Supports\n\nNo supports needed — the overhangs stay under 45°. "
    "If your first layer lifts, raise the bed temperature by 5°C.\n",
    "warping": "## If it warps\n\nClean the plate with dish soap, not IPA alone. "
    "A brim of 5mm fixes almost every warp I have hit with this shape.\n",
    "tipjar": "---\n\nIf this saved you an afternoon, a boost or a tip is always appreciated. "
    "Either way, enjoy the print.\n",
}


@dataclass
class PublishDraft:
    title: str = ""
    description: str = ""
    summary: str = ""
    tags: list[str] | None = None
    category: str = ""
    license: str = ""
    template: str = ""
    print_ids: list[str] | None = None
    assets: list[str] | None = None
    checklist: dict[str, bool] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "summary": self.summary,
            "tags": self.tags or [],
            "category": self.category,
            "license": self.license,
            "template": self.template,
            "print_ids": self.print_ids or [],
            "assets": self.assets or [],
            "checklist": self.checklist or {},
        }


class PublishService:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        library: LibraryService,
        prints: PrintService,
        images: Any = None,
    ):
        self.settings = settings
        self.db = db
        self.library = library
        self.prints = prints
        self.images = images

    # ------------------------------------------------- templates & snippets

    @property
    def templates_dir(self) -> Path:
        return self.library.root / TEMPLATES_DIR

    @property
    def snippets_dir(self) -> Path:
        return self.library.root / SNIPPETS_DIR

    def ensure_defaults(self) -> None:
        """Seed the shared library on first run. Never overwrites your edits."""
        self.templates_dir.mkdir(parents=True, exist_ok=True)
        self.snippets_dir.mkdir(parents=True, exist_ok=True)

        default = self.templates_dir / "default.md"
        if not default.exists():
            default.write_text(DEFAULT_TEMPLATE, encoding="utf-8")
        for name, body in DEFAULT_SNIPPETS.items():
            target = self.snippets_dir / f"{name}.md"
            if not target.exists():
                target.write_text(body, encoding="utf-8")

    def list_templates(self) -> list[dict[str, str]]:
        return _list_markdown(self.templates_dir)

    def list_snippets(self) -> list[dict[str, str]]:
        return _list_markdown(self.snippets_dir)

    def save_template(self, name: str, body: str) -> dict[str, str]:
        return _save_markdown(self.templates_dir, name, body)

    def save_snippet(self, name: str, body: str) -> dict[str, str]:
        return _save_markdown(self.snippets_dir, name, body)

    def delete_template(self, name: str) -> None:
        _delete_markdown(self.templates_dir, name)

    def delete_snippet(self, name: str) -> None:
        _delete_markdown(self.snippets_dir, name)

    # ----------------------------------------------------------- rendering

    def build_context(self, project_id: str, print_ids: list[str] | None = None) -> dict[str, Any]:
        """Everything a template can reference, flattened to dotted keys."""
        project = self.db.query_one("SELECT * FROM projects WHERE id = ?", (project_id,))
        if project is None:
            raise KeyError(project_id)

        selected = [
            p for p in self.prints.list_prints(project_id) if not print_ids or p["id"] in print_ids
        ]
        # Prefer a print that actually succeeded — that is the one worth quoting.
        primary = next(
            (p for p in selected if p["status"] == "done"), selected[0] if selected else None
        )

        doc = read_project_doc(self.library.project_dir(project["slug"]))
        import json as _json

        tags = _json.loads(project["tags"] or "[]")
        notes = project["notes"] or ""
        summary = _first_paragraph(notes)

        settings = primary["settings"] if primary else {}
        filaments = primary["filaments"] if primary else []

        context: dict[str, Any] = {
            "project.title": project["title"],
            "project.status": project["status"],
            "project.created": project["created"] or "",
            "project.tags": ", ".join(tags),
            "project.license": project["license"] or "not specified",
            "project.notes": notes.strip(),
            "project.summary": summary,
            "project.attribution": _attribution_block(doc),
            "print.name": primary["name"] if primary else "",
            "print.filament": ", ".join(sorted({f["type"] for f in filaments if f.get("type")}))
            or "PLA",
            "print.filament_colors": ", ".join(
                sorted({f["color"] for f in filaments if f.get("color")})
            ),
            "print.time": format_duration(primary["estimated_s"]) if primary else "unknown",
            "print.weight": f"{primary['weight_g']:g} g"
            if primary and primary["weight_g"]
            else "unknown",
            "print.printer": (primary["printer"] if primary else "") or "my printer",
            "print.nozzle": f"{primary['nozzle']:g}mm"
            if primary and primary["nozzle"]
            else "0.4mm",
            "print.cost": f"{primary['cost']:.2f}" if primary and primary["cost"] else "",
        }

        for key, value in settings.items():
            context[f"settings.{key}"] = _pretty_setting(key, value)
        context.setdefault("settings.supports", "not required")

        for entry in self.list_snippets():
            context[f"snippet.{entry['name']}"] = entry["body"].strip()

        return context

    def render(self, body: str, context: dict[str, Any], *, depth: int = 0) -> str:
        """Substitute placeholders. Snippets may themselves contain placeholders."""

        def replace(match: re.Match[str]) -> str:
            key = match.group(1)
            if key in context:
                return str(context[key])
            # An unknown placeholder stays visible rather than vanishing silently.
            return f"⟨{key}?⟩"

        rendered = PLACEHOLDER.sub(replace, body)
        if depth < 3 and PLACEHOLDER.search(rendered):
            return self.render(rendered, context, depth=depth + 1)
        return rendered

    def preview(
        self, project_id: str, template_name: str | None, print_ids: list[str] | None
    ) -> str:
        context = self.build_context(project_id, print_ids)
        body = DEFAULT_TEMPLATE
        if template_name:
            target = self.templates_dir / f"{slugify(template_name)}.md"
            if target.exists():
                body = target.read_text(encoding="utf-8")
        return self.render(body, context)

    # -------------------------------------------------------- draft on disk

    def publish_dir(self, project_id: str) -> Path:
        directory = self.library.dir_for_id(project_id)
        if directory is None:
            raise KeyError(project_id)
        return directory / PUBLISH_DIR

    def load_draft(self, project_id: str) -> dict[str, Any]:
        target = self.publish_dir(project_id)
        fields_path = target / "fields.yaml"
        description_path = target / "description.md"

        fields: dict[str, Any] = {}
        if fields_path.exists():
            try:
                fields = yaml.safe_load(fields_path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                log.warning("unreadable %s", fields_path)

        project = self.db.query_one("SELECT * FROM projects WHERE id = ?", (project_id,))
        import json as _json

        return {
            "title": fields.get("title") or (project["title"] if project else ""),
            "summary": fields.get("summary", ""),
            "tags": fields.get("tags") or (_json.loads(project["tags"] or "[]") if project else []),
            "category": fields.get("category", ""),
            "license": fields.get("license") or (project["license"] if project else ""),
            "template": fields.get("template", "default"),
            "print_ids": fields.get("print_ids") or [],
            "assets": fields.get("assets") or [],
            "checklist": fields.get("checklist") or {},
            "attribution": fields.get("attribution") or [],
            "description": description_path.read_text(encoding="utf-8")
            if description_path.exists()
            else "",
            "exported_at": fields.get("exported_at"),
        }

    def save_draft(self, project_id: str, draft: dict[str, Any]) -> dict[str, Any]:
        target = self.publish_dir(project_id)
        target.mkdir(parents=True, exist_ok=True)

        current = self.load_draft(project_id)
        merged = {**current, **{k: v for k, v in draft.items() if v is not None}}

        description = merged.pop("description", "")
        (target / "description.md").write_text(description, encoding="utf-8")

        fields = {
            key: merged.get(key)
            for key in (
                "title",
                "summary",
                "tags",
                "category",
                "license",
                "template",
                "print_ids",
                "assets",
                "checklist",
                "attribution",
            )
        }
        fields["updated_at"] = utcnow()
        if merged.get("exported_at"):
            fields["exported_at"] = merged["exported_at"]
        (target / "fields.yaml").write_text(
            yaml.safe_dump(fields, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )

        # Remembered per project, so the next publish starts from the last one.
        self.library.update_project(
            project_id,
            {
                "publish": {
                    "category": fields.get("category") or "",
                    "template": fields.get("template"),
                }
            },
        )
        bus.publish("publish.saved", {"project_id": project_id})
        return self.load_draft(project_id)

    # -------------------------------------------------------------- export

    def export(self, project_id: str, asset_preset: str = "") -> dict[str, Any]:
        """Write the package: description.md, fields.yaml, and the assets.

        Assets are copied byte-for-byte and filed under the variant they were
        tagged with, because you cut the web and mobile crops yourself. Passing
        a preset opts into resizing, but that is not the default.
        """
        directory = self.library.dir_for_id(project_id)
        if directory is None:
            raise KeyError(project_id)

        draft = self.load_draft(project_id)
        target = self.publish_dir(project_id)
        assets_dir = target / "assets"
        if assets_dir.exists():
            shutil.rmtree(assets_dir)
        assets_dir.mkdir(parents=True, exist_ok=True)

        variants = {
            row["rel_path"]: row["variant"]
            for row in self.db.query(
                "SELECT rel_path, variant FROM images WHERE project_id = ?", (project_id,)
            )
        }

        exported: list[dict[str, str]] = []
        for index, rel_path in enumerate(draft.get("assets") or [], start=1):
            try:
                source = safe_join(directory, rel_path)
            except ValueError:
                continue
            if not source.is_file():
                continue

            variant = variants.get(rel_path, "")
            folder = assets_dir / variant if variant else assets_dir
            folder.mkdir(parents=True, exist_ok=True)

            # Upload order is encoded in the filename, so it survives the copy.
            stem = f"{index:02d}_{slugify(Path(rel_path).stem)}"
            if asset_preset and self.images is not None:
                destination = folder / f"{stem}.png"
                self.images.export_resized(source, destination, asset_preset)
            else:
                destination = folder / f"{stem}{source.suffix.lower()}"
                shutil.copy2(source, destination)

            exported.append(
                {
                    "file": destination.relative_to(assets_dir).as_posix(),
                    "variant": variant,
                    "from": rel_path,
                }
            )

        doc = read_project_doc(directory)
        fields_out = {
            "title": draft["title"],
            "summary": draft["summary"],
            "category": draft["category"],
            "tags": draft["tags"],
            "license": draft["license"],
            "attribution": [r.as_dict() for r in doc.remix_of] if doc else [],
            "print_profiles": self.profile_table(project_id, draft.get("print_ids") or []),
            "assets": exported,
            "exported_at": utcnow(),
        }
        (target / "fields.yaml").write_text(
            yaml.safe_dump(fields_out, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        (target / "description.md").write_text(draft["description"], encoding="utf-8")

        self.library.scan_project_dir(directory)
        bus.publish("publish.exported", {"project_id": project_id, "assets": len(exported)})
        return {
            "path": target.relative_to(self.library.root).as_posix(),
            "assets": exported,
            "fields": fields_out,
        }

    def profile_table(self, project_id: str, print_ids: list[str]) -> list[dict[str, Any]]:
        """The tick-to-include table from §5.5, resolved to plain values."""
        rows = []
        for record in self.prints.list_prints(project_id):
            if print_ids and record["id"] not in print_ids:
                continue
            settings = record["settings"]
            rows.append(
                {
                    "name": record["name"],
                    "printer": record["printer"],
                    "nozzle": record["nozzle"],
                    "layer_height": settings.get("layer_height"),
                    "walls": settings.get("wall_count"),
                    "infill": settings.get("infill_density"),
                    "infill_pattern": settings.get("infill_pattern"),
                    "filament": ", ".join(
                        sorted({f["type"] for f in record["filaments"] if f.get("type")})
                    ),
                    "time": format_duration(record["estimated_s"]),
                    "weight_g": record["weight_g"],
                }
            )
        return rows

    def recent_values(self) -> dict[str, list[str]]:
        """Recently-used tags, categories and licences, for the pickers."""
        import json as _json

        tags: list[str] = []
        for row in self.db.query("SELECT tags FROM projects ORDER BY scanned_at DESC LIMIT 50"):
            for tag in _json.loads(row["tags"] or "[]"):
                if tag not in tags:
                    tags.append(tag)
        licenses = [
            row["license"]
            for row in self.db.query(
                "SELECT DISTINCT license FROM projects WHERE license != '' ORDER BY license"
            )
        ]
        return {"tags": tags[:40], "licenses": licenses}


# ------------------------------------------------------------------ helpers


def _list_markdown(directory: Path) -> list[dict[str, str]]:
    if not directory.is_dir():
        return []
    return [
        {"name": path.stem, "body": path.read_text(encoding="utf-8")}
        for path in sorted(directory.glob("*.md"))
    ]


def _save_markdown(directory: Path, name: str, body: str) -> dict[str, str]:
    directory.mkdir(parents=True, exist_ok=True)
    stem = slugify(name, fallback="untitled")
    target = directory / f"{stem}.md"
    target.write_text(body, encoding="utf-8")
    return {"name": stem, "body": body}


def _delete_markdown(directory: Path, name: str) -> None:
    (directory / f"{slugify(name)}.md").unlink(missing_ok=True)


def _first_paragraph(text: str) -> str:
    for block in text.split("\n\n"):
        cleaned = "\n".join(
            line for line in block.splitlines() if not line.lstrip().startswith("#")
        ).strip()
        if cleaned:
            return cleaned
    return ""


def _attribution_block(doc: Any) -> str:
    if doc is None or not doc.remix_of:
        return ""
    lines = ["Based on:"]
    for source in doc.remix_of:
        label = source.title or source.url
        author = f" by {source.author}" if source.author else ""
        licence = f" ({source.license})" if source.license else ""
        lines.append(f"- [{label}]({source.url}){author}{licence}")
    return "\n".join(lines)


def _pretty_setting(key: str, value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    text = str(value)
    if key == "layer_height" and text and not text.endswith("mm"):
        return f"{text}mm"
    if key in {"supports_enabled"}:
        return "required" if text in {"1", "true", "True"} else "not required"
    return text
