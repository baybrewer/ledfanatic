"""
FastAPI server — REST API + WebSocket + static file serving.
"""

import asyncio
import json
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, HTTPException, Depends, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from ..core.renderer import Renderer, RenderState
from ..core.state import StateManager
from ..core.brightness import BrightnessEngine
from ..transport.usb import TeensyTransport
from ..media.manager import MediaManager
from ..audio.analyzer import AudioAnalyzer
from ..effects.generative import EFFECTS
from ..effects.audio_reactive import AUDIO_EFFECTS
from ..effects.media_playback import MediaPlayback
from ..diagnostics.tests import DIAGNOSTIC_EFFECTS
from ..models.protocol import TestPattern
from .auth import create_auth_dependency

logger = logging.getLogger(__name__)

UI_DIR = Path(__file__).parent.parent / "ui"

ALLOWED_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.bmp', '.gif', '.mp4', '.mov', '.avi', '.webm', '.mkv'}


# --- Pydantic models ---

class SceneRequest(BaseModel):
  effect: str
  params: dict = {}

class BrightnessConfigRequest(BaseModel):
  manual_cap: Optional[float] = None
  auto_enabled: Optional[bool] = None
  location: Optional[dict] = None
  solar: Optional[dict] = None

class BlackoutRequest(BaseModel):
  enabled: bool

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
  brightness_engine: BrightnessEngine,
  media_manager: MediaManager,
  audio_analyzer: AudioAnalyzer,
  config: dict,
) -> FastAPI:

  app = FastAPI(title="Pillar Controller", version="1.0.0")

  max_upload_bytes = config.get('transport', {}).get('max_upload_mb', 50) * 1024 * 1024

  # Auth dependency
  require_auth = create_auth_dependency(config)

  # WebSocket clients
  ws_clients: set[WebSocket] = set()

  # --- System (public reads, protected writes) ---

  @app.get("/api/system/status")
  async def system_status():
    return {
      'transport': {'connected': transport.connected, 'port': transport.serial.port if transport.serial else None},
      'render': render_state.to_dict(),
      'brightness': brightness_engine.get_status(),
      'scenes_count': len(state_manager.list_scenes()),
      'media_count': len(media_manager.items),
    }

  @app.post("/api/system/reboot", dependencies=[Depends(require_auth)])
  async def system_reboot():
    subprocess.Popen(["sudo", "reboot"])
    return {"status": "rebooting"}

  @app.post("/api/system/restart-app", dependencies=[Depends(require_auth)])
  async def restart_app():
    subprocess.Popen(["sudo", "systemctl", "restart", "pillar"])
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

  @app.post("/api/scenes/activate", dependencies=[Depends(require_auth)])
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

  @app.post("/api/scenes/presets/save", dependencies=[Depends(require_auth)])
  async def save_preset(req: SceneSaveRequest):
    state_manager.save_scene(req.name, req.effect, req.params)
    return {"status": "saved"}

  @app.post("/api/scenes/presets/load/{name}", dependencies=[Depends(require_auth)])
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

  @app.delete("/api/scenes/presets/{name}", dependencies=[Depends(require_auth)])
  async def delete_preset(name: str):
    if state_manager.delete_scene(name):
      return {"status": "deleted"}
    raise HTTPException(404, f"Preset not found: {name}")

  # --- Display control ---

  @app.get("/api/brightness/status")
  async def brightness_status():
    return brightness_engine.get_status()

  @app.post("/api/brightness/config", dependencies=[Depends(require_auth)])
  async def update_brightness(req: BrightnessConfigRequest):
    update = {}
    if req.manual_cap is not None:
      update['manual_cap'] = req.manual_cap
      state_manager.brightness_manual_cap = req.manual_cap
    if req.auto_enabled is not None:
      update['auto_enabled'] = req.auto_enabled
      state_manager.brightness_auto_enabled = req.auto_enabled
    if req.location is not None:
      update['location'] = req.location
    if req.solar is not None:
      update['solar'] = req.solar
    if update:
      brightness_engine.update_config(update)
    await broadcast_state()
    return brightness_engine.get_status()

  @app.post("/api/display/brightness", dependencies=[Depends(require_auth)])
  async def set_brightness(req: BrightnessConfigRequest):
    """Legacy endpoint — sets manual cap."""
    if req.manual_cap is not None:
      brightness_engine.manual_cap = req.manual_cap
      state_manager.brightness_manual_cap = req.manual_cap
    await broadcast_state()
    return brightness_engine.get_status()

  @app.post("/api/display/fps", dependencies=[Depends(require_auth)])
  async def set_fps(req: FPSRequest):
    render_state.target_fps = max(1, min(90, req.value))
    state_manager.target_fps = render_state.target_fps
    await broadcast_state()
    return {"fps": render_state.target_fps}

  @app.post("/api/display/blackout", dependencies=[Depends(require_auth)])
  async def set_blackout(req: BlackoutRequest):
    render_state.blackout = req.enabled
    await transport.send_blackout(req.enabled)
    await broadcast_state()
    return {"blackout": render_state.blackout}

  # --- Media ---

  @app.get("/api/media/list")
  async def list_media():
    return {"items": media_manager.list_items()}

  @app.post("/api/media/upload", dependencies=[Depends(require_auth)])
  async def upload_media(file: UploadFile = File(...)):
    # Validate extension
    suffix = Path(file.filename or "upload").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
      raise HTTPException(400, f"Unsupported file type: {suffix}")

    # Stream to temp file with size limit
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
      tmp_path = Path(tmp.name)
      total_bytes = 0
      while True:
        chunk = await file.read(64 * 1024)
        if not chunk:
          break
        total_bytes += len(chunk)
        if total_bytes > max_upload_bytes:
          tmp_path.unlink(missing_ok=True)
          raise HTTPException(413, f"File exceeds maximum upload size ({max_upload_bytes // (1024*1024)}MB)")
        tmp.write(chunk)

    try:
      item = await media_manager.import_file(tmp_path, file.filename or "upload")
      if item:
        return {"status": "ok", "item": item.to_dict()}
      raise HTTPException(400, "Failed to import media")
    finally:
      tmp_path.unlink(missing_ok=True)

  @app.post("/api/media/play/{item_id}", dependencies=[Depends(require_auth)])
  async def play_media(item_id: str, loop: bool = True, speed: float = 1.0):
    if item_id not in media_manager.items:
      raise HTTPException(404, f"Media not found: {item_id}")
    params = {'loop': loop, 'speed': speed}
    success = renderer.activate_scene(
      f"media:{item_id}", params, media_manager=media_manager,
    )
    if not success:
      raise HTTPException(500, f"Failed to activate media: {item_id}")
    await broadcast_state()
    return {"status": "playing", "item_id": item_id}

  @app.delete("/api/media/{item_id}", dependencies=[Depends(require_auth)])
  async def delete_media(item_id: str):
    if media_manager.delete_item(item_id):
      return {"status": "deleted"}
    raise HTTPException(404, f"Media not found: {item_id}")

  # --- Audio ---

  @app.get("/api/audio/devices")
  async def list_audio_devices():
    return {"devices": audio_analyzer.list_devices()}

  @app.post("/api/audio/config", dependencies=[Depends(require_auth)])
  async def configure_audio(req: AudioConfigRequest):
    audio_analyzer.sensitivity = req.sensitivity
    audio_analyzer.gain = req.gain
    if req.device_index is not None:
      audio_analyzer.set_device(req.device_index)
    return {"status": "ok"}

  @app.post("/api/audio/start", dependencies=[Depends(require_auth)])
  async def start_audio():
    audio_analyzer.start()
    return {"status": "started"}

  @app.post("/api/audio/stop", dependencies=[Depends(require_auth)])
  async def stop_audio():
    audio_analyzer.stop()
    return {"status": "stopped"}

  # --- Diagnostics ---

  @app.post("/api/diagnostics/test-pattern", dependencies=[Depends(require_auth)])
  async def run_test_pattern(req: TestPatternRequest):
    teensy_patterns = {p.name.lower(): p.value for p in TestPattern}
    if req.pattern.lower() in teensy_patterns:
      await transport.send_test_pattern(teensy_patterns[req.pattern.lower()])
      return {"status": "ok", "target": "teensy"}

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
      'brightness': brightness_engine.get_status(),
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
      await ws.send_json(render_state.to_dict())
      while True:
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
      state = render_state.to_dict()
      state['brightness'] = brightness_engine.get_status()
      await ws.send_json(state)

  async def broadcast_state():
    data = render_state.to_dict()
    data['brightness'] = brightness_engine.get_status()
    dead = set()
    for ws in ws_clients:
      try:
        await ws.send_json(data)
      except Exception:
        dead.add(ws)
    ws_clients -= dead

  @app.on_event("startup")
  async def start_broadcast():
    async def periodic_broadcast():
      while True:
        try:
          await broadcast_state()
          await asyncio.sleep(0.5)
        except asyncio.CancelledError:
          break
    asyncio.create_task(periodic_broadcast())

  # --- Static files ---

  @app.get("/")
  async def root():
    index = UI_DIR / "static" / "index.html"
    if index.exists():
      return FileResponse(index)
    return JSONResponse({"error": "UI not found"}, status_code=404)

  app.mount("/static", StaticFiles(directory=str(UI_DIR / "static")), name="static")

  return app
