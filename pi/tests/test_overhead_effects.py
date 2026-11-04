"""Overhead (room lighting) effects: shape, param ranges, perf, catalog wiring."""

import time

import numpy as np
import pytest

from app.effects.catalog import EffectCatalogService, EffectMeta, display_category
from app.effects.engine.color import hsv_to_rgb_grid, kelvin_to_rgb
from app.effects.overhead import OVERHEAD_EFFECTS
from app.effects.registry import ALL_EFFECTS

EXPECTED_NAMES = {
  'oh_white', 'oh_color', 'oh_liquid_rainbow', 'oh_rainbow_ink', 'oh_oil_slick',
  'oh_prism_waves', 'oh_rainbow_lava', 'oh_color_drift', 'oh_caustics',
  'oh_night_light',
}
ANIMATED = sorted(n for n in EXPECTED_NAMES if n not in ('oh_white', 'oh_color'))
HEIGHT = 83


def _run(cls, width, params=None, frames=300):
  effect = cls(width, HEIGHT, params or {})
  frame = None
  for i in range(frames):
    frame = effect.render(100.0 + i / 60.0, None)
    assert frame.shape == (width, HEIGHT, 3)
    assert frame.dtype == np.uint8
  return frame


def test_all_ten_registered():
  assert set(OVERHEAD_EFFECTS) == EXPECTED_NAMES
  for name, cls in OVERHEAD_EFFECTS.items():
    assert ALL_EFFECTS[name] is cls
    assert cls.CATEGORY == 'overhead'


@pytest.mark.parametrize('width', [10, 20])
@pytest.mark.parametrize('name', sorted(EXPECTED_NAMES))
def test_shape_dtype_300_frames(name, width):
  _run(OVERHEAD_EFFECTS[name], width)


@pytest.mark.parametrize('speed', [0.1, 3.0])
@pytest.mark.parametrize('name', ANIMATED)
def test_speed_extremes(name, speed):
  _run(OVERHEAD_EFFECTS[name], 20, {'speed': speed}, frames=120)


@pytest.mark.parametrize('saturation', [0.0, 1.0])
@pytest.mark.parametrize('name', ANIMATED)
def test_saturation_extremes(name, saturation):
  frame = _run(OVERHEAD_EFFECTS[name], 20, {'saturation': saturation}, frames=60)
  if saturation == 0.0:
    # Zero saturation must read as a near-neutral wash, not a color
    spread = frame.astype(int).max(axis=2) - frame.astype(int).min(axis=2)
    assert spread.max() <= 8


@pytest.mark.parametrize('name', ANIMATED)
def test_live_param_update_no_jump(name):
  effect = OVERHEAD_EFFECTS[name](20, HEIGHT, {})
  prev = None
  for i in range(120):
    if i == 60:
      effect.update_params({'speed': 3.0})
    frame = effect.render(i / 60.0, None).astype(int)
    if prev is not None:
      # Phase is integrated, so a speed change never teleports the animation
      assert np.abs(frame - prev).max() <= 40
    prev = frame


def test_white_follows_kelvin():
  effect = OVERHEAD_EFFECTS['oh_white'](10, HEIGHT, {'kelvin': 2700})
  assert tuple(effect.render(0.0, None)[0, 0]) == kelvin_to_rgb(2700)
  effect.update_params({'kelvin': 6500})
  assert tuple(effect.render(0.1, None)[3, 40]) == kelvin_to_rgb(6500)


def test_color_hex_and_fallback():
  effect = OVERHEAD_EFFECTS['oh_color'](10, HEIGHT, {})
  assert tuple(effect.render(0.0, None)[0, 0]) == (0xFF, 0x8C, 0x3C)
  effect.update_params({'color': '#0010FF'})
  assert tuple(effect.render(0.1, None)[5, 5]) == (0x00, 0x10, 0xFF)
  effect.update_params({'color': 'not-a-color'})
  assert tuple(effect.render(0.2, None)[5, 5]) == (0xFF, 0x8C, 0x3C)


def test_night_light_stays_dim():
  frame = _run(OVERHEAD_EFFECTS['oh_night_light'], 20, {'saturation': 1.0})
  assert frame.max() <= 64


def test_kelvin_reference_points():
  warm = kelvin_to_rgb(2700)
  assert warm[0] == 255
  assert warm[0] > warm[1] > warm[2]
  assert 150 <= warm[1] <= 180 and 70 <= warm[2] <= 100
  neutral = kelvin_to_rgb(6500)
  assert min(neutral) >= 245
  assert kelvin_to_rgb(500) == kelvin_to_rgb(1000)
  assert kelvin_to_rgb(99999) == kelvin_to_rgb(40000)


def test_hsv_to_rgb_grid_primaries_and_broadcast():
  hues = np.array([0.0, 1 / 3, 2 / 3])
  rgb = hsv_to_rgb_grid(hues, 1.0, 1.0)
  assert rgb.dtype == np.uint8
  assert rgb.tolist() == [[255, 0, 0], [0, 255, 0], [0, 0, 255]]
  grid = hsv_to_rgb_grid(np.zeros((4, 5)), 0.0, 0.5)
  assert grid.shape == (4, 5, 3)
  assert (grid == 128).all()


def test_catalog_metadata():
  # Mirrors main.py's registration loop for overhead effects
  catalog = EffectCatalogService()
  for name, cls in OVERHEAD_EFFECTS.items():
    catalog.register_imported(name, EffectMeta.from_effect_class(name, cls, group='overhead'))
  for name in EXPECTED_NAMES:
    meta = catalog.get_meta(name)
    assert meta.group == 'overhead'
    assert display_category(meta.group) == 'Overhead'
    assert meta.params
  color_params = catalog.get_meta('oh_color').params
  assert color_params[0]['type'] == 'color'
  assert color_params[0]['default'] == '#FF8C3C'
  for name in ANIMATED:
    names = {p['name']: p for p in catalog.get_meta(name).params}
    assert names['speed']['type'] == 'slider'
    assert (names['speed']['min'], names['speed']['max']) == (0.1, 3.0)
    assert (names['saturation']['min'], names['saturation']['max']) == (0.0, 1.0)
  categories = dict(catalog.get_display_categories())
  assert set(categories['Overhead']) == EXPECTED_NAMES


@pytest.mark.parametrize('name', sorted(EXPECTED_NAMES))
def test_render_budget(name):
  effect = OVERHEAD_EFFECTS[name](20, HEIGHT, {})
  for i in range(10):
    effect.render(i / 60.0, None)
  start = time.perf_counter()
  frames = 100
  for i in range(frames):
    effect.render((10 + i) / 60.0, None)
  avg_ms = (time.perf_counter() - start) / frames * 1000.0
  assert avg_ms < 3.0, f"{name} {avg_ms:.2f} ms/frame"
