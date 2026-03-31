"""
Main entry point for the Pillar Controller application.
"""

import asyncio
import logging
import signal
import sys
from pathlib import Path

import uvicorn
import yaml

from .api.server import create_app
from .core.renderer import Renderer, RenderState
from .core.state import StateManager
from .transport.usb import TeensyTransport
from .media.manager import MediaManager
from .audio.analyzer import AudioAnalyzer
from .effects.generative import EFFECTS
from .effects.audio_reactive import AUDIO_EFFECTS
from .diagnostics.tests import DIAGNOSTIC_EFFECTS

# Config paths — use local dev paths if /opt/pillar doesn't exist
if Path("/opt/pillar").exists():
  CONFIG_DIR = Path("/opt/pillar/config")
  MEDIA_DIR = Path("/opt/pillar/media")
  CACHE_DIR = Path("/opt/pillar/cache")
  LOG_DIR = Path("/opt/pillar/logs")
else:
  BASE = Path(__file__).parent.parent
  CONFIG_DIR = BASE / "config"
  MEDIA_DIR = BASE / "media"
  CACHE_DIR = BASE / "cache"
  LOG_DIR = BASE / "logs"


def setup_logging():
  LOG_DIR.mkdir(parents=True, exist_ok=True)
  logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
      logging.StreamHandler(sys.stdout),
      logging.FileHandler(LOG_DIR / "pillar.log"),
    ],
  )


def load_config() -> dict:
  system_config = {}
  for name in ('system.yaml', 'hardware.yaml', 'effects.yaml'):
    path = CONFIG_DIR / name
    if path.exists():
      with open(path) as f:
        system_config[name.replace('.yaml', '')] = yaml.safe_load(f)
  return system_config


def main():
  setup_logging()
  logger = logging.getLogger(__name__)
  logger.info("Starting Pillar Controller")

  config = load_config()
  sys_conf = config.get('system', {})
  display_conf = sys_conf.get('display', {})
  transport_conf = sys_conf.get('transport', {})

  # Initialize components
  render_state = RenderState()
  render_state.brightness = display_conf.get('brightness_cap', 0.8)
  render_state.gamma = display_conf.get('gamma', 2.2)
  render_state.target_fps = display_conf.get('target_fps', 60)

  state_manager = StateManager(config_dir=CONFIG_DIR)
  state_manager.load()

  # Restore saved state
  render_state.brightness = state_manager.brightness
  render_state.target_fps = state_manager.target_fps

  transport = TeensyTransport(
    reconnect_interval=transport_conf.get('reconnect_interval_ms', 1000) / 1000,
    handshake_timeout=transport_conf.get('handshake_timeout_ms', 3000) / 1000,
  )

  internal_width = sys_conf.get('render', {}).get('internal_width', 40)
  renderer = Renderer(transport, render_state, internal_width=internal_width)

  # Register all effects
  for name, cls in EFFECTS.items():
    renderer.register_effect(name, cls)
  for name, cls in AUDIO_EFFECTS.items():
    renderer.register_effect(name, cls)
  for name, cls in DIAGNOSTIC_EFFECTS.items():
    renderer.register_effect(name, cls)

  media_manager = MediaManager(media_dir=MEDIA_DIR, cache_dir=CACHE_DIR)
  media_manager.scan_library()

  audio_analyzer = AudioAnalyzer(render_state)

  # Set startup scene
  startup = state_manager.current_scene or display_conf.get('startup_scene', 'rainbow_rotate')
  renderer.set_scene(startup, state_manager.current_params)

  # Create FastAPI app
  app = create_app(
    transport=transport,
    renderer=renderer,
    render_state=render_state,
    state_manager=state_manager,
    media_manager=media_manager,
    audio_analyzer=audio_analyzer,
  )

  # Start background tasks
  @app.on_event("startup")
  async def startup_tasks():
    asyncio.create_task(transport.reconnect_loop())
    asyncio.create_task(renderer.run())
    logger.info("Background tasks started")

  # Run server
  port = sys_conf.get('ui', {}).get('dev_port', 8000)
  uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
  main()
