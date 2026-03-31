"""
FastAPI server — REST API + WebSocket + static file serving.
"""

import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from ..core.renderer import Renderer, RenderState
from ..core.state import StateManager
from ..transport.usb import TeensyTransport
from ..media.manager import MediaManager
from ..audio.analyzer import AudioAnalyzer
from ..effects.generative import EFFECTS
from ..effects.audio_reactive import AUDIO_EFFECTS
from ..effects.media_playback import MEDIA_EFFECTS, MediaPlayback
from ..diagnostics.tests import DIAGNOSTIC_EFFECTS
from ..models.protocol import TestPattern

logger = logging.getLogger(__name__)

UI_DIR = Path(__file__).parent.parent / "ui"


# --- Pydantic models ---

class SceneRequest(BaseModel):
  effect: str
  params: dict = {}

class BrightnessRequest(BaseModel):
  value: float

class FPSRequest(BaseModel):
  value: int

class SceneSaveRequest(BaseModel):
  name: str
  effect: str
  params: dict = {}

class TestPatternRequest(BaseModel):
  pattern: str

class AudioConfigRequest(BaseModel):
  device_index: Optional[int] = None
  sensitivity: float = 1.0
  gain: float = 1.0


def create_app(
  transport: TeensyTransport,
  renderer: Renderer,
  render_state: RenderState,
  state_manager: StateManager,
  media_manager: MediaManager,
  audio_analyzer: AudioAnalyzer,
) -> FastAPI:

  app = FastAPI(title="Pillar Controller", version="1.0.0")

  # WebSocket clients
  ws_clients: set[WebSocket] = set()

  # --- System ---

  @app.get("/api/system/status")
  async def system_status():
    return {
      'transport': transport.get_status(),
      'render': render_state.to_dict(),
      'scenes': list(state_manager.list_scenes().keys()),
      'media_count': len(media_manager.items),
    }

  @app.post("/api/system/reboot")
  async def system_reboot():
    os.system("sudo reboot")
    return {"status": "rebooting"}

  @app.post("/api/system/restart-app")
  async def restart_app():
    os.system("sudo systemctl restart pillar")
    return {"status": "restarting"}

  # --- Scenes ---

  @app.get("/api/scenes/list")
  async def list_effects():
    all_effects = {}
    for name in EFFECTS:
      all_effects[name] = {'type': 'generative'}
    for name in AUDIO_EFFECTS:
      all_effects[name] = {'type': 'audio'}
    for name in DIAGNOSTIC_EFFECTS:
      all_effects[name] = {'type': 'diagnostic'}
    return {'effects': all_effects, 'current': render_state.current_scene}

  @app.post("/api/scenes/activate")
  async def activate_scene(req: SceneRequest):
    success = renderer.set_scene(req.effect, req.params)
    if success:
      state_manager.current_scene = req.effect
      state_manager.current_params = req.params
      await broadcast_state()
      return {"status": "ok"}
    raise HTTPException(404, f"Unknown effect: {req.effect}")

  @app.get("/api/scenes/presets")
  async def list_presets():
    return state_manager.list_scenes()

  @app.post("/api/scenes/presets/save")
  async def save_preset(req: SceneSaveRequest):
    state_manager.save_scene(req.name, req.effect, req.params)
    return {"status": "saved"}

  @app.post("/api/scenes/presets/load/{name}")
  async def load_preset(name: str):
    scene = state_manager.load_scene(name)
    if not scene:
      raise HTTPException(404, f"Preset not found: {name}")
    success = renderer.set_scene(scene['effect'], scene.get('params', {}))
    if success:
      state_manager.current_scene = scene['effect']
      state_manager.current_params = scene.get('params', {})
      await broadcast_state()
      return {"status": "ok"}
    raise HTTPException(500, "Failed to activate preset")

  @app.delete("/api/scenes/presets/{name}")
  async def delete_preset(name: str):
    if state_manager.delete_scene(name):
      return {"status": "deleted"}
    raise HTTPException(404, f"Preset not found: {name}")

  # --- Display control ---

  @app.post("/api/display/brightness")
  async def set_brightness(req: BrightnessRequest):
    render_state.brightness = max(0.0, min(1.0, req.value))
    state_manager.brightness = render_state.brightness
    await broadcast_state()
    return {"brightness": render_state.brightness}

  @app.post("/api/display/fps")
  async def set_fps(req: FPSRequest):
    render_state.target_fps = max(1, min(90, req.value))
    state_manager.target_fps = render_state.target_fps
    await broadcast_state()
    return {"fps": render_state.target_fps}

  @app.post("/api/display/blackout")
  async def toggle_blackout():
    render_state.blackout = not render_state.blackout
    if render_state.blackout:
      await transport.send_blackout()
    await broadcast_state()
    return {"blackout": render_state.blackout}

  # --- Media ---

  @app.get("/api/media/list")
  async def list_media():
    return {"items": media_manager.list_items()}

  @app.post("/api/media/upload")
  async def upload_media(file: UploadFile = File(...)):
    # Save to temp file
    suffix = Path(file.filename or "upload").suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
      content = await file.read()
      tmp.write(content)
      tmp_path = Path(tmp.name)

    try:
      item = await media_manager.import_file(tmp_path, file.filename or "upload")
      if item:
        return {"status": "ok", "item": item.to_dict()}
      raise HTTPException(400, "Unsupported media type")
    finally:
      tmp_path.unlink(missing_ok=True)

  @app.post("/api/media/play/{item_id}")
  async def play_media(item_id: str, loop: bool = True, speed: float = 1.0):
    if item_id not in media_manager.items:
      raise HTTPException(404, f"Media not found: {item_id}")
    params = {'item_id': item_id, 'loop': loop, 'speed': speed}
    # Create media playback effect
    effect = MediaPlayback(
      width=renderer.internal_width,
      height=172,
      params=params,
      media_manager=media_manager,
    )
    renderer.current_effect = effect
    render_state.current_scene = f"media:{item_id}"
    await broadcast_state()
    return {"status": "playing", "item_id": item_id}

  @app.delete("/api/media/{item_id}")
  async def delete_media(item_id: str):
    if media_manager.delete_item(item_id):
      return {"status": "deleted"}
    raise HTTPException(404, f"Media not found: {item_id}")

  # --- Audio ---

  @app.get("/api/audio/devices")
  async def list_audio_devices():
    return {"devices": audio_analyzer.list_devices()}

  @app.post("/api/audio/config")
  async def configure_audio(req: AudioConfigRequest):
    audio_analyzer.sensitivity = req.sensitivity
    audio_analyzer.gain = req.gain
    if req.device_index is not None:
      audio_analyzer.set_device(req.device_index)
    return {"status": "ok"}

  @app.post("/api/audio/start")
  async def start_audio():
    audio_analyzer.start()
    return {"status": "started"}

  @app.post("/api/audio/stop")
  async def stop_audio():
    audio_analyzer.stop()
    return {"status": "stopped"}

  # --- Diagnostics ---

  @app.post("/api/diagnostics/test-pattern")
  async def run_test_pattern(req: TestPatternRequest):
    # Check if it's a Teensy-side pattern
    teensy_patterns = {p.name.lower(): p.value for p in TestPattern}
    if req.pattern.lower() in teensy_patterns:
      await transport.send_test_pattern(teensy_patterns[req.pattern.lower()])
      return {"status": "ok", "target": "teensy"}

    # Check Pi-side diagnostic effects
    if req.pattern in DIAGNOSTIC_EFFECTS:
      renderer.set_scene(req.pattern)
      return {"status": "ok", "target": "pi"}

    raise HTTPException(404, f"Unknown test pattern: {req.pattern}")

  @app.get("/api/diagnostics/stats")
  async def get_stats():
    teensy_stats = await transport.request_stats()
    return {
      'transport': transport.get_status(),
      'render': render_state.to_dict(),
      'teensy': teensy_stats,
    }

  @app.get("/api/transport/status")
  async def transport_status():
    return transport.get_status()

  # --- WebSocket ---

  @app.websocket("/ws")
  async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    ws_clients.add(ws)
    try:
      # Send initial state
      await ws.send_json(render_state.to_dict())
      while True:
        # Keep connection alive, handle client messages
        data = await ws.receive_text()
        try:
          msg = json.loads(data)
          await handle_ws_message(msg, ws)
        except json.JSONDecodeError:
          pass
    except WebSocketDisconnect:
      pass
    finally:
      ws_clients.discard(ws)

  async def handle_ws_message(msg: dict, ws: WebSocket):
    action = msg.get('action')
    if action == 'ping':
      await ws.send_json({'action': 'pong'})
    elif action == 'get_state':
      await ws.send_json(render_state.to_dict())

  async def broadcast_state():
    data = render_state.to_dict()
    dead = set()
    for ws in ws_clients:
      try:
        await ws.send_json(data)
      except Exception:
        dead.add(ws)
    ws_clients -= dead

  # Background broadcast task
  @app.on_event("startup")
  async def start_broadcast():
    async def periodic_broadcast():
      while True:
        await broadcast_state()
        await asyncio.sleep(0.5)
    asyncio.create_task(periodic_broadcast())

  # --- Static files (UI) ---

  @app.get("/")
  async def root():
    index = UI_DIR / "static" / "index.html"
    if index.exists():
      return FileResponse(index)
    return JSONResponse({"error": "UI not found"}, status_code=404)

  app.mount("/static", StaticFiles(directory=str(UI_DIR / "static")), name="static")

  return app
