"""The library: plain folders on disk, one per project.

`project.yaml` is authoritative for everything the app cannot re-derive — title,
status, tags, licence, attribution, which files belong to which model. The
scanner reads those folders into SQLite; nothing flows the other way except
through an explicit save that rewrites the YAML.
"""

from __future__ import annotations

import json
import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..config import Settings
from ..db import Database
from ..utils import new_ulid, slugify, today, unique_slug, utcnow
from .events import bus

log = logging.getLogger(__name__)

PROJECT_YAML = "project.yaml"
NOTES_FILE = "notes.md"
MODELS_DIR = "models"
MODEL_SOURCES_DIR = "models/sources"
PRINTS_DIR = "prints"
IMAGES_DIR = "images"
PHOTOS_DIR = "images/photos"
IMAGE_SOURCES_DIR = "images/sources"
RENDERS_DIR = "images/renders"
PLATES_DIR = "images/plates"
PUBLISH_DIR = "publish/makerworld"
# Datasheets, manuals, receipts, a spec PDF — anything that belongs with the
# project but is not a model, a print or an image.
DOCS_DIR = "docs"
VERSIONS_DIR = "_versions"

PROJECT_SUBDIRS = (
    MODELS_DIR,
    MODEL_SOURCES_DIR,
    PRINTS_DIR,
    PHOTOS_DIR,
    IMAGE_SOURCES_DIR,
    RENDERS_DIR,
    DOCS_DIR,
    PUBLISH_DIR,
)

# What a published image is aimed at. Uploaded and tagged by hand; the app never
# resamples them, so the crop you exported is the crop that ships.
IMAGE_VARIANTS = ("web", "mobile")

# Library-level folders that are not projects.
RESERVED_DIRS = {"_shared", "_trash", VERSIONS_DIR}

STATUSES = ("idea", "designing", "testing", "ready", "published", "shelved")

# Library sort order: most-done first, so a glance at the grid surfaces what
# shipped. Distinct from STATUSES (the workflow order used for validation) —
# shelved is a dead end rather than a step, so it sorts last, not first.
STATUS_SORT_ORDER = ("published", "ready", "testing", "designing", "idea", "shelved")

CAD_EXTENSIONS = {".step", ".stp", ".f3d", ".scad", ".blend", ".fcstd", ".ipt", ".sldprt"}
MESH_EXTENSIONS = {".stl", ".obj", ".3mf", ".ply"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
# Editable originals behind a rendered image. Never displayed, always downloadable.
IMAGE_SOURCE_EXTENSIONS = {
    ".psd",  # Photoshop
    ".psb",
    ".pxd",  # Pixelmator
    ".pxm",
    ".afphoto",  # Affinity Photo
    ".afdesign",
    ".xcf",  # GIMP
    ".ai",
    ".svg",
    ".sketch",
    ".fig",
}
DOC_EXTENSIONS = {
    ".md",
    ".txt",
    ".pdf",
    ".yaml",
    ".yml",
    ".json",
    ".csv",
    ".rtf",
    ".doc",
    ".docx",
    ".odt",
    ".xls",
    ".xlsx",
    ".ods",
}
# Anything else worth keeping beside a project: firmware, a zip of references,
# a DXF for the laser cutter. Filed as-is, never parsed.
MISC_EXTENSIONS = {".zip", ".7z", ".tar", ".gz", ".dxf", ".svgz", ".bin", ".hex", ".cfg", ".ini"}
SLICED_EXTENSIONS = {".gcode", ".3mf"}


def classify(path: Path) -> str:
    """CAD source / mesh / sliced / image / image source / doc / misc.

    `other` is the deliberate dead end: an extension nobody recognises is never
    accepted by an upload, which is what keeps an executable out of the library.
    """
    name = path.name.lower()
    suffix = path.suffix.lower()

    # A sliced file is a .3mf that says so by name, or raw gcode. Bare .3mf is a mesh.
    if name.endswith(".gcode.3mf") or suffix == ".gcode":
        return "sliced"
    if suffix in IMAGE_SOURCE_EXTENSIONS:
        return "image_source"
    if suffix in CAD_EXTENSIONS:
        return "cad"
    if suffix in MESH_EXTENSIONS:
        return "mesh"
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in DOC_EXTENSIONS:
        return "doc"
    if suffix in MISC_EXTENSIONS:
        return "misc"
    return "other"


def is_sliced(path: Path) -> bool:
    return classify(path) == "sliced" and path.suffix.lower() in SLICED_EXTENSIONS


@dataclass
class RemixSource:
    url: str = ""
    title: str = ""
    author: str = ""
    license: str = ""

    def as_dict(self) -> dict[str, str]:
        return {k: v for k, v in vars(self).items() if v}


@dataclass
class ModelEntry:
    name: str
    files: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "files": self.files}


@dataclass
class ImageEntry:
    """One published image, its target, and the file it was exported from."""

    path: str
    variant: str = ""  # "web" | "mobile" | "" when it serves both
    source: str = ""  # e.g. images/sources/cover-web.psd
    cover: bool = False

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"path": self.path}
        if self.variant:
            payload["variant"] = self.variant
        if self.source:
            payload["source"] = self.source
        if self.cover:
            payload["cover"] = True
        return payload


def _read_images(raw: dict[str, Any]) -> list[ImageEntry]:
    """Read the `images:` list, falling back to the older flat keys."""
    entries: list[ImageEntry] = []
    for item in raw.get("images") or []:
        if isinstance(item, dict) and item.get("path"):
            variant = str(item.get("variant") or "")
            entries.append(
                ImageEntry(
                    path=str(item["path"]),
                    variant=variant if variant in IMAGE_VARIANTS else "",
                    source=str(item.get("source") or ""),
                    cover=bool(item.get("cover")),
                )
            )
        elif isinstance(item, str):
            # Shorthand: a bare list of paths.
            entries.append(ImageEntry(path=item))

    known = {e.path for e in entries}
    for path in raw.get("image_order") or []:
        if str(path) not in known:
            entries.append(ImageEntry(path=str(path)))
            known.add(str(path))

    cover = str(raw.get("cover_image") or "")
    if cover and not any(e.cover for e in entries):
        if cover not in known:
            entries.append(ImageEntry(path=cover))
        for entry in entries:
            entry.cover = entry.path == cover

    return entries


@dataclass
class ProjectDoc:
    """In-memory form of `project.yaml`."""

    id: str = field(default_factory=new_ulid)
    title: str = "Untitled"
    status: str = "idea"
    created: str = field(default_factory=today)
    tags: list[str] = field(default_factory=list)
    license: str = ""
    makerworld_url: str = ""
    remix_of: list[RemixSource] = field(default_factory=list)
    models: list[ModelEntry] = field(default_factory=list)
    # Ordered: position in this list is the upload order for publishing.
    images: list[ImageEntry] = field(default_factory=list)
    publish: dict[str, Any] = field(default_factory=dict)
    # Anything a future version wrote that this one does not understand.
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ProjectDoc:
        known = {
            "id",
            "title",
            "status",
            "created",
            "tags",
            "license",
            "makerworld_url",
            "remix_of",
            "models",
            "images",
            "cover_image",
            "image_order",
            "publish",
        }
        status = str(raw.get("status") or "idea")
        return cls(
            id=str(raw.get("id") or new_ulid()),
            title=str(raw.get("title") or "Untitled"),
            status=status if status in STATUSES else "idea",
            created=str(raw.get("created") or today()),
            tags=[str(t) for t in raw.get("tags") or []],
            license=str(raw.get("license") or ""),
            makerworld_url=str(raw.get("makerworld_url") or ""),
            remix_of=[
                RemixSource(
                    url=str(item.get("url", "")),
                    title=str(item.get("title", "")),
                    author=str(item.get("author", "")),
                    license=str(item.get("license", "")),
                )
                for item in raw.get("remix_of") or []
                if isinstance(item, dict)
            ],
            models=[
                ModelEntry(
                    name=str(item.get("name") or "unnamed"),
                    files=[str(f) for f in item.get("files") or []],
                )
                for item in raw.get("models") or []
                if isinstance(item, dict)
            ],
            images=_read_images(raw),
            publish=dict(raw.get("publish") or {}),
            extra={k: v for k, v in raw.items() if k not in known},
        )

    # `cover_image` and `image_order` predate the per-image list. They are still
    # read (see _read_images) and still exposed here, so the rest of the app and
    # any hand-edited project.yaml keep working.

    @property
    def cover_image(self) -> str:
        return next((i.path for i in self.images if i.cover), "")

    @property
    def image_order(self) -> list[str]:
        return [i.path for i in self.images]

    def image_for(self, rel_path: str) -> ImageEntry | None:
        return next((i for i in self.images if i.path == rel_path), None)

    def upsert_image(self, rel_path: str, **changes: Any) -> ImageEntry:
        entry = self.image_for(rel_path)
        if entry is None:
            entry = ImageEntry(path=rel_path)
            self.images.append(entry)
        for key, value in changes.items():
            if value is not None:
                setattr(entry, key, value)
        return entry

    def set_cover(self, rel_path: str) -> None:
        for entry in self.images:
            entry.cover = entry.path == rel_path
        if rel_path and self.image_for(rel_path) is None:
            self.images.append(ImageEntry(path=rel_path, cover=True))

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "created": self.created,
        }
        if self.tags:
            payload["tags"] = self.tags
        if self.license:
            payload["license"] = self.license
        if self.makerworld_url:
            payload["makerworld_url"] = self.makerworld_url
        if self.remix_of:
            payload["remix_of"] = [r.as_dict() for r in self.remix_of]
        if self.models:
            payload["models"] = [m.as_dict() for m in self.models]
        if self.images:
            payload["images"] = [i.as_dict() for i in self.images]
        if self.publish:
            payload["publish"] = self.publish
        payload.update(self.extra)
        return payload

    @property
    def filed_files(self) -> set[str]:
        return {f for model in self.models for f in model.files}


def _match_source_by_stem(image_rel: str, on_disk: set[str]) -> str:
    """Pair `cover-web.png` with `images/sources/cover-web.psd` automatically.

    Convenience only: an explicit `source:` in project.yaml always wins, and
    dropping both files in together is the common case.
    """
    stem = Path(image_rel).stem.lower()
    for candidate in sorted(on_disk):
        if not candidate.startswith(IMAGE_SOURCES_DIR):
            continue
        if Path(candidate).stem.lower() == stem:
            return candidate
    return ""


def read_project_doc(project_dir: Path) -> ProjectDoc | None:
    target = project_dir / PROJECT_YAML
    if not target.exists():
        return None
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        log.warning("unreadable %s: %s", target, exc)
        return None
    if not isinstance(raw, dict):
        return None
    return ProjectDoc.from_dict(raw)


def write_project_doc(project_dir: Path, doc: ProjectDoc) -> None:
    """Write `project.yaml` atomically, so a crash cannot truncate it."""
    project_dir.mkdir(parents=True, exist_ok=True)
    target = project_dir / PROJECT_YAML
    temporary = target.with_suffix(".yaml.tmp")
    body = yaml.safe_dump(doc.as_dict(), sort_keys=False, allow_unicode=True, width=100)
    temporary.write_text(body, encoding="utf-8")
    temporary.replace(target)


class LibraryService:
    def __init__(self, settings: Settings, db: Database):
        self.settings = settings
        self.db = db
        self.root = settings.library_path
        # Set by AppState to pick up sliced files copied in by hand. Kept as a
        # hook so the library stays unaware of print ingest, which depends on it.
        # Returns True when it wrote files, which triggers one refreshing scan.
        self.after_scan: Callable[[str], bool] | None = None
        # What scan_project_dir last notified about, keyed by project id. The
        # watcher rescans on every filesystem touch — including a NAS indexer
        # or backup tool bumping mtimes with nothing actually changed — so a
        # scan only publishes when the part of the project the UI shows has
        # actually moved, not on every rescan.
        self._fingerprints: dict[str, str] = {}

    # ---------------------------------------------------------------- paths

    def project_dir(self, slug: str) -> Path:
        return self.root / slug

    def project_dirs(self) -> list[Path]:
        if not self.root.exists():
            return []
        return sorted(
            entry
            for entry in self.root.iterdir()
            if entry.is_dir() and not entry.name.startswith(".") and entry.name not in RESERVED_DIRS
        )

    def slug_for_id(self, project_id: str) -> str | None:
        row = self.db.query_one("SELECT slug FROM projects WHERE id = ?", (project_id,))
        return row["slug"] if row else None

    def dir_for_id(self, project_id: str) -> Path | None:
        slug = self.slug_for_id(project_id)
        return self.project_dir(slug) if slug else None

    # -------------------------------------------------------------- scanning

    def scan_all(self, progress: Any = None) -> dict[str, int]:
        """Full rescan. Rebuilds the cache from the folders, dropping stale rows."""
        directories = self.project_dirs()
        seen_ids: list[str] = []

        for index, directory in enumerate(directories):
            try:
                project_id = self.scan_project_dir(directory, notify=False)
                if project_id:
                    seen_ids.append(project_id)
            except Exception:
                log.exception("failed to scan %s", directory)
            if progress is not None:
                progress((index + 1) / max(1, len(directories)), f"scanned {directory.name}")

        removed = self._forget_missing(seen_ids)
        self.db.execute(
            "INSERT INTO meta(key, value) VALUES('last_full_scan', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (utcnow(),),
        )
        bus.publish("library.scanned", {"projects": len(seen_ids), "removed": removed})
        return {"projects": len(seen_ids), "removed": removed}

    def _forget_missing(self, keep_ids: list[str]) -> int:
        rows = self.db.query("SELECT id FROM projects")
        stale = [row["id"] for row in rows if row["id"] not in set(keep_ids)]
        if stale:
            placeholders = ",".join("?" * len(stale))
            self.db.execute(f"DELETE FROM projects WHERE id IN ({placeholders})", tuple(stale))
            for project_id in stale:
                self._fingerprints.pop(project_id, None)
        return len(stale)

    def scan_project(self, slug: str) -> str | None:
        directory = self.project_dir(slug)
        if not directory.is_dir():
            return None
        return self.scan_project_dir(directory)

    def scan_project_dir(
        self, directory: Path, *, notify: bool = True, run_hook: bool = True
    ) -> str | None:
        """Read one project folder into the cache, creating project.yaml if absent."""
        doc = read_project_doc(directory)
        if doc is None:
            # A folder dropped in by hand is a real project; adopt it.
            doc = ProjectDoc(title=directory.name.replace("-", " ").title())
            write_project_doc(directory, doc)
            log.info("adopted untracked folder %s", directory.name)

        slug = directory.name
        notes_path = directory / NOTES_FILE
        notes = notes_path.read_text(encoding="utf-8") if notes_path.exists() else ""

        files = self._walk_files(directory)
        filed = doc.filed_files
        cover = doc.cover_image or self._infer_cover(files)

        with self.db.write() as conn:
            conn.execute(
                """
                INSERT INTO projects(id, slug, path, title, status, created, tags, license,
                                     makerworld_url, remix_of, notes, cover_image, scanned_at)
                VALUES(:id, :slug, :path, :title, :status, :created, :tags, :license,
                       :makerworld_url, :remix_of, :notes, :cover_image, :scanned_at)
                ON CONFLICT(id) DO UPDATE SET
                    slug=excluded.slug, path=excluded.path, title=excluded.title,
                    status=excluded.status, created=excluded.created, tags=excluded.tags,
                    license=excluded.license, makerworld_url=excluded.makerworld_url, remix_of=excluded.remix_of, notes=excluded.notes,
                    cover_image=excluded.cover_image, scanned_at=excluded.scanned_at
                """,
                {
                    "id": doc.id,
                    "slug": slug,
                    "path": str(directory),
                    "title": doc.title,
                    "status": doc.status,
                    "created": doc.created,
                    "tags": json.dumps(doc.tags),
                    "license": doc.license,
                    "makerworld_url": doc.makerworld_url,
                    "remix_of": json.dumps([r.as_dict() for r in doc.remix_of]),
                    "notes": notes,
                    "cover_image": cover,
                    "scanned_at": utcnow(),
                },
            )

            conn.execute("DELETE FROM models WHERE project_id = ?", (doc.id,))
            conn.executemany(
                "INSERT INTO models(project_id, name, files) VALUES(?, ?, ?)",
                [(doc.id, m.name, json.dumps(m.files)) for m in doc.models],
            )

            conn.execute("DELETE FROM files WHERE project_id = ?", (doc.id,))
            conn.executemany(
                "INSERT INTO files(project_id, rel_path, kind, size, mtime, filed) "
                "VALUES(?, ?, ?, ?, ?, ?)",
                [
                    (
                        doc.id,
                        entry["rel_path"],
                        entry["kind"],
                        entry["size"],
                        entry["mtime"],
                        # Only design files can be "unfiled"; prints and images
                        # are located by their folder, not by project.yaml.
                        int(entry["kind"] not in {"cad", "mesh"} or entry["rel_path"] in filed),
                    )
                    for entry in files
                ],
            )

            image_rows = self._image_rows(doc, files)
            conn.execute("DELETE FROM images WHERE project_id = ?", (doc.id,))
            conn.executemany(
                "INSERT INTO images(project_id, rel_path, category, sort_order, variant, "
                "source_path) VALUES(?, ?, ?, ?, ?, ?)",
                image_rows,
            )

        self._sync_versions(doc.id, directory)

        if run_hook and self.after_scan is not None:
            try:
                changed = self.after_scan(doc.id)
            except Exception:
                # A failed ingest must never invalidate an otherwise good scan.
                log.exception("after-scan hook failed for %s", slug)
            else:
                if changed:
                    # Ingest writes sidecars and plate previews. Re-read so they
                    # land in the cache now rather than at the next scan.
                    return self.scan_project_dir(directory, notify=notify, run_hook=False)

        # Always refreshed, even when notify=False (scan_all's boot/nightly
        # pass), so the first watcher-triggered touch after a restart has a
        # real baseline to compare against instead of always looking "new".
        state_changed = self._changed_since_last_scan(doc.id, doc, notes, cover, files, image_rows)
        if notify and state_changed:
            bus.publish("project.updated", {"id": doc.id, "slug": slug})
        return doc.id

    def _changed_since_last_scan(
        self,
        project_id: str,
        doc: ProjectDoc,
        notes: str,
        cover: str,
        files: list[dict[str, Any]],
        image_rows: list[tuple],
    ) -> bool:
        """Whether anything the UI shows moved since the last notified scan.

        The watcher rescans on every filesystem event, including a NAS indexer
        or backup tool touching mtimes with the file itself unchanged. mtime is
        deliberately left out of the fingerprint so that alone cannot trigger a
        client refetch — only size/kind/content changes, which are what a real
        edit produces.
        """
        fingerprint = json.dumps(
            {
                "doc": doc.as_dict(),
                "notes": notes,
                "cover": cover,
                "files": sorted((f["rel_path"], f["kind"], f["size"]) for f in files),
                "images": sorted(image_rows),
            },
            sort_keys=True,
            default=str,
        )
        previous = self._fingerprints.get(project_id)
        self._fingerprints[project_id] = fingerprint
        return previous != fingerprint

    def _walk_files(self, directory: Path) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for path in sorted(directory.rglob("*")):
            if not path.is_file() or path.name.startswith("."):
                continue
            relative = path.relative_to(directory)
            # Snapshots are browsable on disk but are not part of the live tree.
            if relative.parts and relative.parts[0] == VERSIONS_DIR:
                continue
            if path.name == PROJECT_YAML or path.name.endswith(".tmp"):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            entries.append(
                {
                    "rel_path": relative.as_posix(),
                    "kind": classify(path),
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                }
            )
        return entries

    @staticmethod
    def _image_rows(doc: ProjectDoc, files: list[dict[str, Any]]) -> list[tuple]:
        order = {path: index for index, path in enumerate(doc.image_order)}
        on_disk = {entry["rel_path"] for entry in files}
        rows = []
        for entry in files:
            if entry["kind"] != "image":
                continue
            rel = entry["rel_path"]
            if rel.startswith(PHOTOS_DIR):
                category = "photo"
            elif rel.startswith(RENDERS_DIR):
                category = "render"
            elif rel.startswith(PLATES_DIR):
                category = "plate"
            elif rel.startswith(PUBLISH_DIR):
                continue  # export artefacts, not source images
            else:
                category = "other"

            declared = doc.image_for(rel)
            source = declared.source if declared else ""
            # A source named in project.yaml but since deleted should not be
            # advertised as downloadable.
            if source and source not in on_disk:
                source = ""
            if not source:
                source = _match_source_by_stem(rel, on_disk)

            rows.append(
                (
                    doc.id,
                    rel,
                    category,
                    order.get(rel, 10_000),
                    declared.variant if declared else "",
                    source,
                )
            )
        return rows

    @staticmethod
    def _infer_cover(files: list[dict[str, Any]]) -> str:
        """Free tier from §5.4: a plate render is a usable thumbnail on day one."""
        images = [f["rel_path"] for f in files if f["kind"] == "image"]
        for prefix in (PHOTOS_DIR, RENDERS_DIR, PLATES_DIR):
            for rel in images:
                if rel.startswith(prefix):
                    return rel
        return images[0] if images else ""

    def _sync_versions(self, project_id: str, directory: Path) -> None:
        versions_root = directory / VERSIONS_DIR
        rows: list[tuple] = []
        if versions_root.is_dir():
            for folder in sorted(p for p in versions_root.iterdir() if p.is_dir()):
                meta_path = folder / "version.yaml"
                meta: dict[str, Any] = {}
                if meta_path.exists():
                    try:
                        meta = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
                    except yaml.YAMLError:
                        meta = {}
                number = meta.get("number")
                if not isinstance(number, int):
                    head = folder.name.split("__", 1)[0].lstrip("v")
                    number = int(head) if head.isdigit() else 0
                rows.append(
                    (
                        project_id,
                        number,
                        folder.name,
                        str(meta.get("label") or ""),
                        str(meta.get("note") or ""),
                        str(meta.get("created") or ""),
                        int(
                            meta.get("file_count")
                            or sum(1 for _ in folder.rglob("*") if _.is_file())
                        ),
                    )
                )
        with self.db.write() as conn:
            conn.execute("DELETE FROM versions WHERE project_id = ?", (project_id,))
            conn.executemany(
                "INSERT INTO versions(project_id, number, folder, label, note, created, file_count) "  # noqa: E501
                "VALUES(?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

    # -------------------------------------------------------------- mutation

    def create_project(self, title: str, **fields: Any) -> str:
        taken = {d.name for d in self.project_dirs()}
        slug = unique_slug(slugify(title), taken)
        directory = self.project_dir(slug)

        doc = ProjectDoc(title=title.strip() or slug)
        for key in ("status", "license"):
            if fields.get(key):
                setattr(doc, key, fields[key])
        if fields.get("tags"):
            doc.tags = list(fields["tags"])

        for sub in PROJECT_SUBDIRS:
            (directory / sub).mkdir(parents=True, exist_ok=True)
        write_project_doc(directory, doc)
        (directory / NOTES_FILE).write_text(f"# {doc.title}\n", encoding="utf-8")

        self.scan_project_dir(directory)
        bus.publish("project.created", {"id": doc.id, "slug": slug})
        return doc.id

    def update_project(self, project_id: str, changes: dict[str, Any]) -> ProjectDoc:
        """Apply changes to `project.yaml` — the file stays the source of truth."""
        directory = self.dir_for_id(project_id)
        if directory is None or not directory.is_dir():
            raise KeyError(project_id)

        doc = read_project_doc(directory) or ProjectDoc(id=project_id)

        if "title" in changes:
            doc.title = str(changes["title"]).strip() or doc.title
        if "status" in changes and changes["status"] in STATUSES:
            doc.status = changes["status"]
        if "tags" in changes:
            doc.tags = [str(t).strip() for t in changes["tags"] if str(t).strip()]
        if "license" in changes:
            doc.license = str(changes["license"] or "")
        if "makerworld_url" in changes:
            doc.makerworld_url = str(changes["makerworld_url"] or "")
        if "cover_image" in changes:
            doc.set_cover(str(changes["cover_image"] or ""))
        if "image_order" in changes:
            ordered = [str(i) for i in changes["image_order"]]
            known = {i.path for i in doc.images}
            doc.images = [doc.image_for(p) or ImageEntry(path=p) for p in ordered] + [
                i for i in doc.images if i.path not in set(ordered) & known
            ]
        if "image_meta" in changes:
            # {"images/photos/cover.png": {"variant": "web", "source": "..."}}
            for rel_path, meta in (changes["image_meta"] or {}).items():
                variant = meta.get("variant")
                doc.upsert_image(
                    str(rel_path),
                    variant=variant if variant in (*IMAGE_VARIANTS, "") else None,
                    source=meta.get("source"),
                )
        if "remix_of" in changes:
            doc.remix_of = [
                RemixSource(
                    url=str(item.get("url", "")),
                    title=str(item.get("title", "")),
                    author=str(item.get("author", "")),
                    license=str(item.get("license", "")),
                )
                for item in changes["remix_of"]
                if isinstance(item, dict) and item.get("url")
            ]
        if "models" in changes:
            doc.models = [
                ModelEntry(name=str(item["name"]), files=[str(f) for f in item.get("files", [])])
                for item in changes["models"]
                if isinstance(item, dict) and item.get("name")
            ]
        if "publish" in changes and isinstance(changes["publish"], dict):
            doc.publish = {**doc.publish, **changes["publish"]}

        write_project_doc(directory, doc)

        if "notes" in changes:
            (directory / NOTES_FILE).write_text(str(changes["notes"]), encoding="utf-8")

        self.scan_project_dir(directory)
        return doc

    def rename_project(self, project_id: str, new_title: str) -> str:
        """Retitle, and move the folder to match. The ULID keeps history intact."""
        directory = self.dir_for_id(project_id)
        if directory is None or not directory.is_dir():
            raise KeyError(project_id)

        doc = read_project_doc(directory) or ProjectDoc(id=project_id)
        doc.title = new_title.strip() or doc.title

        taken = {d.name for d in self.project_dirs() if d != directory}
        target = self.project_dir(unique_slug(slugify(doc.title), taken))
        write_project_doc(directory, doc)
        if target != directory:
            directory.rename(target)

        self.scan_project_dir(target)
        return target.name

    def delete_project(self, project_id: str, *, keep_files: bool = True) -> str | None:
        """Default is a move to `_trash/` — deleting a folder of work should be deliberate.

        Returns where the folder went, or None when it was actually destroyed.
        """
        directory = self.dir_for_id(project_id)
        if directory is None or not directory.is_dir():
            raise KeyError(project_id)

        trashed_to: str | None = None
        if keep_files:
            trash = self.root / "_trash"
            trash.mkdir(exist_ok=True)
            destination = trash / f"{directory.name}__{utcnow().replace(':', '')}"
            shutil.move(str(directory), str(destination))
            trashed_to = str(destination)
        else:
            shutil.rmtree(directory)

        self.db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        self._fingerprints.pop(project_id, None)
        bus.publish("project.deleted", {"id": project_id})
        return trashed_to

    def attach_files_to_model(self, project_id: str, model_name: str, rel_paths: list[str]) -> None:
        """Move files out of 'unfiled' by naming them in project.yaml."""
        directory = self.dir_for_id(project_id)
        if directory is None:
            raise KeyError(project_id)
        doc = read_project_doc(directory) or ProjectDoc(id=project_id)

        entry = next((m for m in doc.models if m.name == model_name), None)
        if entry is None:
            entry = ModelEntry(name=model_name)
            doc.models.append(entry)
        for rel in rel_paths:
            if rel not in entry.files:
                entry.files.append(rel)

        write_project_doc(directory, doc)
        self.scan_project_dir(directory)

    # -------------------------------------------------------------- querying

    def unfiled(self, project_id: str) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT rel_path, kind, size, mtime FROM files "
            "WHERE project_id = ? AND filed = 0 ORDER BY rel_path",
            (project_id,),
        )
        return [dict(row) for row in rows]

    def last_full_scan(self) -> str | None:
        row = self.db.query_one("SELECT value FROM meta WHERE key = 'last_full_scan'")
        return row["value"] if row else None
