"""Print jobs: ingest a sliced 3MF, then track it through the lifecycle.

Ingest writes a sidecar JSON next to the 3MF holding both the extracted slicer
settings *and* the job record. That keeps the promise from §2 — open
`prints/2026-08-06_tray_v2.json` in any editor and the whole history is there,
including why a print failed and what you changed afterwards.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ..config import Settings
from ..db import Database
from ..utils import new_ulid, utcnow
from .events import bus
from .library import PLATES_DIR, PRINTS_DIR, LibraryService, is_sliced
from .threemf import extract_preview, parse_3mf

log = logging.getLogger(__name__)

STATUSES = ("queued", "printing", "done", "failed")
SIDECAR_VERSION = 1


class PrintService:
    def __init__(self, settings: Settings, db: Database, library: LibraryService):
        self.settings = settings
        self.db = db
        self.library = library

    # --------------------------------------------------------------- ingest

    def sidecar_path(self, threemf: Path) -> Path:
        """`tray_v2.gcode.3mf` -> `tray_v2.json`."""
        name = threemf.name
        for suffix in (".gcode.3mf", ".gcode", ".3mf"):
            if name.lower().endswith(suffix):
                return threemf.with_name(name[: -len(suffix)] + ".json")
        return threemf.with_suffix(".json")

    def ingest_project(self, project_id: str, *, only_stale: bool = False) -> list[str]:
        """Ingest every sliced file in the project's prints/ folder.

        `only_stale` skips files already parsed and unchanged since, which is what
        makes this cheap enough to run after every scan.
        """
        directory = self.library.dir_for_id(project_id)
        if directory is None:
            raise KeyError(project_id)

        prints_dir = directory / PRINTS_DIR
        if not prints_dir.is_dir():
            return []

        known: dict[str, str] = {}
        if only_stale:
            known = {
                row["rel_path"]: row["id"]
                for row in self.db.query(
                    "SELECT rel_path, id FROM prints WHERE project_id = ?", (project_id,)
                )
            }

        ingested = []
        for path in sorted(prints_dir.rglob("*")):
            if not (path.is_file() and is_sliced(path)):
                continue
            if only_stale and not self._needs_ingest(path, directory, known):
                continue
            try:
                ingested.append(self.ingest_file(project_id, path))
            except Exception:
                log.exception("failed to ingest %s", path)
        return ingested

    def _needs_ingest(self, threemf: Path, directory: Path, known: dict[str, str]) -> bool:
        """New file, or re-sliced since the sidecar was written."""
        rel_path = threemf.relative_to(directory).as_posix()
        if rel_path not in known:
            return True
        sidecar = self.sidecar_path(threemf)
        if not sidecar.exists():
            return True
        try:
            return threemf.stat().st_mtime > sidecar.stat().st_mtime
        except OSError:
            return True

    def ingest_file(self, project_id: str, threemf: Path) -> str:
        """Parse one sliced file into a print job, preserving any existing lifecycle state."""
        directory = self.library.dir_for_id(project_id)
        if directory is None:
            raise KeyError(project_id)
        rel_path = threemf.relative_to(directory).as_posix()

        existing = self.db.query_one(
            "SELECT * FROM prints WHERE project_id = ? AND rel_path = ?", (project_id, rel_path)
        )
        sidecar = self.sidecar_path(threemf)
        stored = _read_json(sidecar)

        parsed = parse_3mf(threemf) if threemf.suffix.lower() == ".3mf" else None

        print_id = (
            (existing["id"] if existing else None) or stored.get("job", {}).get("id") or new_ulid()
        )
        # Lifecycle state comes from whichever record already exists; a re-slice
        # must not silently reset a print you already marked done.
        job = dict(stored.get("job") or {})
        if existing:
            job = {
                **job,
                **{
                    k: existing[k]
                    for k in (
                        "status",
                        "actual_s",
                        "notes",
                        "failure_reason",
                        "failure_fix",
                        "model_name",
                        "created",
                    )
                },
            }

        plate = parsed.primary_plate if parsed else None
        filaments = [f.as_dict() for p in parsed.plates for f in p.filaments] if parsed else []
        weight = parsed.total_weight_g if parsed else None

        record = {
            "id": print_id,
            "project_id": project_id,
            "rel_path": rel_path,
            "name": threemf.name,
            "model_name": job.get("model_name"),
            "status": job.get("status") if job.get("status") in STATUSES else "queued",
            "printer": (parsed.settings.get("printer_model") if parsed else None)
            or (plate.printer_model_id if plate else None),
            "nozzle": (plate.nozzle_diameter if plate else None),
            "plate_count": len(parsed.plates) if parsed else 1,
            "filaments": json.dumps(filaments),
            "estimated_s": parsed.estimated_time_s if parsed else None,
            "actual_s": job.get("actual_s"),
            "weight_g": weight,
            "cost": self.estimate_cost(weight),
            "notes": job.get("notes") or "",
            "failure_reason": job.get("failure_reason"),
            "failure_fix": job.get("failure_fix"),
            "settings": json.dumps(parsed.settings if parsed else {}),
            "parser_version": parsed.parser_version if parsed else None,
            "created": job.get("created") or utcnow(),
            "started": job.get("started"),
            "finished": job.get("finished"),
        }
        self._upsert(record)

        if parsed:
            self._write_sidecar(sidecar, parsed, record)
            self._extract_plate_previews(directory, threemf, parsed)

        bus.publish("print.ingested", {"id": print_id, "project_id": project_id})
        return print_id

    def _upsert(self, record: dict[str, Any]) -> None:
        columns = ", ".join(record)
        placeholders = ", ".join(f":{key}" for key in record)
        updates = ", ".join(f"{key}=excluded.{key}" for key in record if key != "id")
        self.db.execute(
            f"INSERT INTO prints({columns}) VALUES({placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}",
            record,
        )

    def _write_sidecar(self, sidecar: Path, parsed: Any, record: dict[str, Any]) -> None:
        payload = parsed.as_dict()
        payload["sidecar_version"] = SIDECAR_VERSION
        payload["job"] = {
            key: record[key]
            for key in (
                "id",
                "status",
                "model_name",
                "actual_s",
                "notes",
                "failure_reason",
                "failure_fix",
                "created",
                "started",
                "finished",
            )
        }
        sidecar.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def _extract_plate_previews(self, directory: Path, threemf: Path, parsed: Any) -> None:
        """Free thumbnails, per §5.4 — the plate PNG is already in the archive."""
        for plate in parsed.plates:
            if not plate.preview_path:
                continue
            stem = threemf.name.split(".")[0]
            target = directory / PLATES_DIR / f"{stem}_plate{plate.index}.png"
            if target.exists():
                continue
            try:
                extract_preview(threemf, target, plate.index)
            except (OSError, KeyError):
                log.debug("no preview for plate %s of %s", plate.index, threemf.name)

    def estimate_cost(self, weight_g: float | None) -> float | None:
        if not weight_g:
            return None
        return round(weight_g / 1000 * self.settings.filament_cost_per_kg, 2)

    # ------------------------------------------------------------ lifecycle

    def update(self, print_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM prints WHERE id = ?", (print_id,))
        if row is None:
            raise KeyError(print_id)
        record = dict(row)

        if "status" in changes:
            status = changes["status"]
            if status not in STATUSES:
                raise ValueError(f"unknown status {status!r}")
            record["status"] = status
            now = utcnow()
            if status == "printing" and not record["started"]:
                record["started"] = now
            if status in {"done", "failed"}:
                record["finished"] = now
            else:
                record["finished"] = None
            if status != "failed":
                # Clearing on re-queue keeps the failure log about real failures.
                record["failure_reason"] = None
                record["failure_fix"] = None

        for key in ("model_name", "notes", "failure_reason", "failure_fix", "actual_s"):
            if key in changes:
                record[key] = changes[key]
        if "cost" in changes:
            record["cost"] = changes["cost"]

        self._upsert(record)
        self._sync_sidecar(record)
        bus.publish("print.updated", {"id": print_id, "status": record["status"]})
        return record

    def _sync_sidecar(self, record: dict[str, Any]) -> None:
        """Push lifecycle changes back into the on-disk JSON."""
        directory = self.library.dir_for_id(record["project_id"])
        if directory is None:
            return
        threemf = directory / record["rel_path"]
        sidecar = self.sidecar_path(threemf)
        payload = _read_json(sidecar)
        if not payload:
            if not threemf.exists():
                return
            payload = {"sidecar_version": SIDECAR_VERSION, "source": threemf.name}
        payload["job"] = {
            key: record[key]
            for key in (
                "id",
                "status",
                "model_name",
                "actual_s",
                "notes",
                "failure_reason",
                "failure_fix",
                "created",
                "started",
                "finished",
            )
        }
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def delete(self, print_id: str, *, remove_files: bool = False) -> None:
        row = self.db.query_one("SELECT * FROM prints WHERE id = ?", (print_id,))
        if row is None:
            raise KeyError(print_id)
        if remove_files:
            directory = self.library.dir_for_id(row["project_id"])
            if directory is not None:
                threemf = directory / row["rel_path"]
                threemf.unlink(missing_ok=True)
                self.sidecar_path(threemf).unlink(missing_ok=True)
        self.db.execute("DELETE FROM prints WHERE id = ?", (print_id,))
        bus.publish("print.deleted", {"id": print_id})

    # -------------------------------------------------------------- reading

    def list_prints(
        self, project_id: str | None = None, status: str | None = None
    ) -> list[dict[str, Any]]:
        clauses, params = [], []
        if project_id:
            clauses.append("p.project_id = ?")
            params.append(project_id)
        if status:
            clauses.append("p.status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.db.query(
            f"SELECT p.*, pr.title AS project_title, pr.slug AS project_slug "
            f"FROM prints p JOIN projects pr ON pr.id = p.project_id {where} "
            f"ORDER BY p.created DESC",
            tuple(params),
        )
        return [_expand(row) for row in rows]

    def get(self, print_id: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT p.*, pr.title AS project_title, pr.slug AS project_slug "
            "FROM prints p JOIN projects pr ON pr.id = p.project_id WHERE p.id = ?",
            (print_id,),
        )
        return _expand(row) if row else None

    def failure_log(self, limit: int = 100) -> list[dict[str, Any]]:
        """The query that turns 'why did this warp last time' into an answer."""
        rows = self.db.query(
            "SELECT p.*, pr.title AS project_title, pr.slug AS project_slug "
            "FROM prints p JOIN projects pr ON pr.id = p.project_id "
            "WHERE p.status = 'failed' ORDER BY p.finished DESC LIMIT ?",
            (limit,),
        )
        return [_expand(row) for row in rows]

    def stats(self) -> dict[str, Any]:
        counts = {
            row["status"]: row["n"]
            for row in self.db.query("SELECT status, COUNT(*) AS n FROM prints GROUP BY status")
        }
        totals = self.db.query_one(
            "SELECT COALESCE(SUM(weight_g), 0) AS weight, COALESCE(SUM(cost), 0) AS cost, "
            "COALESCE(SUM(COALESCE(actual_s, estimated_s)), 0) AS seconds "
            "FROM prints WHERE status = 'done'"
        )
        done, failed = counts.get("done", 0), counts.get("failed", 0)
        finished = done + failed
        return {
            "counts": {status: counts.get(status, 0) for status in STATUSES},
            "filament_g": round(totals["weight"], 1) if totals else 0,
            "filament_cost": round(totals["cost"], 2) if totals else 0,
            "print_seconds": int(totals["seconds"]) if totals else 0,
            "success_rate": round(done / finished, 3) if finished else None,
        }


def _expand(row: Any) -> dict[str, Any]:
    record = dict(row)
    record["filaments"] = json.loads(record.get("filaments") or "[]")
    record["settings"] = json.loads(record.get("settings") or "{}")
    return record


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}
