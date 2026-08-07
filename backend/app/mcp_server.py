"""MCP server, so an agent can read the library and file projects into it.

The shape this is built for: your project folders live on your own machine, the
app runs in a container on the NAS. The agent reads those folders locally — it
already has filesystem access — and pushes what it finds here. So the transport
is HTTP rather than stdio, and files arrive base64-encoded through `upload_file`
rather than by a path the server could never resolve.

Deliberately no delete tool. Everything here creates or updates; removing a
project stays a decision you make in the UI, where the folder-to-trash rule and
a confirmation apply.
"""

from __future__ import annotations

import base64
import binascii
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field

from .services.library import IMAGE_VARIANTS, STATUSES, classify

if TYPE_CHECKING:
    from .state import AppState

log = logging.getLogger(__name__)

# Base64 inflates by ~33%, and the whole body has to fit in memory on both ends.
# Meshes above this should be copied into the library folder directly.
MAX_UPLOAD_BYTES = 32 * 1024 * 1024

INSTRUCTIONS = """Forge Ledger manages 3D-printing projects: models, print jobs,
photos and MakerWorld publishing drafts.

**Getting files in — read this before uploading anything.**
`upload_file()` takes base64 inline, which is cheap for a program and ruinous for
you: an 88KB image is ~118KB of base64 and cost ~112,000 tokens in a real
session, and ordinary meshes here run to 10-26MB. Do not inline anything larger
than a few KB. Pick one of these instead:

- **You share a filesystem with the server** — check whether the `folder` path
  returned by `create_project()` exists on your machine. If it does, copy files
  into it with ordinary shell commands and call `rescan_library()`. Cheapest by
  far, and sliced 3MFs are ingested automatically.
- **You do not** — run `tools/forge-upload.py` from your shell. It calls this
  same endpoint, so nothing is bypassed, but the base64 stays in that process:
  `tools/forge-upload.py --url <this-url> --create-from "<folder>"`, or
  `--project <id-or-slug> <files...>`. Add `--dry-run` to see the plan first.

Typical task — importing a folder from the user's computer:
1. `list_projects()` to check it is not already there, and `library_info()` to
   confirm you are pointed at the user's real library and not a demo instance.
2. `create_project()` with a title, tags and a licence.
3. Get the files in by one of the two routes above. Either way they are filed by
   extension:
   - `.step` `.scad` `.f3d` -> models/sources/   (CAD originals)
   - `.stl` `.3mf` `.obj`   -> models/<model>/   (meshes; pass `model_name`)
   - `.gcode.3mf` `.gcode`  -> prints/           (parsed into a print job)
   - images                 -> images/photos/    (pass `variant`: web or mobile)
   - `.psd` `.pxd` etc.     -> images/sources/   (editable originals)
   - `.pdf` `.csv` `.zip`   -> docs/             (datasheets, manuals, anything else)
4. `attach_files_to_model()` so CAD sources are filed rather than left "unfiled".
5. `update_project(notes=...)` with anything useful you learned from the folder —
   a README, parameters, what the thing is for.

Always read the folder before inventing metadata. Titles, tags and notes should
reflect what is actually in the files. Do not guess print settings: they are
extracted from sliced 3MFs automatically."""


def _project_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "slug": row["slug"],
        "title": row["title"],
        "status": row["status"],
        "tags": row.get("tags", []),
        "license": row.get("license") or "",
        "models": row.get("model_count", 0),
        "prints": row.get("print_count", 0),
        "unfiled": row.get("unfiled_count", 0),
    }


DEMO_BANNER = """
!! THIS IS THE THROWAWAY DEMO INSTANCE (FORGE_DEMO_SEED is on). Its library is
seeded with sample projects and is thrown away when the container stops, so
nothing imported here survives. The projects you see are samples, not the
user's. Say so and confirm the target before importing anything real — the live
instance is normally on port 8000, this one on 8001.
"""


def build_mcp_server(get_state, *, demo: bool = False) -> MCPServer:
    """Wire the tools. `get_state` defers AppState until the app has started."""
    server = MCPServer(
        name="forge-ledger" + ("-demo" if demo else ""),
        title="Forge Ledger" + (" (demo)" if demo else ""),
        version="0.1.0",
        # Said at the handshake, which is the earliest an agent can hear it.
        instructions=(DEMO_BANNER + "\n" + INSTRUCTIONS) if demo else INSTRUCTIONS,
    )

    def state() -> AppState:
        current = get_state()
        if current is None:  # pragma: no cover — only before startup completes
            raise RuntimeError("Forge Ledger is still starting up")
        return current

    def resolve(project: str) -> dict[str, Any]:
        """Accept an id or a slug, because an agent will use whichever it has."""
        app = state()
        row = app.db.query_one(
            "SELECT * FROM projects WHERE id = ? OR slug = ?", (project, project)
        )
        if row is None:
            raise ValueError(f"No project {project!r}. Use list_projects to see what exists.")
        return dict(row)

    # ----------------------------------------------------------------- read

    @server.tool(
        description="List projects in the library, optionally filtered. "
        "Start here to see what already exists before creating anything."
    )
    def list_projects(
        query: Annotated[str, Field(description="Match title or notes")] = "",
        status: Annotated[str, Field(description=f"One of: {', '.join(STATUSES)}")] = "",
        tag: str = "",
    ) -> dict[str, Any]:
        from .api.projects import list_projects as rest_list

        rows = rest_list(state(), q=query, status=status, tag=tag, sort="title")
        return {"count": len(rows), "projects": [_project_summary(r) for r in rows]}

    @server.tool(
        description="Everything about one project: models, files, images, "
        "versions, print jobs and which files are still unfiled."
    )
    def get_project(
        project: Annotated[str, Field(description="Project id or slug")],
    ) -> dict[str, Any]:
        from .api.projects import get_project as rest_get

        row = resolve(project)
        detail = rest_get(state(), row["id"])
        return {
            **_project_summary(detail),
            "created": detail.get("created"),
            "notes": detail.get("notes", ""),
            "remix_of": detail.get("remix_of", []),
            "models": detail.get("models", []),
            "files": [
                {"path": f["rel_path"], "kind": f["kind"], "size": f["size"]}
                for f in detail.get("files", [])
            ],
            "unfiled": [f["rel_path"] for f in detail.get("unfiled", [])],
            "images": [
                {
                    "path": i["rel_path"],
                    "variant": i["variant"],
                    "source": i["source_path"],
                    "cover": i["is_cover"],
                }
                for i in detail.get("images", [])
            ],
            "prints": [
                {
                    "id": p["id"],
                    "name": p["name"],
                    "status": p["status"],
                    "estimated_s": p["estimated_s"],
                    "weight_g": p["weight_g"],
                    "settings": p["settings"],
                    "failure_reason": p["failure_reason"],
                    "failure_fix": p["failure_fix"],
                }
                for p in detail.get("prints", [])
            ],
            "versions": detail.get("versions", []),
        }

    @server.tool(
        description="Read a text file from inside a project, such as notes.md or "
        "project.yaml. Refuses binary files — use the app to view those."
    )
    def read_project_file(
        project: Annotated[str, Field(description="Project id or slug")],
        rel_path: Annotated[str, Field(description="Path relative to the project folder")],
    ) -> dict[str, Any]:
        from .utils import safe_join

        row = resolve(project)
        directory = state().library.project_dir(row["slug"])
        try:
            target = safe_join(directory, rel_path)
        except ValueError as exc:
            raise ValueError(f"path escapes the project folder: {rel_path}") from exc
        if not target.is_file():
            raise ValueError(f"no such file: {rel_path}")
        try:
            return {"rel_path": rel_path, "content": target.read_text(encoding="utf-8")}
        except UnicodeDecodeError as exc:
            raise ValueError(f"{rel_path} is not a text file") from exc

    @server.tool(
        description="Print statistics and the failure log — what went wrong on "
        "past prints and what fixed it. Useful before advising on settings."
    )
    def print_history(limit: int = 20) -> dict[str, Any]:
        app = state()
        return {
            "stats": app.prints.stats(),
            "failures": [
                {
                    "project": f["project_title"],
                    "name": f["name"],
                    "reason": f["failure_reason"],
                    "fix": f["failure_fix"],
                    "settings": f["settings"],
                }
                for f in app.prints.failure_log(limit)
            ],
        }

    # ---------------------------------------------------------------- write

    @server.tool(
        description="Create a project. Returns its id and slug, which you then "
        "pass to upload_file. Does not overwrite an existing project."
    )
    def create_project(
        title: str,
        tags: list[str] | None = None,
        license: Annotated[str, Field(description="e.g. CC-BY-4.0")] = "",
        status: Annotated[str, Field(description=f"One of: {', '.join(STATUSES)}")] = "idea",
        notes: Annotated[str, Field(description="Markdown for notes.md")] = "",
    ) -> dict[str, Any]:
        app = state()
        if not title.strip():
            raise ValueError("title is required")
        if status not in STATUSES:
            raise ValueError(f"status must be one of {', '.join(STATUSES)}")

        project_id = app.library.create_project(
            title, status=status, tags=tags or [], license=license
        )
        if notes.strip():
            app.library.update_project(project_id, {"notes": notes})

        row = resolve(project_id)
        return {
            "id": row["id"],
            "slug": row["slug"],
            "folder": str(app.library.project_dir(row["slug"])),
        }

    @server.tool(
        description="Update a project's metadata. Only the fields you pass change. "
        "Renaming the title also moves the folder; the id stays the same."
    )
    def update_project(
        project: Annotated[str, Field(description="Project id or slug")],
        title: str | None = None,
        status: str | None = None,
        tags: list[str] | None = None,
        license: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        app = state()
        row = resolve(project)
        changes: dict[str, Any] = {}
        for key, value in (
            ("status", status),
            ("tags", tags),
            ("license", license),
            ("notes", notes),
        ):
            if value is not None:
                changes[key] = value
        if status is not None and status not in STATUSES:
            raise ValueError(f"status must be one of {', '.join(STATUSES)}")

        if title:
            app.library.rename_project(row["id"], title)
        if changes:
            app.library.update_project(row["id"], changes)
        return _project_summary(resolve(row["id"]))

    @server.tool(
        description="Upload one file into a project, base64-encoded. Filed by "
        "extension: CAD to models/sources/, meshes to models/<model_name>/, "
        "sliced 3MFs become print jobs, images to images/photos/, editable "
        "originals to images/sources/, PDFs and attachments to docs/. "
        "PREFER NOT TO CALL THIS DIRECTLY: base64 inline costs enormous numbers "
        "of tokens (~112k for one 88KB image). Copy files in and call "
        "rescan_library() if you share a filesystem with the server, otherwise "
        "run tools/forge-upload.py from your shell — it calls this same tool "
        "without the base64 passing through you. Hard ceiling 32MB per file."
    )
    def upload_file(
        project: Annotated[str, Field(description="Project id or slug")],
        filename: Annotated[str, Field(description="Original name, extension matters")],
        content_base64: Annotated[str, Field(description="File bytes, base64-encoded")],
        model_name: Annotated[str, Field(description="For meshes: which model folder to use")] = "",
        variant: Annotated[
            str, Field(description=f"For images: {' or '.join(IMAGE_VARIANTS)}")
        ] = "",
    ) -> dict[str, Any]:
        app = state()
        row = resolve(project)
        directory = app.library.project_dir(row["slug"])

        try:
            payload = base64.b64decode(content_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError(f"content_base64 is not valid base64: {exc}") from exc
        if not payload:
            raise ValueError("the file is empty")
        if len(payload) > MAX_UPLOAD_BYTES:
            raise ValueError(
                f"{filename} is {len(payload) // 1_048_576}MB, over the "
                f"{MAX_UPLOAD_BYTES // 1_048_576}MB limit. Copy it into "
                f"{directory} directly and run rescan_library() instead, or use "
                f"tools/forge-upload.py if you cannot see that path."
            )

        name = Path(filename).name  # never let a path traverse out
        kind = classify(Path(name))

        if kind == "mesh":
            rel = _store_mesh(app, directory, name, payload, model_name)
            result = {"kind": "mesh", "rel_path": rel}
        elif kind == "sliced":
            from .api.media import _ingest_sliced

            result = _ingest_sliced(app, row["id"], directory, name, payload)
        else:
            result = app.images.route_upload(row["id"], name, payload, variant=variant)

        app.library.scan_project_dir(directory)
        return {"filed_as": result.get("kind", kind), **result}

    @server.tool(
        description="Name files as belonging to a model in project.yaml, which "
        "clears them from the unfiled list. Creates the model if it is new."
    )
    def attach_files_to_model(
        project: Annotated[str, Field(description="Project id or slug")],
        model_name: str,
        rel_paths: Annotated[list[str], Field(description="Paths inside the project")],
    ) -> dict[str, Any]:
        app = state()
        row = resolve(project)
        app.library.attach_files_to_model(row["id"], model_name, rel_paths)
        return {
            "model": model_name,
            "unfiled_remaining": [f["rel_path"] for f in app.library.unfiled(row["id"])],
        }

    @server.tool(
        description="Re-read the library folders. Use after copying files in by "
        "hand, or if something looks out of date."
    )
    def rescan_library() -> dict[str, Any]:
        return state().library.scan_all()

    @server.tool(
        description="Where the library lives and how much is in it. Call this "
        "first if you need to know whether a path is reachable by the server."
    )
    def library_info() -> dict[str, Any]:
        app = state()
        info: dict[str, Any] = {
            "library_path": str(app.settings.library_path),
            "counts": {
                table: app.db.count(table) for table in ("projects", "prints", "versions", "images")
            },
            "statuses": list(STATUSES),
            "image_variants": list(IMAGE_VARIANTS),
            "max_upload_bytes": MAX_UPLOAD_BYTES,
            "note": "library_path is on the server. Check whether the `folder` returned "
            "by create_project() exists on your own machine: if it does you share a "
            "filesystem and can copy files in directly, then call rescan_library(). "
            "If it does not, use tools/forge-upload.py rather than inlining base64.",
        }
        if app.settings.demo_seed:
            # The single most useful thing to know before importing anything.
            info["demo_instance"] = True
            info["warning"] = (
                "THROWAWAY DEMO INSTANCE. Its library is seeded with sample projects "
                "and is discarded when the container stops — nothing imported here "
                "survives. Confirm with the user that this is the intended target "
                "before uploading anything real. The live instance is usually on "
                "port 8000; this demo one on 8001."
            )
        return info

    return server


def _store_mesh(app: AppState, directory: Path, name: str, payload: bytes, model_name: str) -> str:
    """Meshes live under models/<model>/, defaulting to the file's own stem."""
    from .utils import slugify

    folder = slugify(model_name or Path(name).stem, fallback="model")
    target_dir = directory / "models" / folder
    target_dir.mkdir(parents=True, exist_ok=True)

    stem, suffix = Path(name).stem, Path(name).suffix.lower()
    target = target_dir / f"{slugify(stem, fallback='part')}{suffix}"
    counter = 2
    while target.exists():
        target = target_dir / f"{slugify(stem, fallback='part')}-{counter}{suffix}"
        counter += 1

    target.write_bytes(payload)
    return target.relative_to(directory).as_posix()


def mcp_asgi_app(server: MCPServer, *, path: str = "/"):
    """The Starlette app to mount, configured for LAN use rather than loopback."""
    return server.streamable_http_app(
        streamable_http_path=path,
        # Every tool here is request/response, so sessions buy nothing and
        # stateless survives restarts and proxies without resumption logic.
        stateless_http=True,
        json_response=True,
        # Base64 inflates the body; the default 4MB would reject ordinary meshes.
        max_request_body_size=MAX_UPLOAD_BYTES * 2,
        # The app is reached by LAN IP or hostname, not just localhost, so the
        # default loopback-only Host allowlist would reject every real request.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
