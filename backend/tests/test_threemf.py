from __future__ import annotations

import json
import zipfile
from pathlib import Path

from app.services.threemf import (
    PARSER_VERSION,
    extract_preview,
    format_duration,
    parse_3mf,
)

from .conftest import SLICE_INFO_TEMPLATE, write_sliced_3mf


def test_parses_headline_numbers(sliced_3mf: Path):
    parsed = parse_3mf(sliced_3mf)

    assert parsed.parser_version == PARSER_VERSION
    assert parsed.slicer_version == "01.09.00.70"
    assert parsed.estimated_time_s == 4521
    assert parsed.total_weight_g == 22.73
    assert parsed.filament_types == ["PLA"]
    assert not parsed.warnings


def test_skipped_objects_are_excluded(sliced_3mf: Path):
    plate = parse_3mf(sliced_3mf).primary_plate
    assert plate is not None
    assert plate.objects == ["tray.stl"]


def test_highlight_settings_are_unwrapped_from_per_extruder_lists(sliced_3mf: Path):
    settings = parse_3mf(sliced_3mf).settings

    assert settings["layer_height"] == "0.2"
    assert settings["infill_density"] == "15%"
    assert settings["nozzle_temperature"] == "220"  # unwrapped from ["220"]
    assert settings["filament_type"] == ["PLA"]  # genuinely per-extruder, kept whole


def test_unknown_keys_survive_in_raw_settings(sliced_3mf: Path):
    raw = parse_3mf(sliced_3mf).raw_settings
    assert raw["some_future_bambu_key"] == "kept in raw_settings"


def test_missing_slice_info_fails_soft(tmp_path: Path):
    target = write_sliced_3mf(tmp_path / "bare.3mf", slice_info=None)

    parsed = parse_3mf(target)

    assert parsed.estimated_time_s is None
    assert any("slice_info" in w for w in parsed.warnings)
    # The plate preview is still worth having.
    assert parsed.plates[0].preview_path == "Metadata/plate_1.png"
    # And the object name still comes from the model.
    assert parsed.object_names == ["tray.stl"]


def test_malformed_settings_fails_soft(tmp_path: Path):
    target = tmp_path / "broken.3mf"
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("Metadata/slice_info.config", SLICE_INFO_TEMPLATE)
        archive.writestr("Metadata/project_settings.config", "{not json")

    parsed = parse_3mf(target)

    assert parsed.estimated_time_s == 4521  # the readable half still parsed
    assert any("project_settings" in w for w in parsed.warnings)


def test_preview_extraction(sliced_3mf: Path, tmp_path: Path):
    out = extract_preview(sliced_3mf, tmp_path / "out" / "cover.png")
    assert out is not None and out.read_bytes().startswith(b"\x89PNG")

    missing = write_sliced_3mf(tmp_path / "nopreview.3mf", with_preview=False)
    assert extract_preview(missing, tmp_path / "none.png") is None


def test_sidecar_json_is_serialisable(sliced_3mf: Path):
    payload = parse_3mf(sliced_3mf).as_dict()
    round_tripped = json.loads(json.dumps(payload))

    assert round_tripped["summary"]["estimated_time_s"] == 4521
    assert round_tripped["plates"][0]["filaments"][0]["type"] == "PLA"


def test_format_duration():
    assert format_duration(4521) == "1h 15m"
    assert format_duration(3600) == "1h"
    assert format_duration(600) == "10m"
    assert format_duration(None) == "unknown"
