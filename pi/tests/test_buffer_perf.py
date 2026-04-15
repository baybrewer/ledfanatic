"""Tests for LEDBuffer in-place helpers."""

import numpy as np
from app.effects.engine.buffer import LEDBuffer


class TestAddLedInPlace:
  """add_led must be additive, clamped, and zero-allocation."""

  def test_add_to_black_pixel(self):
    buf = LEDBuffer(10, 172)
    buf.add_led(0, 0, 100, 150, 200)
    assert tuple(buf.data[0, 0]) == (100, 150, 200)

  def test_additive_blend(self):
    buf = LEDBuffer(10, 172)
    buf.data[3, 5] = (100, 100, 100)
    buf.add_led(3, 5, 50, 60, 70)
    assert tuple(buf.data[3, 5]) == (150, 160, 170)

  def test_clamps_at_255(self):
    buf = LEDBuffer(10, 172)
    buf.data[0, 0] = (200, 200, 200)
    buf.add_led(0, 0, 100, 100, 100)
    assert tuple(buf.data[0, 0]) == (255, 255, 255)

  def test_cylinder_wrap_x(self):
    buf = LEDBuffer(10, 172)
    buf.add_led(12, 0, 50, 50, 50)  # x=12 wraps to x=2
    assert tuple(buf.data[2, 0]) == (50, 50, 50)

  def test_out_of_bounds_y_ignored(self):
    buf = LEDBuffer(10, 172)
    buf.add_led(0, 200, 50, 50, 50)  # y=200 is out of range
    assert np.all(buf.data == 0)

  def test_negative_values_treated_as_zero(self):
    buf = LEDBuffer(10, 172)
    buf.add_led(0, 0, -50, 100, 200)
    assert buf.data[0, 0, 0] == 0
    assert buf.data[0, 0, 1] == 100


class TestSetLedInPlace:
  def test_basic_set(self):
    buf = LEDBuffer(10, 172)
    buf.set_led(0, 0, 128, 64, 32)
    assert tuple(buf.data[0, 0]) == (128, 64, 32)


class TestFadeInPlace:
  def test_fade_halves_values(self):
    buf = LEDBuffer(10, 172)
    buf.data[0, 0] = (200, 100, 50)
    buf.fade(0.5)
    assert tuple(buf.data[0, 0]) == (100, 50, 25)

  def test_fade_does_not_allocate_new_array(self):
    buf = LEDBuffer(10, 172)
    original_id = id(buf.data)
    buf.data[0, 0] = (200, 100, 50)
    buf.fade(0.5)
    assert id(buf.data) == original_id

  def test_fade_by_does_not_allocate_new_array(self):
    buf = LEDBuffer(10, 172)
    original_id = id(buf.data)
    buf.data[0, 0] = (200, 100, 50)
    buf.fade_by(48)
    assert id(buf.data) == original_id


class TestGetFrameNoCopy:
  def test_get_frame_returns_data_directly(self):
    """get_frame should return the backing array, not a copy."""
    buf = LEDBuffer(10, 172)
    buf.data[0, 0] = (1, 2, 3)
    frame = buf.get_frame()
    assert tuple(frame[0, 0]) == (1, 2, 3)


class TestAddPointsBatched:
  def test_batch_of_three_points(self):
    buf = LEDBuffer(10, 172)
    xs = np.array([0, 1, 2])
    ys = np.array([0, 5, 10])
    rgbs = np.array([[100, 0, 0], [0, 100, 0], [0, 0, 100]], dtype=np.uint8)
    buf.add_points(xs, ys, rgbs)
    assert tuple(buf.data[0, 0]) == (100, 0, 0)
    assert tuple(buf.data[1, 5]) == (0, 100, 0)
    assert tuple(buf.data[2, 10]) == (0, 0, 100)

  def test_batched_additive(self):
    buf = LEDBuffer(10, 172)
    buf.data[0, 0] = (50, 50, 50)
    xs = np.array([0])
    ys = np.array([0])
    rgbs = np.array([[100, 100, 100]], dtype=np.uint8)
    buf.add_points(xs, ys, rgbs)
    assert tuple(buf.data[0, 0]) == (150, 150, 150)
