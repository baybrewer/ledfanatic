"""LED buffer — wraps the global leds[] array from led_sim.py as a class.

Provides set_led, add_led, clear, fade, and get_frame for numpy output.
"""

import numpy as np

from .color import clamp


class LEDBuffer:
  """Manages a (cols, rows, 3) uint8 pixel buffer with cylinder wrapping."""

  def __init__(self, cols=10, rows=172):
    self.cols = cols
    self.rows = rows
    self.data = np.zeros((cols, rows, 3), dtype=np.uint8)

  def set_led(self, x, y, r, g, b):
    """Set a pixel with cylinder-wrapped x coordinate."""
    x = int(x) % self.cols
    y = int(y)
    if 0 <= y < self.rows:
      self.data[x, y] = (clamp(r), clamp(g), clamp(b))

  def add_led(self, x, y, r, g, b):
    """Additive blend a pixel (clamps to 255)."""
    x = int(x) % self.cols
    y = int(y)
    if 0 <= y < self.rows:
      self.data[x, y] = np.clip(
        self.data[x, y].astype(np.int16) + [int(r), int(g), int(b)],
        0, 255,
      ).astype(np.uint8)

  def clear(self):
    """Zero all pixels."""
    self.data[:] = 0

  def fade(self, factor):
    """Multiply all pixels by factor (0-1). Used for trail effects."""
    self.data = (self.data.astype(np.float32) * factor).astype(np.uint8)

  def fade_by(self, amount):
    """Proportional fade-to-black. Mimics FastLED fadeToBlackBy.

    Each channel: value = value * (255 - amount) / 256
    A pixel at 100 with amount=48 becomes ~81 (proportional), not 52 (subtractive).
    """
    self.data = (self.data.astype(np.uint16) * (255 - int(amount)) >> 8).astype(np.uint8)

  def get_frame(self):
    """Return the buffer as a (cols, rows, 3) uint8 numpy array."""
    return self.data.copy()
