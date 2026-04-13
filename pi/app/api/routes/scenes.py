"""Scene routes — list, activate, presets."""

from fastapi import APIRouter, Depends, HTTPException

from ..schemas import SceneRequest, SceneSaveRequest
from ...effects.generative import EFFECTS
from ...effects.audio_reactive import AUDIO_EFFECTS
from ...diagnostics.patterns import DIAGNOSTIC_EFFECTS


def create_router(deps, require_auth, broadcast_state) -> APIRouter:
    router = APIRouter(prefix="/api/scenes", tags=["scenes"])

    @router.get("/list")
    async def list_effects():
        all_effects = {}
        for name in EFFECTS:
            all_effects[name] = {'type': 'generative'}
        for name in AUDIO_EFFECTS:
            all_effects[name] = {'type': 'audio'}
        for name in DIAGNOSTIC_EFFECTS:
            all_effects[name] = {'type': 'diagnostic'}
        return {'effects': all_effects, 'current': deps.render_state.current_scene}

    @router.post("/activate", dependencies=[Depends(require_auth)])
    async def activate_scene(req: SceneRequest):
        success = deps.renderer.activate_scene(req.effect, req.params)
        if success:
            deps.state_manager.current_scene = req.effect
            deps.state_manager.current_params = req.params
            await broadcast_state()
            return {"status": "ok"}
        raise HTTPException(404, f"Unknown effect: {req.effect}")

    @router.get("/presets")
    async def list_presets():
        return deps.state_manager.list_scenes()

    @router.post("/presets/save", dependencies=[Depends(require_auth)])
    async def save_preset(req: SceneSaveRequest):
        deps.state_manager.save_scene(req.name, req.effect, req.params)
        return {"status": "saved"}

    @router.post("/presets/load/{name}", dependencies=[Depends(require_auth)])
    async def load_preset(name: str):
        scene = deps.state_manager.load_scene(name)
        if not scene:
            raise HTTPException(404, f"Preset not found: {name}")
        success = deps.renderer.activate_scene(
            scene['effect'], scene.get('params', {}),
        )
        if success:
            deps.state_manager.current_scene = scene['effect']
            deps.state_manager.current_params = scene.get('params', {})
            await broadcast_state()
            return {"status": "ok"}
        raise HTTPException(500, "Failed to activate preset")

    @router.delete("/presets/{name}", dependencies=[Depends(require_auth)])
    async def delete_preset(name: str):
        if deps.state_manager.delete_scene(name):
            return {"status": "deleted"}
        raise HTTPException(404, f"Preset not found: {name}")

    return router
