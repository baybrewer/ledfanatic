"""
Shared helpers for imported led_sim.py effects.

Provides palette sampling, noise functions, and buffer management
that multiple imported effects need. No Pygame dependencies.
"""

import math
import numpy as np

from .base import hsv_to_rgb

# Re-export for imported effect code that references the old name
hsv_to_rgb_fast = hsv_to_rgb


def palette_lerp(colors: list[tuple], t: float) -> tuple[int, int, int]:
  """Sample a color palette at position t (0-1, wrapping)."""
  t = t % 1.0
  n = len(colors)
  scaled = t * n
  idx = int(scaled)
  frac = scaled - idx
  c1 = colors[idx % n]
  c2 = colors[(idx + 1) % n]
  return (
    int(c1[0] + (c2[0] - c1[0]) * frac),
    int(c1[1] + (c2[1] - c1[1]) * frac),
    int(c1[2] + (c2[2] - c1[2]) * frac),
  )


def simplex_noise_2d(x: float, y: float) -> float:
  """Simple 2D value noise (not true simplex, but good enough for effects)."""
  # Use a hash-based approach for quick pseudo-noise
  ix = int(math.floor(x))
  iy = int(math.floor(y))
  fx = x - ix
  fy = y - iy
  # Smoothstep
  fx = fx * fx * (3 - 2 * fx)
  fy = fy * fy * (3 - 2 * fy)
  # Corner values
  def _hash(x, y):
    n = x * 374761393 + y * 668265263
    n = (n ^ (n >> 13)) * 1274126177
    return ((n ^ (n >> 16)) & 0xFFFFFFFF) / 4294967295.0
  n00 = _hash(ix, iy)
  n10 = _hash(ix + 1, iy)
  n01 = _hash(ix, iy + 1)
  n11 = _hash(ix + 1, iy + 1)
  return n00 * (1 - fx) * (1 - fy) + n10 * fx * (1 - fy) + n01 * (1 - fx) * fy + n11 * fx * fy


# Common palettes for imported effects
FIRE_PALETTE = [
  (0, 0, 0), (128, 17, 0), (182, 34, 0), (215, 53, 2),
  (252, 100, 0), (255, 117, 0), (250, 192, 0), (255, 255, 50),
]

OCEAN_PALETTE = [
  (0, 0, 30), (0, 20, 80), (0, 50, 120), (0, 100, 180),
  (20, 150, 200), (50, 200, 220), (100, 220, 240), (150, 240, 255),
]

AURORA_PALETTE = [
  (0, 20, 0), (0, 80, 40), (0, 150, 80), (20, 200, 100),
  (80, 220, 150), (40, 180, 200), (20, 100, 180), (0, 50, 100),
]

LAVA_PALETTE = [
  (20, 0, 0), (80, 0, 0), (150, 20, 0), (200, 50, 0),
  (255, 80, 0), (255, 120, 20), (255, 160, 50), (200, 80, 0),
]
