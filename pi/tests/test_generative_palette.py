# pi/tests/test_generative_palette.py
"""Tests that all generative effects respond to palette selection."""

import time
import numpy as np
from unittest.mock import MagicMock

from app.effects.generative import EFFECTS


def _make_state():
  state = MagicMock()
  state._audio_lock_free = {
    'level': 0.0, 'bass': 0.0, 'mid': 0.0, 'high': 0.0,
    'beat': False, 'bpm': 0.0,
  }
  state.audio_level = 0.0
  state.audio_bass = 0.0
  state.audio_mid = 0.0
  state.audio_high = 0.0
  state.audio_beat = False
  state.audio_bpm = 0.0
  return state


# Effects that should respond to palette changes
PALETTE_EFFECTS = [
  'vertical_gradient', 'rainbow_rotate', 'plasma', 'twinkle', 'spark',
  'noise_wash', 'sine_bands', 'cylinder_rotate', 'fire',
]

# Effects with special color handling (not palette-indexed)
# solid_color: has its own color picker + fade mode
# color_wipe: uses two palette colors
# scanline: uses palette for beam color
# seam_pulse: diagnostic, fixed colors
# diagnostic_labels: diagnostic, fixed colors


class TestGenerativePaletteSupport:
  def test_palette_effects_produce_different_output_per_palette(self):
    """Each palette-supporting effect must produce visibly different frames
    when palette 0 (Rainbow) vs palette 4 (Lava) is selected."""
    state = _make_state()
    t = time.monotonic()
    for name in PALETTE_EFFECTS:
      cls = EFFECTS[name]
      eff_pal0 = cls(width=10, height=172, params={'palette': 0})
      eff_pal4 = cls(width=10, height=172, params={'palette': 4})
      # Render a few frames to let effects warm up
      for _ in range(10):
        f0 = eff_pal0.render(t, state)
        f4 = eff_pal4.render(t, state)
        t += 0.017
      assert not np.array_equal(f0, f4), (
        f"{name}: palette 0 and palette 4 produced identical frames"
      )

  def test_all_effects_render_correct_shape(self):
    state = _make_state()
    t = time.monotonic()
    for name, cls in EFFECTS.items():
      eff = cls(width=10, height=172)
      frame = eff.render(t, state)
      assert frame.shape == (10, 172, 3), f"{name}: wrong shape {frame.shape}"
      assert frame.dtype == np.uint8, f"{name}: wrong dtype {frame.dtype}"
      t += 0.017


class TestSolidColorModes:
  def test_static_mode_is_uniform(self):
    from app.effects.generative import SolidColor
    state = _make_state()
    eff = SolidColor(width=10, height=172, params={'color': '#FF0000', 'speed': 0.0})
    frame = eff.render(time.monotonic(), state)
    # All pixels should be the same color
    assert np.all(frame[0, 0] == frame[-1, -1])

  def test_fade_mode_cycles_palette(self):
    from app.effects.generative import SolidColor
    state = _make_state()
    eff = SolidColor(width=10, height=172, params={'speed': 1.0, 'palette': 0})
    t = time.monotonic()
    frames = []
    for _ in range(30):
      frames.append(eff.render(t, state).copy())
      t += 0.1
    # Frames should change over time when speed > 0
    assert not np.array_equal(frames[0], frames[-1])


class TestFireDirection:
  def test_fire_hot_at_bottom(self):
    """Fire should be brightest at the bottom (low y values)."""
    from app.effects.generative import Fire
    state = _make_state()
    eff = Fire(width=10, height=172, params={'sparking': 200})
    t = time.monotonic()
    # Render enough frames for fire to develop
    for _ in range(120):
      frame = eff.render(t, state)
      t += 0.017
    # Bottom quarter should be brighter than top quarter on average
    bottom_brightness = frame[:, :43, :].astype(float).mean()
    top_brightness = frame[:, 129:, :].astype(float).mean()
    assert bottom_brightness > top_brightness, (
      f"Fire is upside down: bottom={bottom_brightness:.1f}, top={top_brightness:.1f}"
    )


class TestScanlineBounce:
  def test_scanline_ping_pongs(self):
    """Scanline should reverse direction at top."""
    from app.effects.generative import Scanline
    state = _make_state()
    eff = Scanline(width=10, height=172, params={'speed': 2.0})
    t = time.monotonic()
    positions = []
    for _ in range(200):
      frame = eff.render(t, state)
      # Find the brightest row
      row_brightness = frame.sum(axis=(0, 2))
      positions.append(int(np.argmax(row_brightness)))
      t += 0.017
    # Should go up AND down — check we have both increasing and decreasing runs
    diffs = [positions[i+1] - positions[i] for i in range(len(positions)-1) if positions[i+1] != positions[i]]
    has_up = any(d > 0 for d in diffs)
    has_down = any(d < 0 for d in diffs)
    assert has_up and has_down, "Scanline should ping-pong but only moves in one direction"


class TestColorWipeContinuous:
  def test_no_full_blackout(self):
    """Color wipe should transition color-to-color, never go fully black."""
    from app.effects.generative import ColorWipe
    state = _make_state()
    eff = ColorWipe(width=10, height=172, params={'speed': 1.0, 'palette': 0})
    t = time.monotonic()
    black_frames = 0
    for _ in range(120):
      frame = eff.render(t, state)
      if frame.sum() == 0:
        black_frames += 1
      t += 0.017
    assert black_frames == 0, f"ColorWipe went fully black {black_frames} times in 120 frames"
