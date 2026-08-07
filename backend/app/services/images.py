"""Images: thumbnails, photo management, and CPU turntable renders.

Tiering, per §5.4 and the CPU-cost risk:

1. Plate PNGs pulled from ingested 3MFs — free, instant, covers most of the need.
2. Photos you drop into `images/photos/`, ordered, one marked as cover.
3. Turntable renders — queued background work, and entirely optional. When
   trimesh/pyrender are not installed the rest of the app is unaffected.
"""

from __future__ import annotations

import logging
import math
import shutil
from pathlib import Path
from typing import Any

from PIL import Image

from ..config import Settings
from ..db import Database
from ..utils import safe_join, slugify
from .events import bus
from .library import (
    CAD_EXTENSIONS,
    DOCS_DIR,
    IMAGE_EXTENSIONS,
    IMAGE_SOURCE_EXTENSIONS,
    IMAGE_SOURCES_DIR,
    IMAGE_VARIANTS,
    MODEL_SOURCES_DIR,
    PHOTOS_DIR,
    RENDERS_DIR,
    LibraryService,
    classify,
    read_project_doc,
    write_project_doc,
)

log = logging.getLogger(__name__)

THUMBNAIL_SIZE = (400, 400)

# MakerWorld-friendly export presets. No watermarking, by decision.
EXPORT_PRESETS: dict[str, tuple[int, int]] = {
    "cover": (1200, 1200),
    "gallery": (1600, 1200),
    "wide": (1920, 1080),
}


def render_backend_available() -> bool:
    """True when turntable rendering can actually run on this machine."""
    try:
        import numpy  # noqa: F401
        import trimesh  # noqa: F401
    except ImportError:
        return False
    return True


class ImageService:
    def __init__(self, settings: Settings, db: Database, library: LibraryService):
        self.settings = settings
        self.db = db
        self.library = library

    # ------------------------------------------------------------- listing

    def list_images(self, project_id: str) -> list[dict[str, Any]]:
        row = self.db.query_one("SELECT cover_image FROM projects WHERE id = ?", (project_id,))
        cover = row["cover_image"] if row else ""
        rows = self.db.query(
            "SELECT rel_path, category, sort_order, variant, source_path FROM images "
            "WHERE project_id = ? ORDER BY sort_order, rel_path",
            (project_id,),
        )
        return [dict(r) | {"is_cover": r["rel_path"] == cover} for r in rows]

    def list_sources(self, project_id: str) -> dict[str, list[dict[str, Any]]]:
        """The editable originals: image sources and CAD sources."""
        rows = self.db.query(
            "SELECT rel_path, kind, size, mtime, filed FROM files "
            "WHERE project_id = ? AND (kind = 'image_source' OR rel_path LIKE ?) "
            "ORDER BY rel_path",
            (project_id, f"{MODEL_SOURCES_DIR}/%"),
        )
        images, models = [], []
        for row in rows:
            entry = dict(row)
            if entry["kind"] == "image_source":
                images.append(entry)
            else:
                models.append(entry)
        return {"images": images, "models": models}

    # ------------------------------------------------------------- mutation

    def _unique_target(self, folder: Path, filename: str, fallback: str) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        suffix = Path(filename).suffix.lower()
        stem = slugify(Path(filename).stem, fallback=fallback)
        target = folder / f"{stem}{suffix}"
        counter = 2
        while target.exists():
            target = folder / f"{stem}-{counter}{suffix}"
            counter += 1
        return target

    def add_photo(self, project_id: str, filename: str, data: bytes, variant: str = "") -> str:
        directory = self.library.dir_for_id(project_id)
        if directory is None:
            raise KeyError(project_id)

        suffix = Path(filename).suffix.lower() or ".png"
        if suffix not in IMAGE_EXTENSIONS:
            raise ValueError(f"unsupported image type: {suffix}")
        if variant and variant not in IMAGE_VARIANTS:
            raise ValueError(f"variant must be one of {', '.join(IMAGE_VARIANTS)}")

        target = self._unique_target(directory / PHOTOS_DIR, filename, "photo")
        target.write_bytes(data)
        # Reject anything Pillow cannot open, rather than caching a broken file.
        try:
            with Image.open(target) as probe:
                probe.verify()
        except Exception as exc:
            target.unlink(missing_ok=True)
            raise ValueError(f"not a readable image: {exc}") from exc

        rel = target.relative_to(directory).as_posix()
        if variant:
            self.library.update_project(project_id, {"image_meta": {rel: {"variant": variant}}})
        else:
            self.library.scan_project_dir(directory)
        bus.publish("image.added", {"project_id": project_id, "rel_path": rel})
        return rel

    def add_image_source(
        self, project_id: str, filename: str, data: bytes, for_image: str = ""
    ) -> str:
        """Store an editable original (.psd, .pxd, …) and link it to an image."""
        directory = self.library.dir_for_id(project_id)
        if directory is None:
            raise KeyError(project_id)

        suffix = Path(filename).suffix.lower()
        if suffix not in IMAGE_SOURCE_EXTENSIONS:
            raise ValueError(f"not an image source file: {suffix}")

        # Keeping the stem lets the scanner pair it with its export automatically.
        target = self._unique_target(directory / IMAGE_SOURCES_DIR, filename, "source")
        target.write_bytes(data)
        rel = target.relative_to(directory).as_posix()

        if for_image:
            self.library.update_project(project_id, {"image_meta": {for_image: {"source": rel}}})
        else:
            self.library.scan_project_dir(directory)
        bus.publish("image.source_added", {"project_id": project_id, "rel_path": rel})
        return rel

    def add_model_source(self, project_id: str, filename: str, data: bytes) -> str:
        """Store a CAD source (.step, .scad, …) in the shared model sources folder."""
        directory = self.library.dir_for_id(project_id)
        if directory is None:
            raise KeyError(project_id)

        suffix = Path(filename).suffix.lower()
        if suffix not in CAD_EXTENSIONS:
            raise ValueError(f"not a CAD source file: {suffix}")

        target = self._unique_target(directory / MODEL_SOURCES_DIR, filename, "model")
        target.write_bytes(data)
        rel = target.relative_to(directory).as_posix()

        self.library.scan_project_dir(directory)
        bus.publish("model.source_added", {"project_id": project_id, "rel_path": rel})
        return rel

    def write_model_source(self, project_id: str, rel_path: str, text: str) -> None:
        """Overwrite an existing `.scad` source in place — the in-app editor's Save.

        Unlike add_model_source, this never renames or dedups: it edits the file
        that's already there instead of adding a new one. Scoped to `.scad`
        specifically — the other CAD_EXTENSIONS are binary formats with no
        business being pushed through a raw-text overwrite endpoint.
        """
        directory = self.library.dir_for_id(project_id)
        if directory is None:
            raise KeyError(project_id)

        if Path(rel_path).suffix.lower() != ".scad":
            raise ValueError("only .scad sources can be edited in place")
        target = safe_join(directory, rel_path)
        if not target.is_relative_to(directory / MODEL_SOURCES_DIR):
            raise ValueError("not a model source path")
        if not target.is_file():
            raise ValueError("file not found")

        target.write_text(text, encoding="utf-8")
        self.library.scan_project_dir(directory)
        bus.publish("model.source_added", {"project_id": project_id, "rel_path": rel_path})

    def export_model_stl(self, project_id: str, rel_path: str, data: bytes) -> str:
        """Export the editor's compiled STL next to its `.scad` source.

        Same name, `.stl` extension — a re-export always overwrites the
        previous one, the same "the file is the record" rule as Save.
        """
        directory = self.library.dir_for_id(project_id)
        if directory is None:
            raise KeyError(project_id)

        if Path(rel_path).suffix.lower() != ".scad":
            raise ValueError("only .scad sources can be exported")
        source = safe_join(directory, rel_path)
        if not source.is_relative_to(directory / MODEL_SOURCES_DIR):
            raise ValueError("not a model source path")
        if not source.is_file():
            raise ValueError("source file not found")

        target = source.with_suffix(".stl")
        target.write_bytes(data)
        rel = target.relative_to(directory).as_posix()

        self.library.scan_project_dir(directory)
        bus.publish("model.exported", {"project_id": project_id, "rel_path": rel})
        return rel

    def delete_model_source(self, project_id: str, rel_path: str) -> None:
        """Delete a file from models/sources/ — any kind, not just `.scad`."""
        directory = self.library.dir_for_id(project_id)
        if directory is None:
            raise KeyError(project_id)

        target = safe_join(directory, rel_path)
        if not target.is_relative_to(directory / MODEL_SOURCES_DIR):
            raise ValueError("not a model source path")

        target.unlink(missing_ok=True)
        self.library.scan_project_dir(directory)
        bus.publish("model.source_deleted", {"project_id": project_id, "rel_path": rel_path})

    def add_document(self, project_id: str, filename: str, data: bytes) -> str:
        """Store a PDF, datasheet or other attachment in docs/, untouched."""
        directory = self.library.dir_for_id(project_id)
        if directory is None:
            raise KeyError(project_id)

        kind = classify(Path(filename))
        if kind not in {"doc", "misc"}:
            raise ValueError(f"not a document or attachment: {Path(filename).suffix}")

        target = self._unique_target(directory / DOCS_DIR, filename, "document")
        target.write_bytes(data)
        rel = target.relative_to(directory).as_posix()

        self.library.scan_project_dir(directory)
        bus.publish("document.added", {"project_id": project_id, "rel_path": rel})
        return rel

    def list_documents(self, project_id: str) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT rel_path, kind, size, mtime FROM files "
            "WHERE project_id = ? AND rel_path LIKE ? ORDER BY rel_path",
            (project_id, f"{DOCS_DIR}/%"),
        )
        return [dict(row) for row in rows]

    def delete_document(self, project_id: str, rel_path: str) -> None:
        directory = self.library.dir_for_id(project_id)
        if directory is None:
            raise KeyError(project_id)
        if not rel_path.startswith(f"{DOCS_DIR}/"):
            raise ValueError("that is not a document")
        safe_join(directory, rel_path).unlink(missing_ok=True)
        self.library.scan_project_dir(directory)

    def set_variant(self, project_id: str, rel_path: str, variant: str) -> None:
        if variant and variant not in IMAGE_VARIANTS:
            raise ValueError(f"variant must be one of {', '.join(IMAGE_VARIANTS)}")
        self.library.update_project(project_id, {"image_meta": {rel_path: {"variant": variant}}})

    def link_source(self, project_id: str, rel_path: str, source_path: str) -> None:
        self.library.update_project(project_id, {"image_meta": {rel_path: {"source": source_path}}})

    def set_cover(self, project_id: str, rel_path: str) -> None:
        self.library.update_project(project_id, {"cover_image": rel_path})

    def reorder(self, project_id: str, rel_paths: list[str]) -> None:
        self.library.update_project(project_id, {"image_order": rel_paths})

    def delete_image(self, project_id: str, rel_path: str, *, with_source: bool = False) -> None:
        directory = self.library.dir_for_id(project_id)
        if directory is None:
            raise KeyError(project_id)

        doc = read_project_doc(directory)
        entry = doc.image_for(rel_path) if doc else None

        # Deleting the export can take its editable original with it, but only
        # when asked — losing a Photoshop file to a stray click would be cruel.
        if with_source:
            # The resolved path, so a source paired by filename counts too, not
            # just one named explicitly in project.yaml.
            row = self.db.query_one(
                "SELECT source_path FROM images WHERE project_id = ? AND rel_path = ?",
                (project_id, rel_path),
            )
            source = (row["source_path"] if row else "") or (entry.source if entry else "")
            if source:
                safe_join(directory, source).unlink(missing_ok=True)

        safe_join(directory, rel_path).unlink(missing_ok=True)

        if doc is not None and entry is not None:
            doc.images = [i for i in doc.images if i.path != rel_path]
            write_project_doc(directory, doc)

        self.library.scan_project_dir(directory)

    # ------------------------------------------------------- unified upload

    def route_upload(
        self,
        project_id: str,
        filename: str,
        data: bytes,
        *,
        variant: str = "",
        for_image: str = "",
    ) -> dict[str, Any]:
        """File one dropped file by its extension.

        Backs the single drop zone on the project page: photos, editable
        originals, CAD sources and sliced 3MFs can all be dragged in together
        and each lands where it belongs.
        """
        suffix = Path(filename).suffix.lower()

        if suffix in IMAGE_EXTENSIONS:
            rel = self.add_photo(project_id, filename, data, variant)
            return {"kind": "image", "rel_path": rel, "variant": variant}
        if suffix in IMAGE_SOURCE_EXTENSIONS:
            rel = self.add_image_source(project_id, filename, data, for_image)
            return {"kind": "image_source", "rel_path": rel, "linked_to": for_image}
        if suffix in CAD_EXTENSIONS:
            rel = self.add_model_source(project_id, filename, data)
            return {"kind": "model_source", "rel_path": rel}
        if classify(Path(filename)) in {"doc", "misc"}:
            rel = self.add_document(project_id, filename, data)
            return {"kind": "document", "rel_path": rel}

        raise ValueError(
            f"nothing here handles {suffix or filename!r}. "
            "Drop an image, an editable original, a CAD source, a sliced 3MF, "
            "or a document such as a PDF."
        )

    # ----------------------------------------------------------- thumbnails

    def thumbnail(self, project_id: str, rel_path: str) -> Path | None:
        """Cached square-ish thumbnail under /data. Rebuilt on demand if deleted."""
        directory = self.library.dir_for_id(project_id)
        if directory is None:
            return None
        source = safe_join(directory, rel_path)
        if not source.is_file():
            return None

        cache_name = f"{project_id}__{rel_path.replace('/', '__')}.webp"
        cached = self.settings.thumbnail_path / cache_name
        if cached.exists() and cached.stat().st_mtime >= source.stat().st_mtime:
            return cached

        cached.parent.mkdir(parents=True, exist_ok=True)
        try:
            with Image.open(source) as image:
                image = image.convert("RGB")
                image.thumbnail(THUMBNAIL_SIZE, Image.Resampling.LANCZOS)
                image.save(cached, "WEBP", quality=82)
        except Exception:  # noqa: BLE001
            log.warning("could not thumbnail %s", source)
            return None
        return cached

    def export_resized(self, source: Path, destination: Path, preset: str) -> Path:
        """Fit within a preset box without upscaling or cropping."""
        width, height = EXPORT_PRESETS.get(preset, EXPORT_PRESETS["gallery"])
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with Image.open(source) as image:
                image = image.convert("RGB")
                if image.width > width or image.height > height:
                    image.thumbnail((width, height), Image.Resampling.LANCZOS)
                image.save(destination, "PNG")
        except Exception:  # noqa: BLE001 — a copy beats no asset at all
            shutil.copy2(source, destination)
        return destination

    # --------------------------------------------------------- render (M5.3)

    def render_turntable(
        self,
        project_id: str,
        mesh_rel_path: str,
        *,
        frames: int = 24,
        size: int = 1200,
        progress=None,
    ) -> dict[str, Any]:
        """Render a turntable to `images/renders/`. Slow on CPU — always queued."""
        if not render_backend_available():
            raise RuntimeError("rendering needs the optional extras: pip install '.[render]'")

        import numpy as np
        import trimesh

        directory = self.library.dir_for_id(project_id)
        if directory is None:
            raise KeyError(project_id)
        source = safe_join(directory, mesh_rel_path)
        if not source.is_file():
            raise FileNotFoundError(mesh_rel_path)

        scene_or_mesh = trimesh.load(source, force="mesh")
        if scene_or_mesh.is_empty:
            raise ValueError(f"no geometry in {mesh_rel_path}")

        mesh = scene_or_mesh
        mesh.apply_translation(-mesh.centroid)
        scale = float(np.max(mesh.extents)) or 1.0

        out_dir = directory / RENDERS_DIR / slugify(Path(mesh_rel_path).stem, fallback="model")
        out_dir.mkdir(parents=True, exist_ok=True)

        written: list[str] = []
        for index in range(frames):
            angle = 2 * math.pi * index / frames
            frame = mesh.copy()
            frame.apply_transform(trimesh.transformations.rotation_matrix(angle, [0, 0, 1]))

            target = out_dir / f"frame_{index:03d}.png"
            _render_frame(frame, target, size, scale)
            written.append(target.relative_to(directory).as_posix())

            if progress is not None:
                progress((index + 1) / frames, f"frame {index + 1}/{frames}")

        self.library.scan_project_dir(directory)
        bus.publish("render.finished", {"project_id": project_id, "frames": len(written)})
        return {"frames": written, "directory": out_dir.relative_to(directory).as_posix()}


def _render_frame(mesh, target: Path, size: int, scale: float) -> None:
    """One offscreen frame. Falls back to a shaded silhouette without pyrender."""
    import numpy as np

    try:
        import pyrender
    except ImportError:
        _render_silhouette(mesh, target, size)
        return

    scene = pyrender.Scene(bg_color=[0.1, 0.1, 0.12, 1.0], ambient_light=[0.3, 0.3, 0.3])
    scene.add(pyrender.Mesh.from_trimesh(mesh, smooth=False))

    distance = scale * 2.4
    camera_pose = np.array(
        [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 0.7071, -0.7071, -distance * 0.7071],
            [0.0, 0.7071, 0.7071, distance * 0.7071],
            [0.0, 0.0, 0.0, 1.0],
        ]
    )
    scene.add(pyrender.PerspectiveCamera(yfov=math.pi / 4.0), pose=camera_pose)
    scene.add(pyrender.DirectionalLight(color=[1.0, 1.0, 1.0], intensity=4.0), pose=camera_pose)

    renderer = pyrender.OffscreenRenderer(size, size)
    try:
        colour, _ = renderer.render(scene)
    finally:
        renderer.delete()
    Image.fromarray(colour).save(target)


def _render_silhouette(mesh, target: Path, size: int) -> None:
    """Orthographic depth-shaded projection. No GPU, no pyrender, still useful."""
    import numpy as np

    vertices = mesh.vertices
    xs, zs, ys = vertices[:, 0], vertices[:, 2], vertices[:, 1]
    span = max(float(np.ptp(xs)), float(np.ptp(zs)), 1e-6)
    margin = size * 0.08
    usable = size - 2 * margin

    def project(a, b):
        return (
            margin + (a - xs.min()) / span * usable,
            size - margin - (b - zs.min()) / span * usable,
        )

    depth = np.full((size, size), np.inf)
    for face in mesh.faces:
        tri = vertices[face]
        px, py = project(tri[:, 0], tri[:, 2])
        _fill_triangle(depth, px, py, float(ys[face].mean()), size)

    finite = depth[np.isfinite(depth)]
    canvas = np.full((size, size, 3), 26, dtype=np.uint8)
    if finite.size:
        low, high = finite.min(), finite.max()
        norm = np.clip((depth - low) / max(high - low, 1e-6), 0, 1)
        shade = (70 + 165 * (1 - norm)).astype(np.uint8)
        mask = np.isfinite(depth)
        for channel in range(3):
            canvas[..., channel][mask] = shade[mask]
    Image.fromarray(canvas).save(target)


def _fill_triangle(depth, px, py, value: float, size: int) -> None:
    import numpy as np

    min_x, max_x = int(max(0, px.min())), int(min(size - 1, px.max()))
    min_y, max_y = int(max(0, py.min())), int(min(size - 1, py.max()))
    if min_x > max_x or min_y > max_y:
        return

    x = np.arange(min_x, max_x + 1)
    y = np.arange(min_y, max_y + 1)
    grid_x, grid_y = np.meshgrid(x, y)

    x0, y0, x1, y1, x2, y2 = px[0], py[0], px[1], py[1], px[2], py[2]
    area = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
    if abs(area) < 1e-9:
        return
    w0 = ((y1 - y2) * (grid_x - x2) + (x2 - x1) * (grid_y - y2)) / area
    w1 = ((y2 - y0) * (grid_x - x2) + (x0 - x2) * (grid_y - y2)) / area
    inside = (w0 >= 0) & (w1 >= 0) & (w0 + w1 <= 1)
    if not inside.any():
        return

    window = depth[min_y : max_y + 1, min_x : max_x + 1]
    # Nearest surface wins, so the model reads as solid rather than see-through.
    np.minimum(window, np.where(inside, value, np.inf), out=window)
