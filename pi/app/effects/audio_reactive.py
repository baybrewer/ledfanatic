"""
Audio-reactive effects for the LED pillar.
"""

import math
import numpy as np

from .base import Effect, hsv_to_rgb, hex_to_rgb
from .generative import _hsv_array_to_rgb
from ..mapping.cylinder import N


class VUPulse(Effect):
  """VU meter-style pulse from bottom."""

  def render(self, t: float, state) -> np.ndarray:
    sensitivity = self.params.get('sensitivity', 1.0)
    frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)

    level = min(1.0, state.audio_level * sensitivity)
    fill_height = int(level * self.height)

    for y in range(fill_height):
      frac = y / self.height
      if frac < 0.6:
        color = (0, 255, 0)
      elif frac < 0.85:
        color = (255, 255, 0)
      else:
        color = (255, 0, 0)
      frame[:, y] = color

    return frame


class BandColors(Effect):
  """Low/mid/high frequency bands as colored sections."""

  def render(self, t: float, state) -> np.ndarray:
    low_color = hex_to_rgb(self.params.get('low_color', '#FF0000'))
    mid_color = hex_to_rgb(self.params.get('mid_color', '#00FF00'))
    high_color = hex_to_rgb(self.params.get('high_color', '#0000FF'))
    frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)

    third = self.height // 3

    # Low band — bottom third
    low_h = int(state.audio_bass * third)
    for y in range(low_h):
      frame[:, y] = low_color

    # Mid band — middle third
    mid_h = int(state.audio_mid * third)
    for y in range(third, third + mid_h):
      frame[:, y] = mid_color

    # High band — top third
    high_h = int(state.audio_high * third)
    for y in range(2 * third, 2 * third + high_h):
      frame[:, y] = high_color

    return frame


class BeatFlash(Effect):
  """Flash on beat detection."""

  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self._flash_level = 0.0

  def render(self, t: float, state) -> np.ndarray:
    decay = self.params.get('decay', 0.9)
    color = hex_to_rgb(self.params.get('color', '#FFFFFF'))
    frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)

    if state.audio_beat:
      self._flash_level = 1.0
    else:
      self._flash_level *= decay

    if self._flash_level > 0.01:
      c = tuple(int(v * self._flash_level) for v in color)
      frame[:, :] = c

    return frame


class EnergyRing(Effect):
  """Horizontal ring that sweeps vertically. Ring thickness varies around
  the cylinder based on the 16-bin FFT spectrum resampled to 10 bands —
  loud frequencies produce a thicker ring segment at that column."""

  def _resample_16_to_10(self, spectrum):
    """Resample 16-bin spectrum to 10 bands via mean pooling."""
    if spectrum is None:
      return np.zeros(10, dtype=np.float32)
    src = np.asarray(spectrum, dtype=np.float32)
    if len(src) != 16:
      return np.zeros(10, dtype=np.float32)
    out = np.zeros(10, dtype=np.float32)
    ratio = 16 / 10  # 1.6
    for i in range(10):
      lo = i * ratio
      hi = (i + 1) * ratio
      lo_i = int(lo)
      hi_i = min(int(hi) + 1, 16)
      out[i] = float(np.mean(src[lo_i:hi_i])) if hi_i > lo_i else 0.0
    return out

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)
    speed = self.params.get('speed', 2.0)
    gain = self.params.get('gain', 1.0)
    frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)

    # Ring sweep position (no longer audio-modulated — avoids stutter)
    ring_y = int(elapsed * speed * 10) % self.height

    # Per-column thickness from 10-band spectrum
    spectrum = getattr(state, 'audio_spectrum', None)
    bands_10 = self._resample_16_to_10(spectrum)
    col_widths = np.maximum(1, (bands_10 * 30 * gain).astype(np.int32))

    # Toroidal distance from ring_y for every row
    y_coords = np.arange(self.height, dtype=np.int32)
    d1 = np.abs(y_coords - ring_y)
    d2 = self.height - d1
    dists = np.minimum(d1, d2)

    # Per-column hue (drifts over time)
    hue_col = (np.arange(self.width, dtype=np.float64) / self.width + elapsed * 0.1) % 1.0

    # For each column, compute fade where dist < width
    for x in range(self.width):
      w = int(col_widths[x])
      if w <= 0:
        continue
      within = dists < w
      if not np.any(within):
        continue
      fades = 1.0 - dists[within].astype(np.float64) / w
      ys = y_coords[within]
      # One RGB per column, modulated by fade per pixel
      base_rgb = _hsv_array_to_rgb(np.array([hue_col[x]]), 1.0, 1.0)[0]  # (3,) uint8
      for i, y in enumerate(ys):
        f = fades[i]
        frame[x, y] = (int(base_rgb[0] * f), int(base_rgb[1] * f), int(base_rgb[2] * f))

    return frame


class SpectralGlow(Effect):
  """Columns glow based on spectral energy. Bars grow upward from the bottom,
  brightest at the top of each bar (like a flame tip)."""

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)
    gain = self.params.get('gain', 1.0)
    frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)

    bands = [state.audio_bass, state.audio_mid, state.audio_high]

    # Compute per-column fill heights via interpolation
    col_pos = np.arange(self.width, dtype=np.float64) / self.width * (len(bands) - 1)
    band_idx = np.minimum(col_pos.astype(np.int32), len(bands) - 2)
    frac = col_pos - band_idx
    band_arr = np.array(bands, dtype=np.float64)
    levels = band_arr[band_idx] * (1 - frac) + band_arr[band_idx + 1] * frac
    fill_heights = np.clip((levels * gain * self.height).astype(np.int32), 0, self.height)

    # Per-column hue
    hue_base = (np.arange(self.width, dtype=np.float64) / self.width + elapsed * 0.05) % 1.0

    # Lit mask: y < fill_height (y=0 is bottom in logical coords, so bars grow up)
    y_grid = np.arange(self.height, dtype=np.int32)[np.newaxis, :]
    fill_grid = fill_heights[:, np.newaxis]
    lit_mask = y_grid < fill_grid

    # Inverted fade: brightest at the TOP of each column (y=height-1 → 1.0),
    # dimmer near the base (y=0 → 0.5). Fixes perceived upside-down look.
    y_frac = np.arange(self.height, dtype=np.float64) / self.height
    fade = 0.5 + y_frac * 0.5

    hue_grid = np.broadcast_to(hue_base[:, np.newaxis], (self.width, self.height))
    rgb = _hsv_array_to_rgb(hue_grid, 0.8, 1.0)
    rgb_faded = (rgb.astype(np.float32) * fade[np.newaxis, :, np.newaxis]).astype(np.uint8)

    frame[lit_mask] = rgb_faded[lit_mask]
    return frame


AUDIO_EFFECTS = {
  'vu_pulse': VUPulse,
  'band_colors': BandColors,
  'beat_flash': BeatFlash,
  'energy_ring': EnergyRing,
  'spectral_glow': SpectralGlow,
}
