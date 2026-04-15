"""
Built-in generative effects for the LED pillar.
"""

import math
import numpy as np
from typing import Optional

from .base import Effect, hsv_to_rgb, hex_to_rgb, palette_sample, lerp_color
from .engine.palettes import pal_color_grid, NUM_PALETTES
from ..mapping.cylinder import N


def _hsv_array_to_rgb(h: np.ndarray, s: float, v: float) -> np.ndarray:
  """Vectorized HSV to RGB. h is an array of hues (0-1), s and v are scalars."""
  h = h % 1.0
  shape = h.shape
  frame = np.zeros((*shape, 3), dtype=np.uint8)

  i = (h * 6.0).astype(int) % 6
  f = h * 6.0 - (h * 6.0).astype(int)

  p = v * (1.0 - s)
  q = v * (1.0 - s * f)
  t_val = v * (1.0 - s * (1.0 - f))

  # Build RGB channels
  r = np.where(i == 0, v, np.where(i == 1, q, np.where(i == 2, p, np.where(i == 3, p, np.where(i == 4, t_val, v)))))
  g = np.where(i == 0, t_val, np.where(i == 1, v, np.where(i == 2, v, np.where(i == 3, q, np.where(i == 4, p, p)))))
  b = np.where(i == 0, p, np.where(i == 1, p, np.where(i == 2, t_val, np.where(i == 3, v, np.where(i == 4, v, q)))))

  frame[..., 0] = (r * 255).astype(np.uint8)
  frame[..., 1] = (g * 255).astype(np.uint8)
  frame[..., 2] = (b * 255).astype(np.uint8)
  return frame


class SolidColor(Effect):
  """Solid color fill."""

  def render(self, t: float, state) -> np.ndarray:
    color = hex_to_rgb(self.params.get('color', '#FF6600'))
    frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)
    frame[:, :] = color
    return frame


class VerticalGradient(Effect):
  """Animated vertical gradient from a palette."""

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)
    speed = self.params.get('speed', 0.05)
    pal_idx = self.params.get('palette', 0) % NUM_PALETTES

    ys = np.arange(self.height, dtype=np.float64) / self.height
    pos = (ys + elapsed * speed) % 1.0  # (height,)

    # Broadcast to (width, height) and lookup palette
    pos_2d = np.broadcast_to(pos[np.newaxis, :], (self.width, self.height))
    return pal_color_grid(pal_idx, pos_2d)


class RainbowRotate(Effect):
  """Rainbow that rotates around the cylinder."""

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)
    speed = self.params.get('speed', 1.0)
    scale = self.params.get('scale', 1.0)
    pal_idx = self.params.get('palette', 0) % NUM_PALETTES

    xs = np.arange(self.width, dtype=np.float64) / self.width * scale
    ys = np.arange(self.height, dtype=np.float64) / self.height * 0.3
    xx, yy = np.meshgrid(xs, ys, indexing='ij')
    hue = (xx + yy + elapsed * speed * 0.1) % 1.0

    return pal_color_grid(pal_idx, hue)


class Plasma(Effect):
  """Plasma effect using overlapping sine waves."""

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)
    speed = self.params.get('speed', 1.0)
    scale = self.params.get('scale', 2.0)
    pal_idx = self.params.get('palette', 0) % NUM_PALETTES

    tt = elapsed * speed

    xs = np.arange(self.width, dtype=np.float64) / self.width * scale * math.pi * 2
    ys = np.arange(self.height, dtype=np.float64) / self.height * scale * math.pi * 2
    xx, yy = np.meshgrid(xs, ys, indexing='ij')

    v1 = np.sin(xx + tt)
    v2 = np.sin(yy + tt * 0.7)
    v3 = np.sin(xx + yy + tt * 0.5)
    v4 = np.sin(np.sqrt(xx**2 + yy**2) + tt * 1.3)

    v = (v1 + v2 + v3 + v4) / 4.0
    hue = (v + 1.0) / 2.0

    return pal_color_grid(pal_idx, hue)


class Twinkle(Effect):
  """Random twinkling stars."""

  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self._rng = np.random.default_rng(42)
    self._stars = self._rng.random((self.width, self.height)) * 2 * math.pi

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)
    speed = self.params.get('speed', 1.0)
    density = self.params.get('density', 0.05)
    darkness = self.params.get('darkness', 0.0)
    pal_idx = self.params.get('palette', 0) % NUM_PALETTES

    brightness = (np.sin(self._stars * 3.0 + elapsed * speed * 2.0) + 1.0) / 2.0
    mask = self._rng.random((self.width, self.height)) < density
    visible = (brightness > 0.7) | mask

    # Palette position varies by row and time
    y_coords = np.arange(self.height, dtype=np.float64) / self.height * 0.3
    hue = (elapsed * 0.02 + y_coords[np.newaxis, :]) % 1.0

    frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)
    rgb = pal_color_grid(pal_idx, hue)
    # Apply per-pixel brightness and darkness
    dim = 1.0 - min(1.0, max(0.0, darkness))
    scaled = (rgb.astype(np.float32) * brightness[..., np.newaxis] * dim).astype(np.uint8)
    frame[visible] = scaled[visible]

    return frame


class Spark(Effect):
  """Upward-moving sparks."""

  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self._sparks: list[dict] = []
    self._last_spawn = 0.0

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)
    speed = self.params.get('speed', 2.0)
    rate = self.params.get('rate', 10)
    brightness = self.params.get('brightness', 1.0)
    pal_idx = self.params.get('palette', 0) % NUM_PALETTES
    frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)

    # Spawn new sparks
    spawn_interval = 1.0 / max(1, rate)
    while elapsed - self._last_spawn > spawn_interval:
      self._last_spawn += spawn_interval
      self._sparks.append({
        'x': np.random.randint(0, self.width),
        'y': 0.0,
        'speed': speed * (0.5 + np.random.random()),
        'hue': np.random.random(),
        'life': 1.0,
      })

    # Update and draw
    alive = []
    for s in self._sparks:
      s['y'] += s['speed']
      s['life'] -= 0.01
      if s['life'] > 0 and int(s['y']) < self.height:
        yi = int(s['y'])
        c = pal_color_grid(pal_idx, np.array([s['hue']]))[0]
        b = s['life'] * min(2.0, max(0.1, brightness))
        frame[s['x'] % self.width, yi] = np.clip(c.astype(np.float32) * b, 0, 255).astype(np.uint8)
        # Tail
        for tail in range(1, 4):
          ty = yi - tail
          if 0 <= ty < self.height:
            fade = s['life'] * (1 - tail * 0.25) * min(2.0, max(0.1, brightness))
            if fade > 0:
              frame[s['x'] % self.width, ty] = np.clip(c.astype(np.float32) * fade, 0, 255).astype(np.uint8)
        alive.append(s)
    self._sparks = alive[-200:]
    return frame


class NoiseWash(Effect):
  """Smooth noise-based color wash."""

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)
    speed = self.params.get('speed', 0.5)
    scale = self.params.get('scale', 3.0)
    pal_idx = self.params.get('palette', 0) % NUM_PALETTES

    nx = np.arange(self.width, dtype=np.float64) / self.width * scale
    ny = np.arange(self.height, dtype=np.float64) / self.height * scale
    nxx, nyy = np.meshgrid(nx, ny, indexing='ij')

    v = (np.sin(nxx * 2.1 + elapsed * speed) +
         np.sin(nyy * 1.7 + elapsed * speed * 0.8) +
         np.sin((nxx + nyy) * 1.3 + elapsed * speed * 0.6)) / 3.0
    hue = (v + 1.0) / 2.0

    return pal_color_grid(pal_idx, hue)


class ColorWipe(Effect):
  """Color wipe sweeping up the pillar."""

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)
    speed = self.params.get('speed', 1.0)
    color = hex_to_rgb(self.params.get('color', '#0099FF'))
    frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)

    pos = (elapsed * speed * 30) % (self.height * 2)
    for y in range(self.height):
      if y < pos and pos < self.height:
        frame[:, y] = color
      elif pos >= self.height and y >= (pos - self.height):
        pass  # wipe off
      elif pos >= self.height:
        frame[:, y] = color
    return frame


class Scanline(Effect):
  """Horizontal scanline / comet moving up."""

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)
    speed = self.params.get('speed', 2.0)
    width_param = self.params.get('width', 3)
    color = hex_to_rgb(self.params.get('color', '#FFFFFF'))
    frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)

    pos = (elapsed * speed * 20) % self.height
    for y in range(self.height):
      dist = abs(y - pos)
      if dist < width_param:
        fade = 1.0 - dist / width_param
        c = (int(color[0] * fade), int(color[1] * fade), int(color[2] * fade))
        frame[:, y] = c
    return frame


class Fire(Effect):
  """Fire-like effect rising from the bottom. Fully vectorized."""

  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self._heat = np.zeros((self.width, self.height), dtype=np.float64)
    self._rng = np.random.default_rng(42)

  def render(self, t: float, state) -> np.ndarray:
    cooling = self.params.get('cooling', 55)
    sparking = self.params.get('sparking', 120)
    pal_idx = self.params.get('palette', 4) % NUM_PALETTES  # default Lava

    # Cool down
    cool_amount = self._rng.integers(
      0, max(1, (cooling * 10) // self.height + 2),
      size=(self.width, self.height)
    ) / 255.0
    self._heat = np.maximum(0, self._heat - cool_amount)

    # Heat rises: shift upward with averaging
    shifted = np.zeros_like(self._heat)
    shifted[:, 3:] = (
      self._heat[:, 2:-1] +
      self._heat[:, 1:-2] +
      self._heat[:, 1:-2]
    ) / 3.0
    shifted[:, :3] = self._heat[:, :3]
    self._heat = shifted

    # Sparks at bottom
    spark_mask = self._rng.integers(0, 255, size=self.width) < sparking
    for x in np.where(spark_mask)[0]:
      y = self._rng.integers(0, min(7, self.height))
      self._heat[x, y] = min(1.0, self._heat[x, y] + 0.4 + self._rng.random() * 0.4)

    # Palette-based color mapping — heat value (0-1) maps to palette position
    frame = pal_color_grid(pal_idx, self._heat)

    # Flip vertically — heat sim uses y=0 as hot base, but physical pillar
    # needs hot pixels at high y (bottom of display)
    return frame[:, ::-1, :]


class SineBands(Effect):
  """Sine-wave color bands."""

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)
    freq = self.params.get('frequency', 3.0)
    speed = self.params.get('speed', 1.0)
    pal_idx = self.params.get('palette', 0) % NUM_PALETTES

    ys = np.arange(self.height, dtype=np.float64)
    hue_1d = (np.sin(ys / self.height * freq * math.pi * 2 + elapsed * speed) + 1.0) / 2.0

    # Broadcast to (width, height)
    hue_2d = np.broadcast_to(hue_1d[np.newaxis, :], (self.width, self.height))

    # Palette lookup gives us the color; modulate brightness with the sine
    rgb = pal_color_grid(pal_idx, hue_2d)

    # Apply brightness modulation — brighter at sine peaks
    brightness = np.broadcast_to(hue_1d[np.newaxis, :], (self.width, self.height))
    return (rgb.astype(np.float32) * brightness[..., np.newaxis]).astype(np.uint8)


class CylinderRotate(Effect):
  """Color pattern that rotates around the cylinder."""

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)
    speed = self.params.get('speed', 1.0)
    pal_idx = self.params.get('palette', 0) % NUM_PALETTES

    xs = np.arange(self.width, dtype=np.float64) / self.width
    ys = np.arange(self.height, dtype=np.float64)
    xx, yy = np.meshgrid(xs, ys, indexing='ij')

    hue = (xx + elapsed * speed * 0.1) % 1.0
    brightness = (np.sin(yy / self.height * math.pi * 4 + elapsed) + 1.0) / 2.0

    rgb = pal_color_grid(pal_idx, hue)
    return (rgb.astype(np.float32) * brightness[..., np.newaxis]).astype(np.uint8)


class SeamPulse(Effect):
  """Pulse that highlights the seam between S9 and S0."""

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)
    frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)

    pulse = (math.sin(elapsed * 3.0) + 1.0) / 2.0
    v = int(pulse * 255)

    # Light up S0 and S9
    frame[0, :] = (v, 0, 0)
    frame[self.width - 1, :] = (0, 0, v)

    return frame


class DiagnosticLabels(Effect):
  """Shows strip numbers as distinct colors for identification."""

  STRIP_COLORS = [
    (255, 0, 0),    # S0: red
    (0, 255, 0),    # S1: green
    (0, 0, 255),    # S2: blue
    (255, 255, 0),  # S3: yellow
    (255, 0, 255),  # S4: magenta
    (0, 255, 255),  # S5: cyan
    (255, 128, 0),  # S6: orange
    (128, 0, 255),  # S7: purple
    (0, 255, 128),  # S8: spring green
    (255, 255, 255),# S9: white
  ]

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)
    frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)

    for x in range(min(self.width, 10)):
      color = self.STRIP_COLORS[x % 10]
      # Show bottom quarter solid, rest dim
      quarter = self.height // 4
      frame[x, :quarter] = color
      frame[x, quarter:] = tuple(c // 8 for c in color)

      # Animated marker at strip number position
      marker_y = int((elapsed * 20) % self.height)
      if 0 <= marker_y < self.height:
        frame[x, marker_y] = (255, 255, 255)

    return frame


# Effect registry
EFFECTS = {
  'solid_color': SolidColor,
  'vertical_gradient': VerticalGradient,
  'rainbow_rotate': RainbowRotate,
  'plasma': Plasma,
  'twinkle': Twinkle,
  'spark': Spark,
  'noise_wash': NoiseWash,
  'color_wipe': ColorWipe,
  'scanline': Scanline,
  'fire': Fire,
  'sine_bands': SineBands,
  'cylinder_rotate': CylinderRotate,
  'seam_pulse': SeamPulse,
  'diagnostic_labels': DiagnosticLabels,
}
