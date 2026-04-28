from unittest.mock import MagicMock

import numpy as np
from app.core.compositor import blend, BLEND_MODES, Layer, Compositor


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


def _make_effect_cls(r, g, b):
  """Create a simple effect class that fills with a solid color."""
  class SolidEffect:
    def __init__(self, width, height, params=None):
      self.width = width
      self.height = height
    def render(self, t, state):
      return np.full((self.width, self.height, 3), [r, g, b], dtype=np.uint8)
    def update_params(self, p):
      pass
  return SolidEffect


class TestLayer:
  def test_layer_creation(self):
    layer = Layer(effect_name='rainbow_rotate', params={'speed': 0.5})
    assert layer.effect_name == 'rainbow_rotate'
    assert layer.opacity == 1.0
    assert layer.blend_mode == 'normal'
    assert layer.enabled is True

  def test_layer_to_dict(self):
    layer = Layer(effect_name='fire', params={'cooling': 55}, opacity=0.7, blend_mode='add')
    d = layer.to_dict()
    assert d['effect_name'] == 'fire'
    assert d['opacity'] == 0.7
    assert d['blend_mode'] == 'add'


class TestCompositor:
  def _make_compositor(self, width=10, height=20):
    registry = {
      'solid_red': _make_effect_cls(255, 0, 0),
      'solid_blue': _make_effect_cls(0, 0, 255),
    }
    return Compositor(width, height, registry)

  def test_empty_returns_black(self):
    comp = self._make_compositor()
    frame = comp.render(0, MagicMock())
    assert frame.shape == (10, 20, 3)
    assert np.all(frame == 0)

  def test_single_layer(self):
    comp = self._make_compositor()
    comp.add_layer(Layer(effect_name='solid_red'))
    frame = comp.render(0, MagicMock())
    assert frame[0, 0, 0] == 255
    assert frame[0, 0, 2] == 0

  def test_two_layers_add(self):
    comp = self._make_compositor()
    comp.add_layer(Layer(effect_name='solid_red'))
    comp.add_layer(Layer(effect_name='solid_blue', blend_mode='add'))
    frame = comp.render(0, MagicMock())
    assert frame[0, 0, 0] == 255
    assert frame[0, 0, 2] == 255

  def test_disabled_layer_skipped(self):
    comp = self._make_compositor()
    comp.add_layer(Layer(effect_name='solid_red'))
    comp.add_layer(Layer(effect_name='solid_blue', enabled=False))
    frame = comp.render(0, MagicMock())
    assert frame[0, 0, 0] == 255
    assert frame[0, 0, 2] == 0

  def test_opacity(self):
    comp = self._make_compositor()
    comp.add_layer(Layer(effect_name='solid_red'))
    comp.add_layer(Layer(effect_name='solid_blue', opacity=0.5))
    frame = comp.render(0, MagicMock())
    # normal blend at 0.5: red*0.5 + blue*0.5
    assert 125 <= frame[0, 0, 0] <= 130
    assert 125 <= frame[0, 0, 2] <= 130

  def test_remove_layer(self):
    comp = self._make_compositor()
    comp.add_layer(Layer(effect_name='solid_red'))
    assert len(comp.layers) == 1
    comp.remove_layer(0)
    assert len(comp.layers) == 0

  def test_reorder_layer(self):
    comp = self._make_compositor()
    comp.add_layer(Layer(effect_name='solid_red'))
    comp.add_layer(Layer(effect_name='solid_blue'))
    comp.move_layer(1, 0)
    assert comp.layers[0].effect_name == 'solid_blue'
    assert comp.layers[1].effect_name == 'solid_red'

  def test_crashing_layer_isolated_other_layers_survive(self):
    """A crashing layer must not prevent healthy layers from rendering."""
    comp = self._make_compositor()
    comp.add_layer(Layer(effect_name='solid_red'))                       # layer 0: healthy
    comp.add_layer(Layer(effect_name='solid_blue'))                      # layer 1: will crash
    comp.add_layer(Layer(effect_name='solid_red', blend_mode='add'))     # layer 2: healthy
    # Inject crasher into layer 1 only
    class Crasher:
      def __init__(self, *a, **kw): pass
      def render(self, t, state): raise RuntimeError("boom")
      def update_params(self, p): pass
    comp._effect_instances[1] = Crasher()
    frame = comp.render(0, MagicMock())
    assert frame.shape == (10, 20, 3)
    # Layer 0 (red) + layer 2 (red, add) should produce red=255
    # Layer 1 crash is skipped, blue absent
    assert frame[0, 0, 0] == 255  # red from layers 0+2
    assert frame[0, 0, 2] == 0    # no blue -- crasher skipped

  def test_compositor_ms_tracked(self):
    comp = self._make_compositor()
    comp.add_layer(Layer(effect_name='solid_red'))
    comp.render(0, MagicMock())
    assert comp.compositor_ms >= 0

  def test_apply_layout_recreates_instances(self):
    comp = self._make_compositor(width=10, height=20)
    comp.add_layer(Layer(effect_name='solid_red'))
    frame1 = comp.render(0, MagicMock())
    assert frame1.shape == (10, 20, 3)
    # Change layout
    comp.apply_layout(5, 40)
    frame2 = comp.render(0, MagicMock())
    assert frame2.shape == (5, 40, 3)

  def test_to_dict(self):
    comp = self._make_compositor()
    comp.add_layer(Layer(effect_name='solid_red', opacity=0.8))
    comp.add_layer(Layer(effect_name='solid_blue', blend_mode='add'))
    d = comp.to_dict()
    assert len(d['layers']) == 2
    assert d['layers'][0]['opacity'] == 0.8
    assert d['layers'][1]['blend_mode'] == 'add'

  def test_from_dict_round_trip(self):
    comp = self._make_compositor()
    comp.add_layer(Layer(effect_name='solid_red', opacity=0.8))
    comp.add_layer(Layer(effect_name='solid_blue', blend_mode='add', enabled=False))
    d = comp.to_dict()
    comp2 = Compositor.from_dict(d, 10, 20, {
      'solid_red': _make_effect_cls(255, 0, 0),
      'solid_blue': _make_effect_cls(0, 0, 255),
    })
    assert len(comp2.layers) == 2
    assert comp2.layers[0].effect_name == 'solid_red'
    assert comp2.layers[0].opacity == 0.8
    assert comp2.layers[1].blend_mode == 'add'
    assert comp2.layers[1].enabled is False
    # Render should work (only layer 0 is enabled)
    frame = comp2.render(0, MagicMock())
    assert frame.shape == (10, 20, 3)
