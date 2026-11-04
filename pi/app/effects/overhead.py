"""
Overhead mode — calm room-lighting effects for the desk-mounted panel.

Design constraints (these run for hours above someone's head):
- slow, smooth, no flicker/strobe, no hard edges
- time-based via an integrated phase, so live speed changes never jump
- vectorized NumPy, < 3 ms/frame at 20x83
"""

import math
import numpy as np

from .base import Effect, hex_to_rgb
from .engine.color import hsv_to_rgb_grid, kelvin_to_rgb
from .engine.noise import fbm_grid, perlin_grid

_TAU = 2.0 * math.pi


class _P:
  def __init__(self, label, attr, lo, hi, step, default, kind='slider'):
    self.label, self.attr, self.lo, self.hi = label, attr, lo, hi
    self.step, self.default, self.kind = step, default, kind


_SPEED = _P("Speed", "speed", 0.1, 3.0, 0.05, 1.0)
_SATURATION = _P("Saturation", "saturation", 0.0, 1.0, 0.05, 0.85)


class _OverheadEffect(Effect):
  CATEGORY = "overhead"
  PALETTE_SUPPORT = False
  PARAMS = [_SPEED, _SATURATION]

  # Frame gaps beyond this (scene switch, stall) shouldn't fast-forward the animation
  _MAX_DT = 0.25

  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    self._phase = 0.0
    self._last_elapsed = 0.0
    xs = np.arange(width, dtype=np.float32)
    ys = np.arange(height, dtype=np.float32)
    self._x, self._y = np.meshgrid(xs, ys, indexing='ij')

  def _param(self, attr):
    """Read a numeric param live (update_params safe), clamped to its descriptor."""
    desc = next(p for p in self.PARAMS if p.attr == attr)
    try:
      value = float(self.params.get(attr, desc.default))
    except (TypeError, ValueError):
      value = desc.default
    return min(desc.hi, max(desc.lo, value))

  def _advance(self, t):
    """Integrate phase by speed. Returns (phase, dt) in speed-scaled seconds."""
    now = self.elapsed(t)
    dt = min(self._MAX_DT, max(0.0, now - self._last_elapsed))
    self._last_elapsed = now
    step = dt * self._param('speed')
    self._phase += step
    return self._phase, step


# ──────────────────────────────────────────────────────────────────────
#  Solid lights (no animation)
# ──────────────────────────────────────────────────────────────────────

class OverheadWhite(_OverheadEffect):
  """Tunable white light."""

  DISPLAY_NAME = "White"
  DESCRIPTION = "Plain tunable white, 2200-6500 K"
  PARAMS = [_P("Color Temp", "kelvin", 2200, 6500, 100, 3000)]

  def render(self, t, state):
    rgb = kelvin_to_rgb(self._param('kelvin'))
    frame = np.empty((self.width, self.height, 3), dtype=np.uint8)
    frame[:] = rgb
    return frame


class OverheadColor(_OverheadEffect):
  """Solid single color."""

  DISPLAY_NAME = "Color"
  DESCRIPTION = "One solid color across the whole panel"
  DEFAULT_COLOR = '#FF8C3C'
  PARAMS = [_P("Color", "color", None, None, None, DEFAULT_COLOR, kind='color')]

  def _color(self):
    raw = self.params.get('color', self.DEFAULT_COLOR)
    try:
      if isinstance(raw, str):
        rgb = hex_to_rgb(raw)
      else:
        rgb = tuple(int(c) for c in raw)
      if len(rgb) != 3:
        raise ValueError(raw)
      return tuple(max(0, min(255, c)) for c in rgb)
    except (TypeError, ValueError):
      return hex_to_rgb(self.DEFAULT_COLOR)

  def render(self, t, state):
    frame = np.empty((self.width, self.height, 3), dtype=np.uint8)
    frame[:] = self._color()
    return frame


# ──────────────────────────────────────────────────────────────────────
#  Animated rainbow family (speed + saturation)
# ──────────────────────────────────────────────────────────────────────

class OverheadLiquidRainbow(_OverheadEffect):
  """Domain-warped noise rainbow that flows like liquid."""

  DISPLAY_NAME = "Liquid Rainbow"
  DESCRIPTION = "Slow domain-warped rainbow flow"

  _SCALE = 1.0 / 28.0

  def render(self, t, state):
    phase, _ = self._advance(t)
    sx = self._x * self._SCALE
    sy = self._y * self._SCALE
    z = phase * 0.06
    warp_x = fbm_grid(sx + 1.7, sy + 9.2, np.full_like(sx, z))
    warp_y = fbm_grid(sx + 8.3, sy + 2.8, np.full_like(sx, z + 3.1))
    field = fbm_grid(sx + 1.6 * warp_x, sy + 1.6 * warp_y,
                     np.full_like(sx, z * 0.7 + 5.0))
    hue = field * 0.4 + phase * 0.01
    # Gentle depth so it reads as liquid, never dark
    value = 0.82 + 0.18 * np.clip(field * 2.0 + 0.5, 0.0, 1.0)
    return hsv_to_rgb_grid(hue, self._param('saturation'), value)


class OverheadRainbowInk(_OverheadEffect):
  """Rainbow dye advected through a divergence-free curl-noise flow."""

  DISPLAY_NAME = "Rainbow Ink"
  DESCRIPTION = "Rainbow ink swirling through slow invisible currents"

  _FLOW_SCALE = 1.0 / 18.0
  # Stream-function amplitude -> roughly 2-4 px/s drift at speed 1
  _FLOW_AMP = 40.0
  # Fraction of dye replaced per (speed-scaled) second, so it never homogenizes
  _RESEED_RATE = 0.06
  _DIFFUSION = 1.5

  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    # Dye stored as hue unit vector (cos, sin): averaging during advection
    # then mixes hues around the color wheel instead of toward muddy RGB gray.
    self._dye = self._seed(0.0)

  def _seed(self, phase):
    hue = fbm_grid(self._x / 24.0, self._y / 24.0,
                   np.full_like(self._x, 11.0 + phase * 0.02)) * 0.7 + phase * 0.008
    angle = hue * _TAU
    return np.stack([np.cos(angle), np.sin(angle)], axis=-1).astype(np.float32)

  @staticmethod
  def _diffuse(dye, amount):
    padded = np.pad(dye, ((1, 1), (1, 1), (0, 0)), mode='edge')
    neighbors = (padded[:-2, 1:-1] + padded[2:, 1:-1]
                 + padded[1:-1, :-2] + padded[1:-1, 2:]) * 0.25
    return dye + (neighbors - dye) * amount

  def _sample(self, src_x, src_y):
    """Bilinear sample of the dye field at fractional positions (edge-clamped)."""
    w, h = self.width, self.height
    src_x = np.clip(src_x, 0.0, w - 1.0)
    src_y = np.clip(src_y, 0.0, h - 1.0)
    x0 = np.minimum(src_x.astype(np.int32), w - 2) if w > 1 else np.zeros_like(src_x, np.int32)
    y0 = np.minimum(src_y.astype(np.int32), h - 2) if h > 1 else np.zeros_like(src_y, np.int32)
    x1 = np.minimum(x0 + 1, w - 1)
    y1 = np.minimum(y0 + 1, h - 1)
    fx = (src_x - x0)[..., np.newaxis]
    fy = (src_y - y0)[..., np.newaxis]
    dye = self._dye
    top = dye[x0, y0] * (1 - fx) + dye[x1, y0] * fx
    bottom = dye[x0, y1] * (1 - fx) + dye[x1, y1] * fx
    return top * (1 - fy) + bottom * fy

  def render(self, t, state):
    phase, step = self._advance(t)
    if step > 0.0:
      z = np.full_like(self._x, phase * 0.025)
      psi = perlin_grid(self._x * self._FLOW_SCALE, self._y * self._FLOW_SCALE, z)
      psi *= self._FLOW_AMP
      if self.width > 1 and self.height > 1:
        dpsi_dx, dpsi_dy = np.gradient(psi)
      else:
        dpsi_dx = dpsi_dy = np.zeros_like(psi)
      # Curl of a scalar potential is divergence-free: no sinks, no pile-ups
      vel_x, vel_y = dpsi_dy, -dpsi_dx
      dye = self._sample(self._x - vel_x * step, self._y - vel_y * step)
      # Light diffusion: advection stretches dye into razor filaments otherwise
      self._dye = self._diffuse(dye, min(1.0, self._DIFFUSION * step))
      blend = min(1.0, self._RESEED_RATE * step)
      self._dye += (self._seed(phase) - self._dye) * blend

    cos_h, sin_h = self._dye[..., 0], self._dye[..., 1]
    hue = np.arctan2(sin_h, cos_h) / _TAU
    # Where hues have mixed the vector shortens: render that as paler ink
    strength = np.clip(np.hypot(cos_h, sin_h) * 1.4, 0.0, 1.0)
    sat = self._param('saturation') * (0.55 + 0.45 * strength)
    return hsv_to_rgb_grid(hue, sat, 1.0)


class OverheadOilSlick(_OverheadEffect):
  """Thin-film interference colors over a slowly shifting film."""

  DISPLAY_NAME = "Oil Slick"
  DESCRIPTION = "Iridescent thin-film colors, like oil on water"

  _SCALE = 1.0 / 30.0
  # Phase offsets per channel give the rainbow-banded interference look
  _OFFSETS = np.array([0.0, 0.33, 0.67], dtype=np.float32)

  def render(self, t, state):
    phase, _ = self._advance(t)
    z = np.full_like(self._x, phase * 0.05)
    thickness = fbm_grid(self._x * self._SCALE, self._y * self._SCALE, z, octaves=3)
    thickness = thickness * 1.6 + phase * 0.015
    rgb = 0.5 + 0.5 * np.cos(_TAU * (thickness[..., np.newaxis] + self._OFFSETS))
    rgb = rgb.astype(np.float32)
    gray = rgb.mean(axis=-1, keepdims=True)
    rgb = gray + (rgb - gray) * self._param('saturation')
    # Lift the floor so cos troughs don't become dark holes in room light
    rgb = 0.2 + 0.8 * rgb
    return (np.clip(rgb, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)


class OverheadPrismWaves(_OverheadEffect):
  """Broad rainbow bands from a few sines along a slowly rotating axis."""

  DISPLAY_NAME = "Prism Waves"
  DESCRIPTION = "Wide soft rainbow waves rolling at a slowly turning angle"

  # (wavelength px, temporal rate, phase offset, amplitude)
  _WAVES = ((120.0, 0.11, 0.0, 0.5), (70.0, -0.07, 1.3, 0.3), (45.0, 0.05, 2.9, 0.2))

  def render(self, t, state):
    phase, _ = self._advance(t)
    angle = 1.1 + phase * 0.012
    cx, cy = (self.width - 1) / 2.0, (self.height - 1) / 2.0
    along = (self._x - cx) * math.cos(angle) + (self._y - cy) * math.sin(angle)
    across = -(self._x - cx) * math.sin(angle) + (self._y - cy) * math.cos(angle)
    wave = np.zeros_like(along)
    for wavelength, rate, offset, amp in self._WAVES:
      wave += amp * np.sin(along * (_TAU / wavelength) + phase * rate * _TAU + offset)
    # Slight perpendicular wobble keeps the bands from looking ruler-straight
    wave += 0.08 * np.sin(across * (_TAU / 60.0) + phase * 0.3)
    hue = 0.35 * wave + phase * 0.01
    return hsv_to_rgb_grid(hue, self._param('saturation'), 1.0)


class OverheadRainbowLava(_OverheadEffect):
  """Soft metaballs drifting on Lissajous paths, each its own hue."""

  DISPLAY_NAME = "Rainbow Lava"
  DESCRIPTION = "Glowing lava-lamp blobs in blending rainbow colors"

  _BLOBS = 6

  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    rng = np.random.default_rng(7)
    n = self._BLOBS
    self._freq_x = rng.uniform(0.6, 1.4, n)
    self._freq_y = rng.uniform(0.4, 1.0, n)
    self._off_x = rng.uniform(0, _TAU, n)
    self._off_y = rng.uniform(0, _TAU, n)
    self._hues = np.arange(n) / n
    radius = max(2.5, min(width * 0.38, height * 0.12))
    self._radius_sq = (radius * rng.uniform(0.8, 1.2, n)) ** 2

  def render(self, t, state):
    phase, _ = self._advance(t)
    w, h = self.width, self.height
    motion = phase * 0.05
    centers_x = (w - 1) / 2.0 + (w * 0.42) * np.sin(self._freq_x * motion + self._off_x)
    centers_y = (h - 1) / 2.0 + (h * 0.42) * np.sin(self._freq_y * motion + self._off_y)
    hues = self._hues + phase * 0.006

    field = np.zeros_like(self._x)
    hue_x = np.zeros_like(self._x)
    hue_y = np.zeros_like(self._x)
    for i in range(self._BLOBS):
      dist_sq = (self._x - centers_x[i]) ** 2 + (self._y - centers_y[i]) ** 2
      # Bounded kernel (peak 1) so dominance shifts gradually between blobs
      influence = self._radius_sq[i] / (dist_sq + self._radius_sq[i])
      field += influence
      hue_x += influence * math.cos(hues[i] * _TAU)
      hue_y += influence * math.sin(hues[i] * _TAU)
    # Circular mean of blob hues weighted by influence (no wrap seam at hue 0/1)
    hue = np.arctan2(hue_y, hue_x) / _TAU
    # Where opposite hues cancel, atan2 is unstable and would snap colors frame
    # to frame; fading saturation by hue coherence turns that seam into soft pastel.
    coherence = np.hypot(hue_x, hue_y) / field
    sat = self._param('saturation') * np.clip(coherence * 1.25, 0.0, 1.0)
    glow = np.clip((field - 0.35) / 0.8, 0.0, 1.0)
    glow = glow * glow * (3.0 - 2.0 * glow)
    value = 0.3 + 0.7 * glow
    return hsv_to_rgb_grid(hue, sat, value)


class OverheadColorDrift(_OverheadEffect):
  """Whole panel slowly drifts around the color wheel."""

  DISPLAY_NAME = "Color Drift"
  DESCRIPTION = "One hue for the whole room, drifting through the rainbow (~4 min cycle)"

  _CYCLE_SECONDS = 240.0
  _GRADIENT = 0.06

  def render(self, t, state):
    phase, _ = self._advance(t)
    base = phase / self._CYCLE_SECONDS
    angle = phase * 0.02
    u = self._x / max(1, self.width - 1) - 0.5
    v = self._y / max(1, self.height - 1) - 0.5
    hue = base + self._GRADIENT * (u * math.cos(angle) + v * math.sin(angle))
    return hsv_to_rgb_grid(hue, self._param('saturation'), 1.0)


class OverheadCaustics(_OverheadEffect):
  """Pool-floor caustics from warped abs-sine ridges."""

  DISPLAY_NAME = "Caustics"
  DESCRIPTION = "Sunlight rippling on a pool floor"

  _DEEP = np.array([0.0, 0.42, 0.55], dtype=np.float32)
  _BRIGHT = np.array([0.85, 1.0, 1.0], dtype=np.float32)

  def render(self, t, state):
    phase, _ = self._advance(t)
    tm = phase * 0.35
    x, y = self._x, self._y
    k = _TAU / 32.0
    ridge_a = np.sin(x * k + 1.6 * np.sin(y * k * 0.8 + tm) + tm * 0.7)
    ridge_b = np.sin(y * k * 0.9 + 1.6 * np.sin(x * k * 1.1 - tm * 0.8) - tm * 0.5)
    ridge_c = np.sin((x + y) * k * 0.7 + 1.2 * np.sin((x - y) * k * 0.6 + tm * 0.6) + tm * 0.4)
    # (1-|sin|)^3 gives soft bright ridges several px wide: no aliasing at 20 cols
    light = ((1.0 - np.abs(ridge_a)) ** 3 + (1.0 - np.abs(ridge_b)) ** 3
             + (1.0 - np.abs(ridge_c)) ** 3) / 3.0
    light = np.clip(light * 1.6, 0.0, 1.0)[..., np.newaxis]

    sat = self._param('saturation')
    deep = self._DEEP.mean() + (self._DEEP - self._DEEP.mean()) * sat
    bright = 1.0 + (self._BRIGHT - 1.0) * sat
    deep = 0.35 + 0.65 * deep
    rgb = deep + (bright - deep) * light
    return (np.clip(rgb, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)


class OverheadNightLight(_OverheadEffect):
  """Dim amber glow that breathes very slowly."""

  DISPLAY_NAME = "Night Light"
  DESCRIPTION = "Dim warm amber glow, gently breathing"

  _HUE = 0.075
  _MAX_VALUE = 0.25
  _BREATH_SECONDS = 20.0

  def render(self, t, state):
    phase, _ = self._advance(t)
    cx, cy = (self.width - 1) / 2.0, (self.height - 1) / 2.0
    # Normalize per axis so the glow fills the tall panel instead of a tiny circle
    nx = (self._x - cx) / max(1.0, self.width * 0.6)
    ny = (self._y - cy) / max(1.0, self.height * 0.45)
    glow = np.exp(-(nx * nx + ny * ny))
    breath = 0.85 + 0.15 * math.sin(phase * _TAU / self._BREATH_SECONDS)
    value = self._MAX_VALUE * breath * (0.25 + 0.75 * glow)
    return hsv_to_rgb_grid(self._HUE, self._param('saturation'), value)


OVERHEAD_EFFECTS = {
  'oh_white': OverheadWhite,
  'oh_color': OverheadColor,
  'oh_liquid_rainbow': OverheadLiquidRainbow,
  'oh_rainbow_ink': OverheadRainbowInk,
  'oh_oil_slick': OverheadOilSlick,
  'oh_prism_waves': OverheadPrismWaves,
  'oh_rainbow_lava': OverheadRainbowLava,
  'oh_color_drift': OverheadColorDrift,
  'oh_caustics': OverheadCaustics,
  'oh_night_light': OverheadNightLight,
}
