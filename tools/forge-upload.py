#!/usr/bin/env python3
"""Import files into Forge Ledger over MCP, without paying for base64 in tokens.

`upload_file` takes file bytes base64-encoded. That is fine for a program and
ruinous for an LLM agent calling the tool inline: an 88KB PNG becomes ~118KB of
base64, which cost roughly 112,000 tokens in one real session — and ordinary
meshes and photos here run to 10–26MB.

Run this from a shell instead. The encoding and the multi-megabyte request body
happen in this process; only a short JSON summary comes back. It speaks the same
stateless JSON-RPC endpoint the agent would have called, so nothing is bypassed.

    # one folder, into a project created from its README title
    tools/forge-upload.py --url http://nas:8000/mcp/ --create-from ~/Designs/Filament\\ Guide

    # into an existing project, by id or slug
    tools/forge-upload.py --project filament-guide ~/Designs/**/*.stl

    # see what it would do
    tools/forge-upload.py --project filament-guide --dry-run ~/Designs/Guide/

Requires nothing but the standard library.
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_URL = "http://localhost:8000/mcp/"

MESH = {".stl", ".3mf", ".obj", ".ply"}
IMAGE = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
SKIP_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}

_request_id = 0


class RpcError(RuntimeError):
    pass


def rpc(url: str, method: str, params: dict | None = None, timeout: float = 300.0) -> dict:
    global _request_id
    _request_id += 1
    body = json.dumps(
        {"jsonrpc": "2.0", "id": _request_id, "method": method, "params": params or {}}
    ).encode()
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
    )
    try:
        raw = urllib.request.urlopen(request, timeout=timeout).read().decode()
    except urllib.error.URLError as exc:
        raise RpcError(f"cannot reach {url}: {exc}") from exc

    # The endpoint is stateless, but may still answer in SSE framing.
    if raw.lstrip().startswith(("event:", "data:")):
        data_lines = [ln[len("data:") :].strip() for ln in raw.splitlines() if ln.startswith("data:")]
        raw = data_lines[-1] if data_lines else "{}"

    payload = json.loads(raw)
    if "error" in payload:
        raise RpcError(f"{method}: {payload['error'].get('message', payload['error'])}")
    return payload["result"]


def call_tool(url: str, name: str, **arguments) -> dict:
    result = rpc(url, "tools/call", {"name": name, "arguments": arguments})
    text = ""
    if result.get("content"):
        text = result["content"][0].get("text", "")
    if result.get("isError"):
        raise RpcError(f"{name}: {text}")
    if result.get("structuredContent") is not None:
        return result["structuredContent"]
    return json.loads(text) if text else {}


def collect(paths: list[Path]) -> list[Path]:
    """Expand folders, skipping the noise every real directory accumulates."""
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files += [p for p in sorted(path.rglob("*")) if p.is_file()]
        elif path.is_file():
            files.append(path)
        else:
            print(f"  ! not found: {path}", file=sys.stderr)
    return [
        f
        for f in files
        if f.name not in SKIP_NAMES and not f.name.startswith(".") and f.stat().st_size > 0
    ]


def model_name_for(path: Path, roots: list[Path]) -> str:
    """A mesh in `models/tray/` or `Tray/` belongs to a model called `tray`."""
    if path.suffix.lower() not in MESH:
        return ""
    parent = path.parent.name.lower()
    if parent in {"models", "meshes", "stl", "stls", "export", "exports"}:
        return path.stem
    if any(path.parent == root for root in roots):
        return path.stem
    return path.parent.name


def title_from(folder: Path) -> str:
    """Prefer a README's first heading; fall back to the folder name."""
    for name in ("README.md", "readme.md", "README.txt"):
        readme = folder / name
        if readme.is_file():
            for line in readme.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.strip():
                    return line.lstrip("# ").strip() or folder.name
    return folder.name


def notes_from(folder: Path) -> str:
    for name in ("README.md", "readme.md", "README.txt"):
        readme = folder / name
        if readme.is_file():
            return readme.read_text(encoding="utf-8", errors="replace")
    return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Upload files into Forge Ledger through its MCP endpoint.",
        epilog="Point --url at the live instance (usually :8000), not the demo one (:8001).",
    )
    parser.add_argument("paths", nargs="*", type=Path, help="files or folders to upload")
    parser.add_argument("--url", default=DEFAULT_URL, help=f"MCP endpoint (default {DEFAULT_URL})")
    parser.add_argument("--project", help="existing project id or slug")
    parser.add_argument(
        "--create-from",
        type=Path,
        metavar="FOLDER",
        help="create a project from this folder's README title, then upload it",
    )
    parser.add_argument("--title", help="override the title used by --create-from")
    parser.add_argument("--tags", default="", help="comma-separated, for --create-from")
    parser.add_argument("--license", default="", help="e.g. CC-BY-4.0")
    parser.add_argument("--variant", default="", choices=["", "web", "mobile"])
    parser.add_argument(
        "--no-attach",
        action="store_true",
        help="leave uploaded models unfiled instead of naming them in project.yaml",
    )
    parser.add_argument("--dry-run", action="store_true", help="show the plan, upload nothing")
    args = parser.parse_args(argv)

    if not args.project and not args.create_from:
        parser.error("give --project or --create-from")

    roots = [args.create_from] if args.create_from else []
    sources = list(args.paths) + ([args.create_from] if args.create_from else [])
    files = collect(sources)
    if not files:
        print("Nothing to upload.", file=sys.stderr)
        return 1

    # Warn loudly rather than silently filling a library that vanishes.
    try:
        info = call_tool(args.url, "library_info")
        if info.get("demo_instance"):
            print(f"!! {info.get('warning', 'demo instance')}\n", file=sys.stderr)
    except RpcError as exc:
        print(f"!! {exc}", file=sys.stderr)
        return 2

    project = args.project
    if args.create_from:
        title = args.title or title_from(args.create_from)
        if args.dry_run:
            print(f"would create project: {title}")
            project = project or "<new>"
        else:
            created = call_tool(
                args.url,
                "create_project",
                title=title,
                tags=[t.strip() for t in args.tags.split(",") if t.strip()],
                license=args.license,
                notes=notes_from(args.create_from),
            )
            project = created["id"]
            print(f"created {created['slug']}  ({created['id']})")

    total = 0
    failed = 0
    # rel_paths that landed under models/, grouped by the model they belong to,
    # so the import finishes filed rather than leaving everything "unfiled".
    to_attach: dict[str, list[str]] = {}

    for path in files:
        # The README became the project notes; do not also file it as a document.
        if roots and path.name.lower().startswith("readme") and path.parent in roots:
            continue

        model_name = model_name_for(path, roots)
        variant = args.variant if path.suffix.lower() in IMAGE else ""
        size = path.stat().st_size

        if args.dry_run:
            extra = f" model={model_name}" if model_name else ""
            extra += f" variant={variant}" if variant else ""
            print(f"  would upload {path.name}  ({size / 1024:.0f} KB){extra}")
            continue

        try:
            result = call_tool(
                args.url,
                "upload_file",
                project=project,
                filename=path.name,
                content_base64=base64.b64encode(path.read_bytes()).decode(),
                model_name=model_name,
                variant=variant,
            )
            rel_path = result["rel_path"]
            print(f"  {path.name:<34} -> {rel_path}  ({size / 1024:.0f} KB)")
            total += 1

            if rel_path.startswith("models/"):
                # A CAD source has no model of its own; file it under its stem,
                # which is usually the mesh it was exported to.
                owner = model_name or Path(path.name).stem
                to_attach.setdefault(owner.lower(), []).append(rel_path)
        except RpcError as exc:
            print(f"  ! {path.name}: {exc}", file=sys.stderr)
            failed += 1

    if args.dry_run:
        return 0

    if to_attach and not args.no_attach:
        for model, rel_paths in sorted(to_attach.items()):
            try:
                call_tool(
                    args.url,
                    "attach_files_to_model",
                    project=project,
                    model_name=model,
                    rel_paths=rel_paths,
                )
                print(f"  filed {len(rel_paths)} file(s) under model '{model}'")
            except RpcError as exc:
                print(f"  ! could not file '{model}': {exc}", file=sys.stderr)

    print(f"\nUploaded {total} file(s){f', {failed} failed' if failed else ''}.")
    return 1 if failed else 0


if __name__ == "__main__":
    mimetypes.init()
    raise SystemExit(main())
