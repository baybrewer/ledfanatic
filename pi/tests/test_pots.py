"""Tests for pot mapping logic: deadband, hysteresis, live activation, disable."""

from app.core.pots import LIVE_INTERVAL_S, PotController, normalize_raw, select_index


MENU = [
  ('Favorites', ['fav_a', 'fav_b']),
  ('Ambient', ['amb_a', 'amb_b', 'amb_c']),
  ('Game', ['game_a']),
]

ALL_ON = {'brightness': True, 'menu': True, 'pattern': True}


def make_controller(enabled=None, menu=None):
  flags = dict(enabled or ALL_ON)
  return PotController(
    menu_provider=lambda: list(menu if menu is not None else MENU),
    enabled_provider=lambda: dict(flags),
  ), flags


def raw(b=0.0, m=0.0, p=0.0):
  return (int(b * 1023), int(m * 1023), int(p * 1023))


class TestHelpers:
  def test_normalize(self):
    assert normalize_raw(0) == 0.0
    assert normalize_raw(1023) == 1.0
    assert abs(normalize_raw(512) - 0.5005) < 0.001
    assert normalize_raw(2000) == 1.0  # clamped

  def test_select_index_basic(self):
    assert select_index(0.0, 4, None) == 0
    assert select_index(0.99, 4, None) == 3
    assert select_index(0.5, 0, None) is None

  def test_select_index_hysteresis_holds_at_boundary(self):
    # value just past the 0/1 boundary stays at current=0 within guard
    assert select_index(0.26, 4, current=0) == 0
    # well past the guard, switches
    assert select_index(0.35, 4, current=0) == 1


class TestBaseline:
  def test_first_packet_is_silent_baseline(self):
    ctl, _ = make_controller()
    events = ctl.handle_raw(raw(b=0.9, m=0.9, p=0.9), now=0.0)
    assert events == []  # boot position must not take over

  def test_static_pots_stay_silent(self):
    ctl, _ = make_controller()
    ctl.handle_raw(raw(b=0.5), now=0.0)
    for i in range(20):
      assert ctl.handle_raw(raw(b=0.5), now=0.05 * (i + 1)) == []


class TestBrightness:
  def test_movement_emits_brightness(self):
    ctl, _ = make_controller()
    ctl.handle_raw(raw(b=0.5), now=0.0)
    events = ctl.handle_raw(raw(b=0.6), now=0.05)
    assert ('brightness' in [k for k, _ in events])
    val = dict(events)['brightness']
    assert abs(val - 0.6) < 0.01

  def test_tiny_jitter_ignored(self):
    ctl, _ = make_controller()
    ctl.handle_raw(raw(b=0.5), now=0.0)
    events = ctl.handle_raw(raw(b=0.505), now=0.05)  # within 2% deadband
    assert events == []


class TestMenuAndPattern:
  def test_menu_movement_emits_overlay(self):
    ctl, _ = make_controller()
    ctl.handle_raw(raw(m=0.1), now=0.0)
    events = ctl.handle_raw(raw(m=0.5), now=0.05)  # into 'Ambient' zone
    assert ('overlay', 'Ambient') in events
    # Live activation fires in the same call now (pattern falls back to its
    # trusted absolute position: 0.0 -> index 0 of Ambient).
    assert ('activate', 'amb_a') in events

  def test_activation_immediate_on_move(self):
    ctl, _ = make_controller()
    ctl.handle_raw(raw(m=0.1, p=0.1), now=0.0)
    events = ctl.handle_raw(raw(m=0.5, p=0.1), now=0.05)  # menu -> Ambient
    activates = [v for k, v in events if k == 'activate']
    assert activates == ['amb_a']  # pattern pot at 0.1 -> index 0 of Ambient, fires immediately

  def test_sweep_rate_limited(self):
    ctl, _ = make_controller()
    ctl.handle_raw(raw(m=0.5, p=0.0), now=0.0)  # baseline
    # Sweep pattern pot through all 3 Ambient zones, 50ms apart, then hold.
    times = [0.05, 0.10, 0.15, 0.20, 0.30, 0.50, 0.70]
    positions = [0.3, 0.6, 0.95, 0.95, 0.95, 0.95, 0.95]
    calls = [(t, ctl.handle_raw(raw(m=0.5, p=p), now=t))
             for t, p in zip(times, positions)]

    activations = [(t, v) for t, evs in calls for k, v in evs if k == 'activate']
    assert len(activations) > 1  # live feel: more than one activation while turning
    for (t1, _), (t2, _) in zip(activations, activations[1:]):
      assert t2 - t1 >= LIVE_INTERVAL_S - 1e-9  # consecutive activations rate-limited
    assert activations[-1][1] == 'amb_c'  # final position always lands

  def test_no_reactivate_same_effect(self):
    ctl, _ = make_controller()
    ctl.handle_raw(raw(m=0.5, p=0.05), now=0.0)  # baseline
    first = ctl.handle_raw(raw(m=0.5, p=0.10), now=0.05)  # move within zone 0
    assert [v for k, v in first if k == 'activate'] == ['amb_a']
    # Wiggle further within the same zone, well past the rate-limit window:
    # resolved effect is unchanged, so no redundant activate.
    second = ctl.handle_raw(raw(m=0.5, p=0.15), now=0.30)
    assert not any(k == 'activate' for k, _ in second)

  def test_pattern_pot_no_overlay(self):
    ctl, _ = make_controller()
    ctl.handle_raw(raw(p=0.1), now=0.0)
    events = ctl.handle_raw(raw(p=0.8), now=0.05)
    assert not any(k == 'overlay' for k, _ in events)

  def test_empty_favorites_omitted_by_provider_contract(self):
    # Provider contract: caller omits empty favorites. Menu without favorites:
    ctl, _ = make_controller(menu=[('Ambient', ['amb_a']), ('Game', ['game_a'])])
    ctl.handle_raw(raw(m=0.1, p=0.1), now=0.0)
    events = ctl.handle_raw(raw(m=0.9, p=0.1), now=0.05)  # menu -> Game, immediate activation
    assert ('activate', 'game_a') in events


class TestDisable:
  def test_disabled_pattern_pot_inert(self):
    ctl, _ = make_controller(enabled={'brightness': True, 'menu': True, 'pattern': False})
    ctl.handle_raw(raw(m=0.5, p=0.1), now=0.0)
    ctl.handle_raw(raw(m=0.5, p=0.9), now=0.05)
    events = ctl.handle_raw(raw(m=0.5, p=0.9), now=0.5)
    assert not any(k == 'activate' for k, _ in events)

  def test_reenable_requires_new_movement(self):
    ctl, flags = make_controller(enabled={'brightness': False, 'menu': True, 'pattern': True})
    ctl.handle_raw(raw(b=0.2), now=0.0)
    ctl.handle_raw(raw(b=0.9), now=0.05)  # moved while disabled — ignored
    flags['brightness'] = True
    # Re-enabled at resting 0.9: must NOT apply until it moves again
    assert ctl.handle_raw(raw(b=0.9), now=0.1) == []
    events = ctl.handle_raw(raw(b=0.7), now=0.15)
    assert ('brightness' in [k for k, _ in events])

  def test_disabled_menu_pot_no_overlay(self):
    ctl, _ = make_controller(enabled={'brightness': True, 'menu': False, 'pattern': True})
    ctl.handle_raw(raw(m=0.1), now=0.0)
    events = ctl.handle_raw(raw(m=0.9), now=0.05)
    assert not any(k == 'overlay' for k, _ in events)

  def test_disabled_pattern_drift_does_not_leak_into_activation(self):
    # Reproduction: pattern pot gets disabled, drifts to a new resting
    # position, gets re-enabled without moving again. The drifted position
    # must not determine the activated effect — the pattern's last
    # legitimate index (none ever established here) must win, defaulting
    # to index 0 of the category.
    ctl, flags = make_controller(enabled={'brightness': True, 'menu': True, 'pattern': True})
    ctl.handle_raw(raw(m=0.1, p=0.1), now=0.0)          # baseline, all enabled
    flags['pattern'] = False
    events_menu_move = ctl.handle_raw(raw(m=0.5, p=0.1), now=0.05)  # menu -> Ambient; pattern disabled
    assert ('overlay', 'Ambient') in events_menu_move
    # Immediate activation (same call) must use pattern's trusted default
    # (index 0), not any drifted position — pattern is untrusted right now.
    assert ('activate', 'amb_a') in events_menu_move
    ctl.handle_raw(raw(m=0.5, p=0.9), now=0.10)         # pattern (disabled) drifts: silent
    flags['pattern'] = True
    events_reenable = ctl.handle_raw(raw(m=0.5, p=0.9), now=0.15)  # re-enabled, not moved
    assert not any(k == 'activate' for k, _ in events_reenable)
    events_idle = ctl.handle_raw(raw(m=0.5, p=0.9), now=0.40)  # nothing moved since; no new event
    assert not any(k == 'activate' for k, _ in events_idle)

  def test_disabled_menu_never_established_blocks_activation(self):
    ctl, _ = make_controller(enabled={'brightness': True, 'menu': False, 'pattern': True})
    ctl.handle_raw(raw(m=0.5, p=0.1), now=0.0)   # baseline (menu disabled)
    events = ctl.handle_raw(raw(m=0.5, p=0.9), now=0.05)  # pattern moves for real; menu never established
    assert not any(k == 'activate' for k, _ in events)

  def test_pattern_enabled_idle_uses_absolute_fallback(self):
    # Regression: pattern pot enabled at boot but never moved must still
    # fall back to its absolute position (trusted), not default to index 0.
    ctl, _ = make_controller()
    ctl.handle_raw(raw(m=0.1, p=0.6), now=0.0)          # baseline, pattern resting at 0.6
    events = ctl.handle_raw(raw(m=0.5, p=0.6), now=0.05)  # menu -> Ambient, pattern unmoved; immediate
    assert ('activate', 'amb_b') in events  # 0.6 -> index 1 of 3 Ambient effects

  def test_disabled_menu_reenable_without_movement_still_blocks_activation(self):
    # Menu disabled at boot (never legitimately established); pattern moves
    # -> no activate, since menu is untrusted. Menu is then re-enabled
    # without moving, so it still isn't trusted; pattern moves again ->
    # still no activate.
    ctl, flags = make_controller(enabled={'brightness': True, 'menu': False, 'pattern': True})
    ctl.handle_raw(raw(m=0.5, p=0.1), now=0.0)            # baseline, menu disabled
    events1 = ctl.handle_raw(raw(m=0.5, p=0.9), now=0.05)  # pattern moves for real; menu untrusted
    assert not any(k == 'activate' for k, _ in events1)
    flags['menu'] = True
    ctl.handle_raw(raw(m=0.5, p=0.9), now=0.55)           # menu re-enabled, not moved
    events2 = ctl.handle_raw(raw(m=0.5, p=0.2), now=0.6)  # pattern moves again; menu still untrusted
    assert not any(k == 'activate' for k, _ in events2)

  def test_status_survives_menu_shrink(self):
    menu_state = {'items': list(MENU)}
    ctl = PotController(
      menu_provider=lambda: menu_state['items'],
      enabled_provider=lambda: dict(ALL_ON),
    )
    ctl.handle_raw(raw(m=0.1, p=0.1), now=0.0)
    overlay_events = ctl.handle_raw(raw(m=0.9, p=0.1), now=0.05)  # menu -> 'Game' (last category)
    assert ('overlay', 'Game') in overlay_events
    menu_state['items'] = [('Ambient', ['amb_a'])]  # shrink below the previously-set index
    status = ctl.get_status()  # must not raise IndexError
    assert status['category'] is None


import numpy as np

from app.core.renderer import Renderer


class TestOverlayState:
  def _bare_renderer(self):
    # Renderer without transport dependency — we only exercise overlay helpers
    r = Renderer.__new__(Renderer)
    r._overlay_text = None
    r._overlay_until = 0.0
    r._overlay_img = None
    r._overlay_img_key = None
    r.overlay_region = None
    return r

  def test_set_overlay_text_arms_timer(self):
    r = self._bare_renderer()
    r.set_overlay_text('Ambient')
    assert r._overlay_text == 'Ambient'
    assert r._overlay_until > 0

  def test_apply_overlay_writes_pixels(self):
    r = self._bare_renderer()
    r.set_overlay_text('AB')
    frame = np.zeros((20, 40, 3), dtype=np.uint8)
    out = r._apply_overlay(frame, 20, 40)
    assert out.sum() > 0
    assert (out[10:] == 0).all()  # right half untouched (default region = left half)

  def test_expired_overlay_is_identity(self):
    import time as _time
    r = self._bare_renderer()
    r.set_overlay_text('AB', linger=0.0)
    r._overlay_until = _time.monotonic() - 1.0
    frame = np.full((20, 40, 3), 7, dtype=np.uint8)
    out = r._apply_overlay(frame, 20, 40)
    assert (out == frame).all()
