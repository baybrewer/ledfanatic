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
      'beat': False, 'bpm': 0.0,
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
      'audio_beat': self.audio_beat,
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
    self._running = False
    self._gamma_lut = _build_gamma_lut(state.gamma)
    self._fps_samples: list[float] = []
    self._fps_window = 60
    self._last_frame_start: float = 0.0

  def register_effect(self, name: str, effect_class):
    self.effect_registry[name] = effect_class

  def set_scene(self, scene_name: str, params: Optional[dict] = None):
    if scene_name not in self.effect_registry:
      logger.warning(f"Unknown effect: {scene_name}")
      return False

    effect_cls = self.effect_registry[scene_name]
    self.current_effect = effect_cls(
      width=self.internal_width,
      height=N,
      params=params or {},
    )
    self.state.current_scene = scene_name
    logger.info(f"Scene set: {scene_name}")
    return True

  async def run(self):
    """Main render loop."""
    self._running = True
    logger.info(f"Render loop started at {self.state.target_fps} FPS target")

    while self._running:
      frame_start = time.monotonic()
      target_interval = 1.0 / self.state.target_fps

      try:
        await self._render_frame()
      except asyncio.CancelledError:
        break
      except Exception as e:
        logger.error(f"Render error: {e}", exc_info=True)
        self.state.frames_dropped += 1

      elapsed = time.monotonic() - frame_start
      self.state.last_frame_time_ms = elapsed * 1000

      self._fps_samples.append(elapsed)
      if len(self._fps_samples) > self._fps_window:
        self._fps_samples.pop(0)
      if self._fps_samples:
        avg = sum(self._fps_samples) / len(self._fps_samples)
        self.state.actual_fps = 1.0 / avg if avg > 0 else 0

      remaining = target_interval - elapsed
      if remaining > 0:
        await asyncio.sleep(remaining)
      else:
        self.state.frames_dropped += 1

  async def _render_frame(self):
    """Render one frame and send to Teensy."""
    from datetime import datetime, timezone

    if self.state.blackout:
      channel_data = np.zeros((5, 344, 3), dtype=np.uint8)
    elif self.current_effect is None:
      channel_data = np.zeros((5, 344, 3), dtype=np.uint8)
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

      channel_data = map_frame_fast(logical_frame)

    self.state.frames_rendered += 1

    # Send — only count as sent on success
    pixel_bytes = serialize_channels(channel_data)
    success = await self.transport.send_frame(pixel_bytes)
    if success:
      self.state.frames_sent += 1

  def stop(self):
    self._running = False

  def update_gamma(self, gamma: float):
    self.state.gamma = gamma
    self._gamma_lut = _build_gamma_lut(gamma)
