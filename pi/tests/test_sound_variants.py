"""Smoke tests: all SR variants instantiate and render a frame."""

import numpy as np
import pytest

from app.effects.imported.sound_variants import (
  SRFeldstein, SRLavaLamp, SRMatrixRain, SRMoire, SRFlowField,
  SOUND_VARIANTS_EFFECTS,
)


class FakeState:
  """Minimal state object for effect testing."""
  def __init__(self):
    self._audio_lock_free = {
      'level': 0.3, 'bass': 0.4, 'mid': 0.2, 'high': 0.1,
      'beat': False, 'bpm': 120.0,
      'spectrum': [0.1] * 16,
    }


@pytest.mark.parametrize("name,cls", list(SOUND_VARIANTS_EFFECTS.items()))
def test_variant_renders_frame(name, cls):
  """Each SR variant should produce a valid frame of the right shape."""
  effect = cls(width=10, height=172, params={})
  state = FakeState()
  frame = effect.render(0.0, state)
  assert frame.shape == (10, 172, 3), f"{name}: bad shape {frame.shape}"
  assert frame.dtype == np.uint8, f"{name}: bad dtype {frame.dtype}"


@pytest.mark.parametrize("name,cls", list(SOUND_VARIANTS_EFFECTS.items()))
def test_variant_handles_beat(name, cls):
  """Each SR variant should render correctly with a beat event."""
  effect = cls(width=10, height=172, params={})
  state = FakeState()
  state._audio_lock_free['beat'] = True
  frame = effect.render(0.016, state)
  assert frame.shape == (10, 172, 3), f"{name}: bad shape"


@pytest.mark.parametrize("name,cls", list(SOUND_VARIANTS_EFFECTS.items()))
def test_variant_has_gain_param(name, cls):
  """Every SR variant must expose a gain param."""
  params = [p.attr for p in cls.PARAMS]
  assert 'gain' in params, f"{name}: missing gain param"


def test_all_5_variants_registered():
  """Registration dict has exactly the 5 expected variants."""
  assert set(SOUND_VARIANTS_EFFECTS.keys()) == {
    'sr_feldstein', 'sr_lava_lamp', 'sr_matrix_rain', 'sr_moire', 'sr_flow_field',
  }


def test_variants_in_imported_effects():
  """Variants are merged into IMPORTED_EFFECTS so the catalog picks them up."""
  from app.effects.imported import IMPORTED_EFFECTS
  for name in SOUND_VARIANTS_EFFECTS:
    assert name in IMPORTED_EFFECTS
