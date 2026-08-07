"""End-to-end tests over the real app: routers, services, SQLite and the folders."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app

from .conftest import write_sliced_3mf


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("FORGE_LIBRARY_PATH", str(tmp_path / "library"))
    monkeypatch.setenv("FORGE_DATA_PATH", str(tmp_path / "data"))
    monkeypatch.setenv("FORGE_WATCH_ENABLED", "false")
    monkeypatch.setenv("FORGE_OLLAMA_URL", "")
    get_settings.cache_clear()

    with TestClient(create_app()) as test_client:
        yield test_client
    get_settings.cache_clear()


@pytest.fixture
def project(client: TestClient) -> dict:
    response = client.post("/api/projects", json={"title": "Desk Organizer", "tags": ["office"]})
    assert response.status_code == 201
    return response.json()


def library_root(client: TestClient) -> Path:
    return Path(client.get("/api/health").json()["library_path"])


# ------------------------------------------------------------------- basics


def test_health(client: TestClient):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["llm_configured"] is False


def test_create_and_fetch_project(client: TestClient, project: dict):
    body = client.get(f"/api/projects/{project['id']}").json()

    assert body["title"] == "Desk Organizer"
    assert body["tags"] == ["office"]
    assert body["status"] == "idea"
    assert body["models"] == []


def test_update_writes_through_to_project_yaml(client: TestClient, project: dict):
    response = client.patch(
        f"/api/projects/{project['id']}",
        json={"status": "testing", "tags": ["office", "parametric"], "notes": "# Notes\n\nhinge"},
    )
    assert response.status_code == 200

    directory = library_root(client) / "desk-organizer"
    doc = yaml.safe_load((directory / "project.yaml").read_text())
    assert doc["status"] == "testing"
    assert doc["tags"] == ["office", "parametric"]
    assert (directory / "notes.md").read_text() == "# Notes\n\nhinge"


def test_retitling_moves_the_folder(client: TestClient, project: dict):
    client.patch(f"/api/projects/{project['id']}", json={"title": "Pen Caddy"})

    root = library_root(client)
    assert (root / "pen-caddy").is_dir()
    assert not (root / "desk-organizer").exists()
    # Same ULID, new folder.
    assert client.get(f"/api/projects/{project['id']}").json()["title"] == "Pen Caddy"


def test_project_filters(client: TestClient, project: dict):
    client.post("/api/projects", json={"title": "Cable Clip", "tags": ["desk"]})

    assert len(client.get("/api/projects").json()) == 2
    assert len(client.get("/api/projects", params={"q": "caddy"}).json()) == 0
    assert len(client.get("/api/projects", params={"q": "Cable"}).json()) == 1
    assert len(client.get("/api/projects", params={"tag": "office"}).json()) == 1
    assert client.get("/api/projects/tags").json() == [
        {"tag": "desk", "count": 1},
        {"tag": "office", "count": 1},
    ]


def test_sort_by_status_orders_published_first(client: TestClient, project: dict):
    # `project` comes in as "idea"; add one of each other status by hand.
    shelved = client.post("/api/projects", json={"title": "Shelved Thing"}).json()
    client.patch(f"/api/projects/{shelved['id']}", json={"status": "shelved"})
    ready = client.post("/api/projects", json={"title": "Ready Thing"}).json()
    client.patch(f"/api/projects/{ready['id']}", json={"status": "ready"})
    published = client.post("/api/projects", json={"title": "Published Thing"}).json()
    client.patch(f"/api/projects/{published['id']}", json={"status": "published"})

    titles = [p["title"] for p in client.get("/api/projects", params={"sort": "status"}).json()]
    assert titles == ["Published Thing", "Ready Thing", "Desk Organizer", "Shelved Thing"]


def test_invalid_status_is_rejected(client: TestClient, project: dict):
    response = client.patch(f"/api/projects/{project['id']}", json={"status": "nonsense"})
    assert response.status_code == 422


def test_missing_project_is_404(client: TestClient):
    assert client.get("/api/projects/NOPE").status_code == 404


# ----------------------------------------------------------------- deleting


def test_deleting_moves_the_folder_to_trash(client: TestClient, project: dict):
    directory = library_root(client) / "desk-organizer"
    (directory / "models" / "tray.stl").write_bytes(b"solid tray\n")

    result = client.delete(f"/api/projects/{project['id']}").json()

    assert result["title"] == "Desk Organizer"
    assert result["purged"] is False
    # Gone from the library, but the work itself is not destroyed.
    assert not directory.exists()
    assert client.get(f"/api/projects/{project['id']}").status_code == 404
    assert client.get("/api/projects").json() == []

    trashed = Path(result["trashed_to"])
    assert trashed.is_dir()
    assert (trashed / "models" / "tray.stl").read_bytes() == b"solid tray\n"
    assert (trashed / "project.yaml").is_file()


def test_a_trashed_project_is_not_rescanned_as_a_project(client: TestClient, project: dict):
    client.delete(f"/api/projects/{project['id']}")

    client.post("/api/rescan")
    _wait_for_jobs(client)

    # _trash is reserved, so a deleted project does not reappear.
    assert client.get("/api/projects").json() == []


def test_a_trashed_project_can_be_restored_by_moving_the_folder_back(
    client: TestClient, project: dict
):
    """The payoff of folders-being-the-product: undo is a drag in Finder."""
    root = library_root(client)
    trashed = Path(client.delete(f"/api/projects/{project['id']}").json()["trashed_to"])

    trashed.rename(root / "desk-organizer")
    client.post("/api/rescan")
    _wait_for_jobs(client)

    restored = client.get("/api/projects").json()
    assert len(restored) == 1
    # Same ULID, because project.yaml went with it.
    assert restored[0]["id"] == project["id"]
    assert restored[0]["title"] == "Desk Organizer"


def test_purge_destroys_the_folder(client: TestClient, project: dict):
    directory = library_root(client) / "desk-organizer"

    result = client.delete(f"/api/projects/{project['id']}", params={"purge": True}).json()

    assert result["purged"] is True
    assert result["trashed_to"] is None
    assert not directory.exists()
    assert not (library_root(client) / "_trash").exists()


def test_deleting_a_missing_project_is_404(client: TestClient):
    assert client.delete("/api/projects/NOPE").status_code == 404


# -------------------------------------------------------------- print jobs


def upload_print(client: TestClient, project_id: str, tmp_path: Path, name: str = "tray.3mf"):
    source = write_sliced_3mf(tmp_path / name)
    with source.open("rb") as handle:
        return client.post(
            f"/api/projects/{project_id}/prints",
            files={"file": (name, handle, "application/octet-stream")},
        )


@pytest.mark.parametrize(
    ("uploaded", "stored"),
    [
        ("tray_v2.3mf", "tray-v2.gcode.3mf"),
        ("tray_v2.gcode.3mf", "tray-v2.gcode.3mf"),
        ("Tray V2.GCODE.3MF", "tray-v2.gcode.3mf"),
    ],
)
def test_stored_filename_keeps_one_clean_extension(
    client: TestClient, project: dict, tmp_path: Path, uploaded: str, stored: str
):
    record = upload_print(client, project["id"], tmp_path, uploaded).json()
    assert record["name"].endswith(stored)
    # Dated prefix keeps prints/ chronological when browsed in a file manager.
    assert record["name"][:10].count("-") == 2


def test_uploading_a_sliced_3mf_creates_a_print_job(
    client: TestClient, project: dict, tmp_path: Path
):
    response = upload_print(client, project["id"], tmp_path)
    assert response.status_code == 201

    record = response.json()
    assert record["status"] == "queued"
    assert record["estimated_s"] == 4521
    assert record["weight_g"] == 22.73
    assert record["settings"]["layer_height"] == "0.2"
    assert record["filaments"][0]["type"] == "PLA"
    assert record["cost"] == pytest.approx(0.5, abs=0.01)


def test_ingest_writes_a_sidecar_next_to_the_3mf(client: TestClient, project: dict, tmp_path: Path):
    upload_print(client, project["id"], tmp_path)

    prints_dir = library_root(client) / "desk-organizer" / "prints"
    sidecars = list(prints_dir.glob("*.json"))
    assert len(sidecars) == 1

    payload = json.loads(sidecars[0].read_text())
    assert payload["summary"]["estimated_time_s"] == 4521
    assert payload["job"]["status"] == "queued"
    # Raw slicer profile is kept so nothing is lost to format drift.
    assert payload["raw_settings"]["some_future_bambu_key"] == "kept in raw_settings"


def test_plate_preview_becomes_a_free_thumbnail(client: TestClient, project: dict, tmp_path: Path):
    upload_print(client, project["id"], tmp_path)

    plates = list((library_root(client) / "desk-organizer" / "images" / "plates").glob("*.png"))
    assert len(plates) == 1

    images = client.get(f"/api/projects/{project['id']}/images").json()
    assert any(i["category"] == "plate" for i in images)
    # And the project gets a cover without any work from the user.
    assert client.get(f"/api/projects/{project['id']}").json()["cover_image"].endswith(".png")


def test_print_lifecycle_and_failure_log(client: TestClient, project: dict, tmp_path: Path):
    print_id = upload_print(client, project["id"], tmp_path).json()["id"]

    printing = client.patch(f"/api/prints/{print_id}", json={"status": "printing"}).json()
    assert printing["started"] is not None

    failed = client.patch(
        f"/api/prints/{print_id}",
        json={
            "status": "failed",
            "failure_reason": "corner lifted off the plate",
            "failure_fix": "added a 5mm brim, bed to 60C",
            "actual_s": 1800,
        },
    ).json()
    assert failed["status"] == "failed"
    assert failed["finished"] is not None

    log = client.get("/api/prints/failures").json()
    assert len(log) == 1
    assert log[0]["failure_fix"] == "added a 5mm brim, bed to 60C"


def test_failure_details_reach_the_sidecar_on_disk(
    client: TestClient, project: dict, tmp_path: Path
):
    print_id = upload_print(client, project["id"], tmp_path).json()["id"]
    client.patch(
        f"/api/prints/{print_id}",
        json={"status": "failed", "failure_reason": "warped", "failure_fix": "brim"},
    )

    sidecar = next((library_root(client) / "desk-organizer" / "prints").glob("*.json"))
    job = json.loads(sidecar.read_text())["job"]
    assert (job["status"], job["failure_reason"]) == ("failed", "warped")


def test_re_ingest_preserves_lifecycle_state(client: TestClient, project: dict, tmp_path: Path):
    print_id = upload_print(client, project["id"], tmp_path).json()["id"]
    client.patch(f"/api/prints/{print_id}", json={"status": "done", "actual_s": 4800})

    # Re-slicing the same file must not silently reset a print marked done.
    client.post(f"/api/projects/{project['id']}/prints/ingest")

    record = client.get(f"/api/prints/{print_id}").json()
    assert (record["status"], record["actual_s"]) == ("done", 4800)


def test_requeueing_clears_the_failure_reason(client: TestClient, project: dict, tmp_path: Path):
    print_id = upload_print(client, project["id"], tmp_path).json()["id"]
    client.patch(f"/api/prints/{print_id}", json={"status": "failed", "failure_reason": "warped"})

    record = client.patch(f"/api/prints/{print_id}", json={"status": "queued"}).json()

    assert record["failure_reason"] is None
    assert record["finished"] is None


def test_print_stats(client: TestClient, project: dict, tmp_path: Path):
    first = upload_print(client, project["id"], tmp_path, "a.3mf").json()["id"]
    second = upload_print(client, project["id"], tmp_path, "b.3mf").json()["id"]
    client.patch(f"/api/prints/{first}", json={"status": "done"})
    client.patch(f"/api/prints/{second}", json={"status": "failed"})

    stats = client.get("/api/prints/stats").json()
    assert stats["counts"] == {"queued": 0, "printing": 0, "done": 1, "failed": 1}
    assert stats["success_rate"] == 0.5


def test_non_sliced_upload_is_rejected(client: TestClient, project: dict):
    response = client.post(
        f"/api/projects/{project['id']}/prints",
        files={"file": ("model.stl", b"solid\n", "application/octet-stream")},
    )
    assert response.status_code == 422


def test_delete_print_removes_record_and_files(client: TestClient, project: dict, tmp_path: Path):
    response = upload_print(client, project["id"], tmp_path)
    print_id = response.json()["id"]

    # Verify the file is on disk before deletion.
    prints_dir = library_root(client) / "desk-organizer" / "prints"
    threemf = list(prints_dir.glob("*.gcode.3mf"))[0]
    assert threemf.exists()

    # Delete with remove_files=True.
    response = client.delete(f"/api/prints/{print_id}?remove_files=true")
    assert response.status_code == 204

    # Record is gone from the database.
    assert client.get(f"/api/prints/{print_id}").status_code == 404
    assert not any(
        p["id"] == print_id
        for p in client.get(f"/api/projects/{project['id']}").json()["prints"]
    )

    # Files are also gone from disk.
    assert not threemf.exists()


# ---------------------------------------------------------------- versions


def test_snapshot_copies_the_models_tree(client: TestClient, project: dict):
    directory = library_root(client) / "desk-organizer"
    (directory / "models" / "tray").mkdir(parents=True)
    source = directory / "models" / "tray" / "tray.stl"
    source.write_bytes(b"solid tray\n")

    response = client.post(
        f"/api/projects/{project['id']}/versions",
        json={"label": "initial", "note": "first working print"},
    )
    assert response.status_code == 201

    folder = response.json()["folder"]
    assert folder.startswith("v001__") and folder.endswith("__initial")

    copied = directory / "_versions" / folder / "models" / "tray" / "tray.stl"
    assert copied.read_bytes() == b"solid tray\n"
    assert (
        yaml.safe_load((directory / "_versions" / folder / "version.yaml").read_text())["note"]
        == "first working print"
    )


def test_re_exporting_a_model_does_not_rewrite_history(client: TestClient, project: dict):
    """CAD tools re-export by truncating in place. A snapshot must not follow."""
    directory = library_root(client) / "desk-organizer"
    (directory / "models").mkdir(exist_ok=True)
    source = directory / "models" / "tray.stl"
    source.write_bytes(b"version one")

    folder = client.post(f"/api/projects/{project['id']}/versions", json={"label": "v1"}).json()[
        "folder"
    ]
    snapshot = directory / "_versions" / folder / "models" / "tray.stl"
    assert snapshot.stat().st_ino != source.stat().st_ino

    # Exactly what Fusion or OpenSCAD does on export: open the same path "wb".
    with source.open("wb") as handle:
        handle.write(b"version two, reinforced hinge")

    assert snapshot.read_bytes() == b"version one"


def test_snapshot_numbers_increment(client: TestClient, project: dict):
    directory = library_root(client) / "desk-organizer"
    (directory / "models" / "part.stl").write_bytes(b"a")

    client.post(f"/api/projects/{project['id']}/versions", json={"label": "one"})
    second = client.post(f"/api/projects/{project['id']}/versions", json={"label": "two"}).json()

    assert second["number"] == 2
    assert second["folder"].startswith("v002__")


def test_empty_models_folder_cannot_be_snapshotted(client: TestClient, project: dict):
    response = client.post(f"/api/projects/{project['id']}/versions", json={"label": "nope"})
    assert response.status_code == 422


def test_restore_backs_up_current_work_first(client: TestClient, project: dict):
    directory = library_root(client) / "desk-organizer"
    (directory / "models" / "part.stl").write_bytes(b"version one")
    first = client.post(f"/api/projects/{project['id']}/versions", json={"label": "v1"}).json()

    (directory / "models" / "part.stl").write_bytes(b"version two")

    result = client.post(f"/api/projects/{project['id']}/versions/{first['folder']}/restore").json()

    assert (directory / "models" / "part.stl").read_bytes() == b"version one"
    # The work being replaced was snapshotted, not discarded.
    backup = directory / "_versions" / result["backup"] / "models" / "part.stl"
    assert backup.read_bytes() == b"version two"


@pytest.mark.parametrize("folder", ["../../etc", "..", "sub/../../escape"])
def test_version_folder_traversal_is_refused(client: TestClient, project: dict, folder: str):
    """`folder` arrives from the client, so it must never escape _versions/."""
    versions = client.app.state.app_state.versions  # type: ignore[attr-defined]

    with pytest.raises(ValueError, match="invalid version folder"):
        versions.delete(project["id"], folder)


# ----------------------------------------------------------------- publish


def test_default_templates_and_snippets_are_seeded(client: TestClient):
    templates = {t["name"] for t in client.get("/api/templates").json()}
    snippets = {s["name"] for s in client.get("/api/snippets").json()}

    assert "default" in templates
    assert {"license", "warping", "tipjar"} <= snippets


def test_template_placeholders_fill_from_ingested_print_data(
    client: TestClient, project: dict, tmp_path: Path
):
    upload_print(client, project["id"], tmp_path)
    client.patch(
        f"/api/projects/{project['id']}",
        json={"license": "CC-BY-4.0", "notes": "A tray that holds pens.\n\nMore detail here."},
    )

    markdown = client.post(
        f"/api/projects/{project['id']}/publish/preview", json={"template": "default"}
    ).json()["markdown"]

    assert "# Desk Organizer" in markdown
    assert "A tray that holds pens." in markdown
    assert "**Layer height:** 0.2mm" in markdown
    assert "**Walls:** 3" in markdown
    assert "**Infill:** 15% gyroid" in markdown
    assert "**Print time:** 1h 15m" in markdown
    assert "Bambu Lab P1S" in markdown
    # Snippets expand, and their own placeholders resolve too.
    assert "released under CC-BY-4.0" in markdown
    assert "{{" not in markdown


def test_unknown_placeholders_stay_visible(client: TestClient, project: dict):
    client.put("/api/templates", json={"name": "odd", "body": "Value: {{nope.missing}}"})

    markdown = client.post(
        f"/api/projects/{project['id']}/publish/preview", json={"template": "odd"}
    ).json()["markdown"]

    assert "⟨nope.missing?⟩" in markdown


def test_draft_is_stored_as_plain_files(client: TestClient, project: dict):
    client.put(
        f"/api/projects/{project['id']}/publish",
        json={
            "title": "Desk Organizer v2",
            "description": "# Hello\n\nBody text.",
            "tags": ["office", "storage"],
            "category": "Household",
            "license": "CC-BY-4.0",
        },
    )

    publish_dir = library_root(client) / "desk-organizer" / "publish" / "makerworld"
    assert (publish_dir / "description.md").read_text() == "# Hello\n\nBody text."
    fields = yaml.safe_load((publish_dir / "fields.yaml").read_text())
    assert fields["category"] == "Household"
    assert fields["tags"] == ["office", "storage"]

    reloaded = client.get(f"/api/projects/{project['id']}/publish").json()
    assert reloaded["title"] == "Desk Organizer v2"
    assert reloaded["description"] == "# Hello\n\nBody text."


def test_export_builds_the_package(client: TestClient, project: dict, tmp_path: Path):
    upload_print(client, project["id"], tmp_path)
    images = client.get(f"/api/projects/{project['id']}/images").json()

    client.put(
        f"/api/projects/{project['id']}/publish",
        json={
            "title": "Desk Organizer",
            "description": "# Desk Organizer\n\nPrints in an hour.",
            "tags": ["office"],
            "assets": [images[0]["rel_path"]],
        },
    )
    client.patch(
        f"/api/projects/{project['id']}",
        json={
            "remix_of": [
                {
                    "url": "https://makerworld.com/en/models/12345",
                    "title": "Original Tray",
                    "author": "someone",
                    "license": "CC-BY",
                }
            ]
        },
    )

    result = client.post(f"/api/projects/{project['id']}/publish/export").json()

    publish_dir = library_root(client) / "desk-organizer" / "publish" / "makerworld"
    assert (publish_dir / "description.md").read_text().startswith("# Desk Organizer")
    assert len(result["assets"]) == 1
    assert (publish_dir / "assets" / result["assets"][0]["file"]).is_file()

    fields = yaml.safe_load((publish_dir / "fields.yaml").read_text())
    assert fields["attribution"][0]["author"] == "someone"
    # Print profiles come straight from the ingested 3MF.
    assert fields["print_profiles"][0]["layer_height"] == "0.2"
    assert fields["print_profiles"][0]["time"] == "1h 15m"


def test_polish_is_unavailable_without_a_server(client: TestClient):
    assert client.get("/api/llm/status").json()["available"] is False
    assert client.post("/api/llm/polish", json={"text": "hello"}).status_code == 503


# ------------------------------------------------------------- model sources


def test_scad_source_can_be_edited_in_place(client: TestClient, project: dict):
    upload = client.post(
        f"/api/projects/{project['id']}/models/sources",
        files={"file": ("clip.scad", b"cube([10, 10, 10]);", "text/plain")},
    )
    assert upload.status_code == 201
    rel_path = upload.json()["rel_path"]
    assert rel_path == "models/sources/clip.scad"

    edit = client.put(
        f"/api/projects/{project['id']}/models/sources/content",
        params={"rel_path": rel_path},
        content=b"cube([20, 20, 20]);",
    )
    assert edit.status_code == 204

    # Edited in place — same path, new content, no sibling file created.
    saved = client.get(f"/api/projects/{project['id']}/file", params={"rel_path": rel_path})
    assert saved.text == "cube([20, 20, 20]);"
    sources_dir = library_root(client) / "desk-organizer" / "models" / "sources"
    assert [p.name for p in sources_dir.iterdir()] == ["clip.scad"]


def test_editing_a_binary_cad_source_is_rejected(client: TestClient, project: dict):
    upload = client.post(
        f"/api/projects/{project['id']}/models/sources",
        files={"file": ("body.step", b"ISO-10303-21;", "application/octet-stream")},
    )
    rel_path = upload.json()["rel_path"]

    edit = client.put(
        f"/api/projects/{project['id']}/models/sources/content",
        params={"rel_path": rel_path},
        content=b"not a step file",
    )
    assert edit.status_code == 422


def test_editing_a_path_outside_model_sources_is_rejected(client: TestClient, project: dict):
    # Right extension, wrong folder — must not let the editor write anywhere else.
    edit = client.put(
        f"/api/projects/{project['id']}/models/sources/content",
        params={"rel_path": "sneaky.scad"},
        content=b"cube([1, 1, 1]);",
    )
    assert edit.status_code == 422
    assert not (library_root(client) / "desk-organizer" / "sneaky.scad").exists()


def test_exporting_scad_writes_an_stl_next_to_it_and_overwrites(client: TestClient, project: dict):
    upload = client.post(
        f"/api/projects/{project['id']}/models/sources",
        files={"file": ("clip.scad", b"cube([10, 10, 10]);", "text/plain")},
    )
    rel_path = upload.json()["rel_path"]

    export = client.post(
        f"/api/projects/{project['id']}/models/sources/export",
        params={"rel_path": rel_path},
        content=b"solid clip\nendsolid clip\n",
    )
    assert export.status_code == 201
    assert export.json()["rel_path"] == "models/sources/clip.stl"

    stl_path = library_root(client) / "desk-organizer" / "models" / "sources" / "clip.stl"
    assert stl_path.read_bytes() == b"solid clip\nendsolid clip\n"

    # Re-exporting overwrites in place — no clip-2.stl.
    client.post(
        f"/api/projects/{project['id']}/models/sources/export",
        params={"rel_path": rel_path},
        content=b"solid clip v2\nendsolid clip v2\n",
    )
    sources_dir = library_root(client) / "desk-organizer" / "models" / "sources"
    assert sorted(p.name for p in sources_dir.iterdir()) == ["clip.scad", "clip.stl"]
    assert stl_path.read_bytes() == b"solid clip v2\nendsolid clip v2\n"


def test_exporting_a_missing_source_is_rejected(client: TestClient, project: dict):
    export = client.post(
        f"/api/projects/{project['id']}/models/sources/export",
        params={"rel_path": "models/sources/ghost.scad"},
        content=b"solid\nendsolid\n",
    )
    assert export.status_code == 422


def test_deleting_a_model_source_removes_it(client: TestClient, project: dict):
    upload = client.post(
        f"/api/projects/{project['id']}/models/sources",
        files={"file": ("clip.scad", b"cube([10, 10, 10]);", "text/plain")},
    )
    rel_path = upload.json()["rel_path"]

    response = client.delete(
        f"/api/projects/{project['id']}/models/sources", params={"rel_path": rel_path}
    )
    assert response.status_code == 204

    sources_dir = library_root(client) / "desk-organizer" / "models" / "sources"
    assert not (sources_dir / "clip.scad").exists()
    assert client.get(f"/api/projects/{project['id']}/file", params={"rel_path": rel_path}).status_code == 404


def test_deleting_a_missing_model_source_is_a_no_op(client: TestClient, project: dict):
    response = client.delete(
        f"/api/projects/{project['id']}/models/sources",
        params={"rel_path": "models/sources/ghost.scad"},
    )
    assert response.status_code == 204


def test_deleting_outside_model_sources_is_rejected(client: TestClient, project: dict):
    response = client.delete(
        f"/api/projects/{project['id']}/models/sources", params={"rel_path": "notes.md"}
    )
    assert response.status_code == 400
    assert (library_root(client) / "desk-organizer" / "notes.md").exists()


# ------------------------------------------------------------------ images


def test_photo_upload_and_cover_selection(client: TestClient, project: dict):
    from .conftest import _png_bytes

    response = client.post(
        f"/api/projects/{project['id']}/images",
        files={"file": ("My Photo.png", _png_bytes(), "image/png")},
    )
    assert response.status_code == 201
    rel_path = response.json()["rel_path"]
    assert rel_path == "images/photos/my-photo.png"

    client.put(f"/api/projects/{project['id']}/images/cover", json={"rel_path": rel_path})
    assert client.get(f"/api/projects/{project['id']}").json()["cover_image"] == rel_path

    thumb = client.get(f"/api/projects/{project['id']}/thumb", params={"rel_path": rel_path})
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/webp"


def test_corrupt_image_upload_is_rejected(client: TestClient, project: dict):
    response = client.post(
        f"/api/projects/{project['id']}/images",
        files={"file": ("bad.png", b"not a png", "image/png")},
    )
    assert response.status_code == 422
    assert not list((library_root(client) / "desk-organizer" / "images" / "photos").iterdir())


def test_file_serving_refuses_traversal(client: TestClient, project: dict):
    response = client.get(
        f"/api/projects/{project['id']}/file", params={"rel_path": "../../etc/passwd"}
    )
    assert response.status_code == 400


# -------------------------------------------------------------------- jobs


def test_rescan_runs_as_a_job(client: TestClient, project: dict):
    response = client.post("/api/rescan")
    assert response.status_code == 202

    _wait_for_jobs(client)
    kinds = {job["kind"] for job in client.get("/api/jobs").json()}
    assert "library.rescan" in kinds


def test_files_added_by_hand_appear_after_a_rescan(client: TestClient, project: dict):
    directory = library_root(client) / "desk-organizer"
    (directory / "models" / "tray").mkdir(parents=True)
    (directory / "models" / "tray" / "tray.step").write_bytes(b"ISO-10303\n")

    client.post(f"/api/projects/{project['id']}/rescan")

    body = client.get(f"/api/projects/{project['id']}").json()
    assert any(f["rel_path"] == "models/tray/tray.step" for f in body["files"])
    assert body["unfiled"][0]["rel_path"] == "models/tray/tray.step"


def _wait_for_jobs(client: TestClient, attempts: int = 100) -> None:
    import time

    for _ in range(attempts):
        jobs = client.get("/api/jobs").json()
        if jobs and all(job["status"] in {"done", "failed", "cancelled"} for job in jobs):
            return
        time.sleep(0.05)
    raise AssertionError("jobs did not settle")
