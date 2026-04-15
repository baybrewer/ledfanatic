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
from ..engine.noise import perlin as _perlin, cyl_noise, cyl_fbm, cyl_noise_grid, cyl_fbm_grid, perlin_grid, cyl_noise_xy, cyl_fbm_xy
from ..engine.color import hsv2rgb, clamp, clampf, qsub8, qadd8, scale8
from ..engine.palettes import (
  FELDSTEIN_PALETTES, NUM_FELDSTEIN_PALETTES,
  fire_color, pal_color, fire_color_grid, pal_color_grid,
  NUM_PALETTES,
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
  NATIVE_WIDTH = 10

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
    self.buf.data[:] = [c[0], c[1], c[2]]

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
  PALETTE_SUPPORT = True

  PARAMS = [
    _Param("Speed", "speed", 0.2, 3.0, 0.1, 1.0),
    _Param("Bar Speed", "bar_speed", 0.2, 4.0, 0.1, 1.0),
  ]
  _SCALAR_PARAMS = {"speed": 1.0, "bar_speed": 1.0, "palette": 0}
  NATIVE_WIDTH = 10

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
    pal_idx = int(self.params.get("palette", 0)) % NUM_PALETTES

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
      vals = np.clip(np.maximum(0, noise) * v_mult, 0, 255).astype(np.float64)

      # Compute hue for this layer, normalize to 0-1 for palette lookup
      hue_norm = np.full_like(vals, ((h + h_off) & 255) / 255.0)
      # Get palette color and scale by noise intensity
      pal_rgb = pal_color_grid(pal_idx, hue_norm)  # (cols, rows, 3) uint8
      intensity = (vals / 255.0)[..., np.newaxis]  # (cols, rows, 1)
      layer_rgb = (pal_rgb.astype(np.float64) * intensity).astype(np.uint8)

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
  ]
  _SCALAR_PARAMS = {"speed": 1.0, "fade": 48, "palette_idx": 0}
  NATIVE_WIDTH = 10

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
  NATIVE_WIDTH = 10

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

    # Render — vectorized per-band
    self.buf.clear()
    cols = self.width
    rows = self.height
    band_h = max(1, rows // bands)
    x_arr = np.arange(cols, dtype=np.float64)

    for band_idx in range(bands):
      y0 = band_idx * band_h
      y1 = min(y0 + band_h, rows)
      if y0 >= rows:
        break
      base_hue = (self._hue + self._spd[band_idx]) & 255
      base_sat = max(0, 255 - abs(self._spd[band_idx]) * 3)
      r_c, g_c, b_c = hsv2rgb(base_hue, base_sat, 255)
      p = self._pos[band_idx]
      # Vectorized sine across width
      phase = ((p + x_arr * 256 / cols) % 256) / 255.0 * 6.2832
      val = np.clip((np.sin(phase) + 1) * 127.5 - 20, 0, 255).astype(np.uint16)
      # Scale base color by val, broadcast to all rows in this band
      band_rows = y1 - y0
      band_rgb = np.zeros((cols, band_rows, 3), dtype=np.uint8)
      band_rgb[..., 0] = (val[:, np.newaxis] * r_c // 255).astype(np.uint8)
      band_rgb[..., 1] = (val[:, np.newaxis] * g_c // 255).astype(np.uint8)
      band_rgb[..., 2] = (val[:, np.newaxis] * b_c // 255).astype(np.uint8)
      self.buf.data[:, y0:y1] = band_rgb

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
  noise-driven turbulence, and palette-driven coloring."""

  CATEGORY = "classic"
  DISPLAY_NAME = "Fireplace"
  DESCRIPTION = "Warm flickering fireplace with ember particles and heat convection"
  PALETTE_SUPPORT = True

  PARAMS = [
    _Param("Fuel",            "fuel",           0.2, 5.0, 0.1,  0.6),
    _Param("Diffuse Ctr",     "diffuse_center", 0.50, 1.0, 0.02, 0.74),
    _Param("Spark Intensity", "spark_prob",     0.0, 1.0, 0.05, 1.0),
    # palette is handled via dropdown, not slider
  ]
  _SCALAR_PARAMS = {
    "fuel": 0.6, "spark_zone": 60, "spark_prob": 1.0,
    "cool_base": 0.012, "cool_height": 0.045, "cool_noise": 0.5,
    "diffuse_center": 0.74, "diffuse_side": 0.13,
    "turb_x_scale": 1.8, "turb_y_bias": 2.0, "turb_y_range": 3.0,
    "buoyancy": 2.5, "noise_octaves": 2,
    "ember_rate": 0.20, "ember_burst": 6, "ember_spread": 0.65,
    "palette": 4,
  }
  _FLARE_PROB = 0.025
  _SPARK_MIN = 0.55
  _SPARK_MAX = 1.0
  _MAX_EMBERS = 150
  NATIVE_WIDTH = 10

  def __init__(self, width=10, height=N, params=None):
    super().__init__(width, height, params)
    self.buf = LEDBuffer(width, height)
    self._heat = np.zeros((width, height), dtype=np.float64)
    self._time = 0.0
    self._embers = []
    self._last_t = None

    # Pre-compute coordinate grids (reused every frame)
    self._x_g = np.arange(width, dtype=np.float64)[:, np.newaxis] * np.ones(height)
    self._y_g = np.ones(width)[:, np.newaxis] * np.arange(height, dtype=np.float64)
    # Half-resolution grids for noise (2x faster, visually identical for fire)
    half_h = height // 2
    self._x_g_half = np.arange(width, dtype=np.float64)[:, np.newaxis] * np.ones(half_h)
    self._y_g_half = np.ones(width)[:, np.newaxis] * (np.arange(half_h, dtype=np.float64) * 2)
    # Noise cache — update at 30 Hz (every other frame), saves ~6ms/frame
    self._noise_tick = 0
    self._cached_nx = np.zeros((width, height), dtype=np.float64)
    self._cached_ny = np.zeros((width, height), dtype=np.float64)
    self._cached_cn = np.zeros((width, height), dtype=np.float64)

    # Warm up the spark zone
    spark_zone = int(self.params.get("spark_zone", 60))
    self._heat[:, height - spark_zone:] = np.random.uniform(0.4, 0.9, (width, spark_zone))

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
    sz = max(3, int(self.params.get("spark_zone", 60) * (0.2 + fuel * 0.8)))
    spark_prob = self.params.get("spark_prob", 1.0)
    cool_base = self.params.get("cool_base", 0.012)
    cool_height = self.params.get("cool_height", 0.045)
    cool_noise_amt = self.params.get("cool_noise", 0.5)
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

    # ── Sparks (vectorized) ──────────────────────────────────────
    # Per-column hotspot (10 scalar noise calls — negligible)
    hotspots = np.array([
      (cyl_noise(x * 2, sim_t * 3, sim_t * 0.5, 1.0, 1.0) + 1) * 0.5
      for x in range(cols)
    ])
    cw_arr = 1.0 - np.abs(np.arange(cols, dtype=np.float64) - center) / (center + 0.5) * 0.25
    yo_arr = np.arange(sz)
    y_arr = rows - 1 - yo_arr
    valid = y_arr >= 0
    yo_arr = yo_arr[valid]
    y_arr = y_arr[valid]
    depth_arr = 1.0 - yo_arr / sz * 0.6

    # Probability grid: (cols, len(yo_arr))
    prob_grid = spark_prob * fuel * cw_arr[:, np.newaxis] * depth_arr[np.newaxis, :] * (0.5 + hotspots[:, np.newaxis] * 0.7)
    rand_grid = np.random.random((cols, len(yo_arr)))
    spark_mask = rand_grid < prob_grid

    # Apply sparks where mask is True
    intensity_grid = np.random.uniform(self._SPARK_MIN, self._SPARK_MAX, (cols, len(yo_arr))) * (0.3 + fuel * 0.7)
    heat_add = np.zeros_like(spark_mask, dtype=np.float64)
    heat_add[spark_mask] = intensity_grid[spark_mask] * cw_arr[np.where(spark_mask)[0]] * dt
    for j in range(len(y_arr)):
      self._heat[:, y_arr[j]] = np.minimum(1.0, self._heat[:, y_arr[j]] + heat_add[:, j])

    # ── Flares (keep scalar — rarely triggers) ───────────────────
    if random.random() < self._FLARE_PROB * fuel_sq:
      fc = random.randint(0, cols - 1)
      flare_height = int(random.randint(15, min(rows // 2, 60)) * (0.3 + fuel * 0.7))
      for dx in range(-2, 3):
        fx = (fc + dx) % cols
        for yo in range(flare_height):
          y = rows - 1 - yo
          fade = 1.0 - yo / flare_height
          self._heat[fx, y] = min(1.0, self._heat[fx, y] + 0.6 * fade * dt)

    # ── Convection (vectorized — noise at 30 Hz for speed) ──────
    x_g = self._x_g
    y_g = self._y_g

    # Update noise grids every other frame (~6ms savings)
    self._noise_tick += 1
    if self._noise_tick & 1 == 0:
      self._cached_nx = np.repeat(cyl_fbm_xy(self._x_g_half, self._y_g_half, sim_t * 8.0, octs, 0.5, 0.015, cols), 2, axis=1)[:, :rows]
      self._cached_ny = np.repeat(cyl_fbm_xy(self._x_g_half + 5, self._y_g_half, sim_t * 7.0, octs, 0.5, 0.015, cols), 2, axis=1)[:, :rows]
    nx = self._cached_nx
    ny = self._cached_ny

    # Source positions for advection
    sx = np.clip(x_g + nx * turb_x, 0, cols - 1.001)
    sy = np.clip(y_g + ty_bias + self._heat * buoy + np.abs(ny) * ty_range, 0, rows - 1.001)

    # Bilinear interpolation from heat grid
    ix = np.clip(sx.astype(np.int32), 0, cols - 2)
    iy = np.clip(sy.astype(np.int32), 0, rows - 2)
    fx = sx - ix
    fy = sy - iy
    ix2 = np.minimum(ix + 1, cols - 1)
    iy2 = np.minimum(iy + 1, rows - 1)
    new_heat = (
      self._heat[ix, iy] * (1 - fx) * (1 - fy)
      + self._heat[ix2, iy] * fx * (1 - fy)
      + self._heat[ix, iy2] * (1 - fx) * fy
      + self._heat[ix2, iy2] * fx * fy
    )

    # ── Lateral diffusion (vectorized with np.roll) ──────────────
    new_heat = (
      new_heat * diffuse_ctr
      + np.roll(new_heat, 1, axis=0) * diffuse_side
      + np.roll(new_heat, -1, axis=0) * diffuse_side
    )

    # ── Cooling (vectorized) ─────────────────────────────────────
    fuel_cool = 1.5 - fuel
    cb = cool_base * fuel_cool
    ch = cool_height * fuel_cool
    hf = (rows - 1 - y_g) / float(rows)  # 0=bottom, 1=top
    if self._noise_tick & 1 == 0:
      self._cached_cn = np.repeat((cyl_noise_xy(self._x_g_half, self._y_g_half, sim_t * 10.0, 0.8, 0.03, cols) + 1) * 0.5, 2, axis=1)[:, :rows]
    cn = self._cached_cn
    rng = np.random.random((cols, rows))
    top_frac = np.clip((hf - 0.5) * 2, 0, 1)
    cool = np.where(
      hf < 0.5,
      (cb * 0.3 + cn * cool_noise_amt * 0.15 + rng * 0.003) * dt,
      (cb + top_frac ** 2 * ch + cn * cool_noise_amt * top_frac + rng * 0.005) * dt,
    )
    new_heat = np.maximum(0, new_heat - cool)
    self._heat = new_heat

    # ── Ember bed — glowing coals across bottom 12 rows ──────────
    bed_yo = np.arange(12, dtype=np.float64)
    bed_y = rows - 1 - bed_yo.astype(np.int32)
    bed_valid = bed_y >= 0
    bed_yo = bed_yo[bed_valid]
    bed_y = bed_y[bed_valid]
    bed_glow = 0.30 - bed_yo * 0.02  # (12,)
    # Vectorize shimmer: compute noise for all (cols, 12) positions
    bed_x = np.arange(cols, dtype=np.float64) * 2 + 500  # (cols,)
    bed_x_g = bed_x[:, np.newaxis] * np.ones(len(bed_yo))  # (cols, 12)
    bed_yo_g = np.ones(cols)[:, np.newaxis] * (bed_yo * 0.5)  # (cols, 12)
    bed_z = np.full_like(bed_x_g, sim_t * 1.5)
    bed_shimmer = (perlin_grid(bed_x_g, bed_yo_g, bed_z) + 1) * 0.05  # (cols, 12)
    bed_floor = bed_glow[np.newaxis, :] + bed_shimmer  # (cols, 12)
    for j in range(len(bed_y)):
      self._heat[:, bed_y[j]] = np.maximum(self._heat[:, bed_y[j]], bed_floor[:, j])

    # ── Ember particles (keep scalar — sparse particle system) ────
    dt_s = dt_ms * 0.001
    if ember_rate > 0 and fuel > 0.1:
      real_rate = ember_rate * (0.1 + fuel_sq * 0.9)
      for x in range(cols):
        for y in range(0, rows, 3):
          h = self._heat[x, y]
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

    # ── Render heat (vectorized, palette-driven) ────────────────
    pal_idx = int(self.params.get("palette", 4)) % NUM_PALETTES
    pal_rgb = pal_color_grid(pal_idx, self._heat)  # (cols, rows, 3)
    # Multiply by heat so zero heat = black
    heat_scale = self._heat[..., np.newaxis]  # (cols, rows, 1)
    self.buf.data = (pal_rgb.astype(np.float64) * heat_scale).astype(np.uint8)

    # ── Render embers (keep scalar — sparse particles) ───────────
    for e in self._embers:
      ecol = int(round(e.x))
      erow = int(round(e.y))
      if 0 <= ecol < cols and 0 <= erow < rows:
        af = max(0.0, e.life / e.max_life)
        fl = 0.65 + 0.35 * math.sin(e.flicker_phase)
        b = e.brightness * af * fl
        ec = pal_color(pal_idx, min(1.0, af * 0.45 + 0.15))
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
