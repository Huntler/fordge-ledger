"""The demo library, and the auto-ingest it depends on.

The seeder writes only durable files, so these tests double as a check that a
library assembled entirely by hand comes up correctly.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.demo import seed_library
from app.main import create_app

from .conftest import write_sliced_3mf


@pytest.fixture
def demo_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("FORGE_LIBRARY_PATH", str(tmp_path / "library"))
    monkeypatch.setenv("FORGE_DATA_PATH", str(tmp_path / "data"))
    monkeypatch.setenv("FORGE_WATCH_ENABLED", "false")
    monkeypatch.setenv("FORGE_DEMO_SEED", "true")
    get_settings.cache_clear()

    with TestClient(create_app()) as client:
        _settle(client)
        yield client
    get_settings.cache_clear()


def _settle(client: TestClient, attempts: int = 200) -> None:
    """The boot scan runs through the job queue, so wait for it."""
    import time

    for _ in range(attempts):
        jobs = client.get("/api/jobs").json()
        if jobs and all(j["status"] in {"done", "failed", "cancelled"} for j in jobs):
            return
        time.sleep(0.05)
    raise AssertionError("boot scan did not settle")


# ------------------------------------------------------------------ seeding


def test_seeding_produces_a_browsable_library(demo_client: TestClient):
    projects = demo_client.get("/api/projects").json()

    assert {p["slug"] for p in projects} == {
        "desk-organizer",
        "cable-clip",
        "hinged-box",
        "planter-pot",
    }
    # Varied statuses, so the filters have something to filter.
    assert {p["status"] for p in projects} == {"published", "testing", "designing", "idea"}


def test_seeded_prints_are_ingested_with_their_lifecycle(demo_client: TestClient):
    prints = demo_client.get("/api/prints").json()
    by_status: dict[str, int] = {}
    for record in prints:
        by_status[record["status"]] = by_status.get(record["status"], 0) + 1

    assert by_status == {"done": 2, "failed": 1, "queued": 1, "printing": 1}

    # Settings came from parsing the 3MF, not from the seed file.
    tray = next(p for p in prints if "tray-v2" in p["name"])
    assert tray["estimated_s"] == 4521
    assert tray["settings"]["infill_pattern"] == "gyroid"
    assert tray["weight_g"] == 22.73


def test_seeded_failure_log_is_populated(demo_client: TestClient):
    failures = demo_client.get("/api/prints/failures").json()

    assert len(failures) == 1
    assert "lifted" in failures[0]["failure_reason"]
    assert failures[0]["failure_fix"].startswith("5mm brim")


def test_seeded_project_has_versions_images_and_a_draft(demo_client: TestClient):
    projects = demo_client.get("/api/projects").json()
    desk = next(p for p in projects if p["slug"] == "desk-organizer")
    detail = demo_client.get(f"/api/projects/{desk['id']}").json()

    assert detail["versions"][0]["label"] == "pre-fillet"
    assert len(detail["models"]) == 2
    assert detail["remix_of"][0]["author"] == "someone"
    # Photos plus the plate previews pulled out of the ingested 3MFs.
    assert {i["category"] for i in detail["images"]} >= {"photo", "plate"}

    draft = demo_client.get(f"/api/projects/{desk['id']}/publish").json()
    assert draft["category"] == "Household"
    assert draft["description"].startswith("# Desk Organizer")


def test_seeded_project_demonstrates_variants_and_sources(demo_client: TestClient):
    projects = demo_client.get("/api/projects").json()
    desk = next(p for p in projects if p["slug"] == "desk-organizer")

    images = {
        i["rel_path"]: i for i in demo_client.get(f"/api/projects/{desk['id']}/images").json()
    }
    cover = images["images/photos/on-the-desk.png"]
    assert cover["variant"] == "web"
    assert cover["source_path"] == "images/sources/on-the-desk.psd"
    assert images["images/photos/on-the-desk-mobile.png"]["variant"] == "mobile"
    # Paired by filename without being named in project.yaml.
    assert images["images/photos/close-up.png"]["source_path"] == "images/sources/close-up.pxd"

    sources = demo_client.get(f"/api/projects/{desk['id']}/sources").json()
    assert {s["rel_path"] for s in sources["models"]} == {
        "models/sources/tray.step",
        "models/sources/lid.scad",
    }
    assert {s["rel_path"] for s in sources["images"]} == {
        "images/sources/on-the-desk.psd",
        "images/sources/close-up.pxd",
    }


def test_seeded_unfiled_file_is_surfaced(demo_client: TestClient):
    projects = demo_client.get("/api/projects").json()
    clip = next(p for p in projects if p["slug"] == "cable-clip")

    assert clip["unfiled_count"] == 1
    detail = demo_client.get(f"/api/projects/{clip['id']}").json()
    assert detail["unfiled"][0]["rel_path"] == "models/sources/clip-v3-experiment.step"


def test_seeding_refuses_to_touch_a_populated_library(demo_client: TestClient):
    state = demo_client.app.state.app_state  # type: ignore[attr-defined]

    assert state.seed_demo() == []


def test_seed_is_idempotent_on_disk(tmp_path: Path):
    root = tmp_path / "library"
    first = seed_library(root)
    marker = root / "desk-organizer" / "notes.md"
    marker.write_text("edited by hand", encoding="utf-8")

    # Re-seeding the same root rewrites the samples; the guard against that
    # lives in seed_demo(), which is what startup actually calls.
    seed_library(root)
    assert sorted(first) == sorted(p.name for p in root.iterdir() if p.is_dir())


# ------------------------------------------------------- auto-ingest on scan


def test_a_3mf_copied_in_by_hand_becomes_a_print_job(demo_client: TestClient):
    """The plan's "drop in a sliced 3MF" has to work through the filesystem too."""
    projects = demo_client.get("/api/projects").json()
    planter = next(p for p in projects if p["slug"] == "planter-pot")
    assert planter["print_count"] == 0

    library = Path(demo_client.get("/api/health").json()["library_path"])
    write_sliced_3mf(
        library / "planter-pot" / "prints" / "2026-08-06_pot.gcode.3mf",
        object_name="pot.stl",
        prediction=7200,
    )

    demo_client.post(f"/api/projects/{planter['id']}/rescan")

    prints = demo_client.get("/api/prints", params={"project_id": planter["id"]}).json()
    assert len(prints) == 1
    assert prints[0]["estimated_s"] == 7200
    assert prints[0]["status"] == "queued"
    # And the sidecar was written next to it.
    assert (library / "planter-pot" / "prints" / "2026-08-06_pot.json").is_file()


def test_rescanning_does_not_reparse_unchanged_prints(demo_client: TestClient):
    """Auto-ingest runs on every scan, so it must skip files it already knows."""
    projects = demo_client.get("/api/projects").json()
    desk = next(p for p in projects if p["slug"] == "desk-organizer")
    library = Path(demo_client.get("/api/health").json()["library_path"])
    sidecar = library / "desk-organizer" / "prints" / "2026-07-18_tray-v2.json"

    before = sidecar.stat().st_mtime_ns
    demo_client.post(f"/api/projects/{desk['id']}/rescan")

    assert sidecar.stat().st_mtime_ns == before


def test_a_reslice_is_picked_up(demo_client: TestClient):
    projects = demo_client.get("/api/projects").json()
    desk = next(p for p in projects if p["slug"] == "desk-organizer")
    library = Path(demo_client.get("/api/health").json()["library_path"])
    threemf = library / "desk-organizer" / "prints" / "2026-07-18_tray-v2.gcode.3mf"

    print_id = next(
        p["id"]
        for p in demo_client.get("/api/prints", params={"project_id": desk["id"]}).json()
        if "tray-v2" in p["name"]
    )

    write_sliced_3mf(threemf, object_name="tray_v2.stl", prediction=6000, weight=30.0)
    demo_client.post(f"/api/projects/{desk['id']}/rescan")

    record = demo_client.get(f"/api/prints/{print_id}").json()
    assert record["estimated_s"] == 6000
    # Re-slicing must not discard the lifecycle state you recorded.
    assert record["status"] == "done"
    assert record["actual_s"] == 4740
