"""Manual version snapshots.

A snapshot copies `models/` into `_versions/vNNN__date__label/`. Deliberately
not git: binary meshes bloat object stores, and you cannot browse one in Finder.

**On hardlinks.** The plan called for hardlinking unchanged files to make
repeated snapshots nearly free. That turns out to be unsafe here: a hardlink is
the *same inode*, and CAD tools re-export by truncating the existing path rather
than writing a new one. Re-exporting `tray.stl` from Fusion would therefore
rewrite every snapshot that ever contained it — silently destroying the history
the feature exists to keep.

So the cheap path is a copy-on-write clone (APFS `clonefile`, btrfs/XFS
`FICLONE`), which gives the same "costs almost nothing on disk" property while
keeping each snapshot independent. Filesystems without reflink support fall back
to a real copy. `version.yaml` records which strategy was used.
"""

from __future__ import annotations

import ctypes
import fcntl
import logging
import shutil
import sys
from pathlib import Path
from typing import Any

import yaml

from ..db import Database
from ..utils import slugify, today, utcnow
from .events import bus
from .library import MODELS_DIR, VERSIONS_DIR, LibraryService

log = logging.getLogger(__name__)


class VersionService:
    def __init__(self, db: Database, library: LibraryService):
        self.db = db
        self.library = library

    def list_versions(self, project_id: str) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM versions WHERE project_id = ? ORDER BY number DESC", (project_id,)
        )
        return [dict(row) for row in rows]

    def next_number(self, project_id: str) -> int:
        row = self.db.query_one(
            "SELECT MAX(number) AS n FROM versions WHERE project_id = ?", (project_id,)
        )
        return int((row["n"] or 0) + 1) if row else 1

    def create(self, project_id: str, label: str = "", note: str = "") -> dict[str, Any]:
        directory = self.library.dir_for_id(project_id)
        if directory is None or not directory.is_dir():
            raise KeyError(project_id)

        source = directory / MODELS_DIR
        # Files, not entries: models/ now ships with an empty sources/ subfolder.
        if not source.is_dir() or not any(p.is_file() for p in source.rglob("*")):
            raise ValueError("nothing to snapshot — models/ is empty")

        number = self.next_number(project_id)
        slug = slugify(label, fallback="snapshot") if label else "snapshot"
        folder_name = f"v{number:03d}__{today()}__{slug}"
        destination = directory / VERSIONS_DIR / folder_name
        if destination.exists():
            raise ValueError(f"version folder already exists: {folder_name}")

        file_count, cloned = _copy_tree(source, destination / MODELS_DIR)

        metadata = {
            "number": number,
            "label": label,
            "note": note,
            "created": utcnow(),
            "file_count": file_count,
            "storage": "reflink" if cloned == file_count else f"{cloned}/{file_count} reflinked",
        }
        (destination / "version.yaml").write_text(
            yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )

        self.db.execute(
            "INSERT INTO versions(project_id, number, folder, label, note, created, file_count) "
            "VALUES(?, ?, ?, ?, ?, ?, ?)",
            (project_id, number, folder_name, label, note, metadata["created"], file_count),
        )
        bus.publish("version.created", {"project_id": project_id, "folder": folder_name})
        return {"project_id": project_id, "folder": folder_name, **metadata}

    def delete(self, project_id: str, folder: str) -> None:
        directory = self.library.dir_for_id(project_id)
        if directory is None:
            raise KeyError(project_id)
        target = directory / VERSIONS_DIR / folder
        # Containment check: `folder` arrives from the client.
        if target.resolve().parent != (directory / VERSIONS_DIR).resolve():
            raise ValueError("invalid version folder")
        if target.is_dir():
            shutil.rmtree(target)
        self.db.execute(
            "DELETE FROM versions WHERE project_id = ? AND folder = ?", (project_id, folder)
        )
        bus.publish("version.deleted", {"project_id": project_id, "folder": folder})

    def restore(self, project_id: str, folder: str) -> dict[str, Any]:
        """Bring a snapshot back into models/, after snapshotting what is there now."""
        directory = self.library.dir_for_id(project_id)
        if directory is None:
            raise KeyError(project_id)
        source = directory / VERSIONS_DIR / folder / MODELS_DIR
        if not source.is_dir():
            raise ValueError(f"no models/ inside {folder}")

        # Never destroy current work to recover old work.
        backup = self.create(project_id, label="before-restore", note=f"auto-taken before {folder}")

        live = directory / MODELS_DIR
        if live.exists():
            shutil.rmtree(live)
        _copy_tree(source, live)

        self.library.scan_project_dir(directory)
        bus.publish("version.restored", {"project_id": project_id, "folder": folder})
        return {"restored": folder, "backup": backup["folder"]}

    def contents(self, project_id: str, folder: str) -> list[dict[str, Any]]:
        directory = self.library.dir_for_id(project_id)
        if directory is None:
            raise KeyError(project_id)
        root = directory / VERSIONS_DIR / folder
        if not root.is_dir():
            raise KeyError(folder)
        entries = []
        for path in sorted(root.rglob("*")):
            if path.is_file():
                entries.append(
                    {
                        "rel_path": path.relative_to(root).as_posix(),
                        "size": path.stat().st_size,
                    }
                )
        return entries


def _copy_tree(source: Path, destination: Path) -> tuple[int, int]:
    """Copy a tree, preferring reflinks. Returns (files, files_reflinked)."""
    destination.mkdir(parents=True, exist_ok=True)
    files = cloned = 0

    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        target = destination / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if not path.is_file():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if _reflink(path, target):
            cloned += 1
        else:
            shutil.copy2(path, target)
        files += 1

    return files, cloned


# btrfs/XFS ioctl for a copy-on-write clone: _IOW(0x94, 9, int).
_FICLONE = 0x40049409


def _reflink(source: Path, destination: Path) -> bool:
    """Copy-on-write clone. Returns False when the filesystem cannot do it."""
    if sys.platform == "darwin":
        return _clonefile_darwin(source, destination)
    if sys.platform.startswith("linux"):
        try:
            with open(source, "rb") as src, open(destination, "wb") as dst:
                fcntl.ioctl(dst.fileno(), _FICLONE, src.fileno())
            shutil.copystat(source, destination)
            return True
        except OSError:
            # EXDEV, EOPNOTSUPP, or a filesystem without reflink support.
            destination.unlink(missing_ok=True)
            return False
    return False


_libc = None


def _clonefile_darwin(source: Path, destination: Path) -> bool:
    global _libc
    if _libc is None:
        try:
            _libc = ctypes.CDLL("libSystem.B.dylib", use_errno=True)
            _libc.clonefile.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
            _libc.clonefile.restype = ctypes.c_int
        except (OSError, AttributeError):  # pragma: no cover — non-APFS macOS
            _libc = False
    if not _libc:
        return False
    result = _libc.clonefile(str(source).encode(), str(destination).encode(), ctypes.c_int(0))
    if result != 0:
        log.debug("clonefile failed for %s: errno %s", source, ctypes.get_errno())
        return False
    return True


def reflink_probe(directory: Path) -> bool:
    """Does this filesystem actually support cloning? Reported in /api/health."""
    probe = directory / ".forge-reflink-probe"
    clone = directory / ".forge-reflink-probe-clone"
    try:
        probe.write_bytes(b"probe")
        clone.unlink(missing_ok=True)
        return _reflink(probe, clone)
    except OSError:
        return False
    finally:
        probe.unlink(missing_ok=True)
        clone.unlink(missing_ok=True)
