"""
Sound-reactive variants of ambient/classic effects.

Each variant forks the base effect's render loop and layers audio modulation
on top via AudioCompatAdapter. Separate classes (not subclasses) for clarity
and safety — editing these cannot break the originals.
"""

import math
import random

import numpy as np

from ..base import Effect
from ..engine.buffer import LEDBuffer
from ..engine.color import hsv2rgb
from ..engine.palettes import (
  pal_color, NUM_PALETTES, PALETTE_NAMES, pal_color_grid,
  FELDSTEIN_PALETTES, FELDSTEIN_PALETTE_NAMES, NUM_FELDSTEIN_PALETTES,
)
from ..engine.noise import cyl_noise, perlin_grid
from ...audio.adapter import AudioCompatAdapter


def _get_pal_idx(params, default=0, names=PALETTE_NAMES, count=NUM_PALETTES):
  val = params.get('palette', default)
  if isinstance(val, str):
    try:
      return names.index(val) % count
    except ValueError:
      return default % count
  return int(val) % count


class _Param:
  __slots__ = ('label', 'attr', 'lo', 'hi', 'step', 'default')
  def __init__(self, label, attr, lo, hi, step, default):
    self.label = label
    self.attr = attr
    self.lo = lo
    self.hi = hi
    self.step = step
    self.default = default


# ═══════════════════════════════════════════════════════════════════
#  SR FELDSTEIN
# ═══════════════════════════════════════════════════════════════════

class SRFeldstein(Effect):
  """Sound-reactive Feldstein: bass drives speed, beat shifts hue, buildup increases fade."""

  CATEGORY = "sound"
  DISPLAY_NAME = "SR Feldstein"
  DESCRIPTION = "Audio-reactive Feldstein OG — bass speed, beat hue pulse"
  PALETTE_SUPPORT = False  # uses FELDSTEIN_PALETTES

  PARAMS = [
    _Param("Gain", "gain", 0.2, 5.0, 0.1, 1.0),
    _Param("Speed", "speed", 0.04, 0.6, 0.02, 0.2),
    _Param("Fade/Dark", "fade", 10, 200, 5, 48),
  ]
  _SCALAR_PARAMS = {"gain": 1.0, "speed": 0.2, "fade": 48, "palette": 0}
  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    self._audio_adapter = AudioCompatAdapter()
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
    raw = state._audio_lock_free
    audio = self._audio_adapter.adapt(raw, t)

    gain = self.params.get("gain", 1.0)
    base_speed = self.params.get("speed", 0.2)
    base_fade = int(self.params.get("fade", 48))
    pi = _get_pal_idx(self.params, names=FELDSTEIN_PALETTE_NAMES, count=NUM_FELDSTEIN_PALETTES)

    # Audio modulation
    speed = base_speed * (1.0 + audio.bass * gain * 2.0)
    fade = int(max(10, min(200, base_fade - audio.buildup * gain * 30)))
    if audio.beat:
      self._hue = (self._hue + int(38 * gain)) % 256

    self._elapsed_ms += dt_ms
    time_val = int(self._elapsed_ms * speed) // 7 + self._zo
    SCALE = 180

    self._hue_accum += dt_ms
    if self._hue_accum >= 1000:
      self._hue = (self._hue + 1) % 256
      self._hue_accum -= 1000
    h = self._hue

    _pname, layers = FELDSTEIN_PALETTES[pi]
    h1_off, s1, _ = layers[0]
    h2_off, s2, _ = layers[1]
    h3_off, s3, _ = layers[2]

    self.buf.fade_by(fade)

    cols, rows = self.width, self.height
    x_idx = np.arange(cols, dtype=np.float64)
    y_idx = np.arange(rows, dtype=np.float64)
    xS = (x_idx * SCALE + self._xo)[:, np.newaxis] * np.ones(rows)
    yS = np.ones(cols)[:, np.newaxis] * (y_idx * SCALE + self._yo)

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
#  SR LAVA LAMP
# ═══════════════════════════════════════════════════════════════════

class SRLavaLamp(Effect):
  """Sound-reactive Lava Lamp: bass scales blob size, beat pulls blobs
  toward vertical center, drops temporarily add blobs (max 12)."""

  CATEGORY = "sound"
  DISPLAY_NAME = "SR Lava Lamp"
  DESCRIPTION = "Audio-reactive blobs — bass size, beat pulse, drop surge"
  PALETTE_SUPPORT = True

  PARAMS = [
    _Param("Gain", "gain", 0.2, 5.0, 0.1, 1.0),
    _Param("Speed", "speed", 0.05, 2.0, 0.05, 0.3),
    _Param("Blobs", "blobs", 2, 12, 1, 5),
    _Param("Size", "size", 0.3, 3.0, 0.1, 1.0),
  ]
  _SCALAR_PARAMS = {"gain": 1.0, "speed": 0.3, "blobs": 5, "size": 1.0, "palette": 0}
  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    self._audio_adapter = AudioCompatAdapter()
    self.buf = LEDBuffer(width, height)
    self._t = 0.0
    self._last_t = None
    self._blob_seeds = [
      (random.random() * 100, random.random() * 100) for _ in range(12)
    ]
    self._drop_timer = 0.0
    self._beat_pull = 0.0

  def render(self, t, state):
    dt_ms = self._calc_dt_ms(t)
    dt_s = dt_ms * 0.001
    raw = state._audio_lock_free
    audio = self._audio_adapter.adapt(raw, t)

    gain = self.params.get("gain", 1.0)
    speed = self.params.get("speed", 0.3)
    base_blobs = int(self.params.get("blobs", 5))
    base_size = self.params.get("size", 1.0)
    pal_idx = _get_pal_idx(self.params)

    # Audio modulations
    size = base_size * (1.0 + audio.bass * gain * 1.0)
    if audio.drop:
      self._drop_timer = 1.5
    self._drop_timer = max(0.0, self._drop_timer - dt_s)
    extra_blobs = int(4 * min(1.0, self._drop_timer / 1.5))
    num_blobs = min(12, base_blobs + extra_blobs)

    if audio.beat:
      self._beat_pull = 1.0
    self._beat_pull *= 0.9 ** (dt_s * 60)

    self._t += dt_s * speed
    tt = self._t
    cols = self.width
    rows = self.height

    x_g = np.arange(cols, dtype=np.float64)[:, np.newaxis]
    y_g = np.arange(rows, dtype=np.float64)[np.newaxis, :]

    size_x = max(1.0, size * 2)
    size_y = max(1.0, size * 25)

    val = np.zeros((cols, rows), dtype=np.float64)
    for bi in range(num_blobs):
      sx, sy = self._blob_seeds[bi]
      bx = (cols / 2) + math.sin(tt * 0.7 + sx * 6.28) * cols * 0.4
      by = (rows / 2) + math.sin(tt * 0.3 + sy * 6.28) * rows * 0.4
      # Beat pull: blend by toward rows/2
      by = by * (1.0 - self._beat_pull * 0.5 * gain) + (rows / 2) * (self._beat_pull * 0.5 * gain)
      dx = (x_g - bx) / size_x
      dy = (y_g - by) / size_y
      dist_sq = dx * dx + dy * dy
      val += 1.0 / (1.0 + dist_sq * 3)

    val = np.clip(val, 0.0, 1.0)
    hue = val * 0.8 + 0.1
    rgb = pal_color_grid(pal_idx, hue)
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
#  SR MATRIX RAIN
# ═══════════════════════════════════════════════════════════════════

class SRMatrixRain(Effect):
  """Sound-reactive Matrix Rain: bass multiplies drop speed, beat spikes
  spawn density, buildup lengthens trails."""

  CATEGORY = "sound"
  DISPLAY_NAME = "SR Matrix Rain"
  DESCRIPTION = "Audio-reactive digital rain — bass speed, beat burst, buildup trails"
  PALETTE_SUPPORT = True

  PARAMS = [
    _Param("Gain", "gain", 0.2, 5.0, 0.1, 1.0),
    _Param("Speed", "speed", 0.2, 4.0, 0.1, 1.0),
    _Param("Density", "density", 0.1, 1.0, 0.05, 0.4),
    _Param("Trail", "trail", 5, 60, 1, 25),
  ]
  _SCALAR_PARAMS = {"gain": 1.0, "speed": 1.0, "density": 0.4, "trail": 25, "palette": 3}

  _MAX_DROPS = 200

  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    if "palette" not in self.params:
      self.params["palette"] = 3
    self._audio_adapter = AudioCompatAdapter()
    self.buf = LEDBuffer(width, height)
    self._last_t = None

    cap = self._MAX_DROPS
    self._drop_x = np.zeros(cap, dtype=np.int32)
    self._drop_y = np.zeros(cap, dtype=np.float64)
    self._drop_speed = np.zeros(cap, dtype=np.float64)
    self._drop_bright = np.zeros(cap, dtype=np.float64)
    self._active_mask = np.zeros(cap, dtype=np.bool_)

  def render(self, t, state):
    dt_ms = self._calc_dt_ms(t)
    dt = dt_ms * 0.001
    raw = state._audio_lock_free
    audio = self._audio_adapter.adapt(raw, t)

    gain = self.params.get("gain", 1.0)
    base_speed = self.params.get("speed", 1.0)
    base_density = self.params.get("density", 0.4)
    base_trail = int(self.params.get("trail", 25))
    pal_idx = _get_pal_idx(self.params, default=3)

    # Resample bands to match grid width — never hardcode column count
    raw_bands = np.asarray(audio.bands, dtype=np.float64) if audio.bands is not None else np.zeros(10)
    bands = AudioCompatAdapter.resample_bands(raw_bands, cols)
    trail = int(min(60, base_trail * (1.0 + audio.buildup * gain)))

    cols = self.width
    rows = self.height

    self.buf.clear()

    # Spawn new drops — density purely sound-driven per column.
    # No sound = no rain. Each band's energy gates its column's spawn rate.
    for x in range(cols):
      band_val = float(bands[x])
      # Dead zone: below this, no drops at all
      if band_val < 0.03:
        continue
      col_density = band_val * gain * base_density * 6.0
      if audio.beat:
        col_density *= 1.5
      if random.random() < col_density * dt * 3:
        slot = self._find_free_slot()
        if slot < 0:
          continue
        r = random.random()
        if r < 0.5:
          spd = random.uniform(6, 20)
        elif r < 0.85:
          spd = random.uniform(20, 50)
        else:
          spd = random.uniform(50, 90)
        col_speed_mult = base_speed * (1.0 + band_val * gain * 3.0)
        self._drop_x[slot] = x
        self._drop_y[slot] = -1.0
        self._drop_speed[slot] = spd * col_speed_mult
        self._drop_bright[slot] = random.uniform(0.5, 1.0)
        self._active_mask[slot] = True

    # Update positions — also modulate live speed by current band at each drop's column
    active = self._active_mask
    drop_band_mult = 1.0 + bands[np.clip(self._drop_x, 0, cols - 1)] * gain * 2.0
    self._drop_y[active] += self._drop_speed[active] * drop_band_mult[active] * dt

    # Cull dead drops
    heads = self._drop_y.astype(np.int32)
    dead = active & ((heads - trail) >= rows)
    self._active_mask[dead] = False

    # Draw trails
    fade_lut = np.arange(trail, dtype=np.float64)
    fade_factors = (1.0 - fade_lut / trail) ** 1.5
    pal_colors = pal_color_grid(pal_idx, fade_factors).astype(np.float64)

    active_indices = np.where(self._active_mask)[0]
    n_active = len(active_indices)
    if n_active > 0:
      a_heads = self._drop_y[active_indices].astype(np.int32)
      a_brights = self._drop_bright[active_indices]
      a_xs = self._drop_x[active_indices]
      trail_offsets = np.arange(trail, dtype=np.int32)

      py_grid = a_heads[:, np.newaxis] - trail_offsets[np.newaxis, :]
      valid = (py_grid >= 0) & (py_grid < rows)
      bright_grid = fade_factors[np.newaxis, :] * a_brights[:, np.newaxis]
      rgb_grid = (pal_colors[np.newaxis, :, :] * bright_grid[:, :, np.newaxis]).astype(np.int32)

      drop_idx, trail_idx = np.where(valid)
      xs = a_xs[drop_idx]
      ys = py_grid[drop_idx, trail_idx]
      rgbs = rgb_grid[drop_idx, trail_idx]

      buf16 = self.buf.data.astype(np.uint16)
      np.add.at(buf16, (xs, ys, 0), rgbs[:, 0].astype(np.uint16))
      np.add.at(buf16, (xs, ys, 1), rgbs[:, 1].astype(np.uint16))
      np.add.at(buf16, (xs, ys, 2), rgbs[:, 2].astype(np.uint16))
      np.clip(buf16, 0, 255, out=buf16)
      self.buf.data[:] = buf16.astype(np.uint8)

    return self.buf.get_frame()

  def _find_free_slot(self):
    inactive = np.where(~self._active_mask)[0]
    if len(inactive) == 0:
      return -1
    return int(inactive[0])

  def _calc_dt_ms(self, t):
    if self._last_t is None:
      self._last_t = t
      return 16.67
    dt = (t - self._last_t) * 1000.0
    self._last_t = t
    return max(0.0, dt)


# ═══════════════════════════════════════════════════════════════════
#  SR MOIRE
# ═══════════════════════════════════════════════════════════════════

class SRMoire(Effect):
  """Sound-reactive Moire: bass tightens rings, beat pulses centers inward,
  drop expands ring scale."""

  CATEGORY = "sound"
  DISPLAY_NAME = "SR Moire"
  DESCRIPTION = "Audio-reactive ring interference — bass density, beat pulse, drop expand"
  PALETTE_SUPPORT = True

  PARAMS = [
    _Param("Gain", "gain", 0.2, 5.0, 0.1, 1.0),
    _Param("Speed", "speed", 0.05, 2.0, 0.05, 0.4),
    _Param("Scale", "scale", 0.0, 0.7, 0.07, 0.35),
    _Param("Centers", "centers", 2, 5, 1, 3),
  ]
  _SCALAR_PARAMS = {"gain": 1.0, "speed": 0.4, "scale": 0.35, "centers": 3, "palette": 0}
  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    self._audio_adapter = AudioCompatAdapter()
    self.buf = LEDBuffer(width, height)
    self._t = 0.0
    self._last_t = None
    self._beat_pull = 0.0
    self._drop_boost = 0.0

  def render(self, t, state):
    dt_ms = self._calc_dt_ms(t)
    dt_s = dt_ms * 0.001
    raw = state._audio_lock_free
    audio = self._audio_adapter.adapt(raw, t)

    gain = self.params.get("gain", 1.0)
    speed = self.params.get("speed", 0.4)
    base_sc = self.params.get("scale", 1.0)
    nc = int(self.params.get("centers", 3))
    pal_idx = _get_pal_idx(self.params)

    # Audio modulations
    sc = base_sc * (1.0 + audio.bass * gain * 1.5)
    if audio.beat:
      self._beat_pull = 1.0
    self._beat_pull *= 0.88 ** (dt_s * 60)

    if audio.drop:
      self._drop_boost = 1.0
    self._drop_boost *= 0.9 ** (dt_s * 60)
    sc *= 1.0 + self._drop_boost * gain

    self._t += dt_s * speed
    tt = self._t

    cols = self.width
    rows = self.height

    centers = []
    for i in range(nc):
      phase = i * 6.28 / nc
      cx = (math.sin(tt * 0.7 + phase) * 0.5 + 0.5) * cols
      cy = rows / 2 + math.sin(tt * 0.3 + phase * 1.7) * rows * 0.35
      # Beat pull: centers move toward pillar center
      pull = self._beat_pull * 0.6 * gain
      cx = cx * (1.0 - pull) + (cols / 2) * pull
      cy = cy * (1.0 - pull) + (rows / 2) * pull
      centers.append((cx, cy))

    x_g = np.arange(cols, dtype=np.float64)[:, np.newaxis]
    y_g = np.arange(rows, dtype=np.float64)[np.newaxis, :]

    val = np.zeros((cols, rows), dtype=np.float64)
    for cx, cy in centers:
      dx = x_g - cx
      dx = np.where(np.abs(dx) > cols / 2, dx - np.sign(dx) * cols, dx)
      dy = (y_g - cy) * (cols / rows) * 5
      dist = np.sqrt(dx ** 2 + dy ** 2)
      val += np.sin(dist * sc * 3 + tt * 2)
    val /= nc

    hue = (val + 1) * 0.5
    bright = np.clip((np.abs(val) ** 0.5) * 0.9 + 0.1, 0.0, 1.0)

    rgb = pal_color_grid(pal_idx, hue)
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
#  SR FLOW FIELD
# ═══════════════════════════════════════════════════════════════════

class SRFlowField(Effect):
  """Sound-reactive Flow Field: bass speeds flow, beat flashes existing
  particles full-bright, buildup boosts trail brightness. No extra
  particle spawning (keeps 60 FPS budget)."""

  CATEGORY = "sound"
  DISPLAY_NAME = "SR Flow Field"
  DESCRIPTION = "Audio-reactive flow field — bass velocity, beat flash, buildup glow"
  PALETTE_SUPPORT = True

  PARAMS = [
    _Param("Gain", "gain", 0.2, 5.0, 0.1, 1.0),
    _Param("Speed", "speed", 0.05, 2.0, 0.05, 0.3),
    _Param("Particles", "particles", 10, 200, 10, 80),
    _Param("Fade", "fade", 0.8, 0.99, 0.01, 0.92),
    _Param("Noise Scale", "noise_scale", 0.3, 3.0, 0.1, 1.0),
  ]
  _SCALAR_PARAMS = {"gain": 1.0, "speed": 0.3, "particles": 80, "fade": 0.92, "noise_scale": 1.0, "palette": 0}

  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    self._audio_adapter = AudioCompatAdapter()
    self.buf = LEDBuffer(width, height)
    self._t = 0.0
    self._pts = []
    for _ in range(200):
      self._pts.append([
        random.uniform(0, width),
        random.uniform(0, height),
        random.random(),
      ])
    self._beat_flash = 0.0
    self._last_t = None

  def render(self, t, state):
    dt_ms = self._calc_dt_ms(t)
    dt = dt_ms * 0.001
    raw = state._audio_lock_free
    audio = self._audio_adapter.adapt(raw, t)

    gain = self.params.get("gain", 1.0)
    base_speed = self.params.get("speed", 0.3)
    count = int(self.params.get("particles", 80))
    fade = self.params.get("fade", 0.92)
    ns = self.params.get("noise_scale", 1.0)
    pal_idx = _get_pal_idx(self.params)

    # Audio modulations
    # Velocity is strongly sound-dependent: silent = near-still drift,
    # loud music = fast flow. Combine overall level + bass for wide dynamic range.
    audio_energy = audio.volume + audio.bass * 0.5
    speed_mult = 0.2 + audio_energy * gain * 6.0  # 0.2x at silence → up to 5x+ at loud
    speed = base_speed * speed_mult

    if audio.beat:
      self._beat_flash = 1.0
    self._beat_flash *= 0.85 ** (dt * 60)
    bright_scale = 0.8 + min(0.8, audio.buildup * gain + self._beat_flash * 0.8)

    self._t += dt * speed
    self.buf.fade(fade)

    for p in self._pts[:count]:
      angle = cyl_noise(p[0], p[1], self._t * 0.5, ns, 0.008 * ns) * 6.28
      p[0] += math.cos(angle) * 30 * dt * speed
      p[1] += math.sin(angle) * 30 * dt * speed
      p[0] = p[0] % self.width
      if p[1] < 0 or p[1] >= self.height:
        p[0] = random.uniform(0, self.width)
        p[1] = random.uniform(0, self.height)
        p[2] = random.random()
      c = pal_color(pal_idx, p[2])
      self.buf.add_led(int(p[0]), int(p[1]),
                       c[0] * bright_scale, c[1] * bright_scale, c[2] * bright_scale)

    return self.buf.get_frame()

  def _calc_dt_ms(self, t):
    if self._last_t is None:
      self._last_t = t
      return 16.67
    dt = (t - self._last_t) * 1000.0
    self._last_t = t
    return max(0.0, dt)


SOUND_VARIANTS_EFFECTS = {
  'sr_feldstein': SRFeldstein,
  'sr_lava_lamp': SRLavaLamp,
  'sr_matrix_rain': SRMatrixRain,
  'sr_moire': SRMoire,
  'sr_flow_field': SRFlowField,
}
