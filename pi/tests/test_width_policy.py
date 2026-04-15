# pi/tests/test_width_policy.py
"""Tests for per-effect width policy in the renderer."""

import numpy as np
from unittest.mock import MagicMock, AsyncMock

from app.effects.base import Effect
from app.core.renderer import Renderer, RenderState
from app.core.brightness import BrightnessEngine


class WidthTenEffect(Effect):
  """Test effect that declares native width 10."""
  NATIVE_WIDTH = 10

  def render(self, t, state):
    frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)
    frame[0, 0] = (self.width, 0, 0)  # encode width in pixel for verification
    return frame


class WidthFortyEffect(Effect):
  """Test effect that uses supersampling (default behavior)."""

  def render(self, t, state):
    frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)
    frame[0, 0] = (self.width, 0, 0)
    return frame


class TestWidthPolicy:
  def _make_renderer(self, internal_width=40):
    transport = MagicMock()
    transport.send_frame = AsyncMock(return_value=True)
    state = RenderState()
    brightness = BrightnessEngine({})
    return Renderer(transport, state, brightness, internal_width=internal_width)

  def test_native_width_10_effect_gets_width_10(self):
    renderer = self._make_renderer(internal_width=40)
    renderer.register_effect('test_w10', WidthTenEffect)
    renderer._set_scene('test_w10')
    assert renderer.current_effect.width == 10

  def test_default_effect_gets_internal_width(self):
    renderer = self._make_renderer(internal_width=40)
    renderer.register_effect('test_default', WidthFortyEffect)
    renderer._set_scene('test_default')
    assert renderer.current_effect.width == 40

  def test_native_width_attribute_on_base_class(self):
    """Base Effect class should have NATIVE_WIDTH = None (use renderer default)."""
    assert Effect.NATIVE_WIDTH is None

  def test_native_width_10_inherited(self):
    assert WidthTenEffect.NATIVE_WIDTH == 10
