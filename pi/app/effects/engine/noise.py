"""Perlin noise, FBM, and cylinder-aware noise — ported from led_sim.py.

Pure Python, no Pygame dependency.
"""

import math
import random

# Shared permutation table (deterministic seed for reproducibility)
_rng = random.Random(42)
_p = list(range(256))
_rng.shuffle(_p)
_p += _p

# Default cylinder dimensions
_COLS = 10


def _fade(t):
  return t * t * t * (t * (t * 6 - 15) + 10)


def _lerp(t, a, b):
  return a + t * (b - a)


def _grad(h, x, y, z):
  h &= 15
  u = x if h < 8 else y
  if h < 4:
    v = y
  elif h == 12 or h == 14:
    v = x
  else:
    v = z
  return (u if (h & 1) == 0 else -u) + (v if (h & 2) == 0 else -v)


def perlin(x, y, z):
  """3D Perlin noise, returns -1 to +1."""
  fx = math.floor(x)
  fy = math.floor(y)
  fz = math.floor(z)
  X = int(fx) & 255
  Y = int(fy) & 255
  Z = int(fz) & 255
  x -= fx
  y -= fy
  z -= fz
  u = _fade(x)
  v = _fade(y)
  w = _fade(z)
  A = _p[X] + Y
  AA = _p[A] + Z
  AB = _p[A + 1] + Z
  B = _p[X + 1] + Y
  BA = _p[B] + Z
  BB = _p[B + 1] + Z
  return _lerp(w,
    _lerp(v,
      _lerp(u, _grad(_p[AA], x, y, z), _grad(_p[BA], x - 1, y, z)),
      _lerp(u, _grad(_p[AB], x, y - 1, z), _grad(_p[BB], x - 1, y - 1, z))),
    _lerp(v,
      _lerp(u, _grad(_p[AA + 1], x, y, z - 1), _grad(_p[BA + 1], x - 1, y, z - 1)),
      _lerp(u, _grad(_p[AB + 1], x, y - 1, z - 1), _grad(_p[BB + 1], x - 1, y - 1, z - 1))))


def noise01(x, y=0.0, z=0.0):
  """Perlin noise normalized to 0-1."""
  return (perlin(x, y, z) + 1.0) * 0.5


def fbm(x, y, z, octaves=2, lacunarity=2.0, gain=0.5):
  """Fractal Brownian motion."""
  val = 0.0
  amp = 1.0
  freq = 1.0
  for _ in range(octaves):
    val += perlin(x * freq, y * freq, z * freq) * amp
    freq *= lacunarity
    amp *= gain
  return val / (1.0 + gain + gain * gain)


def cyl_noise(x, y, t, x_scale=1.0, y_scale=0.01, cols=_COLS):
  """Perlin noise that wraps seamlessly around x-axis (cylinder mapping).

  Maps x to a circle in 2D noise space so column 0 and cols-1 are adjacent.
  """
  angle = x / cols * 6.2832
  r = cols * x_scale / 6.2832
  return perlin(math.cos(angle) * r, math.sin(angle) * r, y * y_scale + t)


def cyl_fbm(x, y, t, octaves=2, x_scale=1.0, y_scale=0.01, cols=_COLS):
  """Fractal noise with seamless cylinder wrapping."""
  angle = x / cols * 6.2832
  r = cols * x_scale / 6.2832
  return fbm(math.cos(angle) * r, math.sin(angle) * r, y * y_scale + t, octaves)


# Aliases matching vendored source naming convention
_perlin = perlin
_fbm = fbm
