"""Runtime configuration. Everything is env-overridable for the container."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FORGE_", env_file=".env", extra="ignore")

    # The library is the product; /data is a cache that may be deleted.
    library_path: Path = Path("./library")
    data_path: Path = Path("./data")

    # Watcher: CAD tools write in bursts, so events are coalesced.
    watch_enabled: bool = True
    watch_debounce_seconds: float = 2.0
    # Safety net for dropped inotify events, as hours past midnight.
    full_rescan_hour: int = 3

    worker_threads: int = 2

    # MCP server at /mcp, so an agent can read the library and file projects
    # into it. No more exposed than the REST API, which is also unauthenticated.
    mcp_enabled: bool = True

    # Fill an empty library with sample projects on boot. For throwaway
    # containers; it refuses to touch a library that already has anything in it.
    demo_seed: bool = False

    # Seeds `_shared/llm.yaml` on first run only; after that Settings owns it.
    # Empty means the polish button stays greyed out until you configure one.
    ollama_url: str = ""
    ollama_model: str = "llama3.1"
    ollama_timeout_seconds: float = 120.0

    # Used for print cost estimates; overridable per print job.
    filament_cost_per_kg: float = 22.0

    cors_origins: list[str] = ["http://localhost:5173"]

    # Base URL of a forge-scad-editor instance, e.g. http://forge-scad-editor:8080.
    # Empty (the default) means "not configured" — the Editor tab hides, same
    # as if the probe in state.py can't reach it. See EXTRACTION-PROGRESS/
    # 03-host-proxy-and-probe.md for why this is a probe-driven feature flag,
    # not a plain on/off switch.
    editor_url: str = ""

    @property
    def db_path(self) -> Path:
        return self.data_path / "forge.db"

    @property
    def thumbnail_path(self) -> Path:
        return self.data_path / "thumbnails"

    def ensure_directories(self) -> None:
        for path in (self.library_path, self.data_path, self.thumbnail_path):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
