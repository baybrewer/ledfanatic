"""
Main entry point for the Pillar Controller application.
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

import uvicorn
import yaml

from .api.server import create_app
from .core.renderer import Renderer, RenderState
from .core.state import StateManager
from .core.brightness import BrightnessEngine
from .transport.usb import TeensyTransport
from .media.manager import MediaManager
from .audio.analyzer import AudioAnalyzer
from .effects.generative import EFFECTS
from .effects.audio_reactive import AUDIO_EFFECTS
from .diagnostics.patterns import DIAGNOSTIC_EFFECTS
from .config.installation import load_installation
from .config.spatial_map import load_spatial_map
from .setup.session import SetupSessionService
from .mapping.runtime_plan import load_controller_profile, compile_output_plan
from .preview.service import PreviewService

DEV_MODE = os.environ.get('PILLAR_DEV', '').strip() == '1'


def _resolve_paths():
  """Return (config_dir, media_dir, cache_dir, log_dir) based on environment."""
  if DEV_MODE or not Path("/opt/pillar").exists():
    base = Path(__file__).parent.parent
    return base / "config", base / "media", base / "cache", base / "logs"
  return (
    Path("/opt/pillar/config"),
    Path("/opt/pillar/media"),
    Path("/opt/pillar/cache"),
    Path("/opt/pillar/logs"),
  )


def _setup_logging(log_dir: Path):
  log_dir.mkdir(parents=True, exist_ok=True)
  logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
      logging.StreamHandler(sys.stdout),
      logging.FileHandler(log_dir / "pillar.log"),
    ],
  )


def _load_config(config_dir: Path) -> dict:
  config = {}
  for name in ('system.yaml', 'hardware.yaml', 'effects.yaml'):
    path = config_dir / name
    if path.exists():
      with open(path) as f:
        config[name.replace('.yaml', '')] = yaml.safe_load(f) or {}
  return config


def main():
  config_dir, media_dir, cache_dir, log_dir = _resolve_paths()
  _setup_logging(log_dir)
  logger = logging.getLogger(__name__)
  logger.info(f"Starting Pillar Controller (dev={DEV_MODE})")

  config = _load_config(config_dir)
  sys_conf = config.get('system', {})
  display_conf = sys_conf.get('display', {})
  transport_conf = sys_conf.get('transport', {})
  brightness_conf = sys_conf.get('brightness', {})
  render_conf = sys_conf.get('render', {})

  # State manager — load persisted values
  state_manager = StateManager(config_dir=config_dir)
  state_manager.load()

  # Brightness engine — config defaults first, then persisted overrides
  brightness_engine = BrightnessEngine(brightness_conf)
  if state_manager.brightness_manual_cap is not None:
    brightness_engine.manual_cap = state_manager.brightness_manual_cap
  if state_manager.brightness_auto_enabled is not None and state_manager.brightness_auto_enabled:
    brightness_engine.update_config({'auto_enabled': True})

  # Render state — config defaults first, then persisted overrides
  render_state = RenderState()
  render_state.gamma = display_conf.get('gamma', 2.2)
  if state_manager.target_fps is not None:
    render_state.target_fps = state_manager.target_fps
  else:
    render_state.target_fps = display_conf.get('target_fps', 60)

  # Transport
  transport = TeensyTransport(
    reconnect_interval=transport_conf.get('reconnect_interval_ms', 1000) / 1000,
    handshake_timeout=transport_conf.get('handshake_timeout_ms', 3000) / 1000,
  )

  # Renderer
  internal_width = render_conf.get('internal_width', 40)
  renderer = Renderer(transport, render_state, brightness_engine, internal_width=internal_width)
  effects_conf = config.get('effects', {})
  renderer.effects_config = effects_conf

  for name, cls in EFFECTS.items():
    renderer.register_effect(name, cls)
  for name, cls in AUDIO_EFFECTS.items():
    renderer.register_effect(name, cls)
  for name, cls in DIAGNOSTIC_EFFECTS.items():
    renderer.register_effect(name, cls)

  # Installation config — mutable strip setup truth
  installation = load_installation(config_dir)
  spatial_map = load_spatial_map(config_dir)
  controller_profile = load_controller_profile(config.get('hardware'))
  compiled_plan = compile_output_plan(installation, controller_profile)
  logger.info(f"Installation: {installation.profile_name}, {len(installation.strips)} strips, geometry={installation.geometry_mode}")
  logger.info(f"Compiled plan: {compiled_plan.channels}ch x {compiled_plan.leds_per_channel}leds")

  # Apply compiled plan to renderer so it uses plan-driven dimensions and mapping
  renderer.apply_output_plan(compiled_plan)
  if spatial_map:
    logger.info(f"Spatial map: {spatial_map.profile_id}, {len(spatial_map.visible_strips)} visible strips")

  # Media
  media_manager = MediaManager(media_dir=media_dir, cache_dir=cache_dir)
  media_manager.scan_library()

  # Audio
  audio_analyzer = AudioAnalyzer(render_state)

  # Startup scene
  startup = state_manager.current_scene or display_conf.get('startup_scene', 'rainbow_rotate')
  if not renderer.activate_scene(startup, state_manager.current_params, media_manager=media_manager):
    fallback = display_conf.get('startup_scene', 'rainbow_rotate')
    logger.warning(f"Failed to restore scene '{startup}', falling back to '{fallback}'")
    renderer.activate_scene(fallback)

  # Setup service
  setup_service = SetupSessionService(
    installation=installation,
    controller=controller_profile,
    config_dir=config_dir,
    renderer=renderer,
    render_state=render_state,
    state_manager=state_manager,
  )

  # Preview service
  preview_service = PreviewService(renderer)

  # Create app
  app = create_app(
    transport=transport,
    renderer=renderer,
    render_state=render_state,
    state_manager=state_manager,
    brightness_engine=brightness_engine,
    media_manager=media_manager,
    audio_analyzer=audio_analyzer,
    config=sys_conf,
    setup_session_service=setup_service,
    spatial_map=spatial_map,
    preview_service=preview_service,
  )

  # Track background tasks for clean shutdown
  _background_tasks: list[asyncio.Task] = []

  @app.on_event("startup")
  async def startup_tasks():
    _background_tasks.append(asyncio.create_task(transport.reconnect_loop()))
    _background_tasks.append(asyncio.create_task(renderer.run()))
    _background_tasks.append(asyncio.create_task(state_manager.flush_loop()))
    logger.info("Background tasks started")

  @app.on_event("shutdown")
  async def shutdown_tasks():
    logger.info("Shutting down...")
    renderer.stop()
    if hasattr(app.state, 'broadcast_task'):
      app.state.broadcast_task.cancel()
    for task in _background_tasks:
      task.cancel()
    await asyncio.gather(*_background_tasks, return_exceptions=True)
    audio_analyzer.stop()
    transport.disconnect()
    state_manager.force_save()
    logger.info("Shutdown complete")

  # Determine port
  if DEV_MODE:
    port = sys_conf.get('ui', {}).get('dev_port', 8000)
  else:
    port = sys_conf.get('ui', {}).get('port', 80)

  uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
  main()
