"""
Effect catalog routes — rich metadata for UI and preview.

Routes only; service logic lives in pi/app/effects/catalog.py.
"""

import logging

from fastapi import APIRouter, HTTPException, Depends

from ...effects.catalog import EffectCatalogService
from ..schemas import FavoritesRequest

logger = logging.getLogger(__name__)


def create_router(deps, require_auth) -> APIRouter:
  router = APIRouter(prefix="/api/effects", tags=["effects"])

  @router.get("/catalog")
  async def get_catalog():
    """Rich metadata for all registered effects."""
    if hasattr(deps, 'effect_catalog') and deps.effect_catalog:
      catalog = deps.effect_catalog.get_catalog()
    else:
      svc = EffectCatalogService()
      catalog = svc.get_catalog()
    return {
      'effects': {name: meta.to_dict() for name, meta in catalog.items()},
      'current': deps.render_state.current_scene,
      'current_params': deps.state_manager.current_params,
    }

  @router.get("/favorites")
  async def get_favorites():
    return {'favorites': deps.state_manager.favorites}

  @router.post("/favorites", dependencies=[Depends(require_auth)])
  async def set_favorites(req: FavoritesRequest):
    if hasattr(deps, 'effect_catalog') and deps.effect_catalog:
      catalog = deps.effect_catalog.get_catalog()
    else:
      catalog = EffectCatalogService().get_catalog()
    unknown = [n for n in req.favorites if n not in catalog]
    if unknown:
      raise HTTPException(400, f"Unknown effects: {unknown}")
    deps.state_manager.favorites = req.favorites
    return {'favorites': deps.state_manager.favorites}

  @router.get("/{name}")
  async def get_effect_meta(name: str):
    """Metadata for a single effect."""
    if hasattr(deps, 'effect_catalog') and deps.effect_catalog:
      meta = deps.effect_catalog.get_meta(name)
      if meta:
        return meta.to_dict()
    else:
      svc = EffectCatalogService()
      meta = svc.get_meta(name)
      if meta:
        return meta.to_dict()
    raise HTTPException(404, f"Effect not found: {name}")

  return router
