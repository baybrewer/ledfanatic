"""Brightness and display control routes."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from ..schemas import BrightnessConfigRequest, BlackoutRequest, FPSRequest


def create_router(deps, require_auth, broadcast_state) -> APIRouter:
    router = APIRouter(tags=["brightness"])

    @router.get("/api/brightness/status")
    async def brightness_status():
        return deps.brightness_engine.get_status()

    @router.post("/api/brightness/config", dependencies=[Depends(require_auth)])
    async def update_brightness(req: BrightnessConfigRequest):
        update = {}
        if req.manual_cap is not None:
            update['manual_cap'] = req.manual_cap
            deps.state_manager.brightness_manual_cap = req.manual_cap
        if req.auto_enabled is not None:
            update['auto_enabled'] = req.auto_enabled
            deps.state_manager.brightness_auto_enabled = req.auto_enabled
        if req.location is not None:
            update['location'] = req.location
        if req.solar is not None:
            update['solar'] = req.solar
            if 'night_brightness' in req.solar:
                deps.state_manager.night_brightness = req.solar['night_brightness']
        if update:
            deps.brightness_engine.update_config(update)
        effective = deps.brightness_engine.get_effective_brightness(
            datetime.now(timezone.utc),
        )
        await deps.transport.send_brightness(effective)
        await broadcast_state()
        return deps.brightness_engine.get_status()

    @router.post("/api/display/brightness", dependencies=[Depends(require_auth)])
    async def set_brightness(req: BrightnessConfigRequest):
        """Legacy endpoint — sets manual cap."""
        if req.manual_cap is not None:
            deps.brightness_engine.manual_cap = req.manual_cap
            deps.state_manager.brightness_manual_cap = req.manual_cap
        effective = deps.brightness_engine.get_effective_brightness(
            datetime.now(timezone.utc),
        )
        await deps.transport.send_brightness(effective)
        await broadcast_state()
        return deps.brightness_engine.get_status()

    @router.post("/api/display/fps", dependencies=[Depends(require_auth)])
    async def set_fps(req: FPSRequest):
        deps.render_state.target_fps = max(1, min(90, req.value))
        deps.state_manager.target_fps = deps.render_state.target_fps
        await broadcast_state()
        return {"fps": deps.render_state.target_fps}

    @router.post("/api/display/blackout", dependencies=[Depends(require_auth)])
    async def set_blackout(req: BlackoutRequest):
        deps.render_state.blackout = req.enabled
        await deps.transport.send_blackout(req.enabled)
        await broadcast_state()
        return {"blackout": deps.render_state.blackout}

    return router
