"""
Core render loop.

Manages the scene -> render -> map -> send pipeline at the target FPS.
"""

import asyncio
import logging
import time
from typing import Optional

import numpy as np

from ..layout import pack_frame, CompiledLayout, _expand_segment
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
    self.origin: str = 'bottom_left'
    self.grid_width: int = 0
    self.grid_height: int = 0

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


def _hue_to_rgb(hue_deg: float) -> tuple[int, int, int]:
  """Convert hue (0-360) to RGB at full saturation/value."""
  h = (hue_deg % 360) / 60
  i = int(h)
  f = h - i
  q = int(255 * (1 - f))
  t = int(255 * f)
  if i == 0: return (255, t, 0)
  if i == 1: return (q, 255, 0)
  if i == 2: return (0, 255, t)
  if i == 3: return (0, q, 255)
  if i == 4: return (t, 0, 255)
  return (255, 0, q)


class Renderer:
  def __init__(self, transport: TeensyTransport, state: RenderState,
               brightness_engine: BrightnessEngine, layout: CompiledLayout):
    self.transport = transport
    self.state = state
    self.brightness_engine = brightness_engine
    self.layout = layout
    self.effect_registry: dict = {}
    self.current_effect = None
    self._test_segment_id: Optional[str] = None
    self._test_mode: Optional[str] = None  # "segment", "segment_identify", "strip_identify", "probe"
    self._test_strip_until: float = 0.0
    self._probe_strip: int = -1
    self._probe_led: int = -1
    self._running = False
    self._gamma_lut = _build_gamma_lut(state.gamma)
    self._fps_samples: list[float] = []
    self._fps_window = 60
    self._last_frame_start: float = 0.0
    # Segment positions cache for test pattern support
    self._segment_positions: dict[str, list[tuple[int, int]]] = {}
    # Last logical (width×height×3 uint8) frame — snapshot after brightness+gamma,
    # read by live-preview WebSocket. Ring buffer of one frame.
    self._last_logical_frame = np.zeros((layout.width, layout.height, 3), dtype=np.uint8)
    # Populate state with grid dimensions
    self.state.grid_width = layout.width
    self.state.grid_height = layout.height
    self.state.origin = layout.origin

  def register_effect(self, name: str, effect_class):
    self.effect_registry[name] = effect_class

  def apply_layout(self, layout: CompiledLayout, layout_config=None):
    """Hot-swap the compiled layout. Thread-safe: next frame picks it up."""
    self.layout = layout
    self._last_logical_frame = np.zeros((layout.width, layout.height, 3), dtype=np.uint8)
    self.state.grid_width = layout.width
    self.state.grid_height = layout.height
    self.state.origin = layout.origin
    # Rebuild segment cache if config provided
    if layout_config is not None:
      self._rebuild_segment_cache_from_config(layout_config)
    # Recreate current effect at new dimensions (force recreation, not update_params)
    if self.state.current_scene and self.state.current_scene in self.effect_registry:
      saved_scene = self.state.current_scene
      self.state.current_scene = None  # Clear to bypass state-preserving check
      self.current_effect = None
      self._set_scene(saved_scene)
    logger.info(f"Layout applied: {layout.width}x{layout.height} grid, {layout.total_mapped} LEDs")

  def _rebuild_segment_cache_from_config(self, layout_config):
    """Build segment positions cache from layout config for test patterns."""
    self._segment_positions = {}
    self._segment_ids_ordered = []
    self._output_segment_ids = {}  # channel -> [seg_ids]
    for output in layout_config.outputs:
      channel_segs = []
      for seg in output.segments:
        if seg.enabled:
          self._segment_positions[seg.id] = _expand_segment(seg)
          self._segment_ids_ordered.append(seg.id)
          channel_segs.append(seg.id)
      self._output_segment_ids[output.channel] = channel_segs

  def set_test_strip(self, segment_id: Optional[str] = None, duration: float = 5.0):
    """Activate a test pattern on a single segment for identification."""
    if segment_id is not None:
      self._test_segment_id = segment_id
      self._test_mode = "segment"
      self._test_strip_until = time.monotonic() + duration
    else:
      self._test_segment_id = None
      self._test_mode = None
      self._test_strip_until = 0.0

  def set_test_identify(self, mode: str = "segment_identify", duration: float = 10.0):
    """Activate segment-identify or strip-identify test pattern.

    mode="segment_identify": each segment gets a unique color (matching UI swatch).
    mode="strip_identify": each output channel gets a uniform color.
    """
    self._test_mode = mode
    self._test_segment_id = None
    self._test_strip_until = time.monotonic() + duration

  def set_probe(self, strip: int, led: int):
    """Light a single LED by strip/wire position. No timeout — stays until cleared."""
    self._probe_strip = strip
    self._probe_led = led
    self._test_mode = "probe"
    self._test_strip_until = time.monotonic() + 600  # 10 minutes

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
    width = self.layout.width
    height = self.layout.height
    render_scale = getattr(effect_cls, 'RENDER_SCALE', 1)
    if render_scale > 1:
      width *= render_scale
      height *= render_scale
    self.current_effect = effect_cls(
      width=width,
      height=height,
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
        self.current_effect = MediaPlayback(
          width=self.layout.width,
          height=self.layout.height,
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

    w = self.layout.width
    h = self.layout.height

    if self.state.blackout or self.current_effect is None:
      logical_frame = np.zeros((w, h, 3), dtype=np.uint8)
      self._last_logical_frame = logical_frame
    else:
      t = time.monotonic()
      internal_frame = self.current_effect.render(t, self.state)

      # Downsample if effect uses RENDER_SCALE > 1
      if self.current_effect and getattr(self.current_effect, 'RENDER_SCALE', 1) > 1:
        from PIL import Image
        img = Image.fromarray(internal_frame.transpose(1, 0, 2))
        img = img.resize((w, h), Image.LANCZOS)
        logical_frame = np.array(img).transpose(1, 0, 2)
      else:
        logical_frame = internal_frame

      # Apply effective brightness from engine
      effective = self.brightness_engine.get_effective_brightness(
        datetime.now(timezone.utc)
      )
      logical_frame = (logical_frame * effective).astype(np.uint8)

      # Apply gamma
      logical_frame = self._gamma_lut[logical_frame]

      # Test patterns: override frame based on active test mode
      if self._test_mode is not None:
        if time.monotonic() < self._test_strip_until:
          logical_frame[:] = 0

          if self._test_mode == "segment" and self._test_segment_id:
            # Single segment: red-to-blue gradient
            positions = self._segment_positions.get(self._test_segment_id, [])
            for idx, (x, y) in enumerate(positions):
              if x < logical_frame.shape[0] and y < logical_frame.shape[1]:
                frac = idx / max(len(positions) - 1, 1)
                logical_frame[x, y] = [int(255 * (1 - frac)), 0, int(255 * frac)]

          elif self._test_mode == "segment_identify":
            # Each segment gets a unique hue (matching UI swatch: hue = index * 137.508)
            for seg_idx, seg_id in enumerate(self._segment_ids_ordered):
              hue = (seg_idx * 137.508) % 360
              r, g, b = _hue_to_rgb(hue)
              for x, y in self._segment_positions.get(seg_id, []):
                if x < logical_frame.shape[0] and y < logical_frame.shape[1]:
                  logical_frame[x, y] = [r, g, b]

          elif self._test_mode == "strip_identify":
            # Each output channel gets a uniform color
            channel_colors = [
              (255, 0, 0), (0, 255, 0), (0, 0, 255),
              (255, 255, 0), (255, 0, 255), (0, 255, 255),
              (255, 128, 0), (128, 0, 255),
            ]
            for ch, seg_ids in self._output_segment_ids.items():
              color = channel_colors[ch % len(channel_colors)]
              for seg_id in seg_ids:
                for x, y in self._segment_positions.get(seg_id, []):
                  if x < logical_frame.shape[0] and y < logical_frame.shape[1]:
                    logical_frame[x, y] = color

          elif self._test_mode == "probe":
            # Single LED by strip/wire position — write directly to output buffer
            # We need to bypass the logical frame and write to the packed buffer instead.
            # Set a flag so _render_frame knows to inject after packing.
            pass  # Handled below after pack_frame

        else:
          self._test_mode = None
          self._test_segment_id = None

      # Snapshot logical frame for live preview (post-brightness/gamma/test-strip)
      self._last_logical_frame = logical_frame

    self.state.frames_rendered += 1

    # Flip y-axis when origin is bottom-left (effects render y=0 as top, physical y=0 is bottom)
    if self.layout.origin == 'bottom_left':
      logical_frame = logical_frame[:, ::-1, :]

    # Pack frame to output bytes and send
    pixel_bytes = pack_frame(logical_frame, self.layout)

    # Probe mode: override the packed buffer to light one LED by wire position
    if self._test_mode == "probe" and self._probe_strip >= 0:
      buf = bytearray(pixel_bytes)
      # Zero everything
      for i in range(len(buf)):
        buf[i] = 0
      # Calculate byte offset for the target LED
      ch_offset = 0
      for ch in range(self._probe_strip):
        ch_offset += self.layout.output_sizes.get(ch, 0) * 3
      pos = ch_offset + self._probe_led * 3
      if pos + 2 < len(buf):
        buf[pos] = 255
        buf[pos + 1] = 255
        buf[pos + 2] = 255
      pixel_bytes = bytes(buf)

    success = await self.transport.send_frame(pixel_bytes)
    if success:
      self.state.frames_sent += 1

  def stop(self):
    self._running = False

  def update_gamma(self, gamma: float):
    self.state.gamma = gamma
    self._gamma_lut = _build_gamma_lut(gamma)
