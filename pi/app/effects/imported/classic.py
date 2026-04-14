"""
Classic animations ported from led_sim_reference.py.

5 effects: RainbowCycle, FeldsteinEquation, Feldstein2 (OG),
BrettsFavorite, Fireplace. No Pygame dependencies.
"""

import math
import random

import numpy as np

from ..base import Effect
from ..engine.buffer import LEDBuffer
from ..engine.noise import perlin as _perlin, cyl_noise, cyl_fbm, cyl_noise_grid, cyl_fbm_grid, perlin_grid
from ..engine.color import hsv2rgb, clamp, clampf, qsub8, qadd8, scale8
from ..engine.palettes import (
  FELDSTEIN_PALETTES, NUM_FELDSTEIN_PALETTES,
  fire_color, pal_color,
)
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


# ─── Helper: inoise8_sub (lines 739-742 of reference) ──────────────

def _inoise8_sub(x, y, z):
  """FastLED-style inoise8 subtraction — Perlin mapped to 0-255 with
  bottom half removed and top half boosted."""
  v = clamp((_perlin(x / 256.0, y / 256.0, z / 256.0) + 1) * 127.5)
  v = qsub8(v, 128)
  return qadd8(v, scale8(v, 128))


# ─── Ember particle (lines 899-913 of reference) ───────────────────

class _Ember:
  """Rising fire ember particle."""
  __slots__ = (
    'x', 'y', 'vx', 'vy', 'brightness', 'life', 'max_life',
    'flicker_phase', 'flicker_speed',
  )

  def __init__(self, x, y, brightness, spread=0.0):
    self.x = x + random.uniform(-0.4, 0.4)
    self.y = float(y)
    speed = random.uniform(30.0, 100.0)
    angle = random.gauss(0, spread)
    self.vx = speed * math.sin(angle)
    self.vy = -speed * math.cos(angle)
    self.brightness = random.uniform(0.3, 1.0) ** 0.7
    self.max_life = random.uniform(1.0, 4.5)
    self.life = self.max_life
    self.flicker_phase = random.uniform(0, 6.28)
    self.flicker_speed = random.uniform(8.0, 25.0)


# ═══════════════════════════════════════════════════════════════════
#  RAINBOW CYCLE
# ═══════════════════════════════════════════════════════════════════

class RainbowCycle(Effect):
  """Smooth rainbow color cycling across the entire pillar."""

  CATEGORY = "classic"
  DISPLAY_NAME = "Rainbow Cycle"
  DESCRIPTION = "Smooth rainbow color cycling across the pillar"
  PALETTE_SUPPORT = True

  PARAMS = [
    _Param("Speed", "speed", 0.1, 5.0, 0.1, 1.0),
  ]
  _SCALAR_PARAMS = {"speed": 1.0}

  def __init__(self, width=10, height=N, params=None):
    super().__init__(width, height, params)
    self.buf = LEDBuffer(width, height)
    self._hue = 0
    self._timer = 0.0
    self._last_t = None

  def render(self, t, state):
    dt_ms = self._calc_dt_ms(t)
    speed = self.params.get("speed", 1.0)
    pal_idx = self.params.get("palette", 0)

    self._timer += dt_ms * speed
    if self._timer >= 100:
      self._hue = (self._hue + 1) % 256
      self._timer -= 100

    c = pal_color(pal_idx, self._hue / 255.0)
    for x in range(self.width):
      for y in range(self.height):
        self.buf.set_led(x, y, c[0], c[1], c[2])

    return self.buf.get_frame()

  def _calc_dt_ms(self, t):
    if self._last_t is None:
      self._last_t = t
      return 16.67
    dt = (t - self._last_t) * 1000.0
    self._last_t = t
    return max(0.0, dt)


# ═══════════════════════════════════════════════════════════════════
#  FELDSTEIN EQUATION
# ═══════════════════════════════════════════════════════════════════

class FeldsteinEquation(Effect):
  """Cylinder-wrapped noise with alternating up/down traveling bars.
  Even columns scroll noise downward, odd columns scroll upward,
  creating a weaving barber-pole effect around the cylinder."""

  CATEGORY = "classic"
  DISPLAY_NAME = "Feldstein Equation"
  DESCRIPTION = "Cylinder-wrapped noise with alternating barber-pole scrolling"
  PALETTE_SUPPORT = False

  PARAMS = [
    _Param("Speed", "speed", 0.2, 3.0, 0.1, 1.0),
    _Param("Bar Speed", "bar_speed", 0.2, 4.0, 0.1, 1.0),
  ]
  _SCALAR_PARAMS = {"speed": 1.0, "bar_speed": 1.0}

  def __init__(self, width=10, height=N, params=None):
    super().__init__(width, height, params)
    self.buf = LEDBuffer(width, height)
    self._rng = random.Random()
    self._xo = self._rng.randint(0, 65535)
    self._yo = self._rng.randint(0, 65535)
    self._zo = self._rng.randint(0, 65535)
    self._hue = 0
    self._hue_accum = 0.0
    self._elapsed_ms = 0.0
    self._last_t = None

  def render(self, t, state):
    dt_ms = self._calc_dt_ms(t)
    speed = self.params.get("speed", 1.0)
    bar_speed = self.params.get("bar_speed", 1.0)

    self._elapsed_ms += dt_ms
    time_s = self._elapsed_ms * speed * 0.001

    self._hue_accum += dt_ms
    if self._hue_accum >= 1000:
      self._hue = (self._hue + 1) % 256
      self._hue_accum -= 1000
    h = self._hue

    bar_t = time_s * bar_speed * 40

    # Persistent buffer: fade instead of clear
    self.buf.fade_by(48)

    # Vectorized: compute all noise values at once
    cols = self.width
    rows = self.height
    col_idx = np.arange(cols, dtype=np.float64)
    row_idx = np.arange(rows, dtype=np.float64)

    # Build scrolled row grids (even cols scroll +, odd cols scroll -)
    directions = np.where(col_idx % 2 == 0, 1.0, -1.0)
    y_scrolls = bar_t * directions  # (cols,)
    # scrolled_row[col, row] = row + y_scroll[col]
    scrolled = row_idx[np.newaxis, :] + y_scrolls[:, np.newaxis]  # (cols, rows)

    # Cylinder-mapped noise coordinates for each layer
    for layer, (x_off, t_mult, t_off, x_sc, y_sc, v_mult, h_off) in enumerate([
      (0, 1.0, 0, 0.8, 0.015, 300, 0),
      (20, 0.7, 50, 1.2, 0.012, 300, 96),
      (50, 0.4, 100, 0.6, 0.008, 250, 160),
    ]):
      angles = (col_idx + x_off) / cols * 6.2832
      r = cols * x_sc / 6.2832
      cx = np.cos(angles) * r  # (cols,)
      sy = np.sin(angles) * r
      z_vals = scrolled * y_sc + time_s * t_mult + t_off  # (cols, rows)
      cx_grid = cx[:, np.newaxis] * np.ones(rows)
      sy_grid = sy[:, np.newaxis] * np.ones(rows)
      noise = perlin_grid(cx_grid, sy_grid, z_vals)  # (cols, rows)
      vals = np.clip(np.maximum(0, noise) * v_mult, 0, 255).astype(np.uint8)

      # HSV to RGB for this layer (vectorized)
      hue = (h + h_off) & 255
      layer_rgb = np.zeros((cols, rows, 3), dtype=np.uint8)
      # Fast approximate: use hsv2rgb for the single hue, vary only V
      r_c, g_c, b_c = hsv2rgb(hue, 255, 255)
      if r_c + g_c + b_c > 0:
        layer_rgb[..., 0] = (vals.astype(np.uint16) * r_c // 255).astype(np.uint8)
        layer_rgb[..., 1] = (vals.astype(np.uint16) * g_c // 255).astype(np.uint8)
        layer_rgb[..., 2] = (vals.astype(np.uint16) * b_c // 255).astype(np.uint8)

      # Additive blend into buffer
      self.buf.data = np.clip(
        self.buf.data.astype(np.int16) + layer_rgb.astype(np.int16),
        0, 255,
      ).astype(np.uint8)

    return self.buf.get_frame()

  def _calc_dt_ms(self, t):
    if self._last_t is None:
      self._last_t = t
      return 16.67
    dt = (t - self._last_t) * 1000.0
    self._last_t = t
    return max(0.0, dt)


# ═══════════════════════════════════════════════════════════════════
#  FELDSTEIN 2 / OG
# ═══════════════════════════════════════════════════════════════════

class Feldstein2(Effect):
  """Faithful port of the original Noise2DAnimation with scale=500,
  three CHSV layers, fadeToBlackBy, and the exact divisors from C++.
  Adds palette selection and dark/light ratio control."""

  CATEGORY = "classic"
  DISPLAY_NAME = "Feldstein OG"
  DESCRIPTION = "Original Feldstein noise algorithm with 17 custom palettes"
  PALETTE_SUPPORT = False  # uses internal FELDSTEIN_PALETTES, not standard

  PARAMS = [
    _Param("Speed", "speed", 0.2, 3.0, 0.1, 1.0),
    _Param("Fade/Dark", "fade", 10, 200, 5, 48),
    _Param("Palette", "palette_idx", 0, 16, 1, 0),
  ]
  _SCALAR_PARAMS = {"speed": 1.0, "fade": 48, "palette_idx": 0}

  def __init__(self, width=10, height=N, params=None):
    super().__init__(width, height, params)
    self.buf = LEDBuffer(width, height)
    self._rng = random.Random()
    self._xo = self._rng.randint(0, 65535)
    self._yo = self._rng.randint(0, 65535)
    self._zo = self._rng.randint(0, 65535)
    self._hue = 0
    self._hue_accum = 0.0
    self._elapsed_ms = 0.0
    self._last_t = None

  def render(self, t, state):
    dt_ms = self._calc_dt_ms(t)
    speed = self.params.get("speed", 1.0)
    fade = int(self.params.get("fade", 48))
    pi = int(self.params.get("palette_idx", 0)) % NUM_FELDSTEIN_PALETTES

    self._elapsed_ms += dt_ms
    time_val = int(self._elapsed_ms * speed) // 7 + self._zo
    SCALE = 180

    self._hue_accum += dt_ms
    if self._hue_accum >= 1000:
      self._hue = (self._hue + 1) % 256
      self._hue_accum -= 1000
    h = self._hue

    # Get palette hue/sat offsets for each layer
    _pname, layers = FELDSTEIN_PALETTES[pi]
    h1_off, s1, _ = layers[0]
    h2_off, s2, _ = layers[1]
    h3_off, s3, _ = layers[2]

    # Persistent buffer: fade instead of clear
    self.buf.fade_by(fade)

    # Vectorized Feldstein OG noise
    cols, rows = self.width, self.height
    x_idx = np.arange(cols, dtype=np.float64)
    y_idx = np.arange(rows, dtype=np.float64)
    xS = (x_idx * SCALE + self._xo)[:, np.newaxis] * np.ones(rows)  # (cols, rows)
    yS = np.ones(cols)[:, np.newaxis] * (y_idx * SCALE + self._yo)   # (cols, rows)

    for h_off, sat, (nx_div, ny_div, ny_off, nz_val) in [
      (h1_off, s1, (10, 50, time_val // 2, float(time_val))),
      (h2_off, s2, (10, 50, time_val // 2, float(time_val + 100 * SCALE))),
      (h3_off, s3, (100, 40, 0, float(time_val // 10 + 300 * SCALE))),
    ]:
      px = xS / nx_div / 256.0
      py = (yS / ny_div + ny_off) / 256.0
      pz = np.full_like(px, nz_val / 256.0)
      raw = (perlin_grid(px, py, pz) + 1.0) * 127.5
      raw = np.clip(raw, 0, 255).astype(np.int32)
      vals = raw - 128
      vals = np.clip(vals, 0, 255)
      vals = vals + ((vals * 128) >> 8)
      vals = np.clip(vals, 0, 255).astype(np.uint8)

      hue = (h + h_off) & 255
      r_c, g_c, b_c = hsv2rgb(hue, sat, 255)
      layer = np.zeros((cols, rows, 3), dtype=np.uint8)
      if r_c + g_c + b_c > 0:
        layer[..., 0] = (vals.astype(np.uint16) * r_c // 255).astype(np.uint8)
        layer[..., 1] = (vals.astype(np.uint16) * g_c // 255).astype(np.uint8)
        layer[..., 2] = (vals.astype(np.uint16) * b_c // 255).astype(np.uint8)
      self.buf.data = np.clip(
        self.buf.data.astype(np.int16) + layer.astype(np.int16), 0, 255
      ).astype(np.uint8)

    return self.buf.get_frame()

  def _calc_dt_ms(self, t):
    if self._last_t is None:
      self._last_t = t
      return 16.67
    dt = (t - self._last_t) * 1000.0
    self._last_t = t
    return max(0.0, dt)


# ═══════════════════════════════════════════════════════════════════
#  BRETT'S FAVORITE
# ═══════════════════════════════════════════════════════════════════

class BrettsFavorite(Effect):
  """Sine-wave bands with drifting positions and speeds. Each horizontal
  band has its own phase and velocity, creating flowing wave interference
  patterns."""

  CATEGORY = "classic"
  DISPLAY_NAME = "Brett's Favorite"
  DESCRIPTION = "Sine-wave bands with drifting positions and random kicks"
  PALETTE_SUPPORT = True

  PARAMS = [
    _Param("Speed", "speed", 0.2, 3.0, 0.1, 1.0),
    _Param("Bands", "bands", 4, 32, 1, 16),
    _Param("Damping", "damping", 0.8, 0.99, 0.01, 0.95),
  ]
  _SCALAR_PARAMS = {"speed": 1.0, "bands": 16, "damping": 0.95}

  def __init__(self, width=10, height=N, params=None):
    super().__init__(width, height, params)
    self.buf = LEDBuffer(width, height)
    self._hue = random.randint(0, 255)
    self._hue_accum = 0.0
    self._pos = [random.randint(0, 255) for _ in range(32)]
    self._spd = [random.choice([-1, 1]) for _ in range(32)]
    self._spd_accum = 0.0
    self._last_t = None

  def render(self, t, state):
    dt_ms = self._calc_dt_ms(t)
    speed = self.params.get("speed", 1.0)
    bands = int(self.params.get("bands", 16))
    damping = self.params.get("damping", 0.95)

    # Hue drifts slowly
    self._hue_accum += dt_ms
    if self._hue_accum >= 100:
      self._hue = (self._hue + 1) % 256
      self._hue_accum -= 100

    # Speeds decay toward +/-1
    self._spd_accum += dt_ms
    if self._spd_accum >= 30:
      self._spd_accum -= 30
      for i in range(bands):
        if self._spd[i] > 1:
          self._spd[i] -= 1
        elif self._spd[i] < -1:
          self._spd[i] += 1

    # Random kicks (simulates stomp/jerk from original)
    if random.random() < 0.03 * speed:
      for i in range(bands):
        self._spd[i] += random.randint(-5, 5)

    # Update positions
    for i in range(bands):
      self._pos[i] = (self._pos[i] + int(self._spd[i] * speed)) & 255

    # Render
    self.buf.clear()
    band_h = max(1, self.height // bands)
    for y in range(self.height):
      band_idx = min(y // band_h, bands - 1)
      p = self._pos[band_idx]
      base_hue = (self._hue + self._spd[band_idx]) & 255
      base_sat = max(0, 255 - abs(self._spd[band_idx]) * 3)
      for x in range(self.width):
        # sin8 equivalent: sine wave across width
        phase = (p + x * 256 // self.width) & 255
        val = max(0, int((math.sin(phase / 255.0 * 6.2832) + 1) * 127.5) - 20)
        c = hsv2rgb(base_hue, base_sat, val)
        self.buf.set_led(x, y, c[0], c[1], c[2])

    return self.buf.get_frame()

  def _calc_dt_ms(self, t):
    if self._last_t is None:
      self._last_t = t
      return 16.67
    dt = (t - self._last_t) * 1000.0
    self._last_t = t
    return max(0.0, dt)


# ═══════════════════════════════════════════════════════════════════
#  FIREPLACE
# ═══════════════════════════════════════════════════════════════════

class Fireplace(Effect):
  """Full fire simulation with heat convection, ember particles,
  noise-driven turbulence, and 16 tunable parameters."""

  CATEGORY = "classic"
  DISPLAY_NAME = "Fireplace"
  DESCRIPTION = "Warm flickering fireplace with ember particles and heat convection"
  PALETTE_SUPPORT = False

  PARAMS = [
    _Param("** FUEL **",   "fuel",           0.0, 1.0,  0.05, 0.6),
    _Param("Spark Zone",   "spark_zone",     1,   60,   2,    35),
    _Param("Spark Prob",   "spark_prob",     0.0, 1.0,  0.05, 0.85),
    _Param("Cool Base",    "cool_base",      0.0, 0.10, 0.002, 0.012),
    _Param("Cool Height",  "cool_height",    0.0, 0.20, 0.005, 0.045),
    _Param("Cool Noise",   "cool_noise",     0.0, 1.0,  0.05, 0.50),
    _Param("Diffuse Ctr",  "diffuse_center", 0.50, 1.0, 0.02, 0.74),
    _Param("Diffuse Side", "diffuse_side",   0.0, 0.25, 0.01, 0.13),
    _Param("Turb X",       "turb_x_scale",   0.0, 3.0,  0.1,  1.8),
    _Param("Turb Y Bias",  "turb_y_bias",    0.0, 5.0,  0.1,  2.0),
    _Param("Turb Y Range", "turb_y_range",   0.0, 5.0,  0.1,  3.0),
    _Param("Buoyancy",     "buoyancy",       0.0, 5.0,  0.1,  2.5),
    _Param("Noise Detail", "noise_octaves",  1,   3,    1,    2),
    _Param("Ember Rate",   "ember_rate",     0.0, 1.0,  0.05, 0.20),
    _Param("Ember Burst",  "ember_burst",    1,   15,   1,    6),
    _Param("Ember Spread", "ember_spread",   0.0, 1.2,  0.05, 0.65),
  ]
  _SCALAR_PARAMS = {
    "fuel": 0.6, "spark_zone": 35, "spark_prob": 0.85,
    "cool_base": 0.012, "cool_height": 0.045, "cool_noise": 0.50,
    "diffuse_center": 0.74, "diffuse_side": 0.13,
    "turb_x_scale": 1.8, "turb_y_bias": 2.0, "turb_y_range": 3.0,
    "buoyancy": 2.5, "noise_octaves": 2,
    "ember_rate": 0.20, "ember_burst": 6, "ember_spread": 0.65,
  }
  _FLARE_PROB = 0.025
  _SPARK_MIN = 0.55
  _SPARK_MAX = 1.0
  _MAX_EMBERS = 150

  def __init__(self, width=10, height=N, params=None):
    super().__init__(width, height, params)
    self.buf = LEDBuffer(width, height)
    self._heat = [[0.0] * height for _ in range(width)]
    self._time = 0.0
    self._embers = []
    self._last_t = None

    # Warm up the spark zone
    spark_zone = int(self.params.get("spark_zone", 35))
    for x in range(width):
      for y in range(height - spark_zone, height):
        self._heat[x][y] = random.uniform(0.4, 0.9)

  def render(self, t, state):
    dt_ms = self._calc_dt_ms(t)
    self._time += dt_ms * 0.001
    dt = min(dt_ms / 16.67, 2.5)
    sim_t = self._time
    cols = self.width
    rows = self.height
    center = (cols - 1) / 2.0

    # Read all params
    fuel = clampf(self.params.get("fuel", 0.6))
    fuel_sq = fuel * fuel
    spark_zone = max(3, int(self.params.get("spark_zone", 35) * (0.2 + fuel * 0.8)))
    spark_prob = self.params.get("spark_prob", 0.85)
    cool_base = self.params.get("cool_base", 0.012)
    cool_height = self.params.get("cool_height", 0.045)
    cool_noise_amt = self.params.get("cool_noise", 0.50)
    diffuse_ctr = self.params.get("diffuse_center", 0.74)
    diffuse_side = self.params.get("diffuse_side", 0.13)
    turb_x = self.params.get("turb_x_scale", 1.8) * (0.5 + fuel * 0.5)
    ty_bias = self.params.get("turb_y_bias", 2.0) * (0.3 + fuel * 0.7)
    ty_range = self.params.get("turb_y_range", 3.0) * (0.3 + fuel * 0.7)
    buoy = self.params.get("buoyancy", 2.5) * (0.2 + fuel * 0.8)
    octs = max(1, int(self.params.get("noise_octaves", 2)))
    ember_rate = self.params.get("ember_rate", 0.20)
    ember_burst = max(1, int(self.params.get("ember_burst", 6)))
    ember_spread = self.params.get("ember_spread", 0.65)

    # ── Sparks ──────────────────────────────────────────────────
    for x in range(cols):
      cw = 1.0 - abs(x - center) / (center + 0.5) * 0.25
      hotspot = (cyl_noise(x * 2, sim_t * 3, sim_t * 0.5, 1.0, 1.0) + 1) * 0.5
      for yo in range(spark_zone):
        y = rows - 1 - yo
        if y < 0:
          break
        depth_factor = 1.0 - yo / spark_zone * 0.6
        prob = spark_prob * fuel * cw * depth_factor * (0.5 + hotspot * 0.7)
        if random.random() < prob:
          intensity = random.uniform(self._SPARK_MIN, self._SPARK_MAX) * (0.3 + fuel * 0.7)
          self._heat[x][y] = min(1.0, self._heat[x][y] + intensity * cw * dt)

    # ── Flares ──────────────────────────────────────────────────
    if random.random() < self._FLARE_PROB * fuel_sq:
      fc = random.randint(0, cols - 1)
      flare_height = int(random.randint(15, min(rows // 2, 60)) * (0.3 + fuel * 0.7))
      for dx in range(-2, 3):
        fx = (fc + dx) % cols
        for yo in range(flare_height):
          y = rows - 1 - yo
          fade = 1.0 - yo / flare_height
          self._heat[fx][y] = min(1.0, self._heat[fx][y] + 0.6 * fade * dt)

    # ── Convection ──────────────────────────────────────────────
    new_heat = [[0.0] * rows for _ in range(cols)]
    for x in range(cols):
      for y in range(rows):
        nx = cyl_fbm(x, y, sim_t * 8.0, octs, 0.5, 0.015)
        ny = cyl_fbm(x + 5, y, sim_t * 7.0, octs, 0.5, 0.015)
        lh = self._heat[x][y]
        sx = x + nx * turb_x
        sy = y + ty_bias + lh * buoy + abs(ny) * ty_range
        sx = max(0.0, min(cols - 1.001, sx))
        sy = max(0.0, min(rows - 1.001, sy))
        ix = int(sx)
        iy = int(sy)
        ffx = sx - ix
        ffy = sy - iy
        ix2 = min(ix + 1, cols - 1)
        iy2 = min(iy + 1, rows - 1)
        new_heat[x][y] = (
          self._heat[ix][iy] * (1 - ffx) * (1 - ffy)
          + self._heat[ix2][iy] * ffx * (1 - ffy)
          + self._heat[ix][iy2] * (1 - ffx) * ffy
          + self._heat[ix2][iy2] * ffx * ffy
        )

    # ── Lateral diffusion (wraps around cylinder) ───────────────
    for y in range(rows):
      snap = [new_heat[x][y] for x in range(cols)]
      for x in range(cols):
        new_heat[x][y] = (
          snap[x] * diffuse_ctr
          + snap[(x - 1) % cols] * diffuse_side
          + snap[(x + 1) % cols] * diffuse_side
        )

    # ── Cooling ─────────────────────────────────────────────────
    fuel_cool = 1.5 - fuel
    cb = cool_base * fuel_cool
    ch = cool_height * fuel_cool
    for x in range(cols):
      for y in range(rows):
        hf = (rows - 1 - y) / float(rows)  # 0=bottom, 1=top
        cn = (cyl_noise(x, y, sim_t * 10.0, 0.8, 0.03) + 1) * 0.5
        if hf < 0.5:
          cool = (cb * 0.3 + cn * cool_noise_amt * 0.15 + random.random() * 0.003) * dt
        else:
          top_frac = (hf - 0.5) * 2
          cool = (
            cb + top_frac * top_frac * ch
            + cn * cool_noise_amt * top_frac
            + random.random() * 0.005
          ) * dt
        new_heat[x][y] = max(0.0, new_heat[x][y] - cool)
    self._heat = new_heat

    # ── Ember bed — glowing coals across bottom 12 rows ─────────
    for x in range(cols):
      for yo in range(12):
        y = rows - 1 - yo
        if y < 0:
          break
        glow = 0.30 - yo * 0.02
        shimmer = (cyl_noise(x * 2 + 500, yo * 0.5, sim_t * 1.5, 1.0, 1.0) + 1) * 0.05
        self._heat[x][y] = max(self._heat[x][y], glow + shimmer)

    # ── Ember particles ─────────────────────────────────────────
    dt_s = dt_ms * 0.001
    if ember_rate > 0 and fuel > 0.1:
      real_rate = ember_rate * (0.1 + fuel_sq * 0.9)
      for x in range(cols):
        for y in range(0, rows, 3):
          h = self._heat[x][y]
          if 0.15 < h < 0.65 and random.random() < real_rate * h * dt_s * 1.5:
            burst = max(1, int(random.gauss(ember_burst, ember_burst * 0.5)))
            for _ in range(burst):
              if len(self._embers) < self._MAX_EMBERS:
                self._embers.append(_Ember(x, y, h, ember_spread))

    alive = []
    for e in self._embers:
      e.x += e.vx * dt_s
      e.y += e.vy * dt_s
      e.vx += random.uniform(-2.5, 2.5) * dt_s
      e.vy *= 0.98 ** (dt_s * 60)
      e.brightness *= 0.998 ** (dt_s * 60)
      e.flicker_phase += e.flicker_speed * dt_s
      e.life -= dt_s
      if e.life > 0 and -2 < e.y < rows:
        alive.append(e)
    self._embers = alive

    # ── Render heat ─────────────────────────────────────────────
    for x in range(cols):
      for y in range(rows):
        c = fire_color(self._heat[x][y])
        self.buf.set_led(x, y, c[0], c[1], c[2])

    # ── Render embers ───────────────────────────────────────────
    for e in self._embers:
      ecol = int(round(e.x))
      erow = int(round(e.y))
      if 0 <= ecol < cols and 0 <= erow < rows:
        af = max(0.0, e.life / e.max_life)
        fl = 0.65 + 0.35 * math.sin(e.flicker_phase)
        b = e.brightness * af * fl
        ec = fire_color(min(1.0, af * 0.45 + 0.15))
        self.buf.add_led(ecol, erow,
                         int(ec[0] * b * 1.4),
                         int(ec[1] * b * 1.1),
                         int(ec[2] * b * 0.4))

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

CLASSIC_EFFECTS: dict[str, type[Effect]] = {
  "rainbow_cycle_sim": RainbowCycle,
  "feldstein_equation": FeldsteinEquation,
  "feldstein_og": Feldstein2,
  "bretts_favorite": BrettsFavorite,
  "fireplace": Fireplace,
}
