# pi/tests/test_width_policy.py
"""Tests for effect width/height policy using layout in the renderer."""

import numpy as np
import time
from unittest.mock import MagicMock, AsyncMock

from app.effects.base import Effect
from app.core.renderer import Renderer, RenderState
from app.core.brightness import BrightnessEngine
from app.layout.compiler import CompiledLayout


def _make_layout(width=10, height=172):
  """Create a minimal CompiledLayout for testing."""
  return CompiledLayout(
    width=width,
    height=height,
    origin='bottom_left',
    forward_lut=[[None] * height for _ in range(width)],
    reverse_lut={},
    entries=[],
    output_sizes={},
    color_swizzle={},
    total_mapped=0,
  )


class ScaledEffect(Effect):
  """Test effect with RENDER_SCALE = 4 for supersampling."""
  RENDER_SCALE = 4

  def render(self, t, state):
    frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)
    frame[0, 0] = (self.width, 0, 0)
    return frame


class DefaultEffect(Effect):
  """Test effect that uses default grid dimensions."""

  def render(self, t, state):
    frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)
    frame[0, 0] = (self.width, 0, 0)
    return frame


class TestPixelMapWidthPolicy:
  def _make_renderer(self, width=10, height=172):
    transport = MagicMock()
    transport.send_frame = AsyncMock(return_value=True)
    state = RenderState()
    brightness = BrightnessEngine({})
    layout = _make_layout(width=width, height=height)
    return Renderer(transport, state, brightness, layout=layout)

  def test_default_effect_gets_pixel_map_width(self):
    renderer = self._make_renderer(width=10, height=172)
    renderer.register_effect('test_default', DefaultEffect)
    renderer._set_scene('test_default')
    assert renderer.current_effect.width == 10
    assert renderer.current_effect.height == 172

  def test_custom_grid_dimensions(self):
    renderer = self._make_renderer(width=20, height=100)
    renderer.register_effect('test_default', DefaultEffect)
    renderer._set_scene('test_default')
    assert renderer.current_effect.width == 20
    assert renderer.current_effect.height == 100

  def test_render_scale_multiplies_dimensions(self):
    renderer = self._make_renderer(width=10, height=172)
    renderer.register_effect('test_scaled', ScaledEffect)
    renderer._set_scene('test_scaled')
    assert renderer.current_effect.width == 40
    assert renderer.current_effect.height == 688

  def test_state_gets_grid_dimensions(self):
    renderer = self._make_renderer(width=10, height=172)
    assert renderer.state.grid_width == 10
    assert renderer.state.grid_height == 172

  def test_apply_layout_updates_state(self):
    renderer = self._make_renderer(width=10, height=172)
    new_layout = _make_layout(width=20, height=100)
    renderer.apply_layout(new_layout)
    assert renderer.state.grid_width == 20
    assert renderer.state.grid_height == 100
    assert renderer.layout is new_layout

  def test_last_logical_frame_matches_layout(self):
    renderer = self._make_renderer(width=10, height=172)
    assert renderer._last_logical_frame.shape == (10, 172, 3)

  def test_render_scale_on_base_class(self):
    """Base Effect class should have RENDER_SCALE = 1 (no supersampling)."""
    assert Effect.RENDER_SCALE == 1


from app.effects.imported import IMPORTED_EFFECTS


class TestImportedWidthPolicy:
  def test_no_imported_declares_native_width(self):
    """No imported effect should declare NATIVE_WIDTH (removed in dynamic-grid migration)."""
    for name, cls in IMPORTED_EFFECTS.items():
      nw = cls.__dict__.get('NATIVE_WIDTH', None)
      assert nw is None, f"{name}: still has NATIVE_WIDTH = {nw} (should be removed)"


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
