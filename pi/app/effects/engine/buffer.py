"""LED buffer — wraps the global leds[] array from led_sim.py as a class.

Provides set_led, add_led, clear, fade, and get_frame for numpy output.
All mutation is in-place on self.data — no per-pixel allocations.
"""

import numpy as np

from ...hardware_constants import LEDS_PER_STRIP


class LEDBuffer:
  """Manages a (cols, rows, 3) uint8 pixel buffer with cylinder wrapping."""

  def __init__(self, cols=10, rows=LEDS_PER_STRIP):
    self.cols = cols
    self.rows = rows
    self.data = np.zeros((cols, rows, 3), dtype=np.uint8)
    # Scratch buffer for fade operations — avoids per-frame allocation
    self._scratch_u16 = np.zeros((cols, rows, 3), dtype=np.uint16)

  def set_led(self, x, y, r, g, b):
    """Set a pixel with cylinder-wrapped x coordinate."""
    x = int(x) % self.cols
    y = int(y)
    if 0 <= y < self.rows:
      self.data[x, y, 0] = min(255, max(0, int(r)))
      self.data[x, y, 1] = min(255, max(0, int(g)))
      self.data[x, y, 2] = min(255, max(0, int(b)))

  def add_led(self, x, y, r, g, b):
    """Additive blend a pixel (clamps to 255). Zero-allocation scalar path."""
    x = int(x) % self.cols
    y = int(y)
    if 0 <= y < self.rows:
      d = self.data[x, y]
      ri = max(0, int(r))
      gi = max(0, int(g))
      bi = max(0, int(b))
      d[0] = min(255, int(d[0]) + ri)
      d[1] = min(255, int(d[1]) + gi)
      d[2] = min(255, int(d[2]) + bi)

  def add_points(self, xs, ys, rgbs):
    """Batched additive blend. xs, ys: int arrays. rgbs: (N, 3) uint8.

    Out-of-bounds y values are silently skipped. x values wrap.
    """
    xs_wrapped = xs % self.cols
    valid = (ys >= 0) & (ys < self.rows)
    vx = xs_wrapped[valid]
    vy = ys[valid]
    vrgb = rgbs[valid].astype(np.uint16)
    # Use clip to handle additive overflow
    for i in range(len(vx)):
      d = self.data[vx[i], vy[i]]
      d[0] = min(255, int(d[0]) + int(vrgb[i, 0]))
      d[1] = min(255, int(d[1]) + int(vrgb[i, 1]))
      d[2] = min(255, int(d[2]) + int(vrgb[i, 2]))

  def clear(self):
    """Zero all pixels."""
    self.data[:] = 0

  def fade(self, factor):
    """Multiply all pixels by factor (0-1). In-place."""
    np.multiply(self.data, factor, out=self._scratch_u16, casting='unsafe')
    np.copyto(self.data, self._scratch_u16, casting='unsafe')

  def fade_by(self, amount):
    """Proportional fade-to-black. Mimics FastLED fadeToBlackBy. In-place.

    Each channel: value = value * (255 - amount) / 256
    A pixel at 100 with amount=48 becomes ~81 (proportional), not 52 (subtractive).
    """
    scale = 255 - int(amount)
    np.copyto(self._scratch_u16, self.data, casting='unsafe')
    np.multiply(self._scratch_u16, scale, out=self._scratch_u16)
    np.right_shift(self._scratch_u16, 8, out=self._scratch_u16)
    np.copyto(self.data, self._scratch_u16, casting='unsafe')

  def get_frame(self):
    """Return the buffer data array directly (no copy)."""
    return self.data
