"""
Preview service — manages preview effect instances separate from live.

Renders preview frames independently without mutating live LED output.
"""

import logging
import struct
import time
from typing import Optional

import numpy as np
from fastapi import WebSocket

logger = logging.getLogger(__name__)

# Preview frame header: type(1) + frame_id(4) + width(2) + height(2) + encoding(1) = 10 bytes
FRAME_HEADER_FORMAT = '<BIHHB'
FRAME_HEADER_SIZE = struct.calcsize(FRAME_HEADER_FORMAT)
MSG_TYPE_FRAME = 0x01


class PreviewService:
  """Manages preview effect instances and frame streaming."""

  def __init__(self, renderer):
    self._renderer = renderer
    self._effect = None
    self._effect_name: Optional[str] = None
    self._running = False
    self._clients: set[WebSocket] = set()
    self._frame_id = 0
    self._fps = 30

  @property
  def active(self) -> bool:
    return self._running and self._effect is not None

  @property
  def effect_name(self) -> Optional[str]:
    return self._effect_name

  def start(self, effect_name: str, params: dict = None, fps: int = 30):
    """Start a preview effect (separate from live)."""
    # Look up from renderer's registry — includes built-in AND imported effects
    if effect_name not in self._renderer.effect_registry:
      raise ValueError(f"Unknown effect: {effect_name}")

    effect_cls = self._renderer.effect_registry[effect_name]
    width = self._renderer.pixel_map.width
    height = self._renderer.pixel_map.height
    render_scale = getattr(effect_cls, 'RENDER_SCALE', 1)
    if render_scale > 1:
      width *= render_scale
      height *= render_scale

    # Merge params
    yaml_params = {}
    if hasattr(self._renderer, 'effects_config') and self._renderer.effects_config:
      for section in ('effects', 'audio_effects'):
        section_data = self._renderer.effects_config.get(section, {})
        if effect_name in section_data:
          yaml_params = section_data[effect_name].get('params', {})
          break
    merged = {**yaml_params, **(params or {})}

    if effect_name == 'animation_switcher':
      merged['_effect_registry'] = self._renderer.effect_registry

    self._effect = effect_cls(width=width, height=height, params=merged)
    self._effect_name = effect_name
    self._fps = max(1, min(fps, 60))
    self._running = True
    self._frame_id = 0
    logger.info(f"Preview started: {effect_name} at {self._fps} FPS")

  def stop(self):
    """Stop the preview effect."""
    self._running = False
    self._effect = None
    self._effect_name = None
    logger.info("Preview stopped")

  def render_frame(self, state) -> Optional[bytes]:
    """Render one preview frame and return binary payload with header.

    Frame is already at grid dimensions from pixel_map.
    """
    if not self._running or self._effect is None:
      return None

    t = time.monotonic()
    try:
      frame = self._effect.render(t, state)
    except Exception as e:
      logger.error(f"Preview render error: {e}")
      return None

    if frame.ndim != 3 or frame.shape[2] != 3:
      return None

    # Downsample if effect uses RENDER_SCALE > 1
    if self._effect and getattr(self._effect, 'RENDER_SCALE', 1) > 1:
      from PIL import Image
      target_w = self._renderer.pixel_map.width
      target_h = self._renderer.pixel_map.height
      img = Image.fromarray(frame.transpose(1, 0, 2))
      img = img.resize((target_w, target_h), Image.LANCZOS)
      frame = np.array(img).transpose(1, 0, 2)

    width = frame.shape[0]
    height = frame.shape[1]

    self._frame_id += 1
    header = struct.pack(
      FRAME_HEADER_FORMAT,
      MSG_TYPE_FRAME,
      self._frame_id,
      width,
      height,
      0,  # encoding: 0 = RGB
    )
    return header + frame.tobytes()

  def add_client(self, ws: WebSocket):
    self._clients.add(ws)

  def remove_client(self, ws: WebSocket):
    self._clients.discard(ws)

  def get_status(self) -> dict:
    return {
      'active': self.active,
      'effect': self._effect_name,
      'fps': self._fps,
      'frame_id': self._frame_id,
      'clients': len(self._clients),
    }
