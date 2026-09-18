"""Darkness semantics: darkness drives element opacity + presence, never background."""

import numpy as np

from app.effects.negative_space import apply_darkness


class TestApplyDarkness:
  def test_full_darkness_saturated_field_is_black(self):
    frame = np.full((4, 4, 3), 200.0, dtype=np.float32)
    field = np.ones((4, 4), dtype=np.float32)
    out = apply_darkness(frame, field, 1.0)
    assert out.max() == 0.0

  def test_presence_boost_saturates_partial_field_at_max(self):
    # field 0.7 * (0.5 + 1.0) = 1.05 -> clipped 1.0 -> full black at darkness=1
    frame = np.full((2, 2, 3), 200.0, dtype=np.float32)
    field = np.full((2, 2), 0.7, dtype=np.float32)
    out = apply_darkness(frame, field, 1.0)
    assert out.max() == 0.0

  def test_zero_field_leaves_background_untouched(self):
    frame = np.full((3, 3, 3), 180.0, dtype=np.float32)
    field = np.zeros((3, 3), dtype=np.float32)
    for darkness in (0.1, 0.5, 1.0):
      out = apply_darkness(frame, field, darkness)
      assert np.allclose(out, frame)  # background independent of slider

  def test_monotonic_in_darkness(self):
    frame = np.full((2, 2, 3), 200.0, dtype=np.float32)
    field = np.full((2, 2), 0.5, dtype=np.float32)
    lo = apply_darkness(frame, field, 0.1).mean()
    mid = apply_darkness(frame, field, 0.5).mean()
    hi = apply_darkness(frame, field, 1.0).mean()
    assert lo > mid > hi


from app.core.renderer import RenderState
from app.effects.negative_space import NEGATIVE_SPACE_EFFECTS
from app.effects.negative_spinoffs import NEGATIVE_SPINOFF_EFFECTS

import pytest

LOUD = {
  'level': 0.9, 'bass': 0.9, 'mid': 0.7, 'high': 0.5,
  'beat': True, 'beat_frame_id': 1, 'bpm': 120.0, 'spectrum': [0.8] * 16,
}

AFFECTED = {**NEGATIVE_SPACE_EFFECTS, **NEGATIVE_SPINOFF_EFFECTS}
AFFECTED.pop('sr_negative_rain')  # frozen — original semantics


def _loud_state():
  state = RenderState()
  state._audio_lock_free = dict(LOUD)
  state._beat_this_frame = True
  return state


def _run_effect(cls, darkness, frames=90):
  eff = cls(width=20, height=40, params={'darkness': darkness})
  state = _loud_state()
  out = None
  for i in range(frames):
    out = eff.render(i / 30.0, state)
  return out.astype(np.float32)


class TestFamilyDarknessSemantics:
  @pytest.mark.parametrize("name,cls", sorted(AFFECTED.items()))
  def test_max_darkness_darker_than_low(self, name, cls):
    hi = _run_effect(cls, 1.0)
    lo = _run_effect(cls, 0.1)
    # More darkness volume => darker darkest-pixel and lower mean
    assert hi.min() < lo.min() - 5, f"{name}: min {hi.min()} vs {lo.min()}"
    assert hi.mean() < lo.mean(), name

  @pytest.mark.parametrize("name,cls", sorted(AFFECTED.items()))
  def test_max_darkness_reaches_near_black(self, name, cls):
    hi = _run_effect(cls, 1.0)
    assert hi.min() <= 8, f"{name}: darkest pixel {hi.min()} not near black"
    assert hi.mean() > 40, f"{name}: background collapsed (mean {hi.mean()})"

  @pytest.mark.parametrize("name,cls", sorted(AFFECTED.items()))
  def test_darkness_param_range(self, name, cls):
    p = next(p for p in cls.PARAMS if p.attr == 'darkness')
    assert p.lo == 0.1 and p.hi == 1.0, name


MODERATE = {
  'level': 0.4, 'bass': 0.45, 'mid': 0.3, 'high': 0.2,
  'beat': True, 'beat_frame_id': 1, 'bpm': 120.0, 'spectrum': [0.35] * 16,
}


def _moderate_state():
  state = RenderState()
  state._audio_lock_free = dict(MODERATE)
  state._beat_this_frame = True
  return state


class TestModerateAudioDefaults:
  """Default-slider, moderate-music sanity: visible darkness, alive background."""

  @pytest.mark.parametrize("name,cls", sorted(AFFECTED.items()))
  def test_visible_darkness_at_defaults(self, name, cls):
    eff = cls(width=20, height=40, params={})  # default darkness
    state = _moderate_state()
    out = None
    for i in range(90):
      out = eff.render(i / 30.0, state)
    out = out.astype(np.float32)
    assert out.min() <= 110, f"{name}: no visible dark elements at defaults (min {out.min()})"
    assert out.mean() > 40, f"{name}: background collapsed at defaults (mean {out.mean()})"


from app.effects.negative_space import SRNegativeRipples


class TestNegativeRipples:
  def test_registered(self):
    assert NEGATIVE_SPACE_EFFECTS['sr_negative_ripples'] is SRNegativeRipples

  def test_renders_dark_rings_on_bright_bg(self):
    out = _run_effect(SRNegativeRipples, 1.0)
    assert out.min() <= 8       # dark ring cores near black
    assert out.mean() > 20      # plasma background alive (LOUD fixture spawns ripples every frame on continuous beat)

  def test_silence_stays_bright(self):
    eff = SRNegativeRipples(width=20, height=40, params={'darkness': 1.0})
    state = RenderState()  # default silent audio
    out = None
    for i in range(60):
      out = eff.render(i / 30.0, state)
    assert out.astype(np.float32).mean() > 60  # no onsets -> no rings
