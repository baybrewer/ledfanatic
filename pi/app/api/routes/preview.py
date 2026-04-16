"""
Preview API routes — dedicated simulator/preview transport.

Routes only; service logic lives in pi/app/preview/service.py.
"""

import asyncio
import logging
import struct

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from ...preview.service import PreviewService, FRAME_HEADER_FORMAT, MSG_TYPE_FRAME

logger = logging.getLogger(__name__)


class PreviewStartRequest(BaseModel):
  effect: str
  params: dict = {}
  fps: int = 30


def create_router(deps, require_auth) -> APIRouter:
  router = APIRouter(prefix="/api/preview", tags=["preview"])

  def _get_preview_service() -> PreviewService:
    if not hasattr(deps, 'preview_service') or deps.preview_service is None:
      raise HTTPException(503, "Preview service not available")
    return deps.preview_service

  @router.get("/status")
  async def preview_status():
    svc = _get_preview_service()
    return svc.get_status()

  @router.post("/start")
  async def preview_start(req: PreviewStartRequest, auth=Depends(require_auth)):
    svc = _get_preview_service()
    # Check preview_supported from catalog if available
    if hasattr(deps, 'effect_catalog') and deps.effect_catalog:
      meta = deps.effect_catalog.get_meta(req.effect)
      if meta and not meta.preview_supported:
        raise HTTPException(400, f"Effect '{req.effect}' does not support preview")

    # Merge in saved per-effect params so preview reflects what live would show
    params = dict(req.params or {})
    if hasattr(deps, 'state_manager') and deps.state_manager:
      saved = deps.state_manager.get_effect_params(req.effect)
      if saved:
        # Explicit params from request take precedence; saved fills gaps
        merged = dict(saved)
        merged.update(params)
        params = merged

    # Animation Switcher: inject default playlist if none present (mirrors scenes route)
    if req.effect == 'animation_switcher' and 'playlist' not in params:
      if hasattr(deps, 'effect_catalog') and deps.effect_catalog:
        catalog = deps.effect_catalog.get_catalog()
        entries = [
          (name, meta.label or name)
          for name, meta in catalog.items()
          if name != 'animation_switcher'
          and meta.group != 'diagnostic'
          and not name.startswith('diag_')
        ]
        entries.sort(key=lambda e: e[1].lower())
        params['playlist'] = [name for name, _ in entries]

    try:
      svc.start(req.effect, params, req.fps)
    except ValueError as e:
      raise HTTPException(404, str(e))
    return svc.get_status()

  @router.post("/stop")
  async def preview_stop(auth=Depends(require_auth)):
    svc = _get_preview_service()
    svc.stop()
    return {'status': 'stopped'}

  @router.websocket("/ws")
  async def preview_websocket(ws: WebSocket):
    svc = _get_preview_service()
    await ws.accept()
    svc.add_client(ws)
    try:
      while True:
        if svc.active:
          payload = svc.render_frame(deps.render_state)
          if payload:
            await ws.send_bytes(payload)
        await asyncio.sleep(1.0 / max(svc._fps, 1))
    except WebSocketDisconnect:
      pass
    except asyncio.CancelledError:
      pass
    finally:
      svc.remove_client(ws)

  @router.websocket("/live")
  async def live_preview_websocket(ws: WebSocket):
    """Stream the live renderer's logical frame at ~15 FPS for setup-panel simulator."""
    await ws.accept()
    frame_id = 0
    try:
      while True:
        renderer = deps.renderer
        frame = getattr(renderer, '_last_logical_frame', None)
        if frame is not None and frame.ndim == 3:
          width, height = frame.shape[0], frame.shape[1]
          frame_id += 1
          header = struct.pack(
            FRAME_HEADER_FORMAT, MSG_TYPE_FRAME, frame_id, width, height, 0,
          )
          await ws.send_bytes(header + frame.tobytes())
        await asyncio.sleep(1.0 / 15)
    except WebSocketDisconnect:
      pass
    except asyncio.CancelledError:
      pass

  return router
