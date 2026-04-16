"""
Core render loop.

Manages the scene -> render -> map -> send pipeline at the target FPS.
"""

import asyncio
import logging
import time
from typing import Optional

import numpy as np

from ..mapping.cylinder import map_frame_fast, serialize_channels, downsample_width, N
from ..mapping.runtime_mapper import map_frame_compiled, serialize_channels_compiled
from ..hardware_constants import CHANNELS, LEDS_PER_CHANNEL, STRIPS, LEDS_PER_STRIP
from ..transport.usb import TeensyTransport
from .brightness import BrightnessEngine

logger = logging.getLogger(__name__)


class RenderState:
  """Shared mutable state for the current render."""

  def __init__(self):
    self.target_fps: int = 60
    self.current_scene: Optional[str] = None
    self.blackout: bool = False
    self.gamma: float = 2.2

    # Audio modulation (updated by audio worker via snapshot)
    self._audio_lock_free: dict = {
      'level': 0.0, 'bass': 0.0, 'mid': 0.0, 'high': 0.0,
      'beat': False, 'bpm': 0.0, 'spectrum': [0.0] * 16,
    }

    # Stats — separated by concern
    self.actual_fps: float = 0.0
    self.frames_rendered: int = 0
    self.frames_sent: int = 0
    self.frames_dropped: int = 0
    self.last_frame_time_ms: float = 0.0
    self.render_cost_ms: float = 0.0

  def update_audio(self, snapshot: dict):
    """Receive thread-safe audio snapshot."""
    self._audio_lock_free = snapshot

  @property
  def audio_level(self) -> float:
    return self._audio_lock_free.get('level', 0.0)

  @property
  def audio_bass(self) -> float:
    return self._audio_lock_free.get('bass', 0.0)

  @property
  def audio_mid(self) -> float:
    return self._audio_lock_free.get('mid', 0.0)

  @property
  def audio_high(self) -> float:
    return self._audio_lock_free.get('high', 0.0)

  @property
  def audio_beat(self) -> bool:
    return self._audio_lock_free.get('beat', False)

  @property
  def audio_bpm(self) -> float:
    return self._audio_lock_free.get('bpm', 0.0)

  @property
  def audio_spectrum(self) -> list:
    return self._audio_lock_free.get('spectrum', [0.0] * 16)

  def to_dict(self) -> dict:
    return {
      'target_fps': self.target_fps,
      'actual_fps': round(self.actual_fps, 1),
      'current_scene': self.current_scene,
      'blackout': self.blackout,
      'frames_rendered': self.frames_rendered,
      'frames_sent': self.frames_sent,
      'frames_dropped': self.frames_dropped,
      'last_frame_time_ms': round(self.last_frame_time_ms, 2),
      'render_cost_ms': round(self.render_cost_ms, 2),
      'audio_level': round(self.audio_level, 3),
      'audio_bass': round(self.audio_bass, 3),
      'audio_mid': round(self.audio_mid, 3),
      'audio_high': round(self.audio_high, 3),
      'audio_beat': self.audio_beat,
      'audio_spectrum': [round(v, 3) for v in self.audio_spectrum],
    }


def _build_gamma_lut(gamma: float) -> np.ndarray:
  lut = np.zeros(256, dtype=np.uint8)
  for i in range(256):
    lut[i] = int(pow(i / 255.0, gamma) * 255.0 + 0.5)
  return lut


class Renderer:
  def __init__(self, transport: TeensyTransport, state: RenderState,
               brightness_engine: BrightnessEngine, internal_width: int = 40):
    self.transport = transport
    self.state = state
    self.brightness_engine = brightness_engine
    self.internal_width = internal_width
    self.effect_registry: dict = {}
    self.current_effect = None
    self._output_plan = None  # CompiledOutputPlan, set via apply_output_plan()
    self._test_strip_id: Optional[int] = None
    self._test_strip_until: float = 0.0
    self._running = False
    self._gamma_lut = _build_gamma_lut(state.gamma)
    self._fps_samples: list[float] = []
    self._fps_window = 60
    self._last_frame_start: float = 0.0
    # Last logical frame — snapshot after brightness+gamma,
    # read by live-preview WebSocket. Ring buffer of one frame.
    self._last_logical_frame = np.zeros((STRIPS, LEDS_PER_STRIP, 3), dtype=np.uint8)

  def register_effect(self, name: str, effect_class):
    self.effect_registry[name] = effect_class

  def apply_output_plan(self, plan):
    """Hot-swap the compiled output plan. Thread-safe: next frame picks it up."""
    self._output_plan = plan
    logger.info(f"Output plan applied: {plan.channels}ch x {plan.leds_per_channel}leds")

  def set_test_strip(self, strip_id: Optional[int], duration: float = 5.0):
    """Activate a test pattern on a single strip for identification."""
    if strip_id is not None:
      self._test_strip_id = strip_id
      self._test_strip_until = time.monotonic() + duration
    else:
      self._test_strip_id = None
      self._test_strip_until = 0.0

  def _set_scene(self, scene_name: str, params: Optional[dict] = None):
    if scene_name not in self.effect_registry:
      logger.warning(f"Unknown effect: {scene_name}")
      return False

    # Merge: code defaults < yaml config < caller params
    yaml_params = {}
    if hasattr(self, 'effects_config') and self.effects_config:
      for section in ('effects', 'audio_effects'):
        section_data = self.effects_config.get(section, {})
        if scene_name in section_data:
          yaml_params = section_data[scene_name].get('params', {})
          break
    merged = {**yaml_params, **(params or {})}

    # State-preserving: if same effect is already active, update params without reset
    if scene_name == self.state.current_scene and self.current_effect is not None:
      self.current_effect.update_params(merged)
      logger.info(f"Scene params updated: {scene_name}")
      return True

    effect_cls = self.effect_registry[scene_name]
    # Pass effect_registry to AnimationSwitcher so it can instantiate playlist effects
    if scene_name == 'animation_switcher':
      merged['_effect_registry'] = self.effect_registry
    effect_width = getattr(effect_cls, 'NATIVE_WIDTH', None) or self.internal_width
    effect_height = self._output_plan.logical_height if self._output_plan else N
    self.current_effect = effect_cls(
      width=effect_width,
      height=effect_height,
      params=merged,
    )
    self.state.current_scene = scene_name
    logger.info(f"Scene set: {scene_name}")
    return True

  def activate_scene(self, scene_name: str, params: Optional[dict] = None,
                     media_manager=None) -> bool:
    """Unified scene activation for all types (generative, audio, media)."""
    if scene_name.startswith('media:'):
      # State-preserving for media: same item → update params, don't reset playback
      if scene_name == self.state.current_scene and self.current_effect is not None:
        self.current_effect.update_params(params or {})
        return True
      item_id = scene_name[6:]
      if media_manager and item_id in media_manager.items:
        from ..effects.media_playback import MediaPlayback
        media_height = self._output_plan.logical_height if self._output_plan else N
        self.current_effect = MediaPlayback(
          width=self.internal_width,
          height=media_height,
          params={'item_id': item_id, **(params or {})},
          media_manager=media_manager,
        )
        self.state.current_scene = scene_name
        return True
      return False
    return self._set_scene(scene_name, params)

  async def run(self):
    """Main render loop."""
    self._running = True
    logger.info(f"Render loop started at {self.state.target_fps} FPS target")

    while self._running:
      frame_start = time.monotonic()
      target_interval = 1.0 / self.state.target_fps

      # Measure FPS from wall-clock interval between frame starts
      if self._last_frame_start > 0:
        frame_interval = frame_start - self._last_frame_start
        self._fps_samples.append(frame_interval)
        if len(self._fps_samples) > self._fps_window:
          self._fps_samples.pop(0)
        if self._fps_samples:
          avg_interval = sum(self._fps_samples) / len(self._fps_samples)
          self.state.actual_fps = 1.0 / avg_interval if avg_interval > 0 else 0
      self._last_frame_start = frame_start

      try:
        await self._render_frame()
      except asyncio.CancelledError:
        break
      except Exception as e:
        logger.error(f"Render error: {e}", exc_info=True)
        self.state.frames_dropped += 1

      # Track render+send cost (before sleep)
      render_elapsed = time.monotonic() - frame_start
      self.state.render_cost_ms = render_elapsed * 1000
      self.state.last_frame_time_ms = render_elapsed * 1000

      remaining = target_interval - render_elapsed
      if remaining > 0:
        await asyncio.sleep(remaining)
      else:
        self.state.frames_dropped += 1

  async def _render_frame(self):
    """Render one frame and send to Teensy."""
    from datetime import datetime, timezone

    plan = self._output_plan
    # Use plan dimensions when available, fall back to hardware constants
    ch = plan.channels if plan else CHANNELS
    lpc = plan.leds_per_channel if plan else LEDS_PER_CHANNEL

    lh = plan.logical_height if plan else LEDS_PER_STRIP
    lw = plan.logical_width if plan else STRIPS

    if self.state.blackout:
      channel_data = np.zeros((ch, lpc, 3), dtype=np.uint8)
      self._last_logical_frame = np.zeros((lw, lh, 3), dtype=np.uint8)
    elif self.current_effect is None:
      channel_data = np.zeros((ch, lpc, 3), dtype=np.uint8)
      self._last_logical_frame = np.zeros((lw, lh, 3), dtype=np.uint8)
    else:
      t = time.monotonic()
      internal_frame = self.current_effect.render(t, self.state)

      if internal_frame.shape[0] != 10:
        logical_frame = downsample_width(internal_frame, 10)
      else:
        logical_frame = internal_frame

      # Apply effective brightness from engine
      effective = self.brightness_engine.get_effective_brightness(
        datetime.now(timezone.utc)
      )
      logical_frame = (logical_frame * effective).astype(np.uint8)

      # Apply gamma
      logical_frame = self._gamma_lut[logical_frame]

      # Test strip pattern: override one logical column with gradient
      if self._test_strip_id is not None:
        if time.monotonic() < self._test_strip_until:
          logical_frame[:] = 0  # black out everything
          plan = self._output_plan
          if plan:
            for strip in plan.strips:
              if strip.strip_id == self._test_strip_id:
                col = strip.logical_order
                if col < logical_frame.shape[0]:
                  h = logical_frame.shape[1]
                  for y in range(h):
                    t = y / max(h - 1, 1)
                    logical_frame[col, y] = [int(255 * (1 - t)), 0, int(255 * t)]
                break
        else:
          self._test_strip_id = None

      # Snapshot logical frame for live preview (post-brightness/gamma/test-strip)
      self._last_logical_frame = logical_frame

      # Use compiled plan mapper when available, legacy mapper as fallback
      if plan:
        channel_data = map_frame_compiled(logical_frame, plan)
      else:
        channel_data = map_frame_fast(logical_frame)

    self.state.frames_rendered += 1

    # Send — only count as sent on success
    if plan:
      pixel_bytes = serialize_channels_compiled(channel_data)
    else:
      pixel_bytes = serialize_channels(channel_data)
    success = await self.transport.send_frame(pixel_bytes)
    if success:
      self.state.frames_sent += 1

  def stop(self):
    self._running = False

  def update_gamma(self, gamma: float):
    self.state.gamma = gamma
    self._gamma_lut = _build_gamma_lut(gamma)
