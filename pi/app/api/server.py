"""
FastAPI server — app factory and router composition.

Route logic lives in the routes/ subpackage.
Request/response models live in schemas.py.
"""

import asyncio
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

from .auth import create_auth_dependency
from .deps import AppDeps

from .routes import system, scenes, brightness, media, audio, diagnostics, setup, effects, preview
from .routes import transport as transport_routes
from .routes import ws

logger = logging.getLogger(__name__)

UI_DIR = Path(__file__).parent.parent / "ui"


def create_app(
    transport,
    renderer,
    render_state,
    state_manager,
    brightness_engine,
    media_manager,
    audio_analyzer,
    config: dict,
    setup_session_service=None,
    spatial_map=None,
    preview_service=None,
) -> FastAPI:

    app = FastAPI(title="Pillar Controller", version="1.0.0")

    max_upload_bytes = (
        config.get('transport', {}).get('max_upload_mb', 50) * 1024 * 1024
    )

    require_auth = create_auth_dependency(config)

    deps = AppDeps(
        transport=transport,
        renderer=renderer,
        render_state=render_state,
        state_manager=state_manager,
        brightness_engine=brightness_engine,
        media_manager=media_manager,
        audio_analyzer=audio_analyzer,
        max_upload_bytes=max_upload_bytes,
        setup_session_service=setup_session_service,
        spatial_map=spatial_map,
        preview_service=preview_service,
    )

    # --- WebSocket + broadcast ---
    ws_router, broadcast_state = ws.create_router(deps)

    # --- Mount routers ---
    app.include_router(system.create_router(deps, require_auth))
    app.include_router(scenes.create_router(deps, require_auth, broadcast_state))
    app.include_router(brightness.create_router(deps, require_auth, broadcast_state))
    app.include_router(media.create_router(deps, require_auth, broadcast_state))
    app.include_router(audio.create_router(deps, require_auth))
    app.include_router(diagnostics.create_router(deps, require_auth))
    app.include_router(transport_routes.create_router(deps))
    app.include_router(setup.create_router(deps, require_auth, broadcast_state))
    app.include_router(effects.create_router(deps))
    app.include_router(preview.create_router(deps))
    app.include_router(ws_router)

    # --- Periodic broadcast ---
    @app.on_event("startup")
    async def start_broadcast():
        async def periodic_broadcast():
            while True:
                try:
                    await broadcast_state()
                    await asyncio.sleep(0.5)
                except asyncio.CancelledError:
                    break
        app.state.broadcast_task = asyncio.create_task(periodic_broadcast())

    # --- Static files ---
    @app.get("/")
    async def root():
        index = UI_DIR / "static" / "index.html"
        if index.exists():
            return FileResponse(index)
        return JSONResponse({"error": "UI not found"}, status_code=404)

    app.mount("/static", StaticFiles(directory=str(UI_DIR / "static")), name="static")

    return app
