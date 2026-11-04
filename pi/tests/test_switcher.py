"""Tests for Animation Switcher runtime playlist updates and sanitization."""

import numpy as np
import pytest

from app.effects.switcher import AnimationSwitcher


class FakeEffect:
  """Minimal effect stub for switcher tests."""
  def __init__(self, width, height, params=None):
    self.width = width
    self.height = height
    self.params = params or {}

  def render(self, t, state):
    return np.zeros((self.width, self.height, 3), dtype=np.uint8)


REGISTRY = {
  'twinkle': FakeEffect,
  'fire': FakeEffect,
  'plasma': FakeEffect,
  'animation_switcher': FakeEffect,
  'diag_sweep': FakeEffect,
  'diag_strip_identify': FakeEffect,
}


def _make_switcher(playlist=None):
  return AnimationSwitcher(
    width=10,
    height=172,
    params={
      'interval': 15,
      'fade_duration': 2.0,
      '_effect_registry': REGISTRY,
      'playlist': playlist if playlist is not None else [],
    },
  )


class TestRuntimePlaylistUpdate:
  def test_update_playlist_changes_rotation(self):
    s = _make_switcher(playlist=['twinkle', 'fire'])
    s.update_params({'playlist': ['plasma']})
    assert s._playlist == ['plasma']

  def test_update_playlist_resets_index(self):
    s = _make_switcher(playlist=['twinkle', 'fire', 'plasma'])
    s._current_idx = 2
    s.update_params({'playlist': ['fire', 'plasma']})
    assert s._current_idx == 0

  def test_update_interval_no_playlist_reset(self):
    s = _make_switcher(playlist=['twinkle', 'fire'])
    s._current_idx = 1
    s.update_params({'interval': 30})
    assert s._current_idx == 1
    assert s._interval == 30

  def test_update_empty_playlist_clears(self):
    s = _make_switcher(playlist=['twinkle'])
    s.update_params({'playlist': []})
    assert s._playlist == []

  def test_update_same_playlist_does_not_reset_index(self):
    s = _make_switcher(playlist=['twinkle', 'fire', 'plasma'])
    s._current_idx = 2
    s.update_params({'playlist': ['twinkle', 'fire', 'plasma']})
    assert s._current_idx == 2


class TestEmptyPlaylist:
  def test_empty_playlist_renders_black(self):
    s = _make_switcher(playlist=[])
    frame = s.render(0.0, None)
    assert frame.shape == (10, 172, 3)
    assert frame.sum() == 0


class TestSanitization:
  def test_init_strips_unknown_effect_names(self):
    s = _make_switcher(playlist=['twinkle', 'nonexistent', 'fire'])
    assert s._playlist == ['twinkle', 'fire']

  def test_update_strips_unknown_effect_names(self):
    s = _make_switcher(playlist=['twinkle'])
    s.update_params({'playlist': ['fire', 'does_not_exist', 'plasma']})
    assert s._playlist == ['fire', 'plasma']

  def test_all_unknown_becomes_empty(self):
    s = _make_switcher(playlist=['twinkle'])
    s.update_params({'playlist': ['unknown_a', 'unknown_b']})
    assert s._playlist == []


class TestStatus:
  def test_get_switcher_status_shape(self):
    s = _make_switcher(playlist=['twinkle', 'fire'])
    status = s.get_switcher_status()
    assert status['active'] is True
    assert 'current' in status
    assert 'playlist' in status
    assert 'interval' in status
    assert 'time_remaining' in status


# --- Scenes route default-injection tests ---

import asyncio


class _StubRenderer:
  def __init__(self):
    self.current_effect = None
    self.effect_registry = {}
    self.activated = []

  def activate_scene(self, name, params=None, media_manager=None):
    self.activated.append((name, dict(params or {})))
    self.current_effect = type('E', (), {'params': dict(params or {})})()
    return True


class _StubStateManager:
  def __init__(self):
    self._effect_params = {}
    self.current_scene = None
    self.current_params = {}

  def get_effect_params(self, name):
    return dict(self._effect_params.get(name, {}))

  def set_effect_params(self, name, params):
    self._effect_params[name] = dict(params)


def _build_route_test_deps(catalog):
  from types import SimpleNamespace
  from app.core.display_control import DisplayController
  renderer = _StubRenderer()
  state_manager = _StubStateManager()
  return SimpleNamespace(
    renderer=renderer,
    state_manager=state_manager,
    effect_catalog=catalog,
    display=DisplayController(renderer, None, state_manager, None, None),
  )


def _get_activate_handler(deps):
  from app.api.routes.scenes import create_router
  async def noop_broadcast():
    pass
  def noop_auth():
    return None
  router = create_router(deps, noop_auth, noop_broadcast)
  for route in router.routes:
    if getattr(route, 'path', None) == '/api/scenes/activate':
      return route.endpoint
  raise RuntimeError("activate endpoint not found")


def _build_catalog():
  from app.effects.catalog import EffectCatalogService, EffectMeta
  svc = EffectCatalogService()
  svc._catalog['twinkle'] = EffectMeta(name='twinkle', label='Twinkle', group='generative', description='')
  svc._catalog['fire'] = EffectMeta(name='fire', label='Fire', group='generative', description='')
  svc._catalog['animation_switcher'] = EffectMeta(
    name='animation_switcher', label='Animation Switcher', group='special', description=''
  )
  return svc


def test_first_activation_injects_default_playlist():
  catalog = _build_catalog()
  deps = _build_route_test_deps(catalog)
  handler = _get_activate_handler(deps)

  from app.api.schemas import SceneRequest
  req = SceneRequest(effect='animation_switcher', params=None)
  result = asyncio.run(handler(req))

  assert result['status'] == 'ok'
  injected = result['params'].get('playlist', [])
  # Default should contain regular effects, exclude switcher itself
  assert 'twinkle' in injected
  assert 'fire' in injected
  assert 'animation_switcher' not in injected
  # Persistence
  saved = deps.state_manager.get_effect_params('animation_switcher')
  assert saved.get('playlist') == injected


def test_explicit_empty_playlist_saves_empty():
  catalog = _build_catalog()
  deps = _build_route_test_deps(catalog)
  handler = _get_activate_handler(deps)

  from app.api.schemas import SceneRequest
  req = SceneRequest(effect='animation_switcher', params={'playlist': [], 'interval': 10})
  result = asyncio.run(handler(req))

  assert result['params']['playlist'] == []
  saved = deps.state_manager.get_effect_params('animation_switcher')
  assert saved.get('playlist') == []


def test_restore_empty_playlist_does_not_reinject():
  """After user explicitly saved empty playlist, activate-without-params should NOT re-inject default."""
  catalog = _build_catalog()
  deps = _build_route_test_deps(catalog)
  # Pre-populate saved state with empty playlist
  deps.state_manager.set_effect_params('animation_switcher', {'playlist': [], 'interval': 10})
  handler = _get_activate_handler(deps)

  from app.api.schemas import SceneRequest
  req = SceneRequest(effect='animation_switcher', params=None)
  result = asyncio.run(handler(req))

  # Should restore empty playlist, not re-inject default
  assert result['params'].get('playlist') == []
