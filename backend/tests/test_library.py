from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings
from app.db import Database
from app.services.library import (
    LibraryService,
    ProjectDoc,
    classify,
    read_project_doc,
    write_project_doc,
)


@pytest.fixture
def service(tmp_path: Path) -> LibraryService:
    settings = Settings(library_path=tmp_path / "library", data_path=tmp_path / "data")
    settings.ensure_directories()
    db = Database(settings.db_path)
    db.initialise()
    return LibraryService(settings, db)


def test_classify_distinguishes_sliced_from_mesh(tmp_path: Path):
    assert classify(tmp_path / "tray.gcode.3mf") == "sliced"
    assert classify(tmp_path / "tray.3mf") == "mesh"
    assert classify(tmp_path / "tray.step") == "cad"
    assert classify(tmp_path / "photo.JPG") == "image"
    assert classify(tmp_path / "notes.md") == "doc"


def test_project_yaml_round_trip(tmp_path: Path):
    doc = ProjectDoc(title="Desk Organizer", tags=["office", "storage"], license="CC-BY-4.0")
    write_project_doc(tmp_path, doc)

    reloaded = read_project_doc(tmp_path)

    assert reloaded is not None
    assert (reloaded.id, reloaded.title, reloaded.tags) == (doc.id, doc.title, doc.tags)


def test_unknown_yaml_keys_survive_a_round_trip(tmp_path: Path):
    (tmp_path / "project.yaml").write_text(
        "id: X\ntitle: Thing\nfuture_field: keep me\n", encoding="utf-8"
    )

    doc = read_project_doc(tmp_path)
    assert doc is not None
    write_project_doc(tmp_path, doc)

    assert "future_field: keep me" in (tmp_path / "project.yaml").read_text()


def test_create_project_builds_the_folder_layout(service: LibraryService):
    project_id = service.create_project("Desk Organizer")

    directory = service.dir_for_id(project_id)
    assert directory is not None and directory.name == "desk-organizer"
    for sub in ("models", "prints", "images/photos", "publish/makerworld"):
        assert (directory / sub).is_dir()
    assert (directory / "project.yaml").is_file()


def test_slug_collisions_get_a_suffix(service: LibraryService):
    service.create_project("Desk Organizer")
    second = service.create_project("Desk Organizer")

    directory = service.dir_for_id(second)
    assert directory is not None and directory.name == "desk-organizer-2"


def test_folder_dropped_in_by_hand_is_adopted(service: LibraryService):
    manual = service.root / "hand-made-thing"
    (manual / "models").mkdir(parents=True)
    (manual / "models" / "part.stl").write_bytes(b"solid\n")

    service.scan_all()

    rows = service.db.query("SELECT title, slug FROM projects")
    assert [dict(r) for r in rows] == [{"title": "Hand Made Thing", "slug": "hand-made-thing"}]
    assert (manual / "project.yaml").is_file()


def test_unfiled_files_are_surfaced_not_absorbed(service: LibraryService):
    project_id = service.create_project("Tray")
    directory = service.dir_for_id(project_id)
    assert directory is not None
    (directory / "models" / "tray").mkdir(parents=True)
    (directory / "models" / "tray" / "tray.stl").write_bytes(b"solid\n")
    (directory / "models" / "tray" / "tray.step").write_bytes(b"ISO-10303\n")

    service.scan_project_dir(directory)
    assert {f["rel_path"] for f in service.unfiled(project_id)} == {
        "models/tray/tray.stl",
        "models/tray/tray.step",
    }

    service.attach_files_to_model(project_id, "tray", ["models/tray/tray.stl"])
    assert {f["rel_path"] for f in service.unfiled(project_id)} == {"models/tray/tray.step"}


def test_renaming_keeps_the_id_stable(service: LibraryService):
    project_id = service.create_project("Old Name")

    new_slug = service.rename_project(project_id, "Brand New Name")

    assert new_slug == "brand-new-name"
    assert service.slug_for_id(project_id) == "brand-new-name"
    doc = read_project_doc(service.project_dir(new_slug))
    assert doc is not None and doc.id == project_id


def test_renaming_a_folder_by_hand_keeps_the_project(service: LibraryService):
    project_id = service.create_project("Desk Organizer")
    directory = service.dir_for_id(project_id)
    assert directory is not None
    directory.rename(service.root / "renamed-by-hand")

    service.scan_all()

    row = service.db.query_one("SELECT slug, title FROM projects WHERE id = ?", (project_id,))
    assert row is not None
    # The ULID survives the rename; the display title comes from project.yaml.
    assert row["slug"] == "renamed-by-hand"
    assert row["title"] == "Desk Organizer"


def test_full_rescan_drops_projects_that_are_gone(service: LibraryService):
    project_id = service.create_project("Temporary")
    import shutil

    directory = service.dir_for_id(project_id)
    assert directory is not None
    shutil.rmtree(directory)

    result = service.scan_all()

    assert result == {"projects": 0, "removed": 1}
    assert service.db.query("SELECT * FROM projects") == []


def test_the_database_is_a_cache_and_rebuilds_from_disk(service: LibraryService):
    project_id = service.create_project("Desk Organizer", tags=["office"])
    service.update_project(project_id, {"status": "testing", "notes": "hinge is too thin"})

    # The governing principle: deleting the cache loses nothing.
    service.db.reset()
    assert service.db.query("SELECT * FROM projects") == []

    service.scan_all()

    row = service.db.query_one("SELECT * FROM projects WHERE id = ?", (project_id,))
    assert row is not None
    assert row["title"] == "Desk Organizer"
    assert row["status"] == "testing"
    assert row["notes"] == "hinge is too thin"


def test_delete_moves_to_trash_by_default(service: LibraryService):
    project_id = service.create_project("Doomed")

    service.delete_project(project_id)

    assert not (service.root / "doomed").exists()
    assert any((service.root / "_trash").iterdir())
    assert service.db.query("SELECT * FROM projects") == []


def test_versions_folder_is_excluded_from_the_live_file_list(service: LibraryService):
    project_id = service.create_project("Tray")
    directory = service.dir_for_id(project_id)
    assert directory is not None
    (directory / "_versions" / "v001__2026-08-01__initial").mkdir(parents=True)
    (directory / "_versions" / "v001__2026-08-01__initial" / "old.stl").write_bytes(b"x")

    service.scan_project_dir(directory)

    paths = [r["rel_path"] for r in service.db.query("SELECT rel_path FROM files")]
    assert not any(p.startswith("_versions") for p in paths)
