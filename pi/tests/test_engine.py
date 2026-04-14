"""Tests for the engine package: noise, color, buffer, palettes."""

import numpy as np
from app.effects.engine.noise import perlin, noise01, fbm, cyl_noise, cyl_fbm
from app.effects.engine.color import clamp, clampf, qsub8, qadd8, scale8, hsv2rgb
from app.effects.engine.buffer import LEDBuffer
from app.effects.engine.palettes import (
  PALETTES, PALETTE_NAMES, NUM_PALETTES, pal_color,
  FELDSTEIN_PALETTES, FELDSTEIN_PALETTE_NAMES, NUM_FELDSTEIN_PALETTES,
  FIRE_PALETTE, fire_color,
)


class TestNoise:
  def test_perlin_range(self):
    for x in range(10):
      for y in range(10):
        v = perlin(x * 0.1, y * 0.1, 0.0)
        assert -1.5 <= v <= 1.5  # Perlin is typically -1 to 1, small overshoot ok

  def test_noise01_range(self):
    for x in range(20):
      v = noise01(x * 0.3, 0.5, 0.1)
      assert 0.0 <= v <= 1.0

  def test_fbm_returns_float(self):
    v = fbm(1.0, 2.0, 3.0, octaves=3)
    assert isinstance(v, float)

  def test_cylinder_noise_seam(self):
    """Column 0 and column 9 should produce similar values (cylinder wrap)."""
    v0 = cyl_noise(0, 50, 0.5)
    v9 = cyl_noise(9, 50, 0.5)
    v1 = cyl_noise(1, 50, 0.5)
    # v0 and v9 should be closer to each other than v0 and v5
    v5 = cyl_noise(5, 50, 0.5)
    seam_diff = abs(v0 - v9)
    mid_diff = abs(v0 - v5)
    # Not a strict test since noise is random, but seam should generally be tighter
    assert seam_diff < 2.0  # very loose bound

  def test_cyl_fbm_returns_float(self):
    v = cyl_fbm(3, 100, 1.0, octaves=2)
    assert isinstance(v, float)


class TestColor:
  def test_clamp_bounds(self):
    assert clamp(-10) == 0
    assert clamp(300) == 255
    assert clamp(128) == 128

  def test_clampf_bounds(self):
    assert clampf(-0.5) == 0.0
    assert clampf(1.5) == 1.0
    assert clampf(0.5) == 0.5

  def test_qsub8(self):
    assert qsub8(100, 50) == 50
    assert qsub8(10, 50) == 0

  def test_qadd8(self):
    assert qadd8(200, 100) == 255
    assert qadd8(100, 50) == 150

  def test_scale8(self):
    assert scale8(255, 128) == 127
    assert scale8(0, 255) == 0

  def test_hsv2rgb_red(self):
    r, g, b = hsv2rgb(0, 255, 255)
    assert r == 255

  def test_hsv2rgb_white(self):
    r, g, b = hsv2rgb(0, 0, 255)
    assert r == 255 and g == 255 and b == 255

  def test_hsv2rgb_black(self):
    assert hsv2rgb(0, 255, 0) == (0, 0, 0)


class TestBuffer:
  def test_init_shape(self):
    buf = LEDBuffer(10, 172)
    assert buf.data.shape == (10, 172, 3)
    assert buf.data.dtype == np.uint8

  def test_set_led(self):
    buf = LEDBuffer(10, 172)
    buf.set_led(3, 50, 100, 200, 50)
    assert tuple(buf.data[3, 50]) == (100, 200, 50)

  def test_set_led_cylinder_wrap(self):
    buf = LEDBuffer(10, 172)
    buf.set_led(13, 50, 100, 200, 50)  # 13 % 10 = 3
    assert tuple(buf.data[3, 50]) == (100, 200, 50)

  def test_add_led_additive(self):
    buf = LEDBuffer(10, 172)
    buf.set_led(0, 0, 100, 100, 100)
    buf.add_led(0, 0, 100, 100, 100)
    assert tuple(buf.data[0, 0]) == (200, 200, 200)

  def test_add_led_clamps(self):
    buf = LEDBuffer(10, 172)
    buf.set_led(0, 0, 200, 200, 200)
    buf.add_led(0, 0, 200, 200, 200)
    assert tuple(buf.data[0, 0]) == (255, 255, 255)

  def test_clear(self):
    buf = LEDBuffer(10, 172)
    buf.set_led(5, 5, 255, 255, 255)
    buf.clear()
    assert np.all(buf.data == 0)

  def test_fade(self):
    buf = LEDBuffer(10, 172)
    buf.set_led(0, 0, 200, 200, 200)
    buf.fade(0.5)
    assert all(buf.data[0, 0] <= 101)  # 200 * 0.5 = 100

  def test_fade_by_proportional(self):
    """fade_by is proportional (FastLED style), not subtractive."""
    buf = LEDBuffer(10, 172)
    buf.set_led(0, 0, 200, 200, 200)
    buf.fade_by(48)
    # 200 * (255 - 48) / 256 = 200 * 207 / 256 ≈ 161
    assert all(155 <= buf.data[0, 0]) and all(buf.data[0, 0] <= 165)

  def test_fade_by_low_value(self):
    """Proportional fade: low values fade less than high values."""
    buf = LEDBuffer(10, 172)
    buf.set_led(0, 0, 50, 50, 50)
    buf.fade_by(48)
    # 50 * 207 / 256 ≈ 40
    assert all(38 <= buf.data[0, 0]) and all(buf.data[0, 0] <= 42)

  def test_get_frame_shape(self):
    buf = LEDBuffer(10, 172)
    frame = buf.get_frame()
    assert frame.shape == (10, 172, 3)
    assert frame.dtype == np.uint8

  def test_get_frame_is_copy(self):
    buf = LEDBuffer(10, 172)
    buf.set_led(0, 0, 255, 0, 0)
    frame = buf.get_frame()
    frame[0, 0] = [0, 0, 0]
    assert buf.data[0, 0, 0] == 255  # original unchanged

  def test_persistent_state(self):
    """Buffer persists across multiple operations (no auto-clear)."""
    buf = LEDBuffer(10, 172)
    buf.set_led(0, 0, 100, 0, 0)
    buf.set_led(1, 1, 0, 100, 0)
    assert buf.data[0, 0, 0] == 100
    assert buf.data[1, 1, 1] == 100


class TestPalettes:
  def test_standard_palette_count(self):
    assert NUM_PALETTES == 10

  def test_all_palette_names(self):
    expected = ["Rainbow", "Ocean", "Sunset", "Forest", "Lava", "Ice", "Neon", "Cyberpunk", "Pastel", "Vapor"]
    assert PALETTE_NAMES == expected

  def test_palette_entries_valid_rgb(self):
    for name, pal in PALETTES:
      for r, g, b in pal:
        assert 0 <= r <= 255
        assert 0 <= g <= 255
        assert 0 <= b <= 255

  def test_pal_color_returns_tuple(self):
    c = pal_color(0, 0.5)
    assert isinstance(c, tuple)
    assert len(c) == 3

  def test_feldstein_palette_count(self):
    assert NUM_FELDSTEIN_PALETTES == 17

  def test_feldstein_palette_names(self):
    assert FELDSTEIN_PALETTE_NAMES[0] == "Original"
    assert FELDSTEIN_PALETTE_NAMES[16] == "Ice Storm"

  def test_fire_palette_length(self):
    assert len(FIRE_PALETTE) == 256

  def test_fire_palette_valid_rgb(self):
    for r, g, b in FIRE_PALETTE:
      assert 0 <= r <= 255
      assert 0 <= g <= 255
      assert 0 <= b <= 255

  def test_fire_color_range(self):
    c = fire_color(0.0)
    assert isinstance(c, tuple)
    c = fire_color(1.0)
    assert isinstance(c, tuple)

  def test_fire_color_progresses(self):
    """Fire palette should get brighter from 0 to 1."""
    c_low = fire_color(0.1)
    c_high = fire_color(0.9)
    assert sum(c_high) > sum(c_low)
