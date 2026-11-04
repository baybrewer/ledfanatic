"""Color math utilities — ported from led_sim.py.

All functions operate on 0-255 integer ranges unless noted.
"""

import math

import numpy as np


def clamp(v, lo=0, hi=255):
  """Integer clamp."""
  return int(max(lo, min(hi, v)))


def clampf(v, lo=0.0, hi=1.0):
  """Float clamp."""
  return max(lo, min(hi, v))


def qsub8(a, b):
  """Saturating subtract (floor at 0)."""
  return max(0, a - b)


def qadd8(a, b):
  """Saturating add (ceiling at 255)."""
  return min(255, a + b)


def scale8(a, b):
  """Fixed-point 8-bit scaling: (a * b) >> 8."""
  return (a * b) >> 8


def hsv2rgb(h, s, v):
  """Convert HSV to RGB. All values 0-255 range.

  This is the led_sim.py integer-math version, distinct from
  base.py's float-range hsv_to_rgb.
  """
  if v == 0:
    return (0, 0, 0)
  if s == 0:
    return (v, v, v)
  region = (h * 6) >> 8
  frac = (h * 6) & 0xFF
  p = (v * (255 - s)) >> 8
  q = (v * (255 - ((s * frac) >> 8))) >> 8
  t = (v * (255 - ((s * (255 - frac)) >> 8))) >> 8
  lut = [(v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q)]
  return lut[min(region, 5)]


def hsv_to_rgb_grid(h, s, v):
  """Vectorized HSV->RGB. h, s, v are 0-1 floats or arrays (broadcast).

  Returns uint8 array of shape broadcast(h, s, v).shape + (3,).
  Branchless formulation (no np.where chains) keeps it cheap per frame.
  """
  h = np.asarray(h, dtype=np.float32) % 1.0
  s = np.clip(np.asarray(s, dtype=np.float32), 0.0, 1.0)
  v = np.clip(np.asarray(v, dtype=np.float32), 0.0, 1.0)
  h, s, v = np.broadcast_arrays(h, s, v)
  h6 = h[..., np.newaxis] * 6.0
  k = (np.array([5.0, 3.0, 1.0], dtype=np.float32) + h6) % 6.0
  ramp = np.clip(np.minimum(k, 4.0 - k), 0.0, 1.0)
  rgb = v[..., np.newaxis] * (1.0 - s[..., np.newaxis] * ramp)
  return (rgb * 255.0 + 0.5).astype(np.uint8)


def kelvin_to_rgb(kelvin):
  """Color temperature -> (r, g, b) 0-255. Tanner Helland approximation.

  Clamped to 1000-40000 K, the range the curve fit is valid for.
  """
  temp = max(1000.0, min(40000.0, float(kelvin))) / 100.0
  if temp <= 66:
    red = 255.0
    green = 99.4708025861 * math.log(temp) - 161.1195681661
    blue = 0.0 if temp <= 19 else 138.5177312231 * math.log(temp - 10) - 305.0447927307
  else:
    red = 329.698727446 * ((temp - 60) ** -0.1332047592)
    green = 288.1221695283 * ((temp - 60) ** -0.0755148492)
    blue = 255.0
  if temp >= 66:
    blue = 255.0
  return clamp(round(red)), clamp(round(green)), clamp(round(blue))
