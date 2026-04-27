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
from .effects.registry import ALL_EFFECTS
from .effects.imported import IMPORTED_EFFECTS
from .layout import load_layout, compile_layout, validate_layout, output_config_list, CompiledLayout
from .config.spatial_map import load_spatial_map
from .preview.service import PreviewService
from .effects.catalog import EffectCatalogService, EffectMeta

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
      try:
        with open(path) as f:
          config[name.replace('.yaml', '')] = yaml.safe_load(f) or {}
      except yaml.YAMLError as e:
        logging.getLogger(__name__).error(f"Failed to parse {path}: {e} — using empty config")
        config[name.replace('.yaml', '')] = {}
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

  # Layout — load, validate, compile
  layout_config = load_layout(config_dir)
  errors = validate_layout(layout_config)
  if errors:
    for err in errors:
      logger.error(f"Layout validation: {err}")
    logger.error("Layout has errors — using empty config (no LEDs)")
    from .layout.schema import LayoutConfig as _LayoutConfig, MatrixConfig as _MatrixConfig
    layout_config = _LayoutConfig(version=1, matrix=_MatrixConfig(width=0, height=0))
  compiled_layout = compile_layout(layout_config)
  logger.info(
    f"Layout: {compiled_layout.width}x{compiled_layout.height} grid, "
    f"{len(layout_config.outputs)} outputs, {compiled_layout.total_mapped} LEDs"
  )

  # Renderer
  renderer = Renderer(transport, render_state, brightness_engine, compiled_layout)
  renderer._rebuild_segment_cache_from_config(layout_config)
  effects_conf = config.get('effects', {})
  renderer.effects_config = effects_conf

  for name, cls in ALL_EFFECTS.items():
    renderer.register_effect(name, cls)
  logger.info(f"Registered {len(ALL_EFFECTS)} effects from unified registry")

  # Create shared catalog with all effects including imported
  effect_catalog = EffectCatalogService()
  from .effects.engine.palettes import PALETTE_NAMES, FELDSTEIN_PALETTE_NAMES
  for name, cls in IMPORTED_EFFECTS.items():
    # Extract param metadata from class PARAMS attribute
    param_dicts = ()
    if hasattr(cls, 'PARAMS') and cls.PARAMS:
      param_dicts = tuple(
        {'name': p.attr.lower(), 'label': p.label, 'min': p.lo, 'max': p.hi,
         'step': p.step, 'default': p.default, 'type': 'slider'}
        for p in cls.PARAMS
      )
    # Determine available palettes
    palette_support = getattr(cls, 'PALETTE_SUPPORT', False)
    palettes = ()
    if palette_support:
      palettes = tuple(PALETTE_NAMES)
    # Feldstein2 has custom palettes
    if name == 'feldstein_og':
      palettes = tuple(FELDSTEIN_PALETTE_NAMES)
      palette_support = True

    meta = EffectMeta(
      name=name,
      label=getattr(cls, 'DISPLAY_NAME', name.replace('_', ' ').title()),
      group=getattr(cls, 'CATEGORY', 'imported'),
      description=getattr(cls, 'DESCRIPTION', ''),
      imported=True,
      audio_requires=getattr(cls, 'AUDIO_REQUIRES', ()),
      params=param_dicts,
      palettes=palettes,
      palette_support=palette_support,
    )
    effect_catalog.register_imported(name, meta)

  # Register Animation Switcher in catalog
  effect_catalog.register_imported('animation_switcher', EffectMeta(
    name='animation_switcher',
    label='Animation Switcher',
    group='special',
    description='Automatically cycles through selected animations with cross-fade',
    imported=True,
    params=(
      {'name': 'interval', 'label': 'Switch Time (s)', 'min': 5, 'max': 120, 'step': 1, 'default': 15, 'type': 'slider'},
      {'name': 'fade_duration', 'label': 'Fade Duration (s)', 'min': 0.5, 'max': 5.0, 'step': 0.5, 'default': 2.0, 'type': 'slider'},
    ),
  ))

  # Register SR Fireworks in catalog
  effect_catalog.register_imported('sr_fireworks', EffectMeta(
    name='sr_fireworks',
    label='SR Fireworks',
    group='sound',
    description='Beat-triggered fireworks with trailing sparks',
    imported=True,
    params=(
      {'name': 'gain', 'label': 'Gain', 'min': 0.5, 'max': 5.0, 'step': 0.1, 'default': 2.0, 'type': 'slider'},
      {'name': 'gravity', 'label': 'Gravity', 'min': 10, 'max': 80, 'step': 5, 'default': 40, 'type': 'slider'},
      {'name': 'spark_count', 'label': 'Sparks', 'min': 10, 'max': 80, 'step': 5, 'default': 40, 'type': 'slider'},
    ),
  ))

  # Register Scrolling Text in catalog
  effect_catalog.register_imported('scrolling_text', EffectMeta(
    name='scrolling_text',
    label='Scrolling Text',
    group='generative',
    description='Scroll a message up the pillar',
    imported=True,
    params=(
      {'name': 'speed', 'label': 'Speed', 'min': 0.1, 'max': 5.0, 'step': 0.1, 'default': 1.0, 'type': 'slider'},
      {'name': 'text', 'label': 'Message', 'type': 'text', 'default': 'LED FANATIC'},
      {'name': 'color', 'label': 'Color', 'type': 'color', 'default': '#00FFFF'},
    ),
  ))

  # Register Tetris effects in catalog
  effect_catalog.register_imported('tetris_auto', EffectMeta(
    name='tetris_auto',
    label='Tetris (Auto)',
    group='generative',
    description='Watch an AI play Tetris endlessly',
    imported=True,
    params=(
      {'name': 'speed', 'label': 'Speed', 'min': 0.1, 'max': 11.0, 'step': 0.1, 'default': 1.0, 'type': 'slider'},
    ),
  ))
  effect_catalog.register_imported('tetris', EffectMeta(
    name='tetris',
    label='Tetris (Play)',
    group='game',
    description='Playable Tetris — use the Game tab to control',
    imported=True,
  ))

  # Register simulation effects in catalog
  effect_catalog.register_imported('fluid_sim', EffectMeta(
    name='fluid_sim',
    label='Fluid Dynamics',
    group='simulation',
    description='Navier-Stokes fluid simulation — audio drives the flow',
    imported=True,
    audio_requires=('level', 'bass', 'mid', 'high', 'beat'),
    params=(
      {'name': 'viscosity', 'label': 'Viscosity', 'min': 0.0, 'max': 0.001, 'step': 0.00005, 'default': 0.0, 'type': 'slider'},
      {'name': 'diffusion', 'label': 'Diffusion', 'min': 0.0, 'max': 0.001, 'step': 0.00005, 'default': 0.0, 'type': 'slider'},
      {'name': 'force', 'label': 'Force', 'min': 1.0, 'max': 20.0, 'step': 0.5, 'default': 8.0, 'type': 'slider'},
      {'name': 'pressure_iters', 'label': 'Pressure Iters', 'min': 1, 'max': 20, 'step': 1, 'default': 2, 'type': 'slider'},
    ),
  ))
  effect_catalog.register_imported('reaction_diffusion', EffectMeta(
    name='reaction_diffusion',
    label='Reaction Diffusion',
    group='simulation',
    description='Gray-Scott model — organic coral-like patterns that grow and breathe',
    imported=True,
    params=(
      {'name': 'feed', 'label': 'Feed Rate', 'min': 0.01, 'max': 0.08, 'step': 0.001, 'default': 0.037, 'type': 'slider'},
      {'name': 'kill', 'label': 'Kill Rate', 'min': 0.04, 'max': 0.075, 'step': 0.001, 'default': 0.06, 'type': 'slider'},
      {'name': 'speed', 'label': 'Speed', 'min': 1, 'max': 20, 'step': 1, 'default': 8, 'type': 'slider'},
      {'name': 'color_speed', 'label': 'Color Cycle', 'min': 0.0, 'max': 1.0, 'step': 0.05, 'default': 0.2, 'type': 'slider'},
    ),
  ))
  effect_catalog.register_imported('wave_equation', EffectMeta(
    name='wave_equation',
    label='Wave Equation',
    group='simulation',
    description='2D wave simulation — beats create ripples that interfere and decay',
    imported=True,
    audio_requires=('level', 'bass', 'beat'),
    params=(
      {'name': 'wave_speed', 'label': 'Wave Speed', 'min': 0.1, 'max': 1.0, 'step': 0.05, 'default': 0.5, 'type': 'slider'},
      {'name': 'damping', 'label': 'Damping', 'min': 0.9, 'max': 0.999, 'step': 0.001, 'default': 0.985, 'type': 'slider'},
      {'name': 'color_speed', 'label': 'Color Cycle', 'min': 0.0, 'max': 1.0, 'step': 0.05, 'default': 0.3, 'type': 'slider'},
    ),
  ))
  effect_catalog.register_imported('boids', EffectMeta(
    name='boids',
    label='Boids Flock',
    group='simulation',
    description='Emergent flocking — separation, alignment, cohesion create a living swarm',
    imported=True,
    params=(
      {'name': 'count', 'label': 'Count', 'min': 20, 'max': 200, 'step': 10, 'default': 80, 'type': 'slider'},
      {'name': 'speed', 'label': 'Speed', 'min': 5.0, 'max': 60.0, 'step': 1.0, 'default': 25.0, 'type': 'slider'},
      {'name': 'trail', 'label': 'Trail', 'min': 0.0, 'max': 0.95, 'step': 0.05, 'default': 0.7, 'type': 'slider'},
      {'name': 'color_speed', 'label': 'Color Cycle', 'min': 0.0, 'max': 1.0, 'step': 0.05, 'default': 0.2, 'type': 'slider'},
    ),
  ))

  # Spatial map (optional front-projection geometry)
  spatial_map = load_spatial_map(config_dir)
  if spatial_map:
    logger.info(f"Spatial map: {spatial_map.profile_id}, {len(spatial_map.visible_strips)} visible strips")

  # Media
  media_manager = MediaManager(media_dir=media_dir, cache_dir=cache_dir)
  media_manager.scan_library()

  # Audio
  audio_analyzer = AudioAnalyzer(render_state)

  # Restore saved band sensitivities
  if state_manager.audio_bass_sensitivity is not None:
    audio_analyzer.bass_sensitivity = state_manager.audio_bass_sensitivity
  if state_manager.audio_mid_sensitivity is not None:
    audio_analyzer.mid_sensitivity = state_manager.audio_mid_sensitivity
  if state_manager.audio_treble_sensitivity is not None:
    audio_analyzer.treble_sensitivity = state_manager.audio_treble_sensitivity

  # Auto-start audio with USB mic if available
  usb_devices = [d for d in audio_analyzer.list_devices() if 'usb' in d['name'].lower()]
  if usb_devices:
    audio_analyzer.set_device(usb_devices[0]['index'])
    logger.info(f"Auto-selected USB mic: {usb_devices[0]['name']} (device {usb_devices[0]['index']})")
  audio_analyzer.start()
  logger.info("Audio analyzer auto-started")

  # Startup scene
  startup = state_manager.current_scene or display_conf.get('startup_scene', 'rainbow_rotate')
  if not renderer.activate_scene(startup, state_manager.current_params, media_manager=media_manager):
    fallback = display_conf.get('startup_scene', 'rainbow_rotate')
    logger.warning(f"Failed to restore scene '{startup}', falling back to '{fallback}'")
    renderer.activate_scene(fallback)

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
    spatial_map=spatial_map,
    preview_service=preview_service,
    effect_catalog=effect_catalog,
    layout_config=layout_config,
    compiled_layout=compiled_layout,
    config_dir=config_dir,
  )

  # Track background tasks for clean shutdown
  _background_tasks: list[asyncio.Task] = []

  @app.on_event("startup")
  async def startup_tasks():
    # Register CONFIG resend callback — fires on every connect/reconnect
    # References app.state.deps.compiled_layout so layout changes propagate
    async def _on_teensy_connect():
      logger.info("Sending CONFIG to Teensy...")
      deps = app.state.deps
      current_layout = deps.compiled_layout
      if current_layout is None:
        logger.warning("No compiled layout available for CONFIG")
        return
      oc = output_config_list(current_layout)
      ok = await transport.send_config(oc)
      if ok:
        logger.info("CONFIG ACK received")
      else:
        logger.warning("CONFIG send failed (NAK/timeout)")
    transport._on_connect_callback = _on_teensy_connect

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
