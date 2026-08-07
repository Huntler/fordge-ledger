"""Demo library generator, for throwaway containers and tests.

Writes only the *durable* files — `project.yaml`, notes, meshes, sliced 3MFs and
their sidecars, photos, snapshots, publish drafts. Nothing touches SQLite
directly. The normal boot scan then builds the cache from those folders, so
seeding exercises the same path as a real library and doubles as a check on the
governing principle from §2.

Seeding is opt-in (`FORGE_DEMO_SEED=true`) and refuses to run against a library
that already has projects in it.
"""

from __future__ import annotations

import json
import logging
import math
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any

import yaml
from PIL import Image, ImageDraw

from .services.library import (
    DOCS_DIR,
    IMAGE_SOURCES_DIR,
    MODEL_SOURCES_DIR,
    MODELS_DIR,
    NOTES_FILE,
    PHOTOS_DIR,
    PRINTS_DIR,
    PROJECT_YAML,
    PUBLISH_DIR,
    VERSIONS_DIR,
)
from .utils import new_ulid, utcnow

log = logging.getLogger(__name__)


# --------------------------------------------------------------- image bits


def demo_png(size: tuple[int, int] = (640, 480), hue: str = "#3b6ea5", label: str = "") -> bytes:
    """A recognisable placeholder image, so the gallery is not a wall of grey."""
    image = Image.new("RGB", size, "#11141b")
    draw = ImageDraw.Draw(image)

    width, height = size
    for offset in range(0, height, 4):
        shade = int(20 + 26 * (offset / max(height, 1)))
        draw.line([(0, offset), (width, offset)], fill=(shade, shade, shade + 6))

    box = (width * 0.22, height * 0.24, width * 0.78, height * 0.76)
    draw.rounded_rectangle(box, radius=int(min(size) * 0.06), fill=hue)
    draw.rounded_rectangle(box, radius=int(min(size) * 0.06), outline="#f59e0b", width=3)
    if label:
        draw.text((width * 0.26, height * 0.3), label, fill="#f8fafc")

    buffer = BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


# ------------------------------------------------------------ 3MF synthesis

_SLICE_INFO = """<?xml version="1.0" encoding="UTF-8"?>
<config>
  <header>
    <header_item key="X-BBL-Client-Type" value="slicer"/>
    <header_item key="X-BBL-Client-Version" value="01.09.00.70"/>
  </header>
  <plate>
    <metadata key="index" value="1"/>
    <metadata key="printer_model_id" value="C11"/>
    <metadata key="nozzle_diameter" value="{nozzle}"/>
    <metadata key="prediction" value="{prediction}"/>
    <metadata key="weight" value="{weight}"/>
    <metadata key="support_used" value="{support}"/>
    <object identify_id="881" name="{object_name}" skipped="false"/>
    <filament id="1" tray_info_idx="GFA00" type="{filament}" color="{color}" used_m="{used_m}" used_g="{weight}"/>
  </plate>
</config>
"""

_MODEL = """<?xml version="1.0" encoding="UTF-8"?>
<model unit="millimeter" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">
  <resources>
    <object id="1" type="model" name="{object_name}"/>
  </resources>
</model>
"""


def slice_info_xml(
    *,
    nozzle: float = 0.4,
    prediction: int = 4521,
    weight: float = 22.73,
    supports: bool = False,
    object_name: str = "tray.stl",
    filament: str = "PLA",
    color: str = "#161616FF",
) -> str:
    """Render `slice_info.config` with concrete values."""
    return _SLICE_INFO.format(
        nozzle=nozzle,
        prediction=prediction,
        weight=weight,
        support="true" if supports else "false",
        object_name=object_name,
        filament=filament,
        color=color,
        used_m=round(weight / 3.0, 2),
    )


def build_sliced_3mf(
    path: Path,
    *,
    object_name: str = "tray.stl",
    prediction: int = 4521,
    weight: float = 22.73,
    filament: str = "PLA",
    color: str = "#161616FF",
    nozzle: float = 0.4,
    layer_height: str = "0.2",
    walls: str = "3",
    infill: str = "15%",
    infill_pattern: str = "gyroid",
    printer: str = "Bambu Lab P1S",
    supports: bool = False,
    plate_hue: str = "#3b6ea5",
    slice_info: str | None = _SLICE_INFO,
    settings: dict[str, Any] | None = ...,  # type: ignore[assignment]
    with_preview: bool = True,
) -> Path:
    """Write a 3MF matching the Bambu Studio archive layout.

    Used to seed the demo library and as the test fixture, so both exercise the
    same shape the real parser is aimed at.
    """
    profile: dict[str, Any] | None
    if settings is ...:
        profile = {
            "layer_height": layer_height,
            "first_layer_height": "0.25",
            "wall_loops": walls,
            "sparse_infill_density": infill,
            "sparse_infill_pattern": infill_pattern,
            "enable_support": "1" if supports else "0",
            "printer_model": printer,
            "print_settings_id": f"{layer_height}mm Standard @BBL X1C",
            "filament_type": [filament],
            "nozzle_diameter": [str(nozzle)],
            "nozzle_temperature": ["220"],
            "hot_plate_temp": ["55"],
            "some_future_bambu_key": "kept in raw_settings",
        }
    else:
        profile = settings

    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("3D/3dmodel.model", _MODEL.format(object_name=object_name))
        if slice_info is not None:
            # An already-rendered override is passed through untouched.
            body = (
                slice_info_xml(
                    nozzle=nozzle,
                    prediction=prediction,
                    weight=weight,
                    supports=supports,
                    object_name=object_name,
                    filament=filament,
                    color=color,
                )
                if slice_info is _SLICE_INFO
                else slice_info
            )
            archive.writestr("Metadata/slice_info.config", body)
        if profile is not None:
            archive.writestr("Metadata/project_settings.config", json.dumps(profile))
        if with_preview:
            archive.writestr("Metadata/plate_1.png", demo_png((512, 384), plate_hue, object_name))
    return path


# ----------------------------------------------------------------- the seed


def _stl(name: str) -> bytes:
    """A tiny but genuinely valid ASCII STL, so mesh tooling can open it."""
    lines = [f"solid {name}"]
    for index in range(6):
        angle = index * math.pi / 3
        lines += [
            "  facet normal 0 0 1",
            "    outer loop",
            "      vertex 0 0 0",
            f"      vertex {math.cos(angle) * 10:.3f} {math.sin(angle) * 10:.3f} 0",
            f"      vertex {math.cos(angle + 1) * 10:.3f} {math.sin(angle + 1) * 10:.3f} 5",
            "    endloop",
            "  endfacet",
        ]
    lines.append(f"endsolid {name}")
    return ("\n".join(lines) + "\n").encode()


def _pdf(title: str) -> bytes:
    """A genuinely openable one-page PDF, so the demo link is not a dead file."""
    body = (
        f"BT /F1 18 Tf 60 720 Td ({title}) Tj ET\n"
        "BT /F1 11 Tf 60 690 Td (Sample attachment in Forge Ledger.) Tj ET"
    ).encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(body)).encode() + b" >>\nstream\n" + body + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + obj + b"\nendobj\n"

    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    ).encode()
    return bytes(out)


def _write_sidecar_job(path: Path, job: dict[str, Any]) -> None:
    """Seed only the lifecycle half; ingest fills in the parsed settings."""
    path.write_text(json.dumps({"job": {"id": new_ulid(), **job}}, indent=2), encoding="utf-8")


def seed_library(root: Path) -> list[str]:
    """Create the demo projects. Returns the slugs written."""
    root.mkdir(parents=True, exist_ok=True)
    written = []
    for builder in (_desk_organizer, _cable_clip, _hinged_box, _planter):
        written.append(builder(root))
    log.info("seeded demo library with %d projects", len(written))
    return written


def _project(
    root: Path,
    slug: str,
    doc: dict[str, Any],
    notes: str,
) -> Path:
    directory = root / slug
    for sub in (MODELS_DIR, PRINTS_DIR, PHOTOS_DIR, PUBLISH_DIR):
        (directory / sub).mkdir(parents=True, exist_ok=True)
    (directory / PROJECT_YAML).write_text(
        yaml.safe_dump({"id": new_ulid(), **doc}, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    (directory / NOTES_FILE).write_text(notes, encoding="utf-8")
    return directory


def _desk_organizer(root: Path) -> str:
    """The fullest example: published, remixed, with a failed print and a snapshot."""
    slug = "desk-organizer"
    directory = _project(
        root,
        slug,
        {
            "title": "Desk Organizer",
            "status": "published",
            "created": "2026-07-14",
            "tags": ["office", "storage", "parametric"],
            "license": "CC-BY-4.0",
            "remix_of": [
                {
                    "url": "https://makerworld.com/en/models/12345",
                    "title": "Original Tray",
                    "author": "someone",
                    "license": "CC-BY-4.0",
                }
            ],
            "models": [
                {
                    "name": "tray",
                    "files": ["models/sources/tray.step", "models/tray/tray.stl"],
                },
                {"name": "lid", "files": ["models/sources/lid.scad", "models/lid/lid.stl"]},
            ],
            "images": [
                {
                    "path": "images/photos/on-the-desk.png",
                    "variant": "web",
                    "source": "images/sources/on-the-desk.psd",
                    "cover": True,
                },
                {"path": "images/photos/on-the-desk-mobile.png", "variant": "mobile"},
                {"path": "images/photos/close-up.png", "variant": "web"},
                {"path": "images/photos/print-in-progress.png"},
            ],
        },
        "# Desk Organizer\n\n"
        "A stackable tray that holds pens, clips and the bits that end up loose in a drawer.\n\n"
        "Parametric in Fusion — the divider spacing is driven by one sketch dimension.\n\n"
        "## Log\n\n"
        "- v1 warped at the corners on a cold plate.\n"
        "- v2 added a 5mm brim and thicker corner fillets. Solved it.\n",
    )

    (directory / MODELS_DIR / "tray").mkdir(parents=True, exist_ok=True)
    (directory / MODELS_DIR / "lid").mkdir(parents=True, exist_ok=True)
    (directory / MODELS_DIR / "tray" / "tray.stl").write_bytes(_stl("tray"))
    (directory / MODELS_DIR / "lid" / "lid.stl").write_bytes(_stl("lid"))

    # CAD originals live together in models/sources/, meshes stay per model.
    sources = directory / MODEL_SOURCES_DIR
    sources.mkdir(parents=True, exist_ok=True)
    (sources / "tray.step").write_bytes(b"ISO-10303-21;\nHEADER;\n")
    (sources / "lid.scad").write_bytes(b"// Parametric lid\nwall = 2;\ncube([80, 60, wall]);\n")

    prints = directory / PRINTS_DIR
    first = build_sliced_3mf(
        prints / "2026-07-15_tray-v1.gcode.3mf",
        object_name="tray_v1.stl",
        prediction=4210,
        weight=21.4,
        plate_hue="#8b5cf6",
    )
    _write_sidecar_job(
        first.with_name("2026-07-15_tray-v1.json"),
        {
            "status": "failed",
            "model_name": "tray",
            "actual_s": 1980,
            "notes": "Stopped it once the corner lifted.",
            "failure_reason": "Corner lifted off the plate at roughly 15mm",
            "failure_fix": "5mm brim, bed to 60C, cleaned the plate with dish soap",
            "created": "2026-07-15T18:02:00+00:00",
            "started": "2026-07-15T18:05:00+00:00",
            "finished": "2026-07-15T18:38:00+00:00",
        },
    )

    second = build_sliced_3mf(
        prints / "2026-07-18_tray-v2.gcode.3mf",
        object_name="tray_v2.stl",
        prediction=4521,
        weight=22.73,
        plate_hue="#3b6ea5",
    )
    _write_sidecar_job(
        second.with_name("2026-07-18_tray-v2.json"),
        {
            "status": "done",
            "model_name": "tray",
            "actual_s": 4740,
            "notes": "Clean. Brim peeled off without marking the surface.",
            "created": "2026-07-18T09:12:00+00:00",
            "started": "2026-07-18T09:15:00+00:00",
            "finished": "2026-07-18T10:34:00+00:00",
        },
    )

    lid_print = build_sliced_3mf(
        prints / "2026-07-19_lid.gcode.3mf",
        object_name="lid.stl",
        prediction=1980,
        weight=9.8,
        filament="PETG",
        color="#1f7a4dFF",
        layer_height="0.16",
        infill="20%",
        plate_hue="#1f7a4d",
    )
    _write_sidecar_job(
        lid_print.with_name("2026-07-19_lid.json"),
        {"status": "done", "model_name": "lid", "created": "2026-07-19T11:00:00+00:00"},
    )

    photos = directory / PHOTOS_DIR
    # Web is landscape, mobile is the tall crop — cut by hand, shipped as-is.
    (photos / "on-the-desk.png").write_bytes(demo_png((1600, 1200), "#c2703a", "on the desk / web"))
    (photos / "on-the-desk-mobile.png").write_bytes(
        demo_png((1080, 1350), "#c2703a", "on the desk / mobile")
    )
    (photos / "close-up.png").write_bytes(demo_png((1600, 1200), "#3b6ea5", "close up"))
    (photos / "print-in-progress.png").write_bytes(demo_png(hue="#4b5563", label="mid print"))

    # A datasheet of the sort that ends up loose in a downloads folder.
    docs = directory / DOCS_DIR
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "bearing-608zz-datasheet.pdf").write_bytes(_pdf("608ZZ bearing datasheet"))
    (docs / "cut-list.csv").write_text(
        "part,length_mm,qty\ndivider,78,3\nbase,196,1\n", encoding="utf-8"
    )

    # The editable original behind the cover, paired by filename.
    image_sources = directory / IMAGE_SOURCES_DIR
    image_sources.mkdir(parents=True, exist_ok=True)
    (image_sources / "on-the-desk.psd").write_bytes(b"8BPS\x00\x01" + b"\x00" * 64)
    (image_sources / "close-up.pxd").write_bytes(b"PXD\x00" + b"\x00" * 64)

    # A snapshot, written the way the app writes them.
    snapshot = directory / VERSIONS_DIR / "v001__2026-07-16__pre-fillet"
    (snapshot / MODELS_DIR / "tray").mkdir(parents=True, exist_ok=True)
    (snapshot / MODELS_DIR / "tray" / "tray.stl").write_bytes(_stl("tray_v1"))
    (snapshot / "version.yaml").write_text(
        yaml.safe_dump(
            {
                "number": 1,
                "label": "pre-fillet",
                "note": "Before the corner fillets that fixed the warping.",
                "created": "2026-07-16T20:41:00+00:00",
                "file_count": 1,
                "storage": "reflink",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    publish = directory / PUBLISH_DIR
    publish.mkdir(parents=True, exist_ok=True)
    (publish / "description.md").write_text(
        "# Desk Organizer\n\n"
        "A stackable tray that holds pens, clips and the bits that end up loose in a drawer.\n\n"
        "## Print settings\n\n"
        "- **Layer height:** 0.2mm\n"
        "- **Walls:** 3\n"
        "- **Infill:** 15% gyroid\n"
        "- **Supports:** not required\n",
        encoding="utf-8",
    )
    (publish / "fields.yaml").write_text(
        yaml.safe_dump(
            {
                "title": "Desk Organizer",
                "summary": "A stackable desk tray, parametric and support-free.",
                "tags": ["office", "storage", "parametric"],
                "category": "Household",
                "license": "CC-BY-4.0",
                "template": "default",
                "checklist": {"Title pasted": True, "Description pasted": True},
                "updated_at": utcnow(),
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return slug


def _cable_clip(root: Path) -> str:
    """Shows orphan detection: a CAD file on disk that project.yaml does not mention."""
    slug = "cable-clip"
    directory = _project(
        root,
        slug,
        {
            "title": "Cable Clip",
            "status": "testing",
            "created": "2026-08-01",
            "tags": ["desk", "cables"],
            "models": [{"name": "clip", "files": ["models/clip.stl"]}],
        },
        "# Cable Clip\n\nSnaps onto a 20mm desk lip. Testing the grip thickness.\n",
    )

    (directory / MODELS_DIR / "clip.stl").write_bytes(_stl("clip"))
    # Deliberately absent from project.yaml, so it shows up as unfiled.
    (directory / MODEL_SOURCES_DIR).mkdir(parents=True, exist_ok=True)
    (directory / MODEL_SOURCES_DIR / "clip-v3-experiment.step").write_bytes(b"ISO-10303-21;\n")

    queued = build_sliced_3mf(
        directory / PRINTS_DIR / "2026-08-04_clip-test.gcode.3mf",
        object_name="clip.stl",
        prediction=1140,
        weight=4.2,
        layer_height="0.12",
        infill="35%",
        infill_pattern="grid",
        plate_hue="#b45309",
    )
    _write_sidecar_job(
        queued.with_name("2026-08-04_clip-test.json"),
        {"status": "queued", "model_name": "clip", "created": "2026-08-04T16:20:00+00:00"},
    )
    return slug


def _hinged_box(root: Path) -> str:
    """Has a print mid-flight, and supports enabled, so the settings differ."""
    slug = "hinged-box"
    directory = _project(
        root,
        slug,
        {
            "title": "Hinged Box",
            "status": "designing",
            "created": "2026-08-05",
            "tags": ["storage", "print-in-place"],
            "license": "CC-BY-NC-4.0",
            "models": [{"name": "box", "files": ["models/box.stl"]}],
        },
        "# Hinged Box\n\nPrint-in-place hinge. Third attempt at the clearance.\n\n"
        "0.25mm gap fused. Trying 0.35mm.\n",
    )

    (directory / MODELS_DIR / "box.stl").write_bytes(_stl("box"))
    running = build_sliced_3mf(
        directory / PRINTS_DIR / "2026-08-06_box-hinge-035.gcode.3mf",
        object_name="box.stl",
        prediction=9300,
        weight=48.6,
        filament="PLA-CF",
        color="#1f2937FF",
        nozzle=0.6,
        layer_height="0.28",
        walls="4",
        infill="10%",
        supports=True,
        plate_hue="#6d28d9",
    )
    _write_sidecar_job(
        running.with_name("2026-08-06_box-hinge-035.json"),
        {
            "status": "printing",
            "model_name": "box",
            "created": "2026-08-06T08:00:00+00:00",
            "started": "2026-08-06T08:04:00+00:00",
        },
    )
    (directory / PHOTOS_DIR / "hinge-detail.png").write_bytes(
        demo_png(hue="#6d28d9", label="hinge detail")
    )
    return slug


def _planter(root: Path) -> str:
    """Nearly empty on purpose, to exercise the empty states."""
    slug = "planter-pot"
    _project(
        root,
        slug,
        {
            "title": "Planter Pot",
            "status": "idea",
            "created": "2026-08-06",
            "tags": ["garden"],
        },
        "# Planter Pot\n\nSelf-watering, vase mode. Nothing modelled yet.\n",
    )
    return slug
