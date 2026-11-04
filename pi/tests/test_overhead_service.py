"""
OverheadService command semantics against a real Renderer + StateManager.

Only the USB transport is faked (it records what would go to the Teensy).
"""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

import numpy as np
import pytest

from app.core.brightness import BrightnessEngine
from app.core.display_control import DisplayController
from app.core.overhead import OverheadService, OverheadCommand
from app.core.renderer import Renderer, RenderState
from app.core.state import StateManager
from app.effects.catalog import EffectCatalogService, EffectMeta
from app.effects.overhead import OVERHEAD_EFFECTS
from app.effects.registry import ALL_EFFECTS
from app.layout import load_layout, compile_layout


def _run(coro):
  return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture
def env(tmp_path):
  layout = compile_layout(load_layout(Path("config")))
  render_state = RenderState()
  brightness = BrightnessEngine({'manual_cap': 0.8})
  transport = MagicMock()
  transport.send_frame = AsyncMock(return_value=True)
  transport.send_blackout = AsyncMock(return_value=True)
  transport.send_brightness = AsyncMock(return_value=True)
  state_manager = StateManager(config_dir=tmp_path)
  state_manager.load()
  renderer = Renderer(transport, render_state, brightness, layout)
  renderer.state_manager = state_manager
  for name, cls in ALL_EFFECTS.items():
    renderer.register_effect(name, cls)
  catalog = EffectCatalogService()
  for name, cls in OVERHEAD_EFFECTS.items():
    catalog.register_imported(name, EffectMeta.from_effect_class(name, cls, group='overhead'))
  display = DisplayController(renderer, render_state, state_manager, brightness, transport)
  service = OverheadService(display, catalog)
  return service, display, transport


def test_on_with_no_history_starts_white(env):
  service, display, _ = env
  state = _run(service.apply(OverheadCommand(on=True)))
  assert state.on and state.effect == 'oh_white'
  assert state.color_mode == 'color_temp'
  assert display.state_manager.last_overhead_effect == 'oh_white'


def test_off_then_on_resumes_last_effect(env):
  service, display, transport = env
  _run(service.apply(OverheadCommand(effect='oh_caustics')))
  state = _run(service.apply(OverheadCommand(on=False)))
  assert not state.on and display.render_state.blackout
  assert display.state_manager.blackout  # persisted
  transport.send_blackout.assert_awaited_with(True)
  state = _run(service.apply(OverheadCommand(on=True)))
  assert state.on and state.effect == 'oh_caustics'
  assert not display.render_state.blackout


def test_off_during_festival_scene_is_noop(env):
  service, display, transport = env
  display.activate_and_persist('rainbow_rotate', None)
  state = _run(service.apply(OverheadCommand(on=False)))
  assert not state.on
  assert not display.render_state.blackout
  assert display.render_state.current_scene == 'rainbow_rotate'
  transport.send_blackout.assert_not_awaited()


def test_kelvin_switches_to_white_and_clamps(env):
  service, _, _ = env
  _run(service.apply(OverheadCommand(effect='oh_oil_slick')))
  state = _run(service.apply(OverheadCommand(on=True, kelvin=99999)))
  assert state.effect == 'oh_white'
  assert state.kelvin == 6500
  assert state.params['kelvin'] == 6500


def test_rgb_switches_to_color(env):
  service, _, _ = env
  state = _run(service.apply(OverheadCommand(on=True, rgb=(0, 0, 255))))
  assert state.effect == 'oh_color'
  assert state.rgb == (0, 0, 255)
  assert state.color_mode == 'rgb'


def test_effect_plus_color_keeps_effect_and_stores_color(env):
  service, _, _ = env
  state = _run(service.apply(OverheadCommand(on=True, effect='oh_prism_waves', rgb=(10, 20, 30))))
  assert state.effect == 'oh_prism_waves'
  assert state.rgb == (10, 20, 30)  # remembered for later "Color"


def test_unknown_effect_rejected(env):
  service, _, _ = env
  with pytest.raises(ValueError):
    _run(service.apply(OverheadCommand(effect='rainbow_rotate')))


def test_brightness_sets_cap_without_teensy(env):
  service, display, transport = env
  state = _run(service.apply(OverheadCommand(on=True, brightness=0.4)))
  assert state.brightness == pytest.approx(0.4)
  assert display.brightness_engine.manual_cap == pytest.approx(0.4)
  transport.send_brightness.assert_not_awaited()


def test_brightness_in_off_command_ignored(env):
  service, display, _ = env
  _run(service.apply(OverheadCommand(on=True)))
  _run(service.apply(OverheadCommand(on=False, brightness=0.1)))
  assert display.brightness_engine.manual_cap == pytest.approx(0.8)


def test_params_clamped_and_unknown_dropped(env):
  service, _, _ = env
  state = _run(service.apply(OverheadCommand(
    effect='oh_liquid_rainbow', params={'speed': 99, 'saturation': -1, 'bogus': 5})))
  assert state.params['speed'] == pytest.approx(3.0)
  assert state.params['saturation'] == pytest.approx(0.0)
  assert 'bogus' not in state.params


def test_bad_color_param_dropped(env):
  service, _, _ = env
  state = _run(service.apply(OverheadCommand(effect='oh_color', params={'color': 'red'})))
  assert state.params['color'].startswith('#')


def test_diagnostic_pattern_respects_brightness_cap(env):
  service, display, transport = env
  renderer = display.renderer
  display.activate_and_persist('oh_white', None)
  display.set_manual_brightness(0.25)
  renderer._test_mode = "strip_identify"
  renderer._test_strip_until = float('inf')
  _run(renderer._render_frame())
  frame = renderer._last_logical_frame
  assert frame.max() <= int(255 * 0.25) + 1
