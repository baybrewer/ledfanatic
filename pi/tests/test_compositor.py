import numpy as np
from app.core.compositor import blend, BLEND_MODES


def _frame(r, g, b, w=4, h=4):
  f = np.zeros((w, h, 3), dtype=np.uint8)
  f[:, :] = [r, g, b]
  return f


class TestBlendModes:
  def test_normal_full_opacity(self):
    result = blend(_frame(255, 0, 0), _frame(0, 0, 255), 1.0, 'normal')
    assert np.array_equal(result[0, 0], [0, 0, 255])

  def test_normal_half_opacity(self):
    result = blend(_frame(200, 0, 0), _frame(0, 0, 200), 0.5, 'normal')
    assert result[0, 0, 0] == 100
    assert result[0, 0, 2] == 100

  def test_normal_zero_opacity(self):
    base = _frame(255, 0, 0)
    result = blend(base, _frame(0, 0, 255), 0.0, 'normal')
    assert np.array_equal(result, base)

  def test_add_full_opacity(self):
    result = blend(_frame(100, 50, 0), _frame(100, 50, 200), 1.0, 'add')
    assert result[0, 0, 0] == 200
    assert result[0, 0, 2] == 200

  def test_add_clamps(self):
    result = blend(_frame(200, 0, 0), _frame(200, 0, 0), 1.0, 'add')
    assert result[0, 0, 0] == 255

  def test_add_half_opacity(self):
    result = blend(_frame(100, 0, 0), _frame(100, 0, 0), 0.5, 'add')
    assert 148 <= result[0, 0, 0] <= 152

  def test_screen_full_opacity(self):
    result = blend(_frame(128, 0, 0), _frame(128, 0, 0), 1.0, 'screen')
    assert 190 <= result[0, 0, 0] <= 194

  def test_screen_half_opacity(self):
    result = blend(_frame(128, 0, 0), _frame(128, 0, 0), 0.5, 'screen')
    assert 158 <= result[0, 0, 0] <= 162

  def test_multiply_full_opacity(self):
    result = blend(_frame(128, 255, 0), _frame(128, 128, 0), 1.0, 'multiply')
    assert 63 <= result[0, 0, 0] <= 65

  def test_multiply_half_opacity(self):
    result = blend(_frame(128, 0, 0), _frame(128, 0, 0), 0.5, 'multiply')
    assert 94 <= result[0, 0, 0] <= 98

  def test_max_full_opacity(self):
    result = blend(_frame(100, 200, 50), _frame(200, 100, 50), 1.0, 'max')
    assert result[0, 0, 0] == 200
    assert result[0, 0, 1] == 200

  def test_max_half_opacity(self):
    result = blend(_frame(100, 0, 0), _frame(200, 0, 0), 0.5, 'max')
    assert 148 <= result[0, 0, 0] <= 152

  def test_unknown_mode_falls_back_to_normal(self):
    result = blend(_frame(255, 0, 0), _frame(0, 0, 255), 1.0, 'bogus')
    assert np.array_equal(result[0, 0], [0, 0, 255])
