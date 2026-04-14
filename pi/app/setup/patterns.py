"""
Setup pattern runner.

Generates frames for setup-scoped test patterns (strip identification,
color order testing, geometry anchors) without mutating persistent scene state.
"""

import numpy as np
from typing import Optional

from ..hardware_constants import STRIPS, LEDS_PER_STRIP
from ..effects.base import Effect


class SetupPatternEffect(Effect):
  """Lightweight effect that holds and replays a static setup pattern frame."""

  def __init__(self, frame: np.ndarray):
    super().__init__(width=frame.shape[0], height=frame.shape[1])
    self._frame = frame

  def render(self, t: float, state) -> np.ndarray:
    return self._frame


def generate_setup_pattern(
  mode: str,
  targets: list[dict],
  color: tuple[int, int, int] = (255, 255, 255),
  all_others: str = "black",
  use_compiled_color_order: bool = False,
) -> np.ndarray:
  """Generate a setup pattern frame.

  mode: "fill_strip" | "fill_leds" | "clear" | "anchor"
  targets: list of {strip_id, led_index?, led_count?}
  color: RGB color tuple
  all_others: "black" (only option for now)
  use_compiled_color_order: if False, bypass color compensation (for RGB wizard)
  """
  frame = np.zeros((STRIPS, LEDS_PER_STRIP, 3), dtype=np.uint8)

  if mode == "clear":
    return frame

  for target in targets:
    strip_id = target.get('strip_id', 0)
    if strip_id < 0 or strip_id >= STRIPS:
      continue

    if mode == "fill_strip":
      frame[strip_id, :, :] = color

    elif mode == "fill_leds":
      led_index = target.get('led_index', 0)
      led_count = target.get('led_count', 1)
      start = max(0, led_index)
      end = min(LEDS_PER_STRIP, led_index + led_count)
      frame[strip_id, start:end, :] = color

    elif mode == "anchor":
      # Light anchor points at 0%, 25%, 50%, 75%, 100%
      max_led = target.get('installed_led_count', LEDS_PER_STRIP)
      anchors = [0, max_led // 4, max_led // 2, 3 * max_led // 4, max_led - 1]
      for idx in anchors:
        if 0 <= idx < LEDS_PER_STRIP:
          frame[strip_id, idx, :] = color

  return frame
