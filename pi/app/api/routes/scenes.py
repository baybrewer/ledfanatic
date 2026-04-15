"""Scene routes — list, activate, presets."""

from fastapi import APIRouter, Depends, HTTPException

from ..schemas import SceneRequest, SceneSaveRequest
from ...effects.catalog import EffectCatalogService


def create_router(deps, require_auth, broadcast_state) -> APIRouter:
    router = APIRouter(prefix="/api/scenes", tags=["scenes"])

    # Shared catalog instance for consistent metadata
    _catalog = EffectCatalogService()

    @router.get("/list")
    async def list_effects():
        """Compatibility endpoint — projects catalog metadata into the legacy shape."""
        catalog = (
            deps.effect_catalog.get_catalog()
            if hasattr(deps, 'effect_catalog') and deps.effect_catalog
            else _catalog.get_catalog()
        )
        all_effects = {}
        for name, meta in catalog.items():
            # Map catalog group to legacy type field
            effect_type = meta.group if meta.group != 'imported' else 'generative'
            all_effects[name] = {
                'type': effect_type,
                'description': meta.description,
                'preview_supported': meta.preview_supported,
            }
        return {'effects': all_effects, 'current': deps.render_state.current_scene}

    @router.post("/activate", dependencies=[Depends(require_auth)])
    async def activate_scene(req: SceneRequest):
        # If params provided, use them; otherwise keep saved params for this effect
        params = req.params if req.params is not None else deps.state_manager.current_params
        success = deps.renderer.activate_scene(req.effect, params)
        if success:
            deps.state_manager.current_scene = req.effect
            deps.state_manager.current_params = params
            await broadcast_state()
            return {"status": "ok", "params": params}
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

    @router.get("/switcher/status")
    async def switcher_status():
        """Get Animation Switcher state if active."""
        from ...effects.switcher import AnimationSwitcher
        if isinstance(deps.renderer.current_effect, AnimationSwitcher):
            return deps.renderer.current_effect.get_switcher_status()
        return {"active": False}

    return router
