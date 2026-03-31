"""
Base effect class and common utilities.
"""

import numpy as np
from abc import ABC, abstractmethod
from typing import Optional

from ..mapping.cylinder import N


class Effect(ABC):
  """Base class for all effects."""

  def __init__(self, width: int = 10, height: int = N, params: Optional[dict] = None):
    self.width = width
    self.height = height
    self.params = params or {}
    self._start_time: Optional[float] = None

  @abstractmethod
  def render(self, t: float, state) -> np.ndarray:
    """
    Render one frame.

    t: monotonic time in seconds
    state: RenderState with audio modulation etc.
    Returns: np.ndarray of shape (width, height, 3) uint8
    """
    pass

  def elapsed(self, t: float) -> float:
    if self._start_time is None:
      self._start_time = t
    return t - self._start_time


def hsv_to_rgb(h: float, s: float, v: float) -> tuple[int, int, int]:
  """Convert HSV (0-1 range) to RGB (0-255)."""
  if s == 0:
    val = int(v * 255)
    return val, val, val
  i = int(h * 6.0)
  f = (h * 6.0) - i
  p = v * (1.0 - s)
  q = v * (1.0 - s * f)
  t = v * (1.0 - s * (1.0 - f))
  i = i % 6
  if i == 0: r, g, b = v, t, p
  elif i == 1: r, g, b = q, v, p
  elif i == 2: r, g, b = p, v, t
  elif i == 3: r, g, b = p, q, v
  elif i == 4: r, g, b = t, p, v
  else: r, g, b = v, p, q
  return int(r * 255), int(g * 255), int(b * 255)


def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
  """Convert hex color string to RGB tuple."""
  hex_color = hex_color.lstrip('#')
  return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


def lerp_color(c1: tuple, c2: tuple, t: float) -> tuple[int, int, int]:
  """Linearly interpolate between two RGB colors."""
  t = max(0.0, min(1.0, t))
  return (
    int(c1[0] + (c2[0] - c1[0]) * t),
    int(c1[1] + (c2[1] - c1[1]) * t),
    int(c1[2] + (c2[2] - c1[2]) * t),
  )


def palette_sample(colors: list[tuple], t: float) -> tuple[int, int, int]:
  """Sample a color from a palette at position t (0-1, wrapping)."""
  t = t % 1.0
  n = len(colors)
  scaled = t * n
  idx = int(scaled)
  frac = scaled - idx
  c1 = colors[idx % n]
  c2 = colors[(idx + 1) % n]
  return lerp_color(c1, c2, frac)
