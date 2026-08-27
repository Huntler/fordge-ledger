"""Drives content_hash against the fixture shared with forge-scad-editor's
own pytest case and its Vitest case — R10's cross-language agreement, the
same pattern forge-scad-editor uses for R3's tool_use_cases.json.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.utils import content_hash

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "content_hash_cases.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=[c["text"][:20] for c in FIXTURE["cases"]])
def test_content_hash_matches_fixture(case: dict) -> None:
    assert content_hash(case["text"]) == case["hash"]
