"""Tests for all 27 imported animations — render shape, time continuity, graceful degradation."""

import time
import numpy as np
from unittest.mock import MagicMock

from app.effects.imported import IMPORTED_EFFECTS
from app.effects.imported.classic import CLASSIC_EFFECTS
from app.effects.imported.ambient_a import AMBIENT_A_EFFECTS
from app.effects.imported.ambient_b import AMBIENT_B_EFFECTS
from app.effects.imported.sound import SOUND_EFFECTS
from app.effects.switcher import AnimationSwitcher


def _make_state():
  state = MagicMock()
  state._audio_lock_free = {
    'level': 0.0, 'bass': 0.0, 'mid': 0.0, 'high': 0.0,
    'beat': False, 'bpm': 120.0,
  }
  state.current_scene = 'test'
  state.blackout = False
  return state


class TestImportedEffectCount:
  def test_total_27(self):
    assert len(IMPORTED_EFFECTS) == 27

  def test_classic_5(self):
    assert len(CLASSIC_EFFECTS) == 5

  def test_ambient_a_6(self):
    assert len(AMBIENT_A_EFFECTS) == 6

  def test_ambient_b_6(self):
    assert len(AMBIENT_B_EFFECTS) == 6

  def test_sound_10(self):
    assert len(SOUND_EFFECTS) == 10

  def test_no_name_collisions(self):
    all_names = list(IMPORTED_EFFECTS.keys())
    assert len(all_names) == len(set(all_names))

  def test_plasma_uses_sim_suffix(self):
    assert 'plasma_sim' in IMPORTED_EFFECTS
    assert 'plasma' not in IMPORTED_EFFECTS


class TestAllEffectsRender:
  """Every imported effect must produce (10, 172, 3) uint8."""

  def test_all_render_correct_shape(self):
    state = _make_state()
    t = time.monotonic()
    for name, cls in IMPORTED_EFFECTS.items():
      eff = cls(width=10, height=172)
      frame = eff.render(t, state)
      assert frame.shape == (10, 172, 3), f"{name}: wrong shape {frame.shape}"
      assert frame.dtype == np.uint8, f"{name}: wrong dtype {frame.dtype}"
      t += 0.017

  def test_all_render_10_frames_no_crash(self):
    """Time continuity: render 10 consecutive frames."""
    state = _make_state()
    t = time.monotonic()
    for name, cls in IMPORTED_EFFECTS.items():
      eff = cls(width=10, height=172)
      for _ in range(10):
        frame = eff.render(t, state)
        assert frame.shape == (10, 172, 3)
        t += 0.017


class TestSoundEffectsGracefulDegradation:
  """Sound effects must render without crashing when audio is silent."""

  def test_all_sound_render_with_silence(self):
    state = _make_state()
    t = time.monotonic()
    for name, cls in SOUND_EFFECTS.items():
      eff = cls(width=10, height=172)
      frame = eff.render(t, state)
      assert frame.shape == (10, 172, 3), f"{name}: failed with silence"
      t += 0.017


class TestEffectMetadata:
  """Every imported effect must have required metadata."""

  def test_all_have_display_name(self):
    for name, cls in IMPORTED_EFFECTS.items():
      assert hasattr(cls, 'DISPLAY_NAME'), f"{name}: missing DISPLAY_NAME"
      assert cls.DISPLAY_NAME, f"{name}: empty DISPLAY_NAME"

  def test_all_have_category(self):
    for name, cls in IMPORTED_EFFECTS.items():
      assert hasattr(cls, 'CATEGORY'), f"{name}: missing CATEGORY"

  def test_all_have_description(self):
    for name, cls in IMPORTED_EFFECTS.items():
      assert hasattr(cls, 'DESCRIPTION'), f"{name}: missing DESCRIPTION"

  def test_all_have_params(self):
    for name, cls in IMPORTED_EFFECTS.items():
      assert hasattr(cls, 'PARAMS'), f"{name}: missing PARAMS"


class TestAnimationSwitcher:
  def test_render_empty_playlist(self):
    state = _make_state()
    eff = AnimationSwitcher(width=10, height=172, params={'playlist': []})
    frame = eff.render(time.monotonic(), state)
    assert frame.shape == (10, 172, 3)
    assert np.all(frame == 0)  # empty playlist = black

  def test_render_single_effect(self):
    from app.effects.generative import EFFECTS
    state = _make_state()
    eff = AnimationSwitcher(width=10, height=172, params={
      'playlist': ['solid_color'],
      '_effect_registry': EFFECTS,
      'interval': 5,
    })
    t = time.monotonic()
    frame = eff.render(t, state)
    assert frame.shape == (10, 172, 3)

  def test_cross_fade_blending(self):
    from app.effects.generative import EFFECTS
    state = _make_state()
    eff = AnimationSwitcher(width=10, height=172, params={
      'playlist': ['solid_color', 'fire'],
      '_effect_registry': EFFECTS,
      'interval': 0.01,  # very short — trigger fade quickly
      'fade_duration': 0.5,
    })
    t = time.monotonic()
    # Render enough frames to enter fading
    for _ in range(5):
      frame = eff.render(t, state)
      t += 0.02
    assert frame.shape == (10, 172, 3)

  def test_switcher_status(self):
    from app.effects.generative import EFFECTS
    eff = AnimationSwitcher(width=10, height=172, params={
      'playlist': ['solid_color', 'fire'],
      '_effect_registry': EFFECTS,
    })
    status = eff.get_switcher_status()
    assert status['active'] is True
    assert status['current'] == 'solid_color'
    assert len(status['playlist']) == 2

  def test_update_params_preserves_position(self):
    from app.effects.generative import EFFECTS
    eff = AnimationSwitcher(width=10, height=172, params={
      'playlist': ['solid_color', 'fire'],
      '_effect_registry': EFFECTS,
      'interval': 15,
    })
    eff.update_params({'interval': 30})
    assert eff._interval == 30
    assert eff._current_idx == 0  # position preserved
