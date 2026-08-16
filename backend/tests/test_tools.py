"""Per-project tool copies (ToolsService.copy_into_project / remove_from_project).

Covers a shared-copy bug: the physical copy under models/sources/tools/
belongs to the *project*, not to whichever .scad source happened to toggle
it on — removing the reference from one source must not delete the copy out
from under another source in the same project that still `use`s it.

Exercises the services directly (not the HTTP API — see test_library.py for
the same style) rather than through TestClient/app.main, since the app's MCP
integration needs Python 3.10+ and this environment's only interpreter is
3.9; the service layer itself has no such requirement.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings
from app.db import Database
from app.services.library import MODEL_SOURCES_DIR, LibraryService
from app.services.tools import ToolsService


@pytest.fixture
def library(tmp_path: Path) -> LibraryService:
    settings = Settings(library_path=tmp_path / "library", data_path=tmp_path / "data")
    settings.ensure_directories()
    db = Database(settings.db_path)
    db.initialise()
    return LibraryService(settings, db)


@pytest.fixture
def tools(library: LibraryService) -> ToolsService:
    return ToolsService(library)


@pytest.fixture
def project(library: LibraryService) -> tuple[str, Path]:
    """(project_id, directory) — kept as a pair since the id (what
    ToolsService's per-project methods take) and the on-disk directory
    (a slug, not the id — see LibraryService.dir_for_id) aren't the same
    string."""
    project_id = library.create_project("Desk Organizer")
    directory = library.dir_for_id(project_id)
    assert directory is not None
    return project_id, directory


def _write_scad(directory: Path, name: str, body: str) -> Path:
    path = directory / MODEL_SOURCES_DIR / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_removing_a_tool_from_one_file_keeps_it_for_a_sibling_still_using_it(
    tools: ToolsService, project: tuple[str, Path]
):
    tools.save_tool("bracket", "module bracket() { cube([5, 5, 5]); }")
    project_id, project_dir = project

    a = _write_scad(project_dir, "a.scad", "use <tools/bracket.scad>;\nbracket();")
    b = _write_scad(project_dir, "b.scad", "use <tools/bracket.scad>;\nbracket();")

    tools.copy_into_project(project_id, "bracket")
    copy_path = project_dir / MODEL_SOURCES_DIR / "tools" / "bracket.scad"
    assert copy_path.is_file()

    # b.scad still references the tool on disk — removing it (as if toggled
    # off in a's buffer) must not delete the copy b.scad still needs.
    tools.remove_from_project(project_id, "bracket")
    assert copy_path.is_file(), "copy was deleted while b.scad still used it"

    # Drop the reference from the last remaining user — now nothing in the
    # project needs the copy, so removal should actually take effect.
    b.write_text("cube([1, 1, 1]);", encoding="utf-8")
    a.write_text("bracket();", encoding="utf-8")  # a's use line already gone

    tools.remove_from_project(project_id, "bracket")
    assert not copy_path.exists()


def test_removing_an_unreferenced_tool_deletes_the_copy_immediately(
    tools: ToolsService, project: tuple[str, Path]
):
    tools.save_tool("bracket", "module bracket() { cube([5, 5, 5]); }")
    project_id, project_dir = project

    tools.copy_into_project(project_id, "bracket")
    copy_path = project_dir / MODEL_SOURCES_DIR / "tools" / "bracket.scad"
    assert copy_path.is_file()

    tools.remove_from_project(project_id, "bracket")
    assert not copy_path.exists()


def test_a_tools_own_copy_referencing_itself_does_not_block_its_own_removal(
    tools: ToolsService, project: tuple[str, Path]
):
    # The copy under models/sources/tools/ is excluded from the reference
    # scan — otherwise a tool's own body would always look "still used".
    tools.save_tool("bracket", "use <tools/bracket.scad>;\nmodule bracket() { cube([5, 5, 5]); }")
    project_id, project_dir = project

    tools.copy_into_project(project_id, "bracket")
    copy_path = project_dir / MODEL_SOURCES_DIR / "tools" / "bracket.scad"
    assert copy_path.is_file()

    tools.remove_from_project(project_id, "bracket")
    assert not copy_path.exists()


def test_removing_from_a_project_with_no_such_copy_is_a_harmless_no_op(
    tools: ToolsService, project: tuple[str, Path]
):
    tools.save_tool("bracket", "module bracket() { cube([5, 5, 5]); }")
    project_id, _ = project

    tools.remove_from_project(project_id, "bracket")  # never added — must not raise
