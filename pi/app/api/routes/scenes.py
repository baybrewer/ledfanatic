"""Scene routes — list, activate, presets, layer CRUD."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, Literal

from ..schemas import SceneRequest, SceneSaveRequest
from ...effects.catalog import EffectCatalogService
from ...core.compositor import Compositor, Layer

BlendMode = Literal['normal', 'add', 'screen', 'multiply', 'max']


class LayerAddRequest(BaseModel):
  effect_name: str
  params: dict = Field(default_factory=dict)
  opacity: float = Field(default=1.0, ge=0.0, le=1.0)
  blend_mode: BlendMode = 'normal'
  enabled: bool = True


class LayerUpdateRequest(BaseModel):
  opacity: Optional[float] = Field(default=None, ge=0.0, le=1.0)
  blend_mode: Optional[BlendMode] = None
  enabled: Optional[bool] = None
  params: Optional[dict] = None


class LayerReorderRequest(BaseModel):
  from_index: int = Field(ge=0)
  to_index: int = Field(ge=0)


def create_router(deps, require_auth, broadcast_state) -> APIRouter:
    router = APIRouter(prefix="/api/scenes", tags=["scenes"])

    # Shared catalog instance for consistent metadata
    _catalog = EffectCatalogService()

    def _default_switcher_playlist():
      """All non-diagnostic, non-switcher effects sorted alphabetically by label."""
      catalog = (
        deps.effect_catalog.get_catalog()
        if hasattr(deps, 'effect_catalog') and deps.effect_catalog
        else _catalog.get_catalog()
      )
      entries = [
        (name, meta.label or name)
        for name, meta in catalog.items()
        if name != 'animation_switcher'
        and meta.group != 'diagnostic'
        and not name.startswith('diag_')
      ]
      entries.sort(key=lambda e: e[1].lower())
      return [name for name, _ in entries]

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
        # If no params provided, restore this effect's last-known params
        if req.params is None:
            params_to_apply = deps.state_manager.get_effect_params(req.effect) or None
        else:
            params_to_apply = req.params

        # Animation Switcher: inject default playlist on first activation
        if req.effect == 'animation_switcher':
          if params_to_apply is None or 'playlist' not in (params_to_apply or {}):
            base = dict(params_to_apply or {})
            base['playlist'] = _default_switcher_playlist()
            params_to_apply = base

        success = deps.renderer.activate_scene(req.effect, params_to_apply)
        if success:
            deps.state_manager.current_scene = req.effect
            # Resolve to actual effect params (merged with yaml defaults)
            resolved = params_to_apply if params_to_apply is not None else dict(
                getattr(deps.renderer.current_effect, 'params', {}) or {}
            )
            # Filter out internal keys like '_effect_registry'
            resolved = {k: v for k, v in resolved.items() if not k.startswith('_')}
            deps.state_manager.current_params = resolved
            # Persist per-effect params so switching back restores them
            deps.state_manager.set_effect_params(req.effect, resolved)
            await broadcast_state()
            return {"status": "ok", "params": resolved}
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

    @router.post("/game-input/{action}")
    async def game_input(action: str):
        """Send input to game effects (tetris). Actions: left, right, rotate, down, drop."""
        effect = deps.renderer.current_effect
        if effect and hasattr(effect, 'handle_input'):
            effect.handle_input(action)
            return {"status": "ok"}
        return {"status": "ignored", "reason": "no active game effect"}

    # --- Layer CRUD endpoints ---

    def _persist_layers():
      """Persist current compositor layers to state_manager."""
      compositor = deps.renderer.compositor
      if compositor and deps.state_manager:
        deps.state_manager.current_layers = [l.to_dict() for l in compositor.layers]

    @router.get("/layers")
    async def get_layers():
        """Return current layer stack or single-scene info."""
        compositor = deps.renderer.compositor
        if compositor:
          return {
            'render_mode': 'layered',
            'layers': [l.to_dict() for l in compositor.layers],
          }
        return {
          'render_mode': 'single',
          'current_scene': deps.render_state.current_scene,
          'layers': [],
        }

    @router.post("/layers/add", dependencies=[Depends(require_auth)])
    async def add_layer(req: LayerAddRequest):
        """Add a layer. First add bootstraps compositor from current scene."""
        # Reject invalid effect names
        if req.effect_name not in deps.renderer.effect_registry:
          raise HTTPException(422, f"Unknown effect: {req.effect_name}")
        # Reject media: scenes (not compositable)
        if req.effect_name.startswith('media:'):
          raise HTTPException(422, "Media scenes cannot be used as layers")

        compositor = deps.renderer.compositor

        # Bootstrap compositor on first add
        if compositor is None:
          layout = deps.compiled_layout
          compositor = Compositor(
            width=layout.width,
            height=layout.height,
            effect_registry=deps.renderer.effect_registry,
            effects_config=getattr(deps.renderer, 'effects_config', None),
          )
          # Seed layer 0 from current single-mode scene (if registry-backed)
          current = deps.render_state.current_scene
          if current and current in deps.renderer.effect_registry:
            current_params = deps.state_manager.current_params or {}
            compositor.add_layer(Layer(
              effect_name=current,
              params=dict(current_params),
              opacity=1.0,
              blend_mode='normal',
              enabled=True,
            ))
          deps.renderer.compositor = compositor

        # Add the requested layer
        new_layer = Layer(
          effect_name=req.effect_name,
          params=req.params,
          opacity=req.opacity,
          blend_mode=req.blend_mode,
          enabled=req.enabled,
        )
        idx = compositor.add_layer(new_layer)

        # Clear single-effect state, switch to layered mode
        deps.renderer.current_effect = None
        deps.render_state.current_scene = None
        deps.state_manager.current_scene = None
        deps.state_manager.current_params = {}
        deps.state_manager._state['render_mode'] = 'layered'
        _persist_layers()
        deps.state_manager.mark_dirty()

        await broadcast_state()
        return {
          'status': 'ok',
          'index': idx,
          'layers': [l.to_dict() for l in compositor.layers],
        }

    @router.post("/layers/{index}/remove", dependencies=[Depends(require_auth)])
    async def remove_layer(index: int):
        """Remove a layer by index."""
        compositor = deps.renderer.compositor
        if compositor is None:
          raise HTTPException(404, "No compositor active")
        if index < 0 or index >= len(compositor.layers):
          raise HTTPException(422, f"Invalid layer index: {index}")

        compositor.remove_layer(index)

        # If last layer removed, tear down compositor
        if len(compositor.layers) == 0:
          deps.renderer.compositor = None
          deps.state_manager._state['render_mode'] = 'single'
          deps.state_manager.current_layers = []
          deps.state_manager.mark_dirty()
          await broadcast_state()
          return {
            'status': 'ok',
            'layers': [],
            'render_mode': 'single',
          }

        _persist_layers()
        deps.state_manager.mark_dirty()
        await broadcast_state()
        return {
          'status': 'ok',
          'layers': [l.to_dict() for l in compositor.layers],
        }

    @router.post("/layers/{index}/update", dependencies=[Depends(require_auth)])
    async def update_layer(index: int, req: LayerUpdateRequest):
        """Update layer properties (opacity, blend_mode, enabled, params)."""
        compositor = deps.renderer.compositor
        if compositor is None:
          raise HTTPException(404, "No compositor active")
        if index < 0 or index >= len(compositor.layers):
          raise HTTPException(422, f"Invalid layer index: {index}")

        updates = {}
        if req.opacity is not None:
          updates['opacity'] = req.opacity
        if req.blend_mode is not None:
          updates['blend_mode'] = req.blend_mode
        if req.enabled is not None:
          updates['enabled'] = req.enabled
        if req.params is not None:
          updates['params'] = req.params

        compositor.update_layer(index, **updates)
        _persist_layers()
        deps.state_manager.mark_dirty()

        await broadcast_state()
        return {
          'status': 'ok',
          'layers': [l.to_dict() for l in compositor.layers],
        }

    @router.post("/layers/reorder", dependencies=[Depends(require_auth)])
    async def reorder_layers(req: LayerReorderRequest):
        """Move a layer from one position to another."""
        compositor = deps.renderer.compositor
        if compositor is None:
          raise HTTPException(404, "No compositor active")
        if req.from_index >= len(compositor.layers):
          raise HTTPException(422, f"Invalid from_index: {req.from_index}")
        if req.to_index >= len(compositor.layers):
          raise HTTPException(422, f"Invalid to_index: {req.to_index}")

        compositor.move_layer(req.from_index, req.to_index)
        _persist_layers()
        deps.state_manager.mark_dirty()

        await broadcast_state()
        return {
          'status': 'ok',
          'layers': [l.to_dict() for l in compositor.layers],
        }

    return router
