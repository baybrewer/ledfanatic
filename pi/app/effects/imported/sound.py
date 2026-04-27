"""
Sound-reactive animations ported from led_sim_reference.py.

10 effects: Spectrum, VUMeter, BeatPulse, BassFire, SoundRipples,
Spectrogram, SoundWorm, ParticleBurst, SoundPlasma, StrobeChaos.
Each wraps AudioCompatAdapter internally.

Vectorized with numpy for 60+ FPS on Raspberry Pi.
"""

import math
import random

import numpy as np

from ..base import Effect
from ..engine.buffer import LEDBuffer
from ..engine.color import hsv2rgb, clamp, clampf
from ..engine.palettes import (
  pal_color, fire_color, NUM_PALETTES, PALETTE_NAMES,
  pal_color_grid, fire_color_grid,
)


def _get_pal_idx(params, default=0):
  """Get palette index from params, handling string names or int values."""
  val = params.get('palette', default)
  if isinstance(val, str):
    try:
      return PALETTE_NAMES.index(val) % NUM_PALETTES
    except ValueError:
      return default % NUM_PALETTES
  return int(val) % NUM_PALETTES
from ..engine.noise import (
  cyl_noise, cyl_fbm,
  cyl_noise_xy, cyl_fbm_xy,
  cyl_noise_grid,
)
from ...audio.adapter import AudioCompatAdapter


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


# ─── Ember particle (reused by BassFire) ───────────────────────────

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
#  SPECTRUM
# ═══════════════════════════════════════════════════════════════════

class Spectrum(Effect):
  """FFT spectrum analyzer — per-column bars driven by audio.bands,
  with peak hold and rainbow cascade on drop."""

  CATEGORY = "sound"
  DISPLAY_NAME = "SR Spectrum"
  DESCRIPTION = "FFT spectrum analyzer with peak hold and drop cascade"
  PALETTE_SUPPORT = True

  PARAMS = [
    _Param("Gain", "gain", 0.2, 5.0, 0.1, 1.0),
    _Param("Decay", "decay", 0.5, 0.99, 0.01, 0.85),
  ]
  _SCALAR_PARAMS = {"gain": 1.0, "decay": 0.85}
  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    self._audio_adapter = AudioCompatAdapter()
    self.buf = LEDBuffer(width, height)
    self._heights = [0.0] * width
    self._peaks = [0.0] * width
    self._peak_age = [0] * width
    self._drop_flash = 0.0
    self._last_t = None

  def render(self, t, state):
    dt_ms = self._calc_dt_ms(t)
    raw = state._audio_lock_free
    audio = self._audio_adapter.adapt(raw, t)

    cols = self.width
    rows = self.height
    gain = self.params.get("gain", 1.0)
    decay = self.params.get("decay", 0.85)
    pal_idx = _get_pal_idx(self.params)

    self.buf.clear()

    # DROP: all bars slam to max, rainbow cascade
    if audio.drop:
      self._drop_flash = 1.0
      for i in range(cols):
        self._heights[i] = rows
        self._peaks[i] = rows
    self._drop_flash *= 0.96

    adjusted_gain = gain * (1 + audio.buildup * 0.5)
    from ...audio.adapter import AudioCompatAdapter as _AC
    col_bands = _AC.resample_bands(audio.bands, cols)
    for i in range(cols):
      target = col_bands[i] * rows * adjusted_gain
      self._heights[i] = max(target, self._heights[i] * decay)
      h = int(self._heights[i])
      if h > self._peaks[i]:
        self._peaks[i] = h
        self._peak_age[i] = 0
      self._peak_age[i] += 1
      if self._peak_age[i] > 30:
        self._peaks[i] = max(0, self._peaks[i] - 1)

      # Vectorized bar fill for this column
      bar_h = min(h, rows)
      if bar_h > 0:
        y_off_arr = np.arange(bar_h, dtype=np.float64)
        row_arr = rows - 1 - y_off_arr  # row indices (bottom-up)
        if self._drop_flash > 0.1:
          hue_arr = (y_off_arr / rows + i / cols) % 1.0
          rgb = pal_color_grid(0, hue_arr)  # (bar_h, 3) uint8
          b = self._drop_flash
          self.buf.data[i, (rows - bar_h):rows] = (
            rgb[::-1].astype(np.float32) * b
          ).clip(0, 255).astype(np.uint8)
        else:
          hue_arr = y_off_arr / rows
          rgb = pal_color_grid(pal_idx % NUM_PALETTES, hue_arr)
          self.buf.data[i, (rows - bar_h):rows] = rgb[::-1]

      pk = int(self._peaks[i])
      if 0 < pk < rows:
        self.buf.data[i, rows - 1 - pk] = (255, 255, 255)

    return self.buf.get_frame()

  def _calc_dt_ms(self, t):
    if self._last_t is None:
      self._last_t = t
      return 16.67
    dt = (t - self._last_t) * 1000.0
    self._last_t = t
    return max(0.0, dt)


# ═══════════════════════════════════════════════════════════════════
#  VU METER
# ═══════════════════════════════════════════════════════════════════

class VUMeter(Effect):
  """Full-width VU meter with breakdown breathing and drop flash."""

  CATEGORY = "sound"
  DISPLAY_NAME = "SR VU Meter"
  DESCRIPTION = "Full-width volume meter with breakdown pulse and drop flash"
  PALETTE_SUPPORT = True

  PARAMS = [
    _Param("Gain", "gain", 0.2, 5.0, 0.1, 1.5),
    _Param("Decay", "decay", 0.5, 0.99, 0.01, 0.9),
  ]
  _SCALAR_PARAMS = {"gain": 1.5, "decay": 0.9}
  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    self._audio_adapter = AudioCompatAdapter()
    self.buf = LEDBuffer(width, height)
    self._h = 0.0
    self._drop_hue = 0.0
    self._drop_flash = 0.0
    self._last_t = None

  def render(self, t, state):
    dt_ms = self._calc_dt_ms(t)
    raw = state._audio_lock_free
    audio = self._audio_adapter.adapt(raw, t)

    cols = self.width
    rows = self.height
    gain = self.params.get("gain", 1.5)
    decay = self.params.get("decay", 0.9)
    pal_idx = _get_pal_idx(self.params)

    self.buf.clear()

    if audio.drop:
      self._drop_flash = 1.0
      self._drop_hue = random.random()
    self._drop_flash *= 0.95

    # During breakdown, dim and pulse slowly (anticipation)
    if audio.breakdown:
      breath = (math.sin(audio._time * 4) + 1) * 0.15
      c = pal_color(pal_idx % NUM_PALETTES, 0.5)
      self.buf.data[:] = [int(c[0] * breath), int(c[1] * breath), int(c[2] * breath)]
      return self.buf.get_frame()

    adjusted_gain = gain * (1 + audio.buildup * 0.8)
    target = audio.volume * rows * adjusted_gain
    self._h = max(target, self._h * decay)
    h = int(self._h)

    bar_h = min(h, rows)
    if bar_h > 0:
      y_off_arr = np.arange(bar_h, dtype=np.float64)
      if self._drop_flash > 0.1:
        hue_arr = (y_off_arr / rows + self._drop_hue) % 1.0
        rgb = pal_color_grid(0, hue_arr)  # (bar_h, 3)
        rgb_scaled = (rgb.astype(np.float32) * self._drop_flash).clip(0, 255).astype(np.uint8)
        # Fill all columns with the same bar (broadcast)
        self.buf.data[:, (rows - bar_h):rows] = rgb_scaled[::-1][np.newaxis, :, :]
      else:
        hue_arr = y_off_arr / rows
        rgb = pal_color_grid(pal_idx % NUM_PALETTES, hue_arr)
        self.buf.data[:, (rows - bar_h):rows] = rgb[::-1][np.newaxis, :, :]

    return self.buf.get_frame()

  def _calc_dt_ms(self, t):
    if self._last_t is None:
      self._last_t = t
      return 16.67
    dt = (t - self._last_t) * 1000.0
    self._last_t = t
    return max(0.0, dt)


# ═══════════════════════════════════════════════════════════════════
#  BEAT PULSE
# ═══════════════════════════════════════════════════════════════════

class BeatPulse(Effect):
  """Full-matrix beat-driven pulse with drop strobe and breakdown dim."""

  CATEGORY = "sound"
  DISPLAY_NAME = "SR Beat Pulse"
  DESCRIPTION = "Beat-synced pulse with drop strobe burst and breakdown dim"
  PALETTE_SUPPORT = True

  PARAMS = [
    _Param("Gain", "gain", 0.2, 5.0, 0.1, 1.0),
    _Param("Decay", "decay", 0.8, 0.99, 0.01, 0.92),
    _Param("Flash", "flash", 0.3, 2.0, 0.1, 1.0),
  ]
  _SCALAR_PARAMS = {"gain": 1.0, "decay": 0.92, "flash": 1.0}
  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    self._audio_adapter = AudioCompatAdapter()
    self.buf = LEDBuffer(width, height)
    self._energy = 0.0
    self._hue = 0.0
    self._strobe_timer = 0.0
    self._last_t = None

  def render(self, t, state):
    dt_ms = self._calc_dt_ms(t)
    raw = state._audio_lock_free
    audio = self._audio_adapter.adapt(raw, t)

    dt = dt_ms * 0.001
    cols = self.width
    rows = self.height
    decay = self.params.get("decay", 0.92)
    flash = self.params.get("flash", 1.0)
    pal_idx = _get_pal_idx(self.params)

    gain = self.params.get("gain", 1.0)
    if audio.beat:
      self._energy = flash * gain * (1 + audio.buildup)
      self._hue = (self._hue + 0.08) % 1.0

    # DROP: rapid strobe burst for 2 seconds
    if audio.drop:
      self._strobe_timer = 2.0
    self._strobe_timer = max(0, self._strobe_timer - dt)

    if self._strobe_timer > 0:
      # Fast strobe: alternate full bright / dark
      strobe_on = int(self._strobe_timer * 20) % 2 == 0
      if strobe_on:
        hue = (self._hue + random.random() * 0.3) % 1.0
        c = pal_color(0, hue)  # rainbow strobe
        self.buf.data[:] = [c[0], c[1], c[2]]
      else:
        self.buf.clear()
      return self.buf.get_frame()

    # Breakdown: dim pulsing
    if audio.breakdown:
      b = (math.sin(audio._time * 6) + 1) * 0.1
      c = pal_color(pal_idx % NUM_PALETTES, self._hue)
      self.buf.data[:] = [int(c[0] * b), int(c[1] * b), int(c[2] * b)]
      return self.buf.get_frame()

    self._energy *= decay
    c = pal_color(pal_idx % NUM_PALETTES, self._hue)
    e = self._energy
    self.buf.data[:] = [int(c[0] * e), int(c[1] * e), int(c[2] * e)]

    return self.buf.get_frame()

  def _calc_dt_ms(self, t):
    if self._last_t is None:
      self._last_t = t
      return 16.67
    dt = (t - self._last_t) * 1000.0
    self._last_t = t
    return max(0.0, dt)


# ═══════════════════════════════════════════════════════════════════
#  BASS FIRE (BassReactiveFire)
# ═══════════════════════════════════════════════════════════════════

class BassFire(Effect):
  """Beat-tracked fire: bass drives flames, beats trigger flares,
  phrases trigger rainbow explosions. Full fire physics with
  heat convection, embers, and audio-driven fuel injection."""

  CATEGORY = "sound"
  DISPLAY_NAME = "SR Bass Fire"
  DESCRIPTION = "Audio-reactive fire with beat flares and phrase explosions"
  PALETTE_SUPPORT = True

  PARAMS = [
    _Param("Gain", "gain", 0.5, 8.0, 0.5, 3.0),
    _Param("Base Spark", "base_spark", 0.1, 0.8, 0.05, 0.3),
    _Param("Heat Cap", "heat_cap", 0.3, 1.0, 0.05, 0.75),
  ]
  _SCALAR_PARAMS = {"gain": 3.0, "base_spark": 0.3, "heat_cap": 0.75, "palette": 4}

  # Fire physics constants (from Fireplace)
  _SPARK_ZONE = 35
  _SPARK_MIN = 0.55
  _COOL_BASE = 0.012
  _COOL_HEIGHT = 0.045
  _COOL_NOISE = 0.50
  _DIFFUSE_CENTER = 0.74
  _DIFFUSE_SIDE = 0.13
  _TURB_X_SCALE = 1.8
  _TURB_Y_BIAS = 2.0
  _TURB_Y_RANGE = 3.0
  _BUOYANCY = 2.5
  _NOISE_OCTAVES = 2
  _EMBER_RATE = 0.20
  _EMBER_BURST = 6
  _EMBER_SPREAD = 0.65
  _MAX_EMBERS = 150
  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    self._audio_adapter = AudioCompatAdapter()
    self.buf = LEDBuffer(width, height)
    self._heat = np.zeros((width, height), dtype=np.float64)
    self._embers = []
    self._time = 0.0
    self._rainbow_timer = 0.0
    self._flash_bright = 0.0
    self._flash_hue = 0.0
    # Dynamic fire params that audio modulates
    self._spark_prob = self.params.get("base_spark", 0.3)
    self._spark_max = 1.0
    self._flare_prob = 0.025
    self._last_t = None

    # Pre-compute coordinate grids (reused every frame)
    self._x_g = np.arange(width, dtype=np.float64)[:, np.newaxis] * np.ones(height)
    self._y_g = np.ones(width)[:, np.newaxis] * np.arange(height, dtype=np.float64)
    # Half-resolution grids for noise (2x faster, visually identical for fire)
    half_h = (height + 1) // 2  # round up so repeat(2) covers full height
    self._x_g_half = np.arange(width, dtype=np.float64)[:, np.newaxis] * np.ones(half_h)
    self._y_g_half = np.ones(width)[:, np.newaxis] * (np.arange(half_h, dtype=np.float64) * 2)
    # Noise cache — update at 30 Hz (every other frame)
    self._noise_tick = 0
    self._cached_nx = np.zeros((width, height), dtype=np.float64)
    self._cached_ny = np.zeros((width, height), dtype=np.float64)
    self._cached_cn = np.zeros((width, height), dtype=np.float64)

    # Warm up the spark zone
    self._heat[:, height - self._SPARK_ZONE:] = np.random.uniform(
      0.4, 0.9, (width, self._SPARK_ZONE))

  def render(self, t, state):
    dt_ms = self._calc_dt_ms(t)
    raw = state._audio_lock_free
    audio = self._audio_adapter.adapt(raw, t)

    self._time += dt_ms * 0.001
    dt = min(dt_ms / 16.67, 2.5)
    dt_s = dt_ms * 0.001
    sim_t = self._time
    cols = self.width
    rows = self.height
    center = (cols - 1) / 2.0
    gain = self.params.get("gain", 3.0)
    base_spark = self.params.get("base_spark", 0.3)

    # Audio-driven fire parameters
    bass = clampf(base_spark + audio.bass * gain * 2 +
                  audio.bands[0] * gain * 0.5)
    self._spark_prob = bass
    self._spark_max = clampf(bass * 1.5)
    self._flare_prob = 0.01

    # Beat: fire surges
    if audio.beat:
      self._flare_prob = 0.6
      self._spark_max = 1.0
      self._heat[:, (rows - 8):rows] = np.minimum(
        1.0, self._heat[:, (rows - 8):rows] + audio.beat_energy * 0.4)

    # Downbeat: color-shifted flare
    if audio.is_downbeat:
      self._flare_prob = 0.95
      self._flash_bright = 0.6
      self._flash_hue = (self._flash_hue + 0.25) % 1.0
      self._heat[:, (rows - 15):rows] = np.minimum(
        1.0, self._heat[:, (rows - 15):rows] + 0.6)

    # Phrase: rainbow explosion
    if audio.is_phrase:
      self._rainbow_timer = 1.5
      third = rows // 3
      self._heat[:, (rows - third):rows] = 1.0
      for _ in range(40):
        if len(self._embers) < self._MAX_EMBERS:
          ex = random.uniform(0, cols - 1)
          ey = rows - random.randint(1, 20)
          self._embers.append(_Ember(ex, ey, 1.0, 0.8))

    # Drop: everything goes white-hot
    if audio.drop:
      self._rainbow_timer = 2.5
      self._heat = np.minimum(1.0, self._heat + 0.8)

    # ── Fire physics (from Fireplace) ─────────────────────────────
    fuel = clampf(bass)
    fuel_sq = fuel * fuel
    sz = max(3, int(self._SPARK_ZONE * (0.2 + fuel * 0.8)))

    # Sparks (small loop — 10 cols × sz rows, stochastic so keep scalar)
    for x in range(cols):
      cw = 1.0 - abs(x - center) / (center + 0.5) * 0.25
      hotspot = (cyl_noise(x * 2, sim_t * 3, sim_t * 0.5, 1.0, 1.0) + 1) * 0.5
      for yo in range(sz):
        y = rows - 1 - yo
        if y < 0:
          break
        depth_factor = 1.0 - yo / sz * 0.6
        prob = self._spark_prob * fuel * cw * depth_factor * (0.5 + hotspot * 0.7)
        if random.random() < prob:
          intensity = random.uniform(self._SPARK_MIN, self._spark_max) * (0.3 + fuel * 0.7)
          self._heat[x, y] = min(1.0, self._heat[x, y] + intensity * cw * dt)

    # Flares (stochastic, small)
    if random.random() < self._flare_prob * fuel_sq:
      fc = random.randint(0, cols - 1)
      flare_height = int(random.randint(15, min(rows // 2, 60)) * (0.3 + fuel * 0.7))
      for dx in range(-2, 3):
        fx = (fc + dx) % cols
        for yo in range(flare_height):
          y = rows - 1 - yo
          if y >= 0:
            fade = 1.0 - yo / flare_height
            self._heat[fx, y] = min(1.0, self._heat[fx, y] + 0.6 * fade * dt)

    # Convection — VECTORIZED (the main bottleneck)
    x_g = self._x_g
    y_g = self._y_g
    turb_x = self._TURB_X_SCALE * (0.5 + fuel * 0.5)
    ty_bias = self._TURB_Y_BIAS * (0.3 + fuel * 0.7)
    ty_range = self._TURB_Y_RANGE * (0.3 + fuel * 0.7)
    buoy = self._BUOYANCY * (0.2 + fuel * 0.8)
    octs = max(1, int(self._NOISE_OCTAVES))

    self._noise_tick += 1
    if self._noise_tick & 1 == 0:
      self._cached_nx = np.repeat(cyl_fbm_xy(self._x_g_half, self._y_g_half, sim_t * 8.0, octs, 0.5, 0.015, cols), 2, axis=1)[:, :rows]
      self._cached_ny = np.repeat(cyl_fbm_xy(self._x_g_half + 5, self._y_g_half, sim_t * 7.0, octs, 0.5, 0.015, cols), 2, axis=1)[:, :rows]
    nx = self._cached_nx
    ny = self._cached_ny

    sx = np.clip(x_g + nx * turb_x, 0, cols - 1.001)
    sy = np.clip(y_g + ty_bias + self._heat * buoy + np.abs(ny) * ty_range,
                 0, rows - 1.001)

    ix = np.clip(sx.astype(np.int32), 0, cols - 2)
    iy = np.clip(sy.astype(np.int32), 0, rows - 2)
    ffx = sx - ix
    ffy = sy - iy
    ix2 = np.minimum(ix + 1, cols - 1)
    iy2 = np.minimum(iy + 1, rows - 1)

    new_heat = (
      self._heat[ix, iy] * (1 - ffx) * (1 - ffy)
      + self._heat[ix2, iy] * ffx * (1 - ffy)
      + self._heat[ix, iy2] * (1 - ffx) * ffy
      + self._heat[ix2, iy2] * ffx * ffy
    )

    # Lateral diffusion — VECTORIZED
    dc = self._DIFFUSE_CENTER
    ds = self._DIFFUSE_SIDE
    new_heat = (new_heat * dc
                + np.roll(new_heat, 1, axis=0) * ds
                + np.roll(new_heat, -1, axis=0) * ds)

    # Cooling — VECTORIZED
    fuel_cool = 1.5 - fuel
    cb = self._COOL_BASE * fuel_cool
    ch = self._COOL_HEIGHT * fuel_cool
    cn_amt = self._COOL_NOISE

    hf = (rows - 1 - y_g) / float(rows)
    if self._noise_tick & 1 == 0:
      self._cached_cn = np.repeat((cyl_noise_xy(self._x_g_half, self._y_g_half, sim_t * 10.0, 0.8, 0.03, cols) + 1) * 0.5, 2, axis=1)[:, :rows]
    cn = self._cached_cn
    rng = np.random.random((cols, rows))
    top_frac = np.clip((hf - 0.5) * 2, 0, 1)
    cool = np.where(
      hf < 0.5,
      (cb * 0.3 + cn * cn_amt * 0.15 + rng * 0.003) * dt,
      (cb + top_frac ** 2 * ch + cn * cn_amt * top_frac + rng * 0.005) * dt
    )
    new_heat = np.maximum(0.0, new_heat - cool)
    self._heat = new_heat

    # Ember bed (10×12 = 120 ops — keep scalar with cyl_noise)
    for x in range(cols):
      for yo in range(12):
        y = rows - 1 - yo
        if y < 0:
          break
        glow = 0.30 - yo * 0.02
        shimmer = (cyl_noise(x * 2 + 500, yo * 0.5, sim_t * 1.5, 1.0, 1.0) + 1) * 0.05
        self._heat[x, y] = max(self._heat[x, y], glow + shimmer)

    # Ember particles (keep scalar — sparse particle loop)
    if self._EMBER_RATE > 0 and fuel > 0.1:
      real_rate = self._EMBER_RATE * (0.1 + fuel_sq * 0.9)
      for x in range(cols):
        for y in range(0, rows, 3):
          h = self._heat[x, y]
          if 0.15 < h < 0.65 and random.random() < real_rate * h * dt_s * 1.5:
            burst = max(1, int(random.gauss(self._EMBER_BURST, self._EMBER_BURST * 0.5)))
            for _ in range(burst):
              if len(self._embers) < self._MAX_EMBERS:
                self._embers.append(_Ember(x, y, h, self._EMBER_SPREAD))

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

    # ── Render heat — VECTORIZED, palette-driven ─────────────────
    heat_cap = self.params.get("heat_cap", 0.75)
    capped_heat = np.minimum(self._heat, heat_cap)

    pal_idx = _get_pal_idx(self.params, default=4)
    pal_rgb = pal_color_grid(pal_idx, capped_heat)  # (cols, rows, 3) uint8
    brightness = np.sqrt(np.maximum(0, capped_heat))[..., np.newaxis]
    self.buf.data = (pal_rgb.astype(np.float64) * brightness).astype(np.uint8)

    # ── Render embers (keep scalar — sparse particle loop) ────────
    for e in self._embers:
      ecol = int(round(e.x))
      erow = int(round(e.y))
      if 0 <= ecol < cols and 0 <= erow < rows:
        af = max(0.0, e.life / e.max_life)
        fl = 0.65 + 0.35 * math.sin(e.flicker_phase)
        b = e.brightness * af * fl * heat_cap
        ec = pal_color(pal_idx, min(1.0, af * 0.45 + 0.15))
        self.buf.add_led(ecol, erow,
                         int(ec[0] * b * 1.4),
                         int(ec[1] * b * 1.1),
                         int(ec[2] * b * 0.4))

    # ── Rainbow explosion overlay — VECTORIZED ────────────────────
    self._rainbow_timer = max(0, self._rainbow_timer - dt_s)
    if self._rainbow_timer > 0:
      intensity = min(1.0, self._rainbow_timer / 0.5)
      rt = self._rainbow_timer * 5
      mask = self._heat > 0.1
      hue = ((y_g / rows + x_g / cols * 0.3 + rt) % 1.0)
      rc_rgb = pal_color_grid(0, hue)  # (cols, rows, 3) uint8
      heat_bright = np.clip(self._heat * intensity, 0, 1)
      rc_modulated = (rc_rgb.astype(np.float32) * heat_bright[..., np.newaxis]).clip(0, 255).astype(np.uint8)
      blend = intensity * 0.7
      blended = (
        self.buf.data.astype(np.float32) * (1 - blend)
        + rc_modulated.astype(np.float32) * blend
      )
      self.buf.data = np.where(
        mask[..., np.newaxis],
        blended.clip(0, 255).astype(np.uint8),
        self.buf.data
      )

    # ── Downbeat color flash overlay (10×5 = 50 ops — keep scalar) ──
    self._flash_bright *= 0.9
    if self._flash_bright > 0.05:
      fc = hsv2rgb(int(self._flash_hue * 255), 180, int(self._flash_bright * 255))
      for x in range(cols):
        for yo in range(5):
          y = rows - 1 - yo
          if y >= 0:
            self.buf.add_led(x, y, fc[0], fc[1], fc[2])

    return self.buf.get_frame()

  def _calc_dt_ms(self, t):
    if self._last_t is None:
      self._last_t = t
      return 16.67
    dt = (t - self._last_t) * 1000.0
    self._last_t = t
    return max(0.0, dt)


# ═══════════════════════════════════════════════════════════════════
#  SOUND RIPPLES
# ═══════════════════════════════════════════════════════════════════

class SoundRipples(Effect):
  """Beat-tracked ripples — kicks spawn from bottom, snares from center,
  hi-hats from top. Phrase beats spawn full-matrix rainbow rings.
  Uses persistent buffer (fade) for trails."""

  CATEGORY = "sound"
  DISPLAY_NAME = "SR Sound Ripples"
  DESCRIPTION = "Beat-tracked expanding ripples with frequency-mapped origins"
  PALETTE_SUPPORT = True

  PARAMS = [
    _Param("Gain", "gain", 0.2, 5.0, 0.1, 2.0),
    _Param("Speed", "speed", 0.3, 4.0, 0.1, 1.5),
    _Param("Decay", "decay", 0.85, 0.99, 0.01, 0.93),
    _Param("Sensitivity", "sensitivity", 0.02, 0.5, 0.02, 0.15),
  ]
  _SCALAR_PARAMS = {"gain": 2.0, "speed": 1.5, "decay": 0.93, "sensitivity": 0.15}
  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    self._audio_adapter = AudioCompatAdapter()
    self.buf = LEDBuffer(width, height)
    self._ripples = []
    self._bass_prev = 0.0
    self._mids_prev = 0.0
    self._highs_prev = 0.0
    self._last_t = None

    # Pre-compute coordinate grids for distance calculations
    self._x_g = np.arange(width, dtype=np.float64)[:, np.newaxis]   # (cols, 1)
    self._y_g = np.arange(height, dtype=np.float64)[np.newaxis, :]  # (1, rows)

  def render(self, t, state):
    dt_ms = self._calc_dt_ms(t)
    raw = state._audio_lock_free
    audio = self._audio_adapter.adapt(raw, t)

    dt = dt_ms * 0.001
    cols = self.width
    rows = self.height
    gain = self.params.get("gain", 2.0)
    speed = self.params.get("speed", 1.5)
    decay = self.params.get("decay", 0.93)
    sens = self.params.get("sensitivity", 0.15)
    pal_idx = _get_pal_idx(self.params)

    # Fade existing instead of clear — trails look better
    self.buf.fade(0.85)

    # Kick (bass onset) -> ripple from bottom
    bass_delta = audio.bass - self._bass_prev
    if bass_delta > sens or audio.beat:
      intensity = clampf(max(bass_delta, audio.beat_energy * 0.3) * 2)
      self._ripples.append([cols / 2.0, rows * 0.85, 0.0,
        random.uniform(0, 0.15), intensity, 5.0])
    self._bass_prev = audio.bass

    # Snare/clap (mids onset) -> ripple from center
    mids_delta = audio.mids - self._mids_prev
    if mids_delta > sens * 1.5:
      self._ripples.append([random.uniform(1, cols - 2), rows * 0.5, 0.0,
        random.uniform(0.2, 0.5), clampf(mids_delta * 3), 3.0])
    self._mids_prev = audio.mids

    # Hi-hat (highs onset) -> small ripple from top
    highs_delta = audio.highs - self._highs_prev
    if highs_delta > sens * 0.8:
      self._ripples.append([random.uniform(0, cols - 1), rows * 0.15, 0.0,
        random.uniform(0.5, 0.8), clampf(highs_delta * 2), 2.0])
    self._highs_prev = audio.highs

    # Phrase beat -> massive rainbow ring from center
    if audio.is_phrase:
      self._ripples.append([cols / 2.0, rows / 2.0, 0.0,
        -1.0, 1.5, 8.0])  # -1 hue = rainbow mode

    # Downbeat -> bright ring
    elif audio.is_downbeat:
      self._ripples.append([cols / 2.0, rows * 0.7, 0.0,
        random.random(), 1.0, 6.0])

    x_g = self._x_g  # (cols, 1)
    y_g = self._y_g  # (1, rows)

    alive = []
    for r in self._ripples:
      # r = [cx, cy, radius, hue, intensity, ring_width]
      r[2] += speed * 80 * dt
      r[4] *= decay ** (dt * 60)
      if r[4] > 0.015 and r[2] < rows * 1.5:
        alive.append(r)
        rw = r[5]

        # Vectorized distance computation
        dx = x_g - r[0]  # (cols, 1)
        dx = np.where(np.abs(dx) > cols / 2, dx - np.sign(dx) * cols, dx)
        dx = dx * (rows / cols)  # aspect correction
        dy = y_g - r[1]  # (1, rows)
        dist = np.sqrt(dx ** 2 + dy ** 2)  # (cols, rows)

        ring = np.abs(dist - r[2])
        mask = ring < rw
        if not np.any(mask):
          continue

        b = (1.0 - ring / rw) * r[4] * gain  # (cols, rows)

        if r[3] < 0:  # rainbow mode
          hue = (dist * 0.02 + r[2] * 0.01) % 1.0
          c_grid = pal_color_grid(0, hue)
        else:
          hue_fill = np.full((cols, rows), r[3])
          c_grid = pal_color_grid(pal_idx % NUM_PALETTES, hue_fill)

        add_rgb = (c_grid.astype(np.float32) * np.clip(b, 0, None)[..., np.newaxis]).clip(0, 255)
        self.buf.data[mask] = np.clip(
          self.buf.data[mask].astype(np.float32) + add_rgb[mask],
          0, 255
        ).astype(np.uint8)

    self._ripples = alive

    return self.buf.get_frame()

  def _calc_dt_ms(self, t):
    if self._last_t is None:
      self._last_t = t
      return 16.67
    dt = (t - self._last_t) * 1000.0
    self._last_t = t
    return max(0.0, dt)


# ═══════════════════════════════════════════════════════════════════
#  SPECTROGRAM
# ═══════════════════════════════════════════════════════════════════

class Spectrogram(Effect):
  """Scrolling spectrogram — each row is a snapshot of the 10-band FFT,
  scrolling upward over time."""

  CATEGORY = "sound"
  DISPLAY_NAME = "SR Spectrogram"
  DESCRIPTION = "Scrolling FFT spectrogram with drop flash lines"
  PALETTE_SUPPORT = True

  PARAMS = [
    _Param("Gain", "gain", 0.5, 8.0, 0.5, 2.0),
    _Param("Scroll", "scroll", 0.3, 3.0, 0.1, 1.0),
  ]
  _SCALAR_PARAMS = {"gain": 2.0, "scroll": 1.0}
  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    self._audio_adapter = AudioCompatAdapter()
    self.buf = LEDBuffer(width, height)
    self._grid = [[0.0] * width for _ in range(height)]
    self._accum = 0.0
    self._drop_flash = 0.0
    self._last_t = None

  def render(self, t, state):
    dt_ms = self._calc_dt_ms(t)
    raw = state._audio_lock_free
    audio = self._audio_adapter.adapt(raw, t)

    cols = self.width
    rows = self.height
    gain_param = self.params.get("gain", 2.0)
    scroll = self.params.get("scroll", 1.0)
    pal_idx = _get_pal_idx(self.params)

    if audio.drop:
      self._drop_flash = 1.0
    self._drop_flash *= 0.95

    adjusted_gain = gain_param * (1 + audio.buildup * 0.5)
    self._accum += dt_ms * scroll * 0.06
    while self._accum >= 1.0:
      self._accum -= 1.0
      self._grid.pop(0)
      row = [0.0] * cols
      if self._drop_flash > 0.3:
        row = [1.0] * cols  # white flash line on drop
      else:
        from ...audio.adapter import AudioCompatAdapter as _AC
        col_bands = _AC.resample_bands(audio.bands, cols)
        for i in range(cols):
          row[i] = clampf(col_bands[i] * adjusted_gain)
      self._grid.append(row)

    # Vectorized render: convert grid to numpy, palette lookup, brightness
    grid_arr = np.array(self._grid, dtype=np.float64)  # (rows, cols)
    grid_t = grid_arr.T  # (cols, rows) — matches buf.data shape

    if self._drop_flash > 0.1:
      flash_mask = grid_t > 0.8
      # Normal palette lookup
      rgb = pal_color_grid(pal_idx % NUM_PALETTES, grid_t)
      # Rainbow for high values during drop
      y_frac = np.broadcast_to(
        np.arange(rows, dtype=np.float64)[np.newaxis, :] / rows,
        (cols, rows)
      )
      flash_rgb = pal_color_grid(0, y_frac)
      rgb = np.where(flash_mask[..., np.newaxis], flash_rgb, rgb)
    else:
      rgb = pal_color_grid(pal_idx % NUM_PALETTES, grid_t)

    # brightness = value
    self.buf.data = (
      rgb.astype(np.float32) * grid_t[..., np.newaxis]
    ).clip(0, 255).astype(np.uint8)

    return self.buf.get_frame()

  def _calc_dt_ms(self, t):
    if self._last_t is None:
      self._last_t = t
      return 16.67
    dt = (t - self._last_t) * 1000.0
    self._last_t = t
    return max(0.0, dt)


# ═══════════════════════════════════════════════════════════════════
#  SOUND WORM
# ═══════════════════════════════════════════════════════════════════

class SoundWorm(Effect):
  """Audio-driven sine worm with volume amplitude modulation.
  Uses persistent buffer (fade) for trails. On drop, splits into
  multiple rainbow worms."""

  CATEGORY = "sound"
  DISPLAY_NAME = "SR Sound Worm"
  DESCRIPTION = "Audio-driven sine worm with drop split and rainbow trails"
  PALETTE_SUPPORT = True

  PARAMS = [
    _Param("Gain", "gain", 0.2, 5.0, 0.1, 1.0),
    _Param("Speed", "speed", 0.3, 3.0, 0.1, 1.0),
    _Param("Width", "worm_width", 1, 5, 1, 2),
  ]
  _SCALAR_PARAMS = {"gain": 1.0, "speed": 1.0, "worm_width": 2}
  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    self._audio_adapter = AudioCompatAdapter()
    self.buf = LEDBuffer(width, height)
    self._t = 0.0
    self._drop_split = 0.0
    self._last_t = None

  def render(self, t, state):
    dt_ms = self._calc_dt_ms(t)
    raw = state._audio_lock_free
    audio = self._audio_adapter.adapt(raw, t)

    cols = self.width
    rows = self.height
    gain = self.params.get("gain", 1.0)
    speed = self.params.get("speed", 1.0)
    w = int(self.params.get("worm_width", 2))
    pal_idx = _get_pal_idx(self.params)

    self._t += dt_ms * 0.001 * speed

    # Fade instead of clear for trails
    self.buf.fade(0.8)

    vol = audio.volume
    buildup = audio.buildup
    if audio.drop:
      self._drop_split = 2.0
    self._drop_split *= 0.97

    y_arr = np.arange(rows, dtype=np.float64)
    y_idx = np.arange(rows)

    # During drop: split into multiple worms with rainbow
    num_worms = 1 + int(self._drop_split * 2)
    for worm in range(num_worms):
      phase_offset = worm * 6.28 / max(1, num_worms)
      amp = (vol + buildup * 0.5 + self._drop_split * 0.3) * gain * (cols / 2)
      wave_x = cols / 2 + np.sin(y_arr * 0.03 + self._t * 3 + phase_offset) * amp

      # Palette lookup (vectorized per row)
      if self._drop_split > 0.5:
        hue_arr = (y_arr / rows + worm / num_worms + self._t * 0.3) % 1.0
        c_arr = pal_color_grid(0, hue_arr)  # (rows, 3)
      else:
        hue_arr = (y_arr / rows + self._t * 0.1) % 1.0
        c_arr = pal_color_grid(pal_idx % NUM_PALETTES, hue_arr)  # (rows, 3)

      for dx in range(-w, w + 1):
        fade_val = 1.0 - abs(dx) / (w + 1)
        px_arr = (np.round(wave_x) + dx).astype(np.int32) % cols  # (rows,)
        add_vals = (c_arr.astype(np.float32) * fade_val).clip(0, 255).astype(np.int16)
        # Fancy indexing: px_arr[y] and y_idx[y] both shape (rows,)
        self.buf.data[px_arr, y_idx] = np.clip(
          self.buf.data[px_arr, y_idx].astype(np.int16) + add_vals,
          0, 255
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
#  PARTICLE BURST
# ═══════════════════════════════════════════════════════════════════

class ParticleBurst(Effect):
  """Beat-triggered particles. DROP = FIREWORKS: multiple simultaneous
  rainbow explosions from different launch points with trailing sparks.
  Uses persistent buffer (fade) for trails."""

  CATEGORY = "sound"
  DISPLAY_NAME = "SR Particle Burst"
  DESCRIPTION = "Beat-triggered particle explosions with drop fireworks"
  PALETTE_SUPPORT = True

  PARAMS = [
    _Param("Gain", "gain", 0.2, 5.0, 0.1, 1.0),
    _Param("Gravity", "gravity", 0.0, 2.0, 0.1, 0.5),
    _Param("Speed", "speed", 0.3, 3.0, 0.1, 1.0),
    _Param("Count", "count", 5, 60, 5, 30),
  ]
  _SCALAR_PARAMS = {"gain": 1.0, "gravity": 0.5, "speed": 1.0, "count": 30}
  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    self._audio_adapter = AudioCompatAdapter()
    self.buf = LEDBuffer(width, height)
    self._particles = []
    self._last_t = None

  def _spawn_burst(self, cx, cy, count, hue, speed_mult=1.0, rainbow=False):
    speed_param = self.params.get("speed", 1.0)
    for _ in range(count):
      angle = random.uniform(0, 6.28)
      spd = random.uniform(15, 60) * speed_param * speed_mult
      h = random.random() if rainbow else hue + random.uniform(-0.1, 0.1)
      self._particles.append([cx, cy,
        math.cos(angle) * spd, math.sin(angle) * spd * 2.5,
        h, 1.0,
        1 if random.random() < 0.3 else 0])  # [6]=trail flag

  def render(self, t, state):
    dt_ms = self._calc_dt_ms(t)
    raw = state._audio_lock_free
    audio = self._audio_adapter.adapt(raw, t)

    dt = dt_ms * 0.001
    cols = self.width
    rows = self.height
    gravity = self.params.get("gravity", 0.5)
    count = int(self.params.get("count", 30))
    pal_idx = _get_pal_idx(self.params)

    # Fade instead of clear — trails persist
    self.buf.fade(0.82)

    gain = self.params.get("gain", 1.0)
    # Normal beat: single burst
    if audio.beat and not audio.drop:
      cx = random.uniform(1, cols - 2)
      cy = random.uniform(rows * 0.3, rows * 0.7)
      adjusted_count = int(count * gain * (1 + audio.buildup))
      self._spawn_burst(cx, cy, adjusted_count, random.random())

    # DROP: FIREWORKS — 5-8 simultaneous rainbow explosions
    if audio.drop:
      num_fireworks = random.randint(5, 8)
      for _ in range(num_fireworks):
        cx = random.uniform(0, cols)
        cy = random.uniform(rows * 0.15, rows * 0.75)
        adjusted_count = int(count * 2.5)
        self._spawn_burst(cx, cy, adjusted_count, 0, 1.5, rainbow=True)

    # Breakdown: occasional slow sparkle (anticipation)
    if audio.breakdown and random.random() < 0.05:
      cx = random.uniform(0, cols)
      cy = random.uniform(0, rows)
      self._spawn_burst(cx, cy, 3, random.random(), 0.3)

    alive = []
    for p in self._particles:
      p[0] += p[2] * dt
      p[1] += p[3] * dt
      p[2] *= 0.99  # air drag
      p[3] += gravity * 60 * dt
      p[5] -= dt * 0.4
      # Trail particles: spawn child sparks
      if len(p) > 6 and p[6] and p[5] > 0.5 and random.random() < 0.3:
        self._particles.append([p[0], p[1],
          p[2] * 0.1 + random.uniform(-3, 3),
          p[3] * 0.1 + random.uniform(-3, 3),
          p[4] + random.uniform(-0.05, 0.05), 0.4, 0])
      if p[5] > 0 and -5 < p[1] < rows + 5:
        alive.append(p)
        px = int(round(p[0])) % cols
        py = int(round(p[1]))
        if 0 <= py < rows:
          c = pal_color(pal_idx % NUM_PALETTES, p[4] % 1.0)
          b = p[5]
          self.buf.add_led(px, py, int(c[0] * b), int(c[1] * b), int(c[2] * b))
    self._particles = alive[:500]  # cap for performance

    return self.buf.get_frame()

  def _calc_dt_ms(self, t):
    if self._last_t is None:
      self._last_t = t
      return 16.67
    dt = (t - self._last_t) * 1000.0
    self._last_t = t
    return max(0.0, dt)


# ═══════════════════════════════════════════════════════════════════
#  SOUND PLASMA
# ═══════════════════════════════════════════════════════════════════

class SoundPlasma(Effect):
  """Audio-reactive plasma — volume drives brightness, speed, and scale.
  Drop boosts speed dramatically; breakdown dims and slows."""

  CATEGORY = "sound"
  DISPLAY_NAME = "SR Sound Plasma"
  DESCRIPTION = "Audio-reactive plasma with volume-driven speed and drop boost"
  PALETTE_SUPPORT = True

  PARAMS = [
    _Param("Gain", "gain", 0.2, 5.0, 0.1, 1.5),
    _Param("Base Speed", "base_speed", 0.1, 3.0, 0.1, 0.5),
  ]
  _SCALAR_PARAMS = {"gain": 1.5, "base_speed": 0.5}
  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    self._audio_adapter = AudioCompatAdapter()
    self.buf = LEDBuffer(width, height)
    self._t = 0.0
    self._drop_boost = 0.0
    self._last_t = None

  def render(self, t, state):
    dt_ms = self._calc_dt_ms(t)
    raw = state._audio_lock_free
    audio = self._audio_adapter.adapt(raw, t)

    cols = self.width
    rows = self.height
    gain = self.params.get("gain", 1.5)
    base_speed = self.params.get("base_speed", 0.5)
    pal_idx = _get_pal_idx(self.params)

    vol = audio.volume * gain
    buildup = audio.buildup
    if audio.drop:
      self._drop_boost = 3.0  # speed explosion on drop
    self._drop_boost *= 0.97

    speed = base_speed + vol * 2 + buildup * 2 + self._drop_boost
    self._t += dt_ms * 0.001 * speed
    tt = self._t
    scale = 1.0 + vol + self._drop_boost * 0.5

    # During breakdown, go dark and slow
    local_vol = vol
    if audio.breakdown:
      scale *= 0.3
      local_vol *= 0.2

    # Vectorized plasma computation
    x_g = np.arange(cols, dtype=np.float64)[:, np.newaxis]  # (cols, 1)
    y_g = np.arange(rows, dtype=np.float64)[np.newaxis, :]  # (1, rows)
    ax = x_g / cols * 6.2832  # (cols, 1)

    v = (np.sin(ax * 2 * scale + tt * 1.5)
         + np.sin(y_g * scale * 0.035 + tt * 0.8)
         + np.sin(ax * 3 + y_g * 0.02 * scale + tt * 1.2)) / 3.0

    bright = np.clip((v + 1) * 0.5 * (0.4 + local_vol * 0.8), 0, 1)
    hue = (v + 1) * 0.5

    if self._drop_boost > 0.5:
      hue = (hue + tt * 0.2) % 1.0
      rgb = pal_color_grid(0, hue)
    else:
      rgb = pal_color_grid(pal_idx % NUM_PALETTES, hue)

    self.buf.data = (
      rgb.astype(np.float32) * bright[..., np.newaxis]
    ).clip(0, 255).astype(np.uint8)

    return self.buf.get_frame()

  def _calc_dt_ms(self, t):
    if self._last_t is None:
      self._last_t = t
      return 16.67
    dt = (t - self._last_t) * 1000.0
    self._last_t = t
    return max(0.0, dt)


# ═══════════════════════════════════════════════════════════════════
#  STROBE CHAOS
# ═══════════════════════════════════════════════════════════════════

class StrobeChaos(Effect):
  """Beat-triggered segment strobes with drop-triggered full-matrix
  rapid rainbow strobe and breakdown flicker."""

  CATEGORY = "sound"
  DISPLAY_NAME = "SR Strobe Chaos"
  DESCRIPTION = "Beat-triggered segment strobe with drop rainbow burst"
  PALETTE_SUPPORT = True

  PARAMS = [
    _Param("Gain", "gain", 0.2, 5.0, 0.1, 1.0),
    _Param("Intensity", "intensity", 0.1, 1.0, 0.05, 0.8),
    _Param("Segments", "segments", 1, 10, 1, 4),
  ]
  _SCALAR_PARAMS = {"gain": 1.0, "intensity": 0.8, "segments": 4}
  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    self._audio_adapter = AudioCompatAdapter()
    self.buf = LEDBuffer(width, height)
    self._flash = 0.0
    self._hue = 0.0
    self._drop_strobe = 0.0
    self._last_t = None

  def render(self, t, state):
    dt_ms = self._calc_dt_ms(t)
    raw = state._audio_lock_free
    audio = self._audio_adapter.adapt(raw, t)

    dt = dt_ms * 0.001
    cols = self.width
    rows = self.height
    intensity = self.params.get("intensity", 0.8)
    segments = int(self.params.get("segments", 4))
    pal_idx = _get_pal_idx(self.params)

    self.buf.clear()

    if audio.drop:
      self._drop_strobe = 3.0
    self._drop_strobe = max(0, self._drop_strobe - dt)

    # DROP: full-matrix rapid rainbow strobe
    if self._drop_strobe > 0:
      frame = int(self._drop_strobe * 30)
      if frame % 2 == 0:
        hue = (frame * 0.07) % 1.0
        c = pal_color(0, hue)
        b = min(1.0, self._drop_strobe / 1.5)
        self.buf.data[:] = [int(c[0] * b), int(c[1] * b), int(c[2] * b)]
      return self.buf.get_frame()

    # Breakdown: dark with occasional dim flicker
    if audio.breakdown:
      if random.random() < 0.05:
        c = pal_color(pal_idx % NUM_PALETTES, random.random())
        self.buf.data[:] = [int(c[0] * 0.08), int(c[1] * 0.08), int(c[2] * 0.08)]
      return self.buf.get_frame()

    gain = self.params.get("gain", 1.0)
    if audio.beat:
      self._flash = min(1.0, intensity * gain * (1 + audio.buildup))
      self._hue = random.random()
    self._flash *= 0.88

    if self._flash > 0.05:
      seg_h = max(1, rows // segments)
      for seg in range(segments):
        if random.random() < 0.6:
          hue = (self._hue + seg * 0.15) % 1.0
          c = pal_color(pal_idx % NUM_PALETTES, hue)
          y0 = seg * seg_h
          y1 = min(y0 + seg_h, rows)
          b = self._flash * random.uniform(0.5, 1.0)
          self.buf.data[:, y0:y1] = [int(c[0] * b), int(c[1] * b), int(c[2] * b)]

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

SOUND_EFFECTS: dict[str, type[Effect]] = {
  "spectrum": Spectrum,
  "vu_meter": VUMeter,
  "beat_pulse": BeatPulse,
  "bass_fire": BassFire,
  "sound_ripples": SoundRipples,
  "spectrogram": Spectrogram,
  "sound_worm": SoundWorm,
  "particle_burst": ParticleBurst,
  "sound_plasma": SoundPlasma,
  "strobe_chaos": StrobeChaos,
}
