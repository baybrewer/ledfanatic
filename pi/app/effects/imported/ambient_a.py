"""
Ambient animations batch A ported from led_sim_reference.py.

6 effects: Plasma, Aurora Borealis, LavaLamp, OceanWaves, Starfield, MatrixRain.
No Pygame dependencies.
"""

import math
import random

import numpy as np

from ..base import Effect
from ..engine.buffer import LEDBuffer
from ..engine.noise import noise01, cyl_noise, perlin_grid, noise01_grid, cyl_noise_grid, noise01_xy
from ..engine.color import clampf
from ..engine.palettes import pal_color, NUM_PALETTES, pal_color_grid
from ...mapping.cylinder import N


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
#  PLASMA
# ═══════════════════════════════════════════════════════════════════

class Plasma(Effect):
  """Cylinder-wrapped sine/noise plasma with palette coloring."""

  CATEGORY = "ambient"
  DISPLAY_NAME = "Plasma"
  DESCRIPTION = "Layered sine and noise plasma with cylinder wrapping"
  PALETTE_SUPPORT = True

  PARAMS = [
    _Param("Speed", "speed", 0.1, 5.0, 0.1, 1.0),
    _Param("Scale", "scale", 0.2, 3.0, 0.1, 1.0),
  ]
  _SCALAR_PARAMS = {"speed": 1.0, "scale": 1.0}
  NATIVE_WIDTH = 10

  def __init__(self, width=10, height=N, params=None):
    super().__init__(width, height, params)
    self.buf = LEDBuffer(width, height)
    self._t = 0.0
    self._last_t = None

  def render(self, t, state):
    dt_ms = self._calc_dt_ms(t)
    speed = self.params.get("speed", 1.0)
    scale = self.params.get("scale", 1.0)
    pal_idx = self.params.get("palette", 0)

    self._t += dt_ms * 0.001 * speed
    tt = self._t
    cols = self.width
    rows = self.height

    # (cols, 1) and (1, rows) grids for broadcasting
    ax = (np.arange(cols, dtype=np.float64) / cols * 6.2832)[:, np.newaxis]  # (cols, 1)
    y_g = np.arange(rows, dtype=np.float64)[np.newaxis, :]  # (1, rows)

    term1 = np.sin(ax * 2 * scale + tt * 1.3)
    term2 = np.sin(y_g * scale * 0.035 + tt * 0.7)
    term3 = np.sin(ax * 3 * scale + y_g * scale * 0.02 + tt * 1.1)
    term4 = np.sin(np.sqrt(np.abs(np.sin(ax) * 4 + y_g * y_g * 0.001)) * scale * 2 + tt * 0.9)
    term5 = cyl_noise_grid(cols, rows, tt * 0.5, scale, 0.01) * 1.5

    v = (term1 + term2 + term3 + term4 + term5) / 5.0
    hue = (v + 1) * 0.5
    self.buf.data = pal_color_grid(pal_idx % NUM_PALETTES, hue)

    return self.buf.get_frame()

  def _calc_dt_ms(self, t):
    if self._last_t is None:
      self._last_t = t
      return 16.67
    dt = (t - self._last_t) * 1000.0
    self._last_t = t
    return max(0.0, dt)


# ═══════════════════════════════════════════════════════════════════
#  AURORA BOREALIS
# ═══════════════════════════════════════════════════════════════════

class AuroraBorealis(Effect):
  """Northern-lights curtain with noise-driven shimmer."""

  CATEGORY = "ambient"
  DISPLAY_NAME = "Aurora Borealis"
  DESCRIPTION = "Northern-lights curtain with noise-driven shimmer"
  PALETTE_SUPPORT = True

  PARAMS = [
    _Param("Speed", "speed", 0.05, 2.0, 0.05, 0.4),
    _Param("Wave", "wave", 0.2, 3.0, 0.1, 1.0),
    _Param("Bright", "bright", 0.2, 1.0, 0.05, 0.9),
  ]
  _SCALAR_PARAMS = {"speed": 0.4, "wave": 1.0, "bright": 0.9}
  NATIVE_WIDTH = 10

  def __init__(self, width=10, height=N, params=None):
    super().__init__(width, height, params)
    self.buf = LEDBuffer(width, height)
    self._t = 0.0
    self._last_t = None

  def render(self, t, state):
    dt_ms = self._calc_dt_ms(t)
    speed = self.params.get("speed", 0.4)
    wave = self.params.get("wave", 1.0)
    bright = self.params.get("bright", 0.9)
    pal_idx = self.params.get("palette", 0)

    self._t += dt_ms * 0.001 * speed
    tt = self._t
    cols = self.width
    rows = self.height

    # (cols, 1) and (1, rows) grids for broadcasting
    x_g = np.arange(cols, dtype=np.float64)[:, np.newaxis]  # (cols, 1)
    y_g = np.arange(rows, dtype=np.float64)[np.newaxis, :]  # (1, rows)

    curtain = (perlin_grid(x_g * 0.3, y_g * 0.008 * wave, tt * 0.5) + 1.0) * 0.5
    w = (np.sin(y_g * 0.02 * wave + tt * 2 + x_g * 0.8) + 1) * 0.5
    shimmer = (perlin_grid(x_g * 0.5 + 100, y_g * 0.02, tt * 3) + 1.0) * 0.5 * 0.4
    v = np.clip(curtain * w * bright + shimmer * curtain * bright * 0.5, 0.0, 1.0)

    hue = curtain * 0.8 + 0.1
    rgb = pal_color_grid(pal_idx % NUM_PALETTES, hue)
    self.buf.data = (rgb.astype(np.float32) * v[..., np.newaxis]).clip(0, 255).astype(np.uint8)

    return self.buf.get_frame()

  def _calc_dt_ms(self, t):
    if self._last_t is None:
      self._last_t = t
      return 16.67
    dt = (t - self._last_t) * 1000.0
    self._last_t = t
    return max(0.0, dt)


# ═══════════════════════════════════════════════════════════════════
#  LAVA LAMP
# ═══════════════════════════════════════════════════════════════════

class LavaLamp(Effect):
  """Floating blob metaballs with palette coloring."""

  CATEGORY = "ambient"
  DISPLAY_NAME = "Lava Lamp"
  DESCRIPTION = "Floating blob metaballs with smooth palette gradients"
  PALETTE_SUPPORT = True

  PARAMS = [
    _Param("Speed", "speed", 0.05, 2.0, 0.05, 0.3),
    _Param("Blobs", "blobs", 2, 12, 1, 5),
    _Param("Size", "size", 0.3, 3.0, 0.1, 1.0),
  ]
  _SCALAR_PARAMS = {"speed": 0.3, "blobs": 5, "size": 1.0}
  NATIVE_WIDTH = 10

  def __init__(self, width=10, height=N, params=None):
    super().__init__(width, height, params)
    self.buf = LEDBuffer(width, height)
    self._t = 0.0
    self._last_t = None
    self._blob_seeds = [
      (random.random() * 100, random.random() * 100) for _ in range(12)
    ]

  def render(self, t, state):
    dt_ms = self._calc_dt_ms(t)
    speed = self.params.get("speed", 0.3)
    num_blobs = int(self.params.get("blobs", 5))
    size = self.params.get("size", 1.0)
    pal_idx = self.params.get("palette", 0)

    self._t += dt_ms * 0.001 * speed
    tt = self._t
    cols = self.width
    rows = self.height

    # (cols, 1) and (1, rows) grids for broadcasting
    x_g = np.arange(cols, dtype=np.float64)[:, np.newaxis]  # (cols, 1)
    y_g = np.arange(rows, dtype=np.float64)[np.newaxis, :]  # (1, rows)

    size_x = max(1.0, size * 2)
    size_y = max(1.0, size * 25)

    val = np.zeros((cols, rows), dtype=np.float64)
    for bi in range(num_blobs):
      sx, sy = self._blob_seeds[bi]
      bx = (cols / 2) + math.sin(tt * 0.7 + sx * 6.28) * cols * 0.4
      by = (rows / 2) + math.sin(tt * 0.3 + sy * 6.28) * rows * 0.4
      dx = (x_g - bx) / size_x
      dy = (y_g - by) / size_y
      dist_sq = dx * dx + dy * dy
      val += 1.0 / (1.0 + dist_sq * 3)

    val = np.clip(val, 0.0, 1.0)
    hue = val * 0.8 + 0.1
    rgb = pal_color_grid(pal_idx % NUM_PALETTES, hue)
    self.buf.data = (rgb.astype(np.float32) * val[..., np.newaxis]).clip(0, 255).astype(np.uint8)

    return self.buf.get_frame()

  def _calc_dt_ms(self, t):
    if self._last_t is None:
      self._last_t = t
      return 16.67
    dt = (t - self._last_t) * 1000.0
    self._last_t = t
    return max(0.0, dt)


# ═══════════════════════════════════════════════════════════════════
#  OCEAN WAVES
# ═══════════════════════════════════════════════════════════════════

class OceanWaves(Effect):
  """Layered sine waves with depth fade, default Ocean palette."""

  CATEGORY = "ambient"
  DISPLAY_NAME = "Ocean Waves"
  DESCRIPTION = "Layered sine waves with depth fade and palette coloring"
  PALETTE_SUPPORT = True

  PARAMS = [
    _Param("Speed", "speed", 0.1, 3.0, 0.1, 0.6),
    _Param("Depth", "depth", 0.3, 3.0, 0.1, 1.0),
    _Param("Layers", "layers", 1, 5, 1, 3),
  ]
  _SCALAR_PARAMS = {"speed": 0.6, "depth": 1.0, "layers": 3, "palette": 1}
  NATIVE_WIDTH = 10

  def __init__(self, width=10, height=N, params=None):
    super().__init__(width, height, params)
    # Default palette to Ocean (idx 1) if not overridden
    if "palette" not in self.params:
      self.params["palette"] = 1
    self.buf = LEDBuffer(width, height)
    self._t = 0.0
    self._last_t = None

  def render(self, t, state):
    dt_ms = self._calc_dt_ms(t)
    speed = self.params.get("speed", 0.6)
    depth = self.params.get("depth", 1.0)
    num_layers = int(self.params.get("layers", 3))
    pal_idx = self.params.get("palette", 1)

    self._t += dt_ms * 0.001 * speed
    tt = self._t
    cols = self.width
    rows = self.height

    # (cols, 1) and (1, rows) grids for broadcasting
    x_g = np.arange(cols, dtype=np.float64)[:, np.newaxis]  # (cols, 1)
    y_g = np.arange(rows, dtype=np.float64)[np.newaxis, :]  # (1, rows)

    v = np.zeros((cols, rows), dtype=np.float64)
    for layer in range(num_layers):
      freq = (layer + 1) * 0.5
      phase = layer * 1.7
      v += np.sin(
        y_g * 0.02 * depth * freq + tt * (1 + layer * 0.4)
        + phase + x_g * 0.3 * freq
      ) / num_layers

    v = (v + 1) * 0.5
    depth_fade = 1.0 - (rows - 1 - y_g) / rows * 0.3  # (1, rows)
    v *= depth_fade

    self.buf.data = pal_color_grid(pal_idx % NUM_PALETTES, v)

    return self.buf.get_frame()

  def _calc_dt_ms(self, t):
    if self._last_t is None:
      self._last_t = t
      return 16.67
    dt = (t - self._last_t) * 1000.0
    self._last_t = t
    return max(0.0, dt)


# ═══════════════════════════════════════════════════════════════════
#  STARFIELD
# ═══════════════════════════════════════════════════════════════════

class Starfield(Effect):
  """Twinkling starfield with adjustable density and speed."""

  CATEGORY = "ambient"
  DISPLAY_NAME = "Starfield"
  DESCRIPTION = "Twinkling stars with adjustable density and twinkle rate"
  PALETTE_SUPPORT = True

  PARAMS = [
    _Param("Density", "density", 0.005, 0.1, 0.005, 0.03),
    _Param("Twinkle", "twinkle", 0.2, 3.0, 0.1, 1.0),
    _Param("Speed", "speed", 0.2, 3.0, 0.1, 1.0),
  ]
  _SCALAR_PARAMS = {"density": 0.03, "twinkle": 1.0, "speed": 1.0}
  NATIVE_WIDTH = 10

  def __init__(self, width=10, height=N, params=None):
    super().__init__(width, height, params)
    self.buf = LEDBuffer(width, height)
    self._last_t = None
    # Pre-populate stars at default density
    # Each star: [x, y, phase, freq, max_brightness]
    count = int(width * height * 0.03)
    self._stars = [
      [random.randint(0, width - 1), random.randint(0, height - 1),
       random.uniform(0, 6.28), random.uniform(0.5, 3.0),
       random.uniform(0.3, 1.0)]
      for _ in range(count)
    ]

  def render(self, t, state):
    dt_ms = self._calc_dt_ms(t)
    dt = dt_ms * 0.001
    density = self.params.get("density", 0.03)
    twinkle = self.params.get("twinkle", 1.0)
    speed = self.params.get("speed", 1.0)
    pal_idx = self.params.get("palette", 0)

    cols = self.width
    rows = self.height

    # Adjust star count to match density
    target = int(cols * rows * density)
    while len(self._stars) < target:
      self._stars.append([
        random.randint(0, cols - 1), random.randint(0, rows - 1),
        random.uniform(0, 6.28), random.uniform(0.5, 3.0),
        random.uniform(0.3, 1.0),
      ])
    while len(self._stars) > target:
      self._stars.pop()

    self.buf.clear()
    for s in self._stars:
      s[2] += s[3] * speed * dt
      b = (math.sin(s[2] * twinkle) + 1) * 0.5 * s[4]
      c = pal_color(pal_idx % NUM_PALETTES, s[4])
      self.buf.add_led(s[0], s[1], c[0] * b, c[1] * b, c[2] * b)

    return self.buf.get_frame()

  def _calc_dt_ms(self, t):
    if self._last_t is None:
      self._last_t = t
      return 16.67
    dt = (t - self._last_t) * 1000.0
    self._last_t = t
    return max(0.0, dt)


# ═══════════════════════════════════════════════════════════════════
#  MATRIX RAIN
# ═══════════════════════════════════════════════════════════════════

class MatrixRain(Effect):
  """Falling digital rain with varying speed drops and fade trails.
  Default palette: Forest (idx 3)."""

  CATEGORY = "ambient"
  DISPLAY_NAME = "Matrix Rain"
  DESCRIPTION = "Falling digital rain with speed-varied drops and fade trails"
  PALETTE_SUPPORT = True

  PARAMS = [
    _Param("Speed", "speed", 0.2, 4.0, 0.1, 1.0),
    _Param("Density", "density", 0.1, 1.0, 0.05, 0.4),
    _Param("Trail", "trail", 5, 60, 1, 25),
  ]
  _SCALAR_PARAMS = {"speed": 1.0, "density": 0.4, "trail": 25, "palette": 3}
  NATIVE_WIDTH = 10

  def __init__(self, width=10, height=N, params=None):
    super().__init__(width, height, params)
    # Default palette to Forest (idx 3) if not overridden
    if "palette" not in self.params:
      self.params["palette"] = 3
    self.buf = LEDBuffer(width, height)
    self._last_t = None
    # Each drop: [x, y_float, speed, brightness]
    self._drops = []

  def render(self, t, state):
    dt_ms = self._calc_dt_ms(t)
    dt = dt_ms * 0.001
    speed = self.params.get("speed", 1.0)
    density = self.params.get("density", 0.4)
    trail = int(self.params.get("trail", 25))
    pal_idx = self.params.get("palette", 3)

    cols = self.width
    rows = self.height

    self.buf.clear()

    # Spawn new drops
    for x in range(cols):
      if random.random() < density * dt * 3:
        # Mix of speeds: many slow, some medium, few fast
        r = random.random()
        if r < 0.5:
          spd = random.uniform(6, 20)     # slow drips
        elif r < 0.85:
          spd = random.uniform(20, 50)    # medium
        else:
          spd = random.uniform(50, 90)    # fast streaks
        self._drops.append([x, -1.0, spd * speed, random.uniform(0.5, 1.0)])

    # Update and render drops
    alive = []
    for d in self._drops:
      d[1] += d[2] * dt
      head = int(d[1])
      if head - trail < rows:
        alive.append(d)
        for ty in range(trail):
          py = head - ty
          if 0 <= py < rows:
            fade = (1.0 - ty / trail) ** 1.5
            c = pal_color(pal_idx % NUM_PALETTES, fade)
            b = fade * d[3]
            self.buf.add_led(d[0], py, c[0] * b, c[1] * b, c[2] * b)
    self._drops = alive

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

AMBIENT_A_EFFECTS: dict[str, type[Effect]] = {
  "plasma_sim": Plasma,
  "aurora_borealis": AuroraBorealis,
  "lava_lamp": LavaLamp,
  "ocean_waves": OceanWaves,
  "starfield": Starfield,
  "matrix_rain": MatrixRain,
}
