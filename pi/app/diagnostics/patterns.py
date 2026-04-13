"""
Diagnostic test patterns triggered from the UI.

These send TEST_PATTERN commands to the Teensy, or generate
Pi-side diagnostic frames for the render pipeline.
"""

import numpy as np
from ..effects.base import Effect, hsv_to_rgb
from ..mapping.cylinder import N
from ..models.protocol import TestPattern


class StripIdentifyEffect(Effect):
  """Show each strip in a unique color for physical identification."""

  COLORS = [
    (255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0),
    (255, 0, 255), (0, 255, 255), (255, 128, 0), (128, 0, 255),
    (0, 255, 128), (255, 255, 255),
  ]

  def render(self, t: float, state) -> np.ndarray:
    target = self.params.get('strip', -1)  # -1 = all
    frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)

    for x in range(min(self.width, 10)):
      if target >= 0 and x != target:
        continue
      frame[x, :] = self.COLORS[x % 10]
    return frame


class BottomToTopSweep(Effect):
  """White sweep from bottom to top, one strip at a time or all."""

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)
    speed = self.params.get('speed', 1.0)
    target = self.params.get('strip', -1)
    frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)

    pos = int((elapsed * speed * 40) % self.height)

    for x in range(min(self.width, 10)):
      if target >= 0 and x != target:
        continue
      if 0 <= pos < self.height:
        frame[x, pos] = (255, 255, 255)
        # Trail
        for trail in range(1, 6):
          ty = pos - trail
          if 0 <= ty < self.height:
            v = int(255 * (1 - trail / 6))
            frame[x, ty] = (v, v, v)
    return frame


class ChannelIdentifyEffect(Effect):
  """Light up one channel at a time."""

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)
    frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)

    channel = int(elapsed) % 5
    s1 = channel * 2
    s2 = channel * 2 + 1

    if s1 < self.width:
      frame[s1, :] = (0, 255, 0)
    if s2 < self.width:
      frame[s2, :] = (0, 128, 255)

    return frame


class RGBOrderTest(Effect):
  """Show R, G, B in sequence for verifying color order."""

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)
    phase = int(elapsed) % 3
    frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)

    if phase == 0:
      frame[:, :] = (255, 0, 0)  # Should be RED
    elif phase == 1:
      frame[:, :] = (0, 255, 0)  # Should be GREEN
    else:
      frame[:, :] = (0, 0, 255)  # Should be BLUE
    return frame


class SeamTest(Effect):
  """Highlight the seam between S9 and S0."""

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)
    frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)

    # S0 = red, S9 = blue, animated pulse
    import math
    pulse = (math.sin(elapsed * 4) + 1) / 2
    v = int(pulse * 255)

    if self.width >= 10:
      frame[0, :] = (v, 0, 0)
      frame[9, :] = (0, 0, v)
      # Mid-section marker
      mid = self.height // 2
      frame[0, mid-2:mid+2] = (255, 255, 255)
      frame[9, mid-2:mid+2] = (255, 255, 255)
    return frame


class SerpentineChase(Effect):
  """Chase pattern that follows the serpentine wiring path."""

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)
    frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)

    for pair in range(5):
      s1 = pair * 2
      s2 = pair * 2 + 1
      # Position along the full 344-LED chain
      chain_pos = int(elapsed * 60) % 344

      if chain_pos < 172:
        # On first strip (bottom to top)
        y = chain_pos
        if s1 < self.width:
          frame[s1, y] = (255, 255, 0)
      else:
        # On second strip (top to bottom)
        y = 343 - chain_pos  # maps 172->171, 343->0
        if s2 < self.width:
          frame[s2, y] = (0, 255, 255)

    return frame


DIAGNOSTIC_EFFECTS = {
  'diag_strip_identify': StripIdentifyEffect,
  'diag_sweep': BottomToTopSweep,
  'diag_channel_identify': ChannelIdentifyEffect,
  'diag_rgb_order': RGBOrderTest,
  'diag_seam': SeamTest,
  'diag_serpentine': SerpentineChase,
}
