"""
Built-in generative effects for the LED pillar.
"""

import math
import numpy as np
from typing import Optional

from .base import Effect, hsv_to_rgb, hex_to_rgb, palette_sample, lerp_color
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
    speed = self.params.get('speed', 0.5)
    frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)

    for y in range(self.height):
      pos = (y / self.height + elapsed * speed) % 1.0
      r, g, b = hsv_to_rgb(pos, 1.0, 1.0)
      frame[:, y] = (r, g, b)
    return frame


class RainbowRotate(Effect):
  """Rainbow that rotates around the cylinder."""

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)
    speed = self.params.get('speed', 1.0)
    scale = self.params.get('scale', 1.0)

    xs = np.arange(self.width, dtype=np.float64) / self.width * scale
    ys = np.arange(self.height, dtype=np.float64) / self.height * 0.3
    xx, yy = np.meshgrid(xs, ys, indexing='ij')
    hue = (xx + yy + elapsed * speed * 0.1) % 1.0

    frame = _hsv_array_to_rgb(hue, 1.0, 1.0)
    return frame


class Plasma(Effect):
  """Plasma effect using overlapping sine waves."""

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)
    speed = self.params.get('speed', 1.0)
    scale = self.params.get('scale', 2.0)

    tt = elapsed * speed

    # Use vectorized numpy for performance
    xs = np.arange(self.width, dtype=np.float64) / self.width * scale * math.pi * 2
    ys = np.arange(self.height, dtype=np.float64) / self.height * scale * math.pi * 2

    xx, yy = np.meshgrid(xs, ys, indexing='ij')

    v1 = np.sin(xx + tt)
    v2 = np.sin(yy + tt * 0.7)
    v3 = np.sin(xx + yy + tt * 0.5)
    v4 = np.sin(np.sqrt(xx**2 + yy**2) + tt * 1.3)

    v = (v1 + v2 + v3 + v4) / 4.0  # -1 to 1
    hue = (v + 1.0) / 2.0  # 0 to 1

    return _hsv_array_to_rgb(hue, 0.9, 1.0)


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
    frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)

    brightness = (np.sin(self._stars * 3.0 + elapsed * speed * 2.0) + 1.0) / 2.0
    mask = self._rng.random((self.width, self.height)) < density

    for x in range(self.width):
      for y in range(self.height):
        if brightness[x, y] > 0.7 or mask[x, y]:
          v = brightness[x, y]
          hue = (elapsed * 0.02 + y / self.height * 0.3) % 1.0
          r, g, b = hsv_to_rgb(hue, 0.3, v)
          frame[x, y] = (r, g, b)
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
        r, g, b = hsv_to_rgb(s['hue'], 1.0, s['life'])
        frame[s['x'] % self.width, yi] = (r, g, b)
        # Tail
        for tail in range(1, 4):
          ty = yi - tail
          if 0 <= ty < self.height:
            fade = s['life'] * (1 - tail * 0.25)
            if fade > 0:
              r2, g2, b2 = hsv_to_rgb(s['hue'], 1.0, fade)
              frame[s['x'] % self.width, ty] = (r2, g2, b2)
        alive.append(s)
    self._sparks = alive[-200:]  # cap active sparks
    return frame


class NoiseWash(Effect):
  """Smooth noise-based color wash."""

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)
    speed = self.params.get('speed', 0.5)
    scale = self.params.get('scale', 3.0)

    nx = np.arange(self.width, dtype=np.float64) / self.width * scale
    ny = np.arange(self.height, dtype=np.float64) / self.height * scale
    nxx, nyy = np.meshgrid(nx, ny, indexing='ij')

    # Sine-based noise approximation
    v = (np.sin(nxx * 2.1 + elapsed * speed) +
         np.sin(nyy * 1.7 + elapsed * speed * 0.8) +
         np.sin((nxx + nyy) * 1.3 + elapsed * speed * 0.6)) / 3.0
    hue = (v + 1.0) / 2.0

    return _hsv_array_to_rgb(hue, 0.8, 0.9)


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
  """Fire-like effect rising from the bottom."""

  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self._heat = np.zeros((self.width, self.height), dtype=np.float64)

  def render(self, t: float, state) -> np.ndarray:
    cooling = self.params.get('cooling', 55)
    sparking = self.params.get('sparking', 120)
    frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)

    for x in range(self.width):
      # Cool down
      for y in range(self.height):
        cooldown = np.random.randint(0, max(1, (cooling * 10) // self.height + 2))
        self._heat[x, y] = max(0, self._heat[x, y] - cooldown / 255.0)

      # Heat rises
      for y in range(self.height - 1, 2, -1):
        self._heat[x, y] = (self._heat[x, y-1] + self._heat[x, y-2] + self._heat[x, y-2]) / 3.0

      # Sparks at bottom
      if np.random.randint(0, 255) < sparking:
        y = np.random.randint(0, min(7, self.height))
        self._heat[x, y] = min(1.0, self._heat[x, y] + 0.4 + np.random.random() * 0.4)

    # Map heat to color
    for x in range(self.width):
      for y in range(self.height):
        h = self._heat[x, y]
        if h < 0.33:
          r, g, b = min(255, max(0, int(h * 3 * 255))), 0, 0
        elif h < 0.66:
          r, g, b = 255, min(255, max(0, int((h - 0.33) * 3 * 255))), 0
        else:
          r, g, b = 255, 255, min(255, max(0, int((h - 0.66) * 3 * 255)))
        frame[x, y] = (r, g, b)
    return frame


class SineBands(Effect):
  """Sine-wave color bands."""

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)
    freq = self.params.get('frequency', 3.0)
    speed = self.params.get('speed', 1.0)
    frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)

    for y in range(self.height):
      v = (math.sin(y / self.height * freq * math.pi * 2 + elapsed * speed) + 1.0) / 2.0
      hue = v
      r, g, b = hsv_to_rgb(hue, 1.0, v)
      frame[:, y] = (r, g, b)
    return frame


class CylinderRotate(Effect):
  """Color pattern that rotates around the cylinder."""

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)
    speed = self.params.get('speed', 1.0)
    frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)

    for x in range(self.width):
      hue = ((x / self.width) + elapsed * speed * 0.1) % 1.0
      for y in range(self.height):
        v = (math.sin(y / self.height * math.pi * 4 + elapsed) + 1.0) / 2.0
        r, g, b = hsv_to_rgb(hue, 0.9, v)
        frame[x, y] = (r, g, b)
    return frame


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
