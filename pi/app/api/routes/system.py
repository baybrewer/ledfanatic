"""System routes — status, reboot, restart."""

import subprocess

from fastapi import APIRouter, Depends


def create_router(deps, require_auth) -> APIRouter:
    router = APIRouter(prefix="/api/system", tags=["system"])

    @router.get("/status")
    async def system_status():
        return {
            'transport': deps.transport.get_status(),
            'render': deps.render_state.to_dict(),
            'brightness': deps.brightness_engine.get_status(),
            'scenes_count': len(deps.state_manager.list_scenes()),
            'media_count': len(deps.media_manager.items),
        }

    @router.post("/reboot", dependencies=[Depends(require_auth)])
    async def system_reboot():
        subprocess.Popen(["sudo", "reboot"])
        return {"status": "rebooting"}

    @router.post("/restart-app", dependencies=[Depends(require_auth)])
    async def restart_app():
        subprocess.Popen(["sudo", "systemctl", "restart", "ledfanatic"])
        return {"status": "restarting"}

    return router
