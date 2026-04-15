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


from app.effects.imported import IMPORTED_EFFECTS


class TestImportedWidthPolicy:
  def test_all_imported_declare_native_width_10(self):
    """Every imported effect must declare NATIVE_WIDTH = 10."""
    for name, cls in IMPORTED_EFFECTS.items():
      assert hasattr(cls, 'NATIVE_WIDTH'), f"{name}: missing NATIVE_WIDTH"
      assert cls.NATIVE_WIDTH == 10, f"{name}: NATIVE_WIDTH is {cls.NATIVE_WIDTH}, expected 10"


import time


class TestBrokenWidthEffects:
  """Spectrum and Spectrogram must not crash at their native width."""

  def test_spectrum_renders_at_width_10(self):
    from app.effects.imported.sound import Spectrum
    state = MagicMock()
    state._audio_lock_free = {
      'level': 0.5, 'bass': 0.6, 'mid': 0.4, 'high': 0.3,
      'beat': False, 'bpm': 128.0,
    }
    eff = Spectrum(width=10, height=172)
    t = time.monotonic()
    for _ in range(10):
      frame = eff.render(t, state)
      assert frame.shape == (10, 172, 3)
      t += 0.017

  def test_spectrogram_renders_at_width_10(self):
    from app.effects.imported.sound import Spectrogram
    state = MagicMock()
    state._audio_lock_free = {
      'level': 0.5, 'bass': 0.6, 'mid': 0.4, 'high': 0.3,
      'beat': False, 'bpm': 128.0,
    }
    eff = Spectrogram(width=10, height=172)
    t = time.monotonic()
    for _ in range(10):
      frame = eff.render(t, state)
      assert frame.shape == (10, 172, 3)
      t += 0.017

  def test_spectrum_crashes_at_width_40(self):
    """Document that Spectrum does NOT support width > 10."""
    from app.effects.imported.sound import Spectrum
    import pytest
    state = MagicMock()
    state._audio_lock_free = {
      'level': 0.5, 'bass': 0.6, 'mid': 0.4, 'high': 0.3,
      'beat': False, 'bpm': 128.0,
    }
    eff = Spectrum(width=40, height=172)
    with pytest.raises(IndexError):
      eff.render(time.monotonic(), state)
