"""
Ambient animations batch B ported from led_sim_reference.py.

6 effects: Breathing, Fireflies, Nebula, Kaleidoscope, FlowField, Moire.
No Pygame dependencies.
"""

import math
import random

import numpy as np

from ..base import Effect
from ..engine.buffer import LEDBuffer
from ..engine.noise import perlin as _perlin, fbm as _fbm, cyl_noise, perlin_grid, fbm_grid
from ..engine.color import clampf
from ..engine.palettes import pal_color, pal_color_grid


# ─── Param descriptor (mirrors led_sim_reference.py Param) ─────────

class _Param:
  """Metadata for a tunable parameter."""
  __slots__ = ('label', 'attr', 'lo', 'hi', 'step', 'default')

  def __init__(self, label, attr, lo, hi, step, default):
    self.label = label
    self.attr = attr
    self.lo = lo
    self.hi = hi
    self.step = step
    self.default = default


# ═══════════════════════════════════════════════════════════════════
#  BREATHING
# ═══════════════════════════════════════════════════════════════════

class Breathing(Effect):
  """Slow, full-pillar brightness pulsing with vertical hue waves."""

  CATEGORY = "ambient"
  DISPLAY_NAME = "Breathing"
  DESCRIPTION = "Slow brightness pulsing with vertical hue waves"
  PALETTE_SUPPORT = True

  PARAMS = [
    _Param("Speed", "speed", 0.05, 2.0, 0.05, 0.3),
    _Param("Wave", "wave", 0.0, 3.0, 0.1, 1.0),
  ]
  _SCALAR_PARAMS = {"speed": 0.3, "wave": 1.0}
  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    self.buf = LEDBuffer(width, height)
    self._t = 0.0
    self._last_t = None

  def render(self, t, state):
    dt_ms = self._calc_dt_ms(t)
    speed = self.params.get("speed", 0.3)
    wave = self.params.get("wave", 1.0)
    pal_idx = self.params.get("palette", 0)

    self._t += dt_ms * 0.001 * speed
    tt = self._t

    breath = (math.sin(tt * 2) + 1) * 0.5

    # Build coordinate grids: x_g is (cols, 1), y_g is (1, rows)
    x_g = np.arange(self.width, dtype=np.float64)[:, np.newaxis]
    y_g = np.arange(self.height, dtype=np.float64)[np.newaxis, :]

    # Vectorized hue and brightness — (cols, rows) arrays
    hue_shift = y_g / self.height + np.sin(y_g * 0.01 * wave + tt) * 0.1
    b = breath * (0.7 + 0.3 * np.sin(y_g * 0.02 * wave + x_g * 0.5 + tt * 0.5))

    rgb = pal_color_grid(pal_idx, hue_shift % 1.0)  # (cols, rows, 3) uint8
    self.buf.data = (rgb.astype(np.float32) * b[..., np.newaxis]).clip(0, 255).astype(np.uint8)

    return self.buf.get_frame()

  def _calc_dt_ms(self, t):
    if self._last_t is None:
      self._last_t = t
      return 16.67
    dt = (t - self._last_t) * 1000.0
    self._last_t = t
    return max(0.0, dt)


# ═══════════════════════════════════════════════════════════════════
#  FIREFLIES
# ═══════════════════════════════════════════════════════════════════

class Fireflies(Effect):
  """Drifting glowing firefly particles with pulsing brightness."""

  CATEGORY = "ambient"
  DISPLAY_NAME = "Fireflies"
  DESCRIPTION = "Drifting glowing firefly particles with pulsing brightness"
  PALETTE_SUPPORT = True

  PARAMS = [
    _Param("Count", "count", 3, 60, 1, 20),
    _Param("Speed", "speed", 0.1, 2.0, 0.1, 0.5),
    _Param("Glow", "glow", 0.3, 3.0, 0.1, 1.0),
  ]
  _SCALAR_PARAMS = {"count": 20, "speed": 0.5, "glow": 1.0}
  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    self.buf = LEDBuffer(width, height)
    self._flies = []
    for _ in range(60):
      self._flies.append({
        'x': random.uniform(0, width - 1),
        'y': random.uniform(0, height - 1),
        'vx': random.uniform(-1, 1),
        'vy': random.uniform(-3, 3),
        'phase': random.uniform(0, 6.28),
        'freq': random.uniform(0.3, 1.5),
        'hue': random.random(),
      })
    self._last_t = None

  def render(self, t, state):
    dt_ms = self._calc_dt_ms(t)
    dt = dt_ms * 0.001
    speed = self.params.get("speed", 0.5)
    count = int(self.params.get("count", 20))
    glow = self.params.get("glow", 1.0)
    pal_idx = self.params.get("palette", 0)

    self.buf.clear()

    for f in self._flies[:count]:
      f['x'] += f['vx'] * speed * dt
      f['y'] += f['vy'] * speed * dt

      if f['x'] < 0 or f['x'] >= self.width:
        f['vx'] *= -1
        f['x'] = clampf(f['x'], 0, self.width - 1)
      if f['y'] < 0 or f['y'] >= self.height:
        f['vy'] *= -1
        f['y'] = clampf(f['y'], 0, self.height - 1)

      f['vx'] += random.uniform(-0.5, 0.5) * dt
      f['vy'] += random.uniform(-1, 1) * dt
      f['phase'] += f['freq'] * dt

      b = max(0, math.sin(f['phase'] * 3)) ** 2 * glow
      c = pal_color(pal_idx, f['hue'])
      cx = int(round(f['x']))
      cy = int(round(f['y']))

      # Glow radius
      for dx in range(-1, 2):
        for dy in range(-2, 3):
          dist = abs(dx) + abs(dy) * 0.5
          fade = max(0, 1.0 - dist * 0.5) * b
          if fade > 0.01:
            self.buf.add_led(
              cx + dx, cy + dy,
              c[0] * fade, c[1] * fade, c[2] * fade,
            )

    return self.buf.get_frame()

  def _calc_dt_ms(self, t):
    if self._last_t is None:
      self._last_t = t
      return 16.67
    dt = (t - self._last_t) * 1000.0
    self._last_t = t
    return max(0.0, dt)


# ═══════════════════════════════════════════════════════════════════
#  NEBULA
# ═══════════════════════════════════════════════════════════════════

class Nebula(Effect):
  """Multi-layer FBM noise clouds with drifting colors."""

  CATEGORY = "ambient"
  DISPLAY_NAME = "Nebula"
  DESCRIPTION = "Multi-layer fractal noise clouds with drifting colors"
  PALETTE_SUPPORT = True

  PARAMS = [
    _Param("Speed", "speed", 0.05, 1.5, 0.05, 0.2),
    _Param("Scale", "scale", 0.3, 3.0, 0.1, 1.0),
    _Param("Layers", "layers", 1, 3, 1, 2),
  ]
  _SCALAR_PARAMS = {"speed": 0.2, "scale": 1.0, "layers": 2}
  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    self.buf = LEDBuffer(width, height)
    self._t = 0.0
    self._last_t = None
    # Default palette idx = 9 (Vapor) per spec
    if "palette" not in self.params:
      self.params["palette"] = 9

  def render(self, t, state):
    dt_ms = self._calc_dt_ms(t)
    speed = self.params.get("speed", 0.2)
    scale = self.params.get("scale", 1.0)
    layers = int(self.params.get("layers", 2))
    pal_idx = self.params.get("palette", 9)

    self._t += dt_ms * 0.001 * speed
    tt = self._t

    # Build coordinate grids: x_g is (cols, 1), y_g is (1, rows)
    x_g = np.arange(self.width, dtype=np.float64)[:, np.newaxis]
    y_g = np.arange(self.height, dtype=np.float64)[np.newaxis, :]

    # Pre-broadcast to (cols, rows) — fbm_grid needs matching shapes for np.zeros_like
    xb, yb = np.broadcast_arrays(x_g, y_g)

    # Vectorized FBM — two (cols, rows) noise fields
    v = fbm_grid(xb * 0.15 * scale + 10, yb * 0.008 * scale, tt * 0.5, layers)
    v2 = fbm_grid(xb * 0.2 * scale + 50, yb * 0.006 * scale, tt * 0.3 + 100, layers)

    hue = (v + 1) * 0.5
    bright = np.clip((v2 + 0.8) * 0.7, 0.0, 1.0)

    rgb = pal_color_grid(pal_idx, hue)  # (cols, rows, 3) uint8
    self.buf.data = (rgb.astype(np.float32) * bright[..., np.newaxis]).clip(0, 255).astype(np.uint8)

    return self.buf.get_frame()

  def _calc_dt_ms(self, t):
    if self._last_t is None:
      self._last_t = t
      return 16.67
    dt = (t - self._last_t) * 1000.0
    self._last_t = t
    return max(0.0, dt)


# ═══════════════════════════════════════════════════════════════════
#  KALEIDOSCOPE
# ═══════════════════════════════════════════════════════════════════

class Kaleidoscope(Effect):
  """Mirrored segment patterns radiating from center."""

  CATEGORY = "ambient"
  DISPLAY_NAME = "Kaleidoscope"
  DESCRIPTION = "Mirrored segment patterns radiating from center"
  PALETTE_SUPPORT = True

  PARAMS = [
    _Param("Speed", "speed", 0.1, 3.0, 0.1, 0.5),
    _Param("Segments", "segments", 3, 12, 1, 6),
    _Param("Zoom", "zoom", 0.3, 3.0, 0.1, 1.0),
  ]
  _SCALAR_PARAMS = {"speed": 0.5, "segments": 6, "zoom": 1.0}
  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    self.buf = LEDBuffer(width, height)
    self._t = 0.0
    self._last_t = None

  def render(self, t, state):
    dt_ms = self._calc_dt_ms(t)
    speed = self.params.get("speed", 0.5)
    seg = int(self.params.get("segments", 6))
    zm = self.params.get("zoom", 1.0)
    pal_idx = self.params.get("palette", 0)

    self._t += dt_ms * 0.001 * speed
    tt = self._t

    cx = self.width / 2.0
    cy = self.height / 2.0
    seg_angle = 6.28 / seg
    half_seg = 3.14 / seg

    # Build coordinate grids: x_g is (cols, 1), y_g is (1, rows)
    x_g = np.arange(self.width, dtype=np.float64)[:, np.newaxis]
    y_g = np.arange(self.height, dtype=np.float64)[np.newaxis, :]

    dx = (x_g - cx) / max(1, cx)
    dy = (y_g - cy) / max(1, cy) * 0.3
    angle = np.arctan2(dy, dx)
    dist = np.sqrt(dx ** 2 + dy ** 2) * zm

    # Mirror into segments
    angle = np.abs(((angle + tt) % seg_angle) - half_seg)
    v = np.sin(dist * 5 + tt * 2) * 0.5 + np.sin(angle * seg + tt) * 0.5
    hue = (v + 1) * 0.25 + dist * 0.3
    bright = np.clip(0.3 + (np.sin(dist * 3 - tt * 2) + 1) * 0.35, 0.0, 1.0)

    rgb = pal_color_grid(pal_idx, hue % 1.0)  # (cols, rows, 3) uint8
    self.buf.data = (rgb.astype(np.float32) * bright[..., np.newaxis]).clip(0, 255).astype(np.uint8)

    return self.buf.get_frame()

  def _calc_dt_ms(self, t):
    if self._last_t is None:
      self._last_t = t
      return 16.67
    dt = (t - self._last_t) * 1000.0
    self._last_t = t
    return max(0.0, dt)


# ═══════════════════════════════════════════════════════════════════
#  FLOW FIELD
# ═══════════════════════════════════════════════════════════════════

class FlowField(Effect):
  """Perlin noise flow field with particle trails — Fidenza-style generative art."""

  CATEGORY = "ambient"
  DISPLAY_NAME = "Flow Field"
  DESCRIPTION = "Perlin noise flow field with particle trails"
  PALETTE_SUPPORT = True

  PARAMS = [
    _Param("Speed", "speed", 0.05, 2.0, 0.05, 0.3),
    _Param("Particles", "particles", 10, 200, 10, 80),
    _Param("Fade", "fade", 0.8, 0.99, 0.01, 0.92),
    _Param("Noise Scale", "noise_scale", 0.3, 3.0, 0.1, 1.0),
  ]
  _SCALAR_PARAMS = {"speed": 0.3, "particles": 80, "fade": 0.92, "noise_scale": 1.0}
  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    self.buf = LEDBuffer(width, height)
    self._t = 0.0
    self._pts = []
    for _ in range(200):
      self._pts.append([
        random.uniform(0, width),
        random.uniform(0, height),
        random.random(),  # hue
      ])
    self._last_t = None

  def render(self, t, state):
    dt_ms = self._calc_dt_ms(t)
    dt = dt_ms * 0.001
    speed = self.params.get("speed", 0.3)
    count = int(self.params.get("particles", 80))
    fade = self.params.get("fade", 0.92)
    ns = self.params.get("noise_scale", 1.0)
    pal_idx = self.params.get("palette", 0)

    self._t += dt * speed

    # Persistent buffer: fade, not clear (particle trails)
    self.buf.fade(fade)

    for p in self._pts[:count]:
      # Get flow angle from cylinder-wrapped noise
      angle = cyl_noise(p[0], p[1], self._t * 0.5, ns, 0.008 * ns) * 6.28

      # Move particle along flow
      p[0] += math.cos(angle) * 30 * dt * speed
      p[1] += math.sin(angle) * 30 * dt * speed

      # Cylinder wrap x
      p[0] = p[0] % self.width

      # Respawn if off grid vertically
      if p[1] < 0 or p[1] >= self.height:
        p[0] = random.uniform(0, self.width)
        p[1] = random.uniform(0, self.height)
        p[2] = random.random()

      # Draw
      c = pal_color(pal_idx, p[2])
      self.buf.add_led(int(p[0]), int(p[1]), c[0] * 0.8, c[1] * 0.8, c[2] * 0.8)

    return self.buf.get_frame()

  def _calc_dt_ms(self, t):
    if self._last_t is None:
      self._last_t = t
      return 16.67
    dt = (t - self._last_t) * 1000.0
    self._last_t = t
    return max(0.0, dt)


# ═══════════════════════════════════════════════════════════════════
#  MOIRE
# ═══════════════════════════════════════════════════════════════════

class Moire(Effect):
  """Hypnotic moire interference — overlapping rings create depth illusion."""

  CATEGORY = "ambient"
  DISPLAY_NAME = "Moire"
  DESCRIPTION = "Overlapping ring interference creating hypnotic depth illusion"
  PALETTE_SUPPORT = True

  PARAMS = [
    _Param("Speed", "speed", 0.05, 2.0, 0.05, 0.4),
    _Param("Scale", "scale", 0.0, 0.7, 0.07, 0.35),
    _Param("Centers", "centers", 2, 5, 1, 3),
  ]
  _SCALAR_PARAMS = {"speed": 0.4, "scale": 0.35, "centers": 3}
  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    self.buf = LEDBuffer(width, height)
    self._t = 0.0
    self._last_t = None

  def render(self, t, state):
    dt_ms = self._calc_dt_ms(t)
    speed = self.params.get("speed", 0.4)
    sc = self.params.get("scale", 1.0)
    nc = int(self.params.get("centers", 3))
    pal_idx = self.params.get("palette", 0)

    self._t += dt_ms * 0.001 * speed
    tt = self._t

    cols = self.width
    rows = self.height

    # Center points orbit on Lissajous curves (cylinder-aware)
    centers = []
    for i in range(nc):
      phase = i * 6.28 / nc
      cx = (math.sin(tt * 0.7 + phase) * 0.5 + 0.5) * cols
      cy = rows / 2 + math.sin(tt * 0.3 + phase * 1.7) * rows * 0.35
      centers.append((cx, cy))

    # Build coordinate grids: x_g is (cols, 1), y_g is (1, rows)
    x_g = np.arange(cols, dtype=np.float64)[:, np.newaxis]
    y_g = np.arange(rows, dtype=np.float64)[np.newaxis, :]

    # Accumulate interference from each center
    val = np.zeros((cols, rows), dtype=np.float64)
    for cx, cy in centers:
      # Cylinder-aware distance: shortest path around x-axis
      dx = x_g - cx
      dx = np.where(np.abs(dx) > cols / 2, dx - np.sign(dx) * cols, dx)
      dy = (y_g - cy) * (cols / rows) * 5  # aspect correction
      dist = np.sqrt(dx ** 2 + dy ** 2)
      val += np.sin(dist * sc * 3 + tt * 2)
    val /= nc

    hue = (val + 1) * 0.5
    bright = np.clip((np.abs(val) ** 0.5) * 0.9 + 0.1, 0.0, 1.0)

    rgb = pal_color_grid(pal_idx, hue)  # (cols, rows, 3) uint8
    self.buf.data = (rgb.astype(np.float32) * bright[..., np.newaxis]).clip(0, 255).astype(np.uint8)

    return self.buf.get_frame()

  def _calc_dt_ms(self, t):
    if self._last_t is None:
      self._last_t = t
      return 16.67
    dt = (t - self._last_t) * 1000.0
    self._last_t = t
    return max(0.0, dt)


# ═══════════════════════════════════════════════════════════════════
#  REGISTRY
# ═══════════════════════════════════════════════════════════════════

AMBIENT_B_EFFECTS: dict[str, type[Effect]] = {
  "breathing": Breathing,
  "fireflies": Fireflies,
  "nebula": Nebula,
  "kaleidoscope": Kaleidoscope,
  "flow_field": FlowField,
  "moire": Moire,
}
