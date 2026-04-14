"""
Preview API routes — dedicated simulator/preview transport.

Routes only; service logic lives in pi/app/preview/service.py.
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from ...preview.service import PreviewService

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
    try:
      svc.start(req.effect, req.params, req.fps)
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

  return router
