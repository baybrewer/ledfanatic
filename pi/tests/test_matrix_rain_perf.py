# pi/tests/test_matrix_rain_perf.py
"""MatrixRain performance regression and correctness tests."""

import time
import numpy as np
from unittest.mock import MagicMock


def _make_state():
  state = MagicMock()
  state._audio_lock_free = {
    'level': 0.0, 'bass': 0.0, 'mid': 0.0, 'high': 0.0,
    'beat': False, 'bpm': 120.0,
  }
  return state


class TestMatrixRainCorrectness:
  def test_render_shape(self):
    from app.effects.imported.ambient_a import MatrixRain
    eff = MatrixRain(width=10, height=172)
    state = _make_state()
    frame = eff.render(time.monotonic(), state)
    assert frame.shape == (10, 172, 3)
    assert frame.dtype == np.uint8

  def test_produces_nonzero_pixels_after_warmup(self):
    from app.effects.imported.ambient_a import MatrixRain
    eff = MatrixRain(width=10, height=172)
    state = _make_state()
    t = time.monotonic()
    # Render 60 frames to let drops populate
    for _ in range(60):
      frame = eff.render(t, state)
      t += 0.017
    assert np.any(frame > 0), "MatrixRain produced no visible pixels after 60 frames"

  def test_drops_are_capped(self):
    """Active drops should not grow unbounded."""
    from app.effects.imported.ambient_a import MatrixRain
    eff = MatrixRain(width=10, height=172)
    state = _make_state()
    t = time.monotonic()
    for _ in range(600):
      eff.render(t, state)
      t += 0.017
    # Max drops: 10 columns * max_per_col (should be well under 500)
    active = int(np.sum(eff._active_mask)) if hasattr(eff, '_active_mask') else len(eff._drops)
    assert active < 500, f"Too many active drops: {active}"


class TestMatrixRainPerformance:
  def test_600_frames_no_degradation(self):
    """Last 60 frames must not be >4x slower than first 60 frames."""
    from app.effects.imported.ambient_a import MatrixRain
    eff = MatrixRain(width=10, height=172)
    state = _make_state()
    t = time.monotonic()
    times = []
    for _ in range(600):
      start = time.perf_counter()
      eff.render(t, state)
      times.append(time.perf_counter() - start)
      t += 0.017
    first_60_avg = sum(times[:60]) / 60
    last_60_avg = sum(times[-60:]) / 60
    ratio = last_60_avg / max(first_60_avg, 1e-9)
    assert ratio < 4.0, (
      f"MatrixRain degraded {ratio:.1f}x over 600 frames "
      f"(first60={first_60_avg*1000:.2f}ms, last60={last_60_avg*1000:.2f}ms)"
    )
