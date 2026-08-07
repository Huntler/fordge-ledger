"""Image variants, editable source files, and the models/sources/ folder."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app
from app.services.library import classify

from .conftest import _png_bytes, write_sliced_3mf


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("FORGE_LIBRARY_PATH", str(tmp_path / "library"))
    monkeypatch.setenv("FORGE_DATA_PATH", str(tmp_path / "data"))
    monkeypatch.setenv("FORGE_WATCH_ENABLED", "false")
    get_settings.cache_clear()
    with TestClient(create_app()) as test_client:
        yield test_client
    get_settings.cache_clear()


@pytest.fixture
def project(client: TestClient) -> dict:
    return client.post("/api/projects", json={"title": "Desk Organizer"}).json()


def library_root(client: TestClient) -> Path:
    return Path(client.get("/api/health").json()["library_path"])


def project_yaml(client: TestClient, slug: str = "desk-organizer") -> dict:
    return yaml.safe_load((library_root(client) / slug / "project.yaml").read_text())


# ------------------------------------------------------------- classification


def test_editable_originals_are_their_own_kind(tmp_path: Path):
    for name in ("cover.psd", "cover.pxd", "cover.afphoto", "cover.xcf"):
        assert classify(tmp_path / name) == "image_source"
    # Still distinct from the rendered export and from CAD.
    assert classify(tmp_path / "cover.png") == "image"
    assert classify(tmp_path / "tray.step") == "cad"
    assert classify(tmp_path / "tray.scad") == "cad"


def test_new_project_has_the_source_folders(client: TestClient, project: dict):
    directory = library_root(client) / "desk-organizer"
    assert (directory / "models" / "sources").is_dir()
    assert (directory / "images" / "sources").is_dir()


# ------------------------------------------------------------------ variants


def test_image_uploads_carry_a_variant(client: TestClient, project: dict):
    client.post(
        f"/api/projects/{project['id']}/images",
        files={"file": ("cover-web.png", _png_bytes(), "image/png")},
        data={"variant": "web"},
    )
    client.post(
        f"/api/projects/{project['id']}/images",
        files={"file": ("cover-mobile.png", _png_bytes(), "image/png")},
        data={"variant": "mobile"},
    )

    images = {i["rel_path"]: i for i in client.get(f"/api/projects/{project['id']}/images").json()}
    assert images["images/photos/cover-web.png"]["variant"] == "web"
    assert images["images/photos/cover-mobile.png"]["variant"] == "mobile"


def test_variants_are_recorded_in_project_yaml(client: TestClient, project: dict):
    client.post(
        f"/api/projects/{project['id']}/images",
        files={"file": ("cover-web.png", _png_bytes(), "image/png")},
        data={"variant": "web"},
    )

    entries = project_yaml(client)["images"]
    assert entries == [{"path": "images/photos/cover-web.png", "variant": "web"}]


def test_variant_can_be_changed_and_cleared(client: TestClient, project: dict):
    rel = client.post(
        f"/api/projects/{project['id']}/images",
        files={"file": ("cover.png", _png_bytes(), "image/png")},
        data={"variant": "web"},
    ).json()["rel_path"]

    client.put(
        f"/api/projects/{project['id']}/images/variant",
        json={"rel_path": rel, "variant": "mobile"},
    )
    images = client.get(f"/api/projects/{project['id']}/images").json()
    assert images[0]["variant"] == "mobile"

    # Empty means the one image serves both listings.
    client.put(
        f"/api/projects/{project['id']}/images/variant", json={"rel_path": rel, "variant": ""}
    )
    assert client.get(f"/api/projects/{project['id']}/images").json()[0]["variant"] == ""


def test_unknown_variant_is_rejected(client: TestClient, project: dict):
    response = client.post(
        f"/api/projects/{project['id']}/images",
        files={"file": ("cover.png", _png_bytes(), "image/png")},
        data={"variant": "tablet"},
    )
    assert response.status_code == 422


# ------------------------------------------------------------ image sources


def test_source_file_is_stored_and_linked(client: TestClient, project: dict):
    image = client.post(
        f"/api/projects/{project['id']}/images",
        files={"file": ("cover-web.png", _png_bytes(), "image/png")},
        data={"variant": "web"},
    ).json()["rel_path"]

    response = client.post(
        f"/api/projects/{project['id']}/images/sources",
        files={"file": ("Cover Web.psd", b"8BPS fake photoshop", "application/octet-stream")},
        data={"for_image": image},
    )
    assert response.status_code == 201
    assert response.json()["rel_path"] == "images/sources/cover-web.psd"

    images = client.get(f"/api/projects/{project['id']}/images").json()
    assert images[0]["source_path"] == "images/sources/cover-web.psd"
    assert project_yaml(client)["images"][0]["source"] == "images/sources/cover-web.psd"


def test_source_is_paired_by_filename_when_not_linked_explicitly(client: TestClient, project: dict):
    """Dropping the export and its original together should just work."""
    client.post(
        f"/api/projects/{project['id']}/images",
        files={"file": ("cover-web.png", _png_bytes(), "image/png")},
    )
    client.post(
        f"/api/projects/{project['id']}/images/sources",
        files={"file": ("cover-web.psd", b"8BPS", "application/octet-stream")},
    )

    images = client.get(f"/api/projects/{project['id']}/images").json()
    assert images[0]["source_path"] == "images/sources/cover-web.psd"


def test_a_source_is_not_listed_as_an_image(client: TestClient, project: dict):
    client.post(
        f"/api/projects/{project['id']}/images/sources",
        files={"file": ("loose.psd", b"8BPS", "application/octet-stream")},
    )

    # It is a file and a source, but never something the gallery tries to render.
    assert client.get(f"/api/projects/{project['id']}/images").json() == []
    sources = client.get(f"/api/projects/{project['id']}/sources").json()
    assert [s["rel_path"] for s in sources["images"]] == ["images/sources/loose.psd"]


def test_deleting_an_image_keeps_its_source_unless_asked(client: TestClient, project: dict):
    image = client.post(
        f"/api/projects/{project['id']}/images",
        files={"file": ("cover.png", _png_bytes(), "image/png")},
    ).json()["rel_path"]
    client.post(
        f"/api/projects/{project['id']}/images/sources",
        files={"file": ("cover.psd", b"8BPS", "application/octet-stream")},
    )
    directory = library_root(client) / "desk-organizer"

    client.delete(f"/api/projects/{project['id']}/images", params={"rel_path": image})
    assert (directory / "images" / "sources" / "cover.psd").is_file()

    client.post(
        f"/api/projects/{project['id']}/images",
        files={"file": ("cover.png", _png_bytes(), "image/png")},
    )
    client.delete(
        f"/api/projects/{project['id']}/images",
        params={"rel_path": image, "with_source": True},
    )
    assert not (directory / "images" / "sources" / "cover.psd").exists()


def test_a_deleted_source_stops_being_advertised(client: TestClient, project: dict):
    client.post(
        f"/api/projects/{project['id']}/images",
        files={"file": ("cover.png", _png_bytes(), "image/png")},
    )
    client.post(
        f"/api/projects/{project['id']}/images/sources",
        files={"file": ("cover.psd", b"8BPS", "application/octet-stream")},
    )
    (library_root(client) / "desk-organizer" / "images" / "sources" / "cover.psd").unlink()

    client.post(f"/api/projects/{project['id']}/rescan")

    assert client.get(f"/api/projects/{project['id']}/images").json()[0]["source_path"] == ""


# ------------------------------------------------------------ model sources


def test_cad_sources_land_in_models_sources(client: TestClient, project: dict):
    response = client.post(
        f"/api/projects/{project['id']}/models/sources",
        files={"file": ("Tray Body.step", b"ISO-10303-21;\n", "application/octet-stream")},
    )
    assert response.status_code == 201
    assert response.json()["rel_path"] == "models/sources/tray-body.step"

    sources = client.get(f"/api/projects/{project['id']}/sources").json()
    assert [s["rel_path"] for s in sources["models"]] == ["models/sources/tray-body.step"]


def test_scad_is_accepted_as_a_model_source(client: TestClient, project: dict):
    response = client.post(
        f"/api/projects/{project['id']}/models/sources",
        files={"file": ("tray.scad", b"cube([10,10,10]);\n", "application/octet-stream")},
    )
    assert response.status_code == 201
    assert response.json()["rel_path"] == "models/sources/tray.scad"


def test_an_image_is_refused_as_a_model_source(client: TestClient, project: dict):
    response = client.post(
        f"/api/projects/{project['id']}/models/sources",
        files={"file": ("photo.png", _png_bytes(), "image/png")},
    )
    assert response.status_code == 422


def test_model_sources_show_as_unfiled_until_attached(client: TestClient, project: dict):
    client.post(
        f"/api/projects/{project['id']}/models/sources",
        files={"file": ("tray.step", b"ISO-10303-21;\n", "application/octet-stream")},
    )

    detail = client.get(f"/api/projects/{project['id']}").json()
    assert [u["rel_path"] for u in detail["unfiled"]] == ["models/sources/tray.step"]

    client.post(
        f"/api/projects/{project['id']}/attach",
        json={"model_name": "tray", "files": ["models/sources/tray.step"]},
    )
    detail = client.get(f"/api/projects/{project['id']}").json()
    assert detail["unfiled"] == []
    assert detail["models"][0]["files"] == ["models/sources/tray.step"]


# --------------------------------------------------------------- documents


PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"


def test_documents_are_their_own_kind(tmp_path: Path):
    for name in ("datasheet.pdf", "costs.csv", "manual.docx", "notes.txt"):
        assert classify(tmp_path / name) == "doc"
    for name in ("refs.zip", "panel.dxf", "firmware.bin"):
        assert classify(tmp_path / name) == "misc"
    # Still a dead end for anything nobody asked for.
    assert classify(tmp_path / "installer.exe") == "other"


def test_new_project_has_a_docs_folder(client: TestClient, project: dict):
    assert (library_root(client) / "desk-organizer" / "docs").is_dir()


def test_uploading_a_pdf(client: TestClient, project: dict):
    response = client.post(
        f"/api/projects/{project['id']}/documents",
        files={"file": ("Bearing Datasheet.pdf", PDF, "application/pdf")},
    )
    assert response.status_code == 201
    assert response.json()["rel_path"] == "docs/bearing-datasheet.pdf"

    documents = client.get(f"/api/projects/{project['id']}/documents").json()
    assert [d["rel_path"] for d in documents] == ["docs/bearing-datasheet.pdf"]
    assert documents[0]["kind"] == "doc"

    # Stored byte-for-byte; nothing parses or rewrites an attachment.
    stored = library_root(client) / "desk-organizer" / "docs" / "bearing-datasheet.pdf"
    assert stored.read_bytes() == PDF


def test_documents_appear_on_the_project(client: TestClient, project: dict):
    client.post(
        f"/api/projects/{project['id']}/documents",
        files={"file": ("manual.pdf", PDF, "application/pdf")},
    )

    detail = client.get(f"/api/projects/{project['id']}").json()
    assert [d["rel_path"] for d in detail["documents"]] == ["docs/manual.pdf"]


def test_a_pdf_can_be_dropped_on_the_unified_zone(client: TestClient, project: dict):
    response = client.post(
        f"/api/projects/{project['id']}/upload",
        files=[("files", ("spec.pdf", PDF, "application/pdf"))],
    )

    body = response.json()
    assert body["rejected"] == []
    assert body["accepted"][0]["kind"] == "document"
    assert body["accepted"][0]["rel_path"] == "docs/spec.pdf"


def test_an_archive_is_kept_as_a_misc_attachment(client: TestClient, project: dict):
    result = client.post(
        f"/api/projects/{project['id']}/upload",
        files=[("files", ("reference-photos.zip", b"PK\x03\x04", "application/zip"))],
    ).json()

    assert result["accepted"][0]["rel_path"] == "docs/reference-photos.zip"
    documents = client.get(f"/api/projects/{project['id']}/documents").json()
    assert documents[0]["kind"] == "misc"


def test_an_executable_is_still_refused(client: TestClient, project: dict):
    """Adding a docs folder must not turn the library into a dumping ground."""
    result = client.post(
        f"/api/projects/{project['id']}/upload",
        files=[("files", ("installer.exe", b"MZ", "application/octet-stream"))],
    ).json()

    assert result["accepted"] == []
    assert ".exe" in result["rejected"][0]["error"]
    assert not (library_root(client) / "desk-organizer" / "docs" / "installer.exe").exists()


def test_documents_can_be_downloaded_and_deleted(client: TestClient, project: dict):
    rel = client.post(
        f"/api/projects/{project['id']}/documents",
        files={"file": ("manual.pdf", PDF, "application/pdf")},
    ).json()["rel_path"]

    download = client.get(f"/api/projects/{project['id']}/file", params={"rel_path": rel})
    assert download.status_code == 200
    assert download.content == PDF

    client.delete(f"/api/projects/{project['id']}/documents", params={"rel_path": rel})
    assert client.get(f"/api/projects/{project['id']}/documents").json() == []


def test_delete_refuses_paths_outside_docs(client: TestClient, project: dict):
    directory = library_root(client) / "desk-organizer"
    (directory / "notes.md").write_text("keep me", encoding="utf-8")

    response = client.delete(
        f"/api/projects/{project['id']}/documents", params={"rel_path": "notes.md"}
    )

    assert response.status_code == 400
    assert (directory / "notes.md").read_text() == "keep me"


def test_a_pdf_dropped_into_the_folder_is_picked_up(client: TestClient, project: dict):
    """Copying one in by hand should work as well as uploading it."""
    directory = library_root(client) / "desk-organizer"
    (directory / "docs").mkdir(exist_ok=True)
    (directory / "docs" / "hand-copied.pdf").write_bytes(PDF)

    client.post(f"/api/projects/{project['id']}/rescan")

    documents = client.get(f"/api/projects/{project['id']}/documents").json()
    assert [d["rel_path"] for d in documents] == ["docs/hand-copied.pdf"]


# ------------------------------------------------------- the unified dropzone


def test_a_mixed_drop_is_routed_by_extension(client: TestClient, project: dict, tmp_path: Path):
    sliced = write_sliced_3mf(tmp_path / "tray.gcode.3mf").read_bytes()

    response = client.post(
        f"/api/projects/{project['id']}/upload",
        files=[
            ("files", ("cover.png", _png_bytes(), "image/png")),
            ("files", ("cover.psd", b"8BPS", "application/octet-stream")),
            ("files", ("tray.step", b"ISO-10303-21;\n", "application/octet-stream")),
            ("files", ("tray.gcode.3mf", sliced, "application/octet-stream")),
        ],
        data={"variant": "web"},
    )
    assert response.status_code == 201

    body = response.json()
    assert body["rejected"] == []
    landed = {a["kind"]: a["rel_path"] for a in body["accepted"]}
    assert landed["image"] == "images/photos/cover.png"
    assert landed["image_source"] == "images/sources/cover.psd"
    assert landed["model_source"] == "models/sources/tray.step"
    assert landed["print"].startswith("prints/") and landed["print"].endswith(".gcode.3mf")

    # The sliced file became a real print job, not just a file on disk.
    assert len(client.get("/api/prints", params={"project_id": project["id"]}).json()) == 1
    # And the variant was applied to the image in the same drop.
    assert client.get(f"/api/projects/{project['id']}/images").json()[0]["variant"] == "web"


def test_one_bad_file_does_not_sink_the_batch(client: TestClient, project: dict):
    response = client.post(
        f"/api/projects/{project['id']}/upload",
        files=[
            ("files", ("good.png", _png_bytes(), "image/png")),
            ("files", ("notes.exe", b"MZ", "application/octet-stream")),
        ],
    )

    body = response.json()
    assert [a["rel_path"] for a in body["accepted"]] == ["images/photos/good.png"]
    assert body["rejected"][0]["filename"] == "notes.exe"
    assert ".exe" in body["rejected"][0]["error"]


# ------------------------------------------------------------------ export


def test_export_files_assets_by_variant_and_leaves_pixels_alone(client: TestClient, project: dict):
    """You cut the crops; export must not resample them."""
    original = _png_bytes(64, 64)
    web = client.post(
        f"/api/projects/{project['id']}/images",
        files={"file": ("cover-web.png", original, "image/png")},
        data={"variant": "web"},
    ).json()["rel_path"]
    mobile = client.post(
        f"/api/projects/{project['id']}/images",
        files={"file": ("cover-mobile.png", _png_bytes(32, 32), "image/png")},
        data={"variant": "mobile"},
    ).json()["rel_path"]
    plain = client.post(
        f"/api/projects/{project['id']}/images",
        files={"file": ("extra.png", _png_bytes(), "image/png")},
    ).json()["rel_path"]

    client.put(
        f"/api/projects/{project['id']}/publish",
        json={"title": "Desk Organizer", "assets": [web, mobile, plain]},
    )
    result = client.post(f"/api/projects/{project['id']}/publish/export").json()

    by_variant = {a["variant"]: a["file"] for a in result["assets"]}
    assert by_variant["web"] == "web/01_cover-web.png"
    assert by_variant["mobile"] == "mobile/02_cover-mobile.png"
    assert by_variant[""] == "03_extra.png"

    assets = library_root(client) / "desk-organizer" / "publish" / "makerworld" / "assets"
    # Byte-for-byte: no resampling, no recompression.
    assert (assets / "web" / "01_cover-web.png").read_bytes() == original


# ------------------------------------------------ backwards compatible yaml


def test_old_cover_and_order_keys_are_still_read(client: TestClient, project: dict):
    directory = library_root(client) / "desk-organizer"
    (directory / "images" / "photos").mkdir(parents=True, exist_ok=True)
    for name in ("a.png", "b.png"):
        (directory / "images" / "photos" / name).write_bytes(_png_bytes())

    (directory / "project.yaml").write_text(
        yaml.safe_dump(
            {
                "id": project["id"],
                "title": "Desk Organizer",
                "cover_image": "images/photos/b.png",
                "image_order": ["images/photos/b.png", "images/photos/a.png"],
            }
        ),
        encoding="utf-8",
    )
    client.post(f"/api/projects/{project['id']}/rescan")

    images = client.get(f"/api/projects/{project['id']}/images").json()
    assert [i["rel_path"] for i in images] == ["images/photos/b.png", "images/photos/a.png"]
    assert images[0]["is_cover"] is True

    # Rewritten in the new shape once anything touches it.
    client.put(
        f"/api/projects/{project['id']}/images/variant",
        json={"rel_path": "images/photos/a.png", "variant": "mobile"},
    )
    doc = project_yaml(client)
    assert "cover_image" not in doc and "image_order" not in doc
    assert doc["images"] == [
        {"path": "images/photos/b.png", "cover": True},
        {"path": "images/photos/a.png", "variant": "mobile"},
    ]
