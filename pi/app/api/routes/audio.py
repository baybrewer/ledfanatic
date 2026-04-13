"""Audio routes — devices, config, start/stop."""

from fastapi import APIRouter, Depends

from ..schemas import AudioConfigRequest


def create_router(deps, require_auth) -> APIRouter:
    router = APIRouter(prefix="/api/audio", tags=["audio"])

    @router.get("/devices")
    async def list_audio_devices():
        return {"devices": deps.audio_analyzer.list_devices()}

    @router.post("/config", dependencies=[Depends(require_auth)])
    async def configure_audio(req: AudioConfigRequest):
        if req.sensitivity is not None:
            deps.audio_analyzer.sensitivity = req.sensitivity
        if req.gain is not None:
            deps.audio_analyzer.gain = req.gain
        if req.device_index is not None:
            deps.audio_analyzer.set_device(req.device_index)
        return {"status": "ok"}

    @router.post("/start", dependencies=[Depends(require_auth)])
    async def start_audio():
        deps.audio_analyzer.start()
        return {"status": "started"}

    @router.post("/stop", dependencies=[Depends(require_auth)])
    async def stop_audio():
        deps.audio_analyzer.stop()
        return {"status": "stopped"}

    return router
