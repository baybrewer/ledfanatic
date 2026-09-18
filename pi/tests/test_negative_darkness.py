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
