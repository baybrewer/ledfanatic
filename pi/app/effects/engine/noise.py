"""Perlin noise, FBM, and cylinder-aware noise — ported from led_sim.py.

Includes both scalar (per-pixel) and vectorized (NumPy) implementations.
The vectorized versions are 50-100x faster for grid operations.
"""

import math
import random

import numpy as np

# Shared permutation table (deterministic seed for reproducibility)
_rng = random.Random(42)
_p = list(range(256))
_rng.shuffle(_p)
_p += _p
_p_np = np.array(_p, dtype=np.int32)

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


# ═══════════════════════════════════════════════════════════════════
#  VECTORIZED (NumPy) IMPLEMENTATIONS — 50-100x faster for grids
# ═══════════════════════════════════════════════════════════════════

def _fade_v(t):
  return t * t * t * (t * (t * 6 - 15) + 10)


def _grad_v(h, x, y, z):
  """Vectorized gradient function."""
  h = h & 15
  u = np.where(h < 8, x, y)
  v = np.where(h < 4, y, np.where((h == 12) | (h == 14), x, z))
  return np.where(h & 1, -u, u) + np.where(h & 2, -v, v)


def perlin_grid(x, y, z):
  """Vectorized 3D Perlin noise for numpy arrays. Returns array of -1 to +1."""
  fx = np.floor(x).astype(np.int32)
  fy = np.floor(y).astype(np.int32)
  fz = np.floor(z).astype(np.int32)
  X = fx & 255
  Y = fy & 255
  Z = fz & 255
  x = x - fx
  y = y - fy
  z = z - fz
  u = _fade_v(x)
  v = _fade_v(y)
  w = _fade_v(z)
  A = _p_np[X] + Y
  AA = _p_np[A] + Z
  AB = _p_np[A + 1] + Z
  B = _p_np[X + 1] + Y
  BA = _p_np[B] + Z
  BB = _p_np[B + 1] + Z
  # Trilinear interpolation of gradient values
  x1 = x - 1; y1 = y - 1; z1 = z - 1
  g1 = _grad_v(_p_np[AA], x, y, z)
  g2 = _grad_v(_p_np[BA], x1, y, z)
  g3 = _grad_v(_p_np[AB], x, y1, z)
  g4 = _grad_v(_p_np[BB], x1, y1, z)
  g5 = _grad_v(_p_np[AA + 1], x, y, z1)
  g6 = _grad_v(_p_np[BA + 1], x1, y, z1)
  g7 = _grad_v(_p_np[AB + 1], x, y1, z1)
  g8 = _grad_v(_p_np[BB + 1], x1, y1, z1)
  l1 = g1 + u * (g2 - g1)
  l2 = g3 + u * (g4 - g3)
  l3 = g5 + u * (g6 - g5)
  l4 = g7 + u * (g8 - g7)
  m1 = l1 + v * (l2 - l1)
  m2 = l3 + v * (l4 - l3)
  return m1 + w * (m2 - m1)


def noise01_grid(x, y=0.0, z=0.0):
  """Vectorized Perlin noise normalized to 0-1."""
  return (perlin_grid(x, y, z) + 1.0) * 0.5


def fbm_grid(x, y, z, octaves=2, lacunarity=2.0, gain=0.5):
  """Vectorized fractal Brownian motion."""
  val = np.zeros_like(x, dtype=np.float64)
  amp = 1.0
  freq = 1.0
  for _ in range(octaves):
    val += perlin_grid(x * freq, y * freq, z * freq) * amp
    freq *= lacunarity
    amp *= gain
  return val / (1.0 + gain + gain * gain)


def cyl_noise_grid(cols, rows, t, x_scale=1.0, y_scale=0.01):
  """Vectorized cylinder noise for entire grid. Returns (cols, rows) array."""
  x_idx = np.arange(cols, dtype=np.float64)
  y_idx = np.arange(rows, dtype=np.float64)
  angles = x_idx / cols * 6.2832
  r = cols * x_scale / 6.2832
  # Build 2D grids
  cx = np.cos(angles) * r  # (cols,)
  sy = np.sin(angles) * r  # (cols,)
  yv = y_idx * y_scale + t  # (rows,)
  # Broadcast to (cols, rows)
  cx_grid = cx[:, np.newaxis] * np.ones(rows)
  sy_grid = sy[:, np.newaxis] * np.ones(rows)
  z_grid = np.ones(cols)[:, np.newaxis] * yv
  return perlin_grid(cx_grid, sy_grid, z_grid)


def cyl_fbm_grid(cols, rows, t, octaves=2, x_scale=1.0, y_scale=0.01):
  """Vectorized cylinder FBM for entire grid. Returns (cols, rows) array."""
  x_idx = np.arange(cols, dtype=np.float64)
  y_idx = np.arange(rows, dtype=np.float64)
  angles = x_idx / cols * 6.2832
  r = cols * x_scale / 6.2832
  cx = np.cos(angles) * r
  sy = np.sin(angles) * r
  yv = y_idx * y_scale + t
  cx_grid = cx[:, np.newaxis] * np.ones(rows)
  sy_grid = sy[:, np.newaxis] * np.ones(rows)
  z_grid = np.ones(cols)[:, np.newaxis] * yv
  return fbm_grid(cx_grid, sy_grid, z_grid, octaves)
