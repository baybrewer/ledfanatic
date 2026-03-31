"""Tests for the brightness engine."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from app.core.brightness import BrightnessEngine, SolarPhase

# San Francisco, known date for predictable sunrise/sunset
LAT = 37.7749
LON = -122.4194
TZ_NAME = "America/Los_Angeles"
TZ = ZoneInfo(TZ_NAME)

# 2026-06-15 in SF: sunrise ~05:48, sunset ~20:34 (PDT)
DATE = datetime(2026, 6, 15, tzinfo=TZ)
NIGHT_BRIGHTNESS = 0.15
DAWN_OFFSET = 30  # minutes


def _make_engine(auto_enabled=False, manual_cap=0.8):
  return BrightnessEngine({
    'manual_cap': manual_cap,
    'auto_enabled': auto_enabled,
    'location': {
      'lat': LAT,
      'lon': LON,
      'timezone': TZ_NAME,
    },
    'solar': {
      'night_brightness': NIGHT_BRIGHTNESS,
      'dawn_offset_minutes': DAWN_OFFSET,
      'dusk_offset_minutes': DAWN_OFFSET,
    },
  })


class TestManualMode:
  def test_manual_mode_returns_cap(self):
    engine = _make_engine(auto_enabled=False, manual_cap=0.7)
    now = datetime(2026, 6, 15, 12, 0, tzinfo=TZ)
    assert engine.get_effective_brightness(now) == 0.7

  def test_manual_mode_clamps(self):
    engine = _make_engine(auto_enabled=False, manual_cap=0.5)
    engine.manual_cap = 1.5  # property setter clamps to [0, 1]
    now = datetime(2026, 6, 15, 12, 0, tzinfo=TZ)
    assert engine.get_effective_brightness(now) == 1.0


class TestAutoMode:
  def test_auto_mode_midday(self):
    engine = _make_engine(auto_enabled=True, manual_cap=1.0)
    noon = datetime(2026, 6, 15, 12, 0, tzinfo=TZ)
    effective = engine.get_effective_brightness(noon)
    # Midday: solar_factor should be 1.0
    assert effective == pytest.approx(1.0, abs=0.01)

  def test_auto_mode_midnight(self):
    engine = _make_engine(auto_enabled=True, manual_cap=1.0)
    midnight = datetime(2026, 6, 15, 0, 0, tzinfo=TZ)
    effective = engine.get_effective_brightness(midnight)
    assert effective == pytest.approx(NIGHT_BRIGHTNESS, abs=0.01)

  def test_auto_mode_dawn_start(self):
    """At sunrise - offset, solar_factor should be night_brightness."""
    engine = _make_engine(auto_enabled=True, manual_cap=1.0)
    # Get actual sunrise time to compute dawn boundaries
    sun_times = engine._get_sun_times(DATE)
    sunrise = sun_times['sunrise']
    from datetime import timedelta
    dawn_start = sunrise - timedelta(minutes=DAWN_OFFSET)
    effective = engine.get_effective_brightness(dawn_start)
    assert effective == pytest.approx(NIGHT_BRIGHTNESS, abs=0.02)

  def test_auto_mode_dawn_mid(self):
    """At sunrise (midpoint of dawn window), solar_factor ~midpoint."""
    engine = _make_engine(auto_enabled=True, manual_cap=1.0)
    sun_times = engine._get_sun_times(DATE)
    sunrise = sun_times['sunrise']
    effective = engine.get_effective_brightness(sunrise)
    midpoint = (NIGHT_BRIGHTNESS + 1.0) / 2.0
    assert effective == pytest.approx(midpoint, abs=0.02)

  def test_auto_mode_dawn_end(self):
    """At sunrise + offset, solar_factor should be 1.0."""
    engine = _make_engine(auto_enabled=True, manual_cap=1.0)
    sun_times = engine._get_sun_times(DATE)
    sunrise = sun_times['sunrise']
    from datetime import timedelta
    dawn_end = sunrise + timedelta(minutes=DAWN_OFFSET)
    effective = engine.get_effective_brightness(dawn_end)
    # dawn_end is the boundary — at dawn_end it transitions to DAY (1.0)
    assert effective == pytest.approx(1.0, abs=0.02)

  def test_auto_mode_dusk_start(self):
    """At sunset - offset, solar_factor should be 1.0."""
    engine = _make_engine(auto_enabled=True, manual_cap=1.0)
    sun_times = engine._get_sun_times(DATE)
    sunset = sun_times['sunset']
    from datetime import timedelta
    dusk_start = sunset - timedelta(minutes=DAWN_OFFSET)
    effective = engine.get_effective_brightness(dusk_start)
    assert effective == pytest.approx(1.0, abs=0.02)

  def test_auto_mode_dusk_end(self):
    """At sunset + offset, solar_factor should be night_brightness."""
    engine = _make_engine(auto_enabled=True, manual_cap=1.0)
    sun_times = engine._get_sun_times(DATE)
    sunset = sun_times['sunset']
    from datetime import timedelta
    dusk_end = sunset + timedelta(minutes=DAWN_OFFSET)
    effective = engine.get_effective_brightness(dusk_end)
    # At dusk_end boundary, transitions to NIGHT
    assert effective == pytest.approx(NIGHT_BRIGHTNESS, abs=0.02)


class TestFallback:
  def test_fallback_on_invalid_location(self):
    """Invalid lat (999) should cause astral to fail; falls back to manual_cap."""
    engine = BrightnessEngine({
      'manual_cap': 0.6,
      'auto_enabled': True,
      'location': {
        'lat': 999,
        'lon': 0,
        'timezone': TZ_NAME,
      },
    })
    now = datetime(2026, 6, 15, 12, 0, tzinfo=TZ)
    effective = engine.get_effective_brightness(now)
    # solar_factor falls back to 1.0, so min(0.6, 1.0) = 0.6
    assert effective == pytest.approx(0.6, abs=0.01)


class TestStatus:
  def test_get_status_keys(self):
    engine = _make_engine(auto_enabled=True, manual_cap=0.8)
    status = engine.get_status()
    expected_keys = {
      'manual_cap', 'auto_enabled', 'effective_brightness',
      'solar_phase', 'solar_phase_value',
    }
    assert expected_keys.issubset(status.keys())


class TestUpdateConfig:
  def test_update_config(self):
    engine = _make_engine(manual_cap=0.5)
    assert engine.manual_cap == 0.5
    engine.update_config({'manual_cap': 0.9})
    assert engine.manual_cap == 0.9


class TestSolarPhase:
  def test_phase_day(self):
    engine = _make_engine(auto_enabled=True)
    noon = datetime(2026, 6, 15, 12, 0, tzinfo=TZ)
    assert engine.get_solar_phase(noon) == SolarPhase.DAY

  def test_phase_night(self):
    engine = _make_engine(auto_enabled=True)
    midnight = datetime(2026, 6, 15, 2, 0, tzinfo=TZ)
    assert engine.get_solar_phase(midnight) == SolarPhase.NIGHT
