"""Parser for sliced 3MF files (Bambu Studio / Orca Slicer flavour).

Runs standalone — `python -m app.services.threemf file.3mf` — so the format
assumptions can be checked against real files before any UI depends on them.

Design rules, per the plan's risk list:

* **Version-tagged.** Every result carries ``parser_version``. When the format
  drifts, old extractions stay identifiable.
* **Raw kept alongside parsed.** ``raw_settings`` holds the untouched slicer
  profile, so a field this parser does not know about is never lost.
* **Fail soft.** A missing or malformed section produces a warning, not an
  exception. A 3MF that is only half-understood is still worth ingesting.
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

PARSER_VERSION = 1

SLICE_INFO = "Metadata/slice_info.config"
PROJECT_SETTINGS = "Metadata/project_settings.config"
MODEL_PATH = "3D/3dmodel.model"

# Settings surfaced in the UI and offered as publish-template placeholders.
# Everything else stays reachable through raw_settings.
HIGHLIGHT_SETTINGS = {
    "layer_height": "layer_height",
    "initial_layer_height": "first_layer_height",
    "wall_loops": "wall_count",
    "sparse_infill_density": "infill_density",
    "sparse_infill_pattern": "infill_pattern",
    "support_type": "support_type",
    "enable_support": "supports_enabled",
    "brim_type": "brim_type",
    "printer_model": "printer_model",
    "printer_settings_id": "printer_profile",
    "print_settings_id": "print_profile",
    "filament_settings_id": "filament_profile",
    "filament_type": "filament_type",
    "nozzle_diameter": "nozzle_diameter",
    "nozzle_temperature": "nozzle_temperature",
    "hot_plate_temp": "bed_temperature",
    "outer_wall_speed": "outer_wall_speed",
}


@dataclass(slots=True)
class Filament:
    """One filament slot used by a plate."""

    slot: int | None = None
    type: str | None = None
    color: str | None = None
    used_m: float | None = None
    used_g: float | None = None
    tray_info_idx: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "slot": self.slot,
            "type": self.type,
            "color": self.color,
            "used_m": self.used_m,
            "used_g": self.used_g,
            "tray_info_idx": self.tray_info_idx,
        }


@dataclass(slots=True)
class Plate:
    """One build plate inside the project."""

    index: int = 1
    prediction_s: int | None = None
    weight_g: float | None = None
    printer_model_id: str | None = None
    nozzle_diameter: float | None = None
    support_used: bool | None = None
    objects: list[str] = field(default_factory=list)
    filaments: list[Filament] = field(default_factory=list)
    preview_path: str | None = None
    raw: dict[str, str] = field(default_factory=dict)

    @property
    def total_weight_g(self) -> float | None:
        """Filament weight for this plate, preferring the per-slot sum."""
        per_slot = [f.used_g for f in self.filaments if f.used_g is not None]
        if per_slot:
            return round(sum(per_slot), 2)
        return self.weight_g

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "prediction_s": self.prediction_s,
            "weight_g": self.total_weight_g,
            "printer_model_id": self.printer_model_id,
            "nozzle_diameter": self.nozzle_diameter,
            "support_used": self.support_used,
            "objects": self.objects,
            "filaments": [f.as_dict() for f in self.filaments],
            "preview_path": self.preview_path,
            "raw": self.raw,
        }


@dataclass(slots=True)
class SlicedProject:
    """Everything this parser could recover from one sliced 3MF."""

    source: str
    parser_version: int = PARSER_VERSION
    slicer: str | None = None
    slicer_version: str | None = None
    plates: list[Plate] = field(default_factory=list)
    settings: dict[str, Any] = field(default_factory=dict)
    raw_settings: dict[str, Any] = field(default_factory=dict)
    object_names: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def primary_plate(self) -> Plate | None:
        return self.plates[0] if self.plates else None

    @property
    def estimated_time_s(self) -> int | None:
        """Summed print prediction across plates."""
        values = [p.prediction_s for p in self.plates if p.prediction_s]
        return sum(values) if values else None

    @property
    def total_weight_g(self) -> float | None:
        values = [p.total_weight_g for p in self.plates if p.total_weight_g]
        return round(sum(values), 2) if values else None

    @property
    def filament_types(self) -> list[str]:
        seen: list[str] = []
        for plate in self.plates:
            for fil in plate.filaments:
                if fil.type and fil.type not in seen:
                    seen.append(fil.type)
        if not seen:
            declared = self.settings.get("filament_type")
            if isinstance(declared, list):
                seen = [str(v) for v in declared if v]
            elif declared:
                seen = [str(declared)]
        return seen

    def as_dict(self) -> dict[str, Any]:
        """Shape written next to the 3MF as JSON, so it survives without the app."""
        return {
            "parser_version": self.parser_version,
            "source": self.source,
            "slicer": self.slicer,
            "slicer_version": self.slicer_version,
            "summary": {
                "estimated_time_s": self.estimated_time_s,
                "total_weight_g": self.total_weight_g,
                "filament_types": self.filament_types,
                "plate_count": len(self.plates),
                "objects": self.object_names,
            },
            "settings": self.settings,
            "plates": [p.as_dict() for p in self.plates],
            "raw_settings": self.raw_settings,
            "warnings": self.warnings,
        }


def _to_float(value: Any) -> float | None:
    try:
        return float(str(value).strip().rstrip("%"))
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    number = _to_float(value)
    return int(number) if number is not None else None


def _to_bool(value: Any) -> bool | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"1", "true", "yes"}:
        return True
    if text in {"0", "false", "no"}:
        return False
    return None


def _first(value: Any) -> Any:
    """Bambu stores many per-extruder settings as single-element lists."""
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _parse_slice_info(data: bytes, result: SlicedProject) -> None:
    root = ElementTree.fromstring(data)

    for item in root.iterfind("./header/header_item"):
        key, value = item.get("key", ""), item.get("value")
        if key == "X-BBL-Client-Type":
            result.slicer = value
        elif key == "X-BBL-Client-Version":
            result.slicer_version = value

    for node in root.iterfind("./plate"):
        raw = {
            m.get("key", ""): m.get("value", "")
            for m in node.iterfind("./metadata")
            if m.get("key")
        }
        plate = Plate(
            index=_to_int(raw.get("index")) or 1,
            prediction_s=_to_int(raw.get("prediction")),
            weight_g=_to_float(raw.get("weight")),
            printer_model_id=raw.get("printer_model_id"),
            nozzle_diameter=_to_float(raw.get("nozzle_diameter")),
            support_used=_to_bool(raw.get("support_used")),
            raw=raw,
        )
        for obj in node.iterfind("./object"):
            name = obj.get("name")
            # `skipped` objects are on the plate but excluded from the print.
            if name and obj.get("skipped", "false").lower() != "true":
                plate.objects.append(name)
        for fil in node.iterfind("./filament"):
            plate.filaments.append(
                Filament(
                    slot=_to_int(fil.get("id")),
                    type=fil.get("type"),
                    color=fil.get("color"),
                    used_m=_to_float(fil.get("used_m")),
                    used_g=_to_float(fil.get("used_g")),
                    tray_info_idx=fil.get("tray_info_idx"),
                )
            )
        result.plates.append(plate)

    result.plates.sort(key=lambda p: p.index)


def _parse_project_settings(data: bytes, result: SlicedProject) -> None:
    raw = json.loads(data.decode("utf-8", errors="replace"))
    if not isinstance(raw, dict):
        raise ValueError("project_settings.config is not a JSON object")

    result.raw_settings = raw
    for source_key, friendly in HIGHLIGHT_SETTINGS.items():
        if source_key not in raw:
            continue
        value = raw[source_key]
        # filament_type is genuinely a per-extruder list; keep it whole.
        result.settings[friendly] = value if friendly == "filament_type" else _first(value)


def _parse_model_objects(data: bytes, result: SlicedProject) -> None:
    root = ElementTree.fromstring(data)
    ns = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
    for obj in root.iterfind(".//m:resources/m:object", ns):
        name = obj.get("name")
        if name and name not in result.object_names:
            result.object_names.append(name)


def parse_3mf(path: str | Path) -> SlicedProject:
    """Parse a sliced 3MF. Never raises for content problems, only for unreadable files."""
    path = Path(path)
    result = SlicedProject(source=path.name)

    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())

        for member, parser, label in (
            (SLICE_INFO, _parse_slice_info, "slice info"),
            (PROJECT_SETTINGS, _parse_project_settings, "project settings"),
            (MODEL_PATH, _parse_model_objects, "model objects"),
        ):
            if member not in names:
                result.warnings.append(f"missing {member} — no {label} available")
                continue
            try:
                parser(archive.read(member), result)
            except Exception as exc:  # noqa: BLE001 — fail soft, record, carry on
                result.warnings.append(f"could not parse {member}: {exc}")

        for plate in result.plates:
            candidate = f"Metadata/plate_{plate.index}.png"
            if candidate in names:
                plate.preview_path = candidate

        # A plate-less file still deserves its preview, if one exists.
        if not result.plates and "Metadata/plate_1.png" in names:
            result.plates.append(Plate(index=1, preview_path="Metadata/plate_1.png"))

    if result.object_names:
        for plate in result.plates:
            if not plate.objects:
                plate.objects = list(result.object_names)
    else:
        result.object_names = [name for plate in result.plates for name in plate.objects]

    return result


def extract_preview(path: str | Path, destination: str | Path, plate_index: int = 1) -> Path | None:
    """Pull one plate preview PNG out of the 3MF. Returns None when absent."""
    path, destination = Path(path), Path(destination)
    member = f"Metadata/plate_{plate_index}.png"
    with zipfile.ZipFile(path) as archive:
        if member not in archive.namelist():
            return None
        payload = archive.read(member)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)
    return destination


def format_duration(seconds: int | None) -> str:
    """`4521` -> `1h 15m`. Used in the UI and in publish templates."""
    if not seconds:
        return "unknown"
    hours, remainder = divmod(int(seconds), 3600)
    minutes = remainder // 60
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"


def main(argv: list[str] | None = None) -> int:
    """CLI for milestone M1: validate the format assumptions against real files."""
    parser = argparse.ArgumentParser(description="Parse sliced 3MF files.")
    parser.add_argument("files", nargs="+", type=Path, help="one or more .3mf files")
    parser.add_argument("--json", action="store_true", help="dump the full parse as JSON")
    parser.add_argument(
        "--extract-previews",
        type=Path,
        metavar="DIR",
        help="write each file's plate previews into DIR",
    )
    args = parser.parse_args(argv)

    exit_code = 0
    for target in args.files:
        if not target.exists():
            print(f"!! {target}: not found", file=sys.stderr)
            exit_code = 1
            continue
        try:
            parsed = parse_3mf(target)
        except (zipfile.BadZipFile, OSError) as exc:
            print(f"!! {target}: {exc}", file=sys.stderr)
            exit_code = 1
            continue

        if args.json:
            print(json.dumps(parsed.as_dict(), indent=2, ensure_ascii=False))
        else:
            slicer = f"{parsed.slicer or '?'} {parsed.slicer_version or ''}".strip()
            print(f"\n== {target.name}  [{slicer}]")
            print(f"   time     {format_duration(parsed.estimated_time_s)}")
            print(f"   weight   {parsed.total_weight_g or '?'} g")
            print(f"   filament {', '.join(parsed.filament_types) or '?'}")
            print(f"   objects  {', '.join(parsed.object_names) or '?'}")
            for key in ("layer_height", "wall_count", "infill_density", "infill_pattern"):
                if key in parsed.settings:
                    print(f"   {key:<16} {parsed.settings[key]}")
            for warning in parsed.warnings:
                print(f"   warn: {warning}")

        if args.extract_previews:
            for plate in parsed.plates:
                out = args.extract_previews / f"{target.stem}_plate{plate.index}.png"
                if extract_preview(target, out, plate.index):
                    print(f"   preview -> {out}")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
