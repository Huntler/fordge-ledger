"""Reusable OpenSCAD snippet "tools" for the in-browser editor's toolbar.

Library-wide, not per-project — same `_shared/` idiom as
`PublishService`'s templates/snippets (`publish.py`), including its "the
slugified name *is* the identity, saving under a new name creates a new
entry" semantics. The only addition here is an optional square icon image,
validated and stored alongside the snippet.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from PIL import Image

from ..utils import slugify
from .library import MODEL_SOURCES_DIR, LibraryService

log = logging.getLogger(__name__)

# Matches a `use <tools/slug.scad>;` or `include <tools/slug.scad>;` line —
# mirrors TOOL_USE_RE in ScadWorkspace.tsx (kept in sync by hand; not a real
# OpenSCAD parser, just enough to tell whether a source still wants a given
# tool). Used by remove_from_project to decide whether another .scad source
# in the project still needs the copy before deleting it.
TOOL_USE_RE = re.compile(
    r"^[ \t]*(?:use|include)[ \t]*<[ \t]*tools/([a-z0-9-]+)\.scad[ \t]*>", re.MULTILINE
)

TOOLS_DIR = "_shared/tools"

MAX_ICON_SIZE = 512

# Packaged alongside this module (not the top-level repo `resources/`, which
# isn't shipped in the built image) so it resolves the same way in a dev
# checkout and inside the container.
DEFAULT_ICONS_DIR = Path(__file__).resolve().parent.parent / "resources" / "tools"

DEFAULT_TOOL_ICONS = {
    "screw": DEFAULT_ICONS_DIR / "screw.png",
    "nut": DEFAULT_ICONS_DIR / "nut.png",
}

DEFAULT_TOOLS = {
    "screw": (
        "// Screw import — a plain (unthreaded) shaft with a selectable head.\n"
        "// All dimensions in mm.\n"
        "\n"
        "// Dispatches to the matching head module below based on `head`:\n"
        "// \"flat\" | \"round\" | \"hex\" | \"socket\" | \"none\"\n"
        "module screw(\n"
        "    length,          // shaft length, head excluded\n"
        "    diameter,        // shaft diameter\n"
        "    head = \"flat\",   // head type, see above\n"
        "    head_diameter,   // head diameter (flat/round/socket) or across-flats size (hex)\n"
        "    head_height = 0, // head height (round/hex/socket); ignored for \"flat\"\n"
        "    chamfer = 0      // edge chamfer size on the head, 0 disables\n"
        ") {\n"
        "    if (head == \"flat\")\n"
        "        screw_flat_head(length, diameter, head_diameter, chamfer=chamfer);\n"
        "    else if (head == \"round\")\n"
        "        screw_round_head(length, diameter, head_diameter, head_height, chamfer=chamfer);\n"
        "    else if (head == \"hex\")\n"
        "        screw_hex_head(length, diameter, head_diameter, head_height, chamfer=chamfer);\n"
        "    else if (head == \"socket\")\n"
        "        screw_socket_head(length, diameter, head_diameter, head_height, chamfer=chamfer);\n"
        "    else\n"
        "        screw_shaft(length, diameter, chamfer=chamfer);\n"
        "}\n"
        "\n"
        "// Plain shaft, no head — reused by every head module below.\n"
        "module screw_shaft(length, diameter, chamfer = 0) {\n"
        "    r = diameter / 2;\n"
        "    if (chamfer > 0) {\n"
        "        cylinder(h = length - chamfer, r = r);\n"
        "        translate([0, 0, length - chamfer])\n"
        "            cylinder(h = chamfer, r1 = r, r2 = r - chamfer);\n"
        "    } else {\n"
        "        cylinder(h = length, r = r);\n"
        "    }\n"
        "}\n"
        "\n"
        "// Flat (countersunk) head screw: conical head flush with the shaft,\n"
        "// widening from the shaft radius up to head_diameter/2.\n"
        "module screw_flat_head(length, diameter, head_diameter, angle = 90, chamfer = 0) {\n"
        "    r = diameter / 2;\n"
        "    hr = head_diameter / 2;\n"
        "    head_h = (hr - r) / tan(angle / 2);\n"
        "    screw_shaft(length, diameter);\n"
        "    translate([0, 0, length])\n"
        "        cylinder(h = head_h, r1 = r, r2 = hr);\n"
        "    if (chamfer > 0)\n"
        "        translate([0, 0, length + head_h - chamfer])\n"
        "            cylinder(h = chamfer, r1 = hr, r2 = hr - chamfer);\n"
        "}\n"
        "\n"
        "// Round / pan head screw: flat cylindrical head, optional chamfered top edge.\n"
        "module screw_round_head(length, diameter, head_diameter, head_height, chamfer = 0) {\n"
        "    hr = head_diameter / 2;\n"
        "    screw_shaft(length, diameter);\n"
        "    translate([0, 0, length]) {\n"
        "        if (chamfer > 0) {\n"
        "            cylinder(h = head_height - chamfer, r = hr);\n"
        "            translate([0, 0, head_height - chamfer])\n"
        "                cylinder(h = chamfer, r1 = hr, r2 = hr - chamfer);\n"
        "        } else {\n"
        "            cylinder(h = head_height, r = hr);\n"
        "        }\n"
        "    }\n"
        "}\n"
        "\n"
        "// Hex head screw / bolt: head_diameter is the across-flats size.\n"
        "module screw_hex_head(length, diameter, head_diameter, head_height, chamfer = 0) {\n"
        "    screw_shaft(length, diameter);\n"
        "    translate([0, 0, length])\n"
        "        _screw_hex_prism(head_diameter, head_height, chamfer);\n"
        "}\n"
        "\n"
        "// Socket / cap head screw: cylindrical head, e.g. a hex-socket cap screw.\n"
        "module screw_socket_head(length, diameter, head_diameter, head_height, chamfer = 0) {\n"
        "    screw_round_head(length, diameter, head_diameter, head_height, chamfer=chamfer);\n"
        "}\n"
        "\n"
        "module _screw_hex_prism(across_flats, height, chamfer = 0) {\n"
        "    r = across_flats / sqrt(3); // circumradius from the across-flats size\n"
        "    if (chamfer > 0) {\n"
        "        cylinder(h = height - chamfer, r = r, $fn = 6);\n"
        "        translate([0, 0, height - chamfer])\n"
        "            cylinder(h = chamfer, r1 = r, r2 = r - chamfer, $fn = 6);\n"
        "    } else {\n"
        "        cylinder(h = height, r = r, $fn = 6);\n"
        "    }\n"
        "}\n"
        "\n"
        "// screw(length=20, diameter=4, head=\"hex\", head_diameter=7, head_height=3, chamfer=0.4);\n"
    ),
    "nut": (
        "// Nut import — a plain (unthreaded) ring with a selectable outer shape.\n"
        "// All dimensions in mm.\n"
        "\n"
        "// Dispatches to the matching module below based on `type`:\n"
        "// \"hex\" | \"square\" | \"wing\" | \"flange\" | \"none\"\n"
        "module nut(\n"
        "    height,               // nut height, flange excluded\n"
        "    hole_diameter,        // center hole diameter\n"
        "    width,                // across-flats size (hex/square), hub diameter (wing)\n"
        "                          // or hex-body across-flats size (flange)\n"
        "    type = \"hex\",         // nut type, see above\n"
        "    chamfer = 0,          // edge chamfer size, 0 disables\n"
        "    wing_span,            // \"wing\" only — how far each wing extends past the hub\n"
        "    wing_thickness,       // \"wing\" only — a wing's vertical thickness, defaults to height\n"
        "    flange_diameter,      // \"flange\" only — outer diameter of the base disc\n"
        "    flange_height         // \"flange\" only — height of the base disc\n"
        ") {\n"
        "    if (type == \"hex\")\n"
        "        nut_hex(height, hole_diameter, width, chamfer=chamfer);\n"
        "    else if (type == \"square\")\n"
        "        nut_square(height, hole_diameter, width, chamfer=chamfer);\n"
        "    else if (type == \"wing\")\n"
        "        nut_wing(\n"
        "            height, hole_diameter, width, wing_span,\n"
        "            is_undef(wing_thickness) ? height : wing_thickness,\n"
        "            chamfer=chamfer\n"
        "        );\n"
        "    else if (type == \"flange\")\n"
        "        nut_flange(height, hole_diameter, width, flange_diameter, flange_height, chamfer=chamfer);\n"
        "    else\n"
        "        nut_ring(height, hole_diameter, width, chamfer=chamfer);\n"
        "}\n"
        "\n"
        "// Hex nut: width is the across-flats size.\n"
        "module nut_hex(height, hole_diameter, width, chamfer = 0) {\n"
        "    difference() {\n"
        "        _nut_prism(width, height, 6, chamfer=chamfer);\n"
        "        translate([0, 0, -0.5])\n"
        "            cylinder(h = height + 1, r = hole_diameter / 2);\n"
        "    }\n"
        "}\n"
        "\n"
        "// Square nut: width is the across-flats size.\n"
        "module nut_square(height, hole_diameter, width, chamfer = 0) {\n"
        "    difference() {\n"
        "        _nut_prism(width, height, 4, chamfer=chamfer);\n"
        "        translate([0, 0, -0.5])\n"
        "            cylinder(h = height + 1, r = hole_diameter / 2);\n"
        "    }\n"
        "}\n"
        "\n"
        "// Wing nut: a round hub (width is its diameter) with two hand-tightening\n"
        "// wings, each reaching wing_span past the hub edge.\n"
        "module nut_wing(height, hole_diameter, width, wing_span, wing_thickness, chamfer = 0) {\n"
        "    hub_r = width / 2;\n"
        "    difference() {\n"
        "        union() {\n"
        "            _nut_round(height, hub_r, chamfer=chamfer);\n"
        "            for (a = [0, 180])\n"
        "                rotate([0, 0, a])\n"
        "                    translate([0, 0, height / 2])\n"
        "                        _nut_wing_fin(hub_r, wing_span, wing_thickness);\n"
        "        }\n"
        "        translate([0, 0, -0.5])\n"
        "            cylinder(h = height + 1, r = hole_diameter / 2);\n"
        "    }\n"
        "}\n"
        "\n"
        "// Flange nut: a hex nut (width is its across-flats size) sitting on a\n"
        "// wider disc base for load distribution.\n"
        "module nut_flange(height, hole_diameter, width, flange_diameter, flange_height, chamfer = 0) {\n"
        "    difference() {\n"
        "        cylinder(h = flange_height, r = flange_diameter / 2);\n"
        "        translate([0, 0, -0.5])\n"
        "            cylinder(h = flange_height + 1, r = hole_diameter / 2);\n"
        "    }\n"
        "    translate([0, 0, flange_height])\n"
        "        nut_hex(height, hole_diameter, width, chamfer=chamfer);\n"
        "}\n"
        "\n"
        "// No specific outer shape — a plain round ring, width is the outer diameter.\n"
        "module nut_ring(height, hole_diameter, width, chamfer = 0) {\n"
        "    difference() {\n"
        "        _nut_round(height, width / 2, chamfer=chamfer);\n"
        "        translate([0, 0, -0.5])\n"
        "            cylinder(h = height + 1, r = hole_diameter / 2);\n"
        "    }\n"
        "}\n"
        "\n"
        "module _nut_prism(width, height, sides, chamfer = 0) {\n"
        "    r = width / (2 * cos(180 / sides)); // circumradius from the across-flats size\n"
        "    if (chamfer > 0) {\n"
        "        cylinder(h = chamfer, r1 = r - chamfer, r2 = r, $fn = sides);\n"
        "        translate([0, 0, chamfer])\n"
        "            cylinder(h = height - 2 * chamfer, r = r, $fn = sides);\n"
        "        translate([0, 0, height - chamfer])\n"
        "            cylinder(h = chamfer, r1 = r, r2 = r - chamfer, $fn = sides);\n"
        "    } else {\n"
        "        cylinder(h = height, r = r, $fn = sides);\n"
        "    }\n"
        "}\n"
        "\n"
        "module _nut_round(height, radius, chamfer = 0) {\n"
        "    if (chamfer > 0) {\n"
        "        cylinder(h = chamfer, r1 = radius - chamfer, r2 = radius);\n"
        "        translate([0, 0, chamfer])\n"
        "            cylinder(h = height - 2 * chamfer, r = radius);\n"
        "        translate([0, 0, height - chamfer])\n"
        "            cylinder(h = chamfer, r1 = radius, r2 = radius - chamfer);\n"
        "    } else {\n"
        "        cylinder(h = height, r = radius);\n"
        "    }\n"
        "}\n"
        "\n"
        "// A single tapered wing extending in +x, hulled between a hub-side post\n"
        "// and a narrower, shorter tip.\n"
        "module _nut_wing_fin(hub_r, span, thickness) {\n"
        "    hull() {\n"
        "        translate([hub_r, 0, 0])\n"
        "            cylinder(h = thickness, r = thickness / 2, center = true);\n"
        "        translate([hub_r + span, 0, 0])\n"
        "            cylinder(h = thickness * 0.4, r = thickness * 0.2, center = true);\n"
        "    }\n"
        "}\n"
        "\n"
        "// nut(height=4, hole_diameter=4.3, width=7.5, type=\"hex\", chamfer=0.4);\n"
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

            icon_source = DEFAULT_TOOL_ICONS.get(stem)
            icon_target = self.tools_dir / f"{stem}.png"
            if icon_source is not None and icon_source.is_file() and not icon_target.exists():
                _save_icon(icon_target, icon_source.read_bytes())

    def list_tools(self) -> list[dict[str, Any]]:
        if not self.tools_dir.is_dir():
            return []
        out = []
        for path in sorted(self.tools_dir.glob("*.scad")):
            icon_path = path.with_suffix(".png")
            has_icon = icon_path.exists()
            out.append(
                {
                    "name": path.stem,
                    "body": path.read_text(encoding="utf-8"),
                    "has_icon": has_icon,
                    "has_alpha": has_icon and _icon_has_alpha(icon_path),
                }
            )
        return out

    def save_tool(self, name: str, body: str, icon_bytes: bytes | None = None) -> dict[str, Any]:
        self.tools_dir.mkdir(parents=True, exist_ok=True)
        stem = slugify(name, fallback="tool")
        target = self.tools_dir / f"{stem}.scad"
        target.write_text(body, encoding="utf-8")

        icon_path = self.tools_dir / f"{stem}.png"
        if icon_bytes is not None:
            _save_icon(icon_path, icon_bytes)

        has_icon = icon_path.exists()
        return {
            "name": stem,
            "body": body,
            "has_icon": has_icon,
            "has_alpha": has_icon and _icon_has_alpha(icon_path),
        }

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
        """The other half of copy_into_project. The copy under
        models/sources/tools/ is shared by every .scad source in the
        project that references it — toggling a tool off in the file
        you're currently editing doesn't mean some other source in the
        project isn't still `use <tools/...>;`-ing it, and deleting the
        copy out from under a source that still needs it breaks that
        source everywhere except this app's own live renderer (which
        pulls tool bodies from the shared `_shared/tools/` library, not
        this per-project copy — see extractToolFiles in ScadWorkspace.tsx).
        So: only actually delete once nothing else in the project still
        references it; otherwise leave the copy in place. Leaves an empty
        tools/ dir behind if this was the last one — harmless, and nothing
        else in the app proactively cleans up empty directories either.
        """
        directory = self.library.dir_for_id(project_id)
        if directory is None:
            raise KeyError(project_id)
        stem = slugify(name, fallback="tool")
        if self._still_referenced(directory, stem):
            return
        (directory / MODEL_SOURCES_DIR / "tools" / f"{stem}.scad").unlink(missing_ok=True)
        self.library.scan_project_dir(directory)

    def _still_referenced(self, directory: Path, stem: str) -> bool:
        """Whether any .scad source in the project — other than the tools/
        copies themselves — still references tools/<stem>.scad. Reads
        on-disk content, so an unsaved buffer's edits (e.g. a toggle-off
        that hasn't been Saved yet) don't count; that's the conservative
        direction to be wrong in, since it just keeps a copy around a
        little longer rather than deleting one something still needs.
        """
        sources_dir = directory / MODEL_SOURCES_DIR
        tools_dir = sources_dir / "tools"
        if not sources_dir.is_dir():
            return False
        for path in sources_dir.rglob("*.scad"):
            if path.is_relative_to(tools_dir):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            if any(match == stem for match in TOOL_USE_RE.findall(text)):
                return True
        return False


def _icon_has_alpha(path: Path) -> bool:
    """True if the icon has any actually-transparent pixels, not just an
    alpha channel that happens to be uniformly opaque (every icon is stored
    as RGBA by _save_icon regardless). Line-art/silhouette icons — the kind
    worth tinting to match the toolbar's active/inactive state in the UI —
    have real transparency; a flat photo icon converted to RGBA doesn't.
    """
    try:
        with Image.open(path) as image:
            return image.convert("RGBA").getchannel("A").getextrema()[0] < 255
    except Exception:
        return False


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
