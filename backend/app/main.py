"""FastAPI entrypoint. Serves the API and the built React bundle from one image."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api import media, prints, projects, publish, system
from .api import settings as settings_api
from .config import get_settings
from .mcp_server import build_mcp_server, mcp_asgi_app
from .services.events import bus
from .state import AppState

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("forge")

# Populated by the Docker build; absent in local dev, where Vite serves the UI.
STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    bus.bind_loop(asyncio.get_running_loop())
    state = AppState()
    app.state.app_state = state
    state.startup()

    mcp_app = getattr(app.state, "mcp_asgi", None)
    try:
        if mcp_app is None:
            yield
        else:
            # The mounted MCP app owns a session manager that has to be entered,
            # and a mounted app's lifespan is not run for it.
            async with mcp_app.router.lifespan_context(mcp_app):
                yield
    finally:
        state.shutdown()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Forge Ledger", version="0.1.0", lifespan=lifespan)

    # LAN, single user, no login — CORS only needs to allow the Vite dev server.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    for router in (
        projects.router,
        prints.router,
        media.router,
        publish.router,
        settings_api.router,
        system.router,
    ):
        app.include_router(router)

    if settings.mcp_enabled:
        # Mounted before the SPA catch-all, which would otherwise swallow /mcp.
        mcp_server = build_mcp_server(
            lambda: getattr(app.state, "app_state", None), demo=settings.demo_seed
        )
        mcp_asgi = mcp_asgi_app(mcp_server)
        app.state.mcp_asgi = mcp_asgi
        app.mount("/mcp", mcp_asgi, name="mcp")
        log.info("MCP server mounted at /mcp")

    if STATIC_DIR.is_dir():
        app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa(full_path: str) -> FileResponse:
            """Client-side routing: unknown paths fall through to index.html."""
            candidate = STATIC_DIR / full_path
            if full_path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(STATIC_DIR / "index.html")

    return app


app = create_app()
