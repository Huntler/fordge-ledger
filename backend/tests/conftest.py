"""Shared fixtures.

The 3MF builder lives in `app.demo` and is shared with the demo-library seeder,
so the fixture the parser is tested against and the sample data you click
through in a throwaway container can never drift apart.

Neither is a substitute for running the M1 CLI over your own files:
`forge-parse3mf ~/prints/*.3mf`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.demo import build_sliced_3mf, demo_png, slice_info_xml

# Rendered with the default values, ready to drop into an archive as-is.
SLICE_INFO_TEMPLATE = slice_info_xml()


def _png_bytes(width: int = 8, height: int = 8) -> bytes:
    return demo_png((width, height))


def write_sliced_3mf(path: Path, **kwargs) -> Path:
    """Write a 3MF resembling a Bambu Studio export."""
    return build_sliced_3mf(path, **kwargs)


@pytest.fixture
def sliced_3mf(tmp_path: Path) -> Path:
    return write_sliced_3mf(tmp_path / "tray_v2.gcode.3mf")


@pytest.fixture
def library(tmp_path: Path) -> Path:
    root = tmp_path / "library"
    root.mkdir()
    return root
