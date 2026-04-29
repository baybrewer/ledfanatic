"""
Brightness engine.

Provides manual brightness capping and optional solar-aware auto-dimming
based on sunrise/sunset calculations from the astral library.
Pure computation — no side effects, fully testable.
"""

import logging
from datetime import datetime, timedelta, timezone
from enum import IntEnum
from typing import Optional
from zoneinfo import ZoneInfo

from astral import LocationInfo
from astral.sun import sun

logger = logging.getLogger(__name__)


class SolarPhase(IntEnum):
  NIGHT = 0
  DAWN = 1
  DAY = 2
  DUSK = 3


DEFAULT_CONFIG = {
  'manual_cap': 0.8,
  'auto_enabled': False,
  'location': {
    'lat': 37.7749,
    'lon': -122.4194,
    'timezone': 'America/Los_Angeles',
  },
  'solar': {
    'night_brightness': 0.60,
    'dawn_offset_minutes': 30,
    'dusk_offset_minutes': 30,
  },
}


class BrightnessEngine:
  """Calculates effective brightness from manual cap and optional solar phase."""

  def __init__(self, config: Optional[dict] = None):
    self._config = _deep_merge(DEFAULT_CONFIG, config or {})

  @property
  def manual_cap(self) -> float:
    return self._config['manual_cap']

  @manual_cap.setter
  def manual_cap(self, value: float):
    self._config['manual_cap'] = _clamp(value, 0.0, 1.0)

  @property
  def auto_enabled(self) -> bool:
    return self._config['auto_enabled']

  def update_config(self, config: dict):
    """Update settings at runtime. Merges into existing config."""
    self._config = _deep_merge(self._config, config)
    # Enforce manual_cap bounds after merge
    self._config['manual_cap'] = _clamp(self._config['manual_cap'], 0.0, 1.0)

  def get_effective_brightness(self, now: datetime) -> float:
    """Return the brightness value to apply right now.

    When auto mode is enabled, returns min(manual_cap, solar_factor).
    When auto mode is disabled, returns manual_cap.
    """
    cap = self._config['manual_cap']

    if not self._config['auto_enabled']:
      return cap

    solar_factor = self._compute_solar_factor(now)
    return min(cap, solar_factor)

  def get_solar_phase(self, now: datetime) -> SolarPhase:
    """Determine the current solar phase for the given time."""
    try:
      dawn_start, dawn_end, dusk_start, dusk_end = self._get_phase_boundaries(now)
    except ValueError:
      # Polar region or other astral calculation failure
      return SolarPhase.DAY

    aware_now = _ensure_aware(now, self._get_tz())

    if dawn_start <= aware_now < dawn_end:
      return SolarPhase.DAWN
    elif dawn_end <= aware_now < dusk_start:
      return SolarPhase.DAY
    elif dusk_start <= aware_now < dusk_end:
      return SolarPhase.DUSK
    else:
      return SolarPhase.NIGHT

  def get_status(self) -> dict:
    """Return current state for API consumption."""
    now = datetime.now(timezone.utc)
    phase = self.get_solar_phase(now)

    status = {
      'manual_cap': self._config['manual_cap'],
      'auto_enabled': self._config['auto_enabled'],
      'effective_brightness': self.get_effective_brightness(now),
      'solar_phase': phase.name,
      'solar_phase_value': int(phase),
    }

    # Always include solar config so UI can show/edit night brightness
    status['solar'] = self._config['solar'].copy()
    if self._config['auto_enabled']:
      status['solar_factor'] = self._compute_solar_factor(now)
      status['location'] = self._config['location'].copy()

    return status

  # -- internal helpers --

  def _get_tz(self) -> ZoneInfo:
    return ZoneInfo(self._config['location']['timezone'])

  def _get_location_info(self) -> LocationInfo:
    loc = self._config['location']
    return LocationInfo(
      name="ledfanatic",
      region="",
      timezone=loc['timezone'],
      latitude=loc['lat'],
      longitude=loc['lon'],
    )

  def _get_sun_times(self, now: datetime) -> dict:
    """Get sunrise/sunset for the date of `now` in the configured timezone.

    Raises ValueError if astral cannot compute (e.g. polar regions).
    """
    tz = self._get_tz()
    local_date = _ensure_aware(now, tz).date()
    location = self._get_location_info()
    return sun(location.observer, date=local_date, tzinfo=tz)

  def _get_phase_boundaries(self, now: datetime):
    """Return (dawn_start, dawn_end, dusk_start, dusk_end) as aware datetimes.

    Raises ValueError on astral calculation failure.
    """
    sun_times = self._get_sun_times(now)
    sunrise = sun_times['sunrise']
    sunset = sun_times['sunset']

    dawn_offset = timedelta(minutes=self._config['solar']['dawn_offset_minutes'])
    dusk_offset = timedelta(minutes=self._config['solar']['dusk_offset_minutes'])

    dawn_start = sunrise - dawn_offset
    dawn_end = sunrise + dawn_offset
    dusk_start = sunset - dusk_offset
    dusk_end = sunset + dusk_offset

    return dawn_start, dawn_end, dusk_start, dusk_end

  def _compute_solar_factor(self, now: datetime) -> float:
    """Compute brightness factor (0.0-1.0) based on solar position.

    Returns 1.0 on calculation failure (graceful fallback).
    """
    try:
      dawn_start, dawn_end, dusk_start, dusk_end = self._get_phase_boundaries(now)
    except ValueError:
      logger.warning("Solar calculation failed; falling back to manual cap")
      return 1.0

    night_brightness = self._config['solar']['night_brightness']
    aware_now = _ensure_aware(now, self._get_tz())

    if dawn_start <= aware_now < dawn_end:
      # Linear interpolation: night_brightness -> 1.0 during dawn
      progress = _safe_progress(aware_now, dawn_start, dawn_end)
      return _lerp(night_brightness, 1.0, progress)

    elif dawn_end <= aware_now < dusk_start:
      return 1.0

    elif dusk_start <= aware_now < dusk_end:
      # Linear interpolation: 1.0 -> night_brightness during dusk
      progress = _safe_progress(aware_now, dusk_start, dusk_end)
      return _lerp(1.0, night_brightness, progress)

    else:
      return night_brightness


# -- module-level pure helpers --

def _clamp(value: float, low: float, high: float) -> float:
  return max(low, min(high, value))


def _lerp(a: float, b: float, t: float) -> float:
  """Linear interpolation from a to b by factor t (0.0-1.0)."""
  return a + (b - a) * _clamp(t, 0.0, 1.0)


def _safe_progress(now: datetime, start: datetime, end: datetime) -> float:
  """Fraction of time elapsed from start to end, clamped to [0, 1]."""
  total = (end - start).total_seconds()
  if total <= 0:
    return 1.0
  elapsed = (now - start).total_seconds()
  return _clamp(elapsed / total, 0.0, 1.0)


def _ensure_aware(dt: datetime, tz: ZoneInfo) -> datetime:
  """Make a datetime timezone-aware if it isn't already."""
  if dt.tzinfo is None:
    return dt.replace(tzinfo=tz)
  return dt


def _deep_merge(base: dict, override: dict) -> dict:
  """Recursively merge override into a copy of base."""
  result = base.copy()
  for key, value in override.items():
    if key in result and isinstance(result[key], dict) and isinstance(value, dict):
      result[key] = _deep_merge(result[key], value)
    else:
      result[key] = value
  return result
