"""
Pot input mapping: raw ADC values -> control events.

Pure logic with injected time so every behavior is unit-testable.
Events returned by handle_raw:
  ('brightness', float 0-1)  -> apply as manual brightness cap
  ('overlay', str)           -> show category name on the LED overlay
  ('activate', str)          -> activate this effect via the renderer

Last-writer-wins: a pot is inert until it physically moves beyond the
deadband from its last settled position. The first packet after startup
(or after re-enable) only establishes a baseline.
"""

import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)

ADC_MAX = 1023
DEADBAND = 0.02       # 2% of travel to count as movement
ZONE_GUARD = 0.15     # hysteresis: fraction of zone width past the boundary
SETTLE_S = 0.3        # selector pots activate this long after motion stops

POT_BRIGHTNESS, POT_MENU, POT_PATTERN = 0, 1, 2
_POT_KEYS = ('brightness', 'menu', 'pattern')


def normalize_raw(raw: int) -> float:
  return max(0.0, min(1.0, raw / ADC_MAX))


def select_index(value: float, count: int, current: Optional[int],
                 guard: float = ZONE_GUARD) -> Optional[int]:
  """Quantize value into one of `count` zones with hysteresis.

  Keeps `current` while value stays within the current zone expanded by
  guard * zone_width on both sides, so a knob resting on a boundary never
  flickers between two selections.
  """
  if count <= 0:
    return None
  zone_width = 1.0 / count
  naive = min(int(value / zone_width), count - 1)
  if current is None or not (0 <= current < count):
    return naive
  lo = (current - guard) * zone_width
  hi = (current + 1 + guard) * zone_width
  if lo <= value < hi:
    return current
  return naive


class PotController:
  def __init__(self, menu_provider: Callable, enabled_provider: Callable,
               deadband: float = DEADBAND, settle_s: float = SETTLE_S):
    self._menu_provider = menu_provider
    self._enabled_provider = enabled_provider
    self._deadband = deadband
    self._settle_s = settle_s
    self._settled: list = [None, None, None]     # last settled value per pot
    self._was_enabled: list = [True, True, True]
    self._last_move: list = [None, None]          # menu, pattern move times
    self._menu_idx: Optional[int] = None
    self._pattern_idx: Optional[int] = None
    self._pending_selection = False
    self._pattern_live = False   # True once pattern has moved since last (re)enable
    self.last_values: tuple = (0.0, 0.0, 0.0)

  def handle_raw(self, raw: tuple, now: float) -> list:
    values = tuple(normalize_raw(r) for r in raw)
    self.last_values = values
    enabled = self._enabled_provider()
    events: list = []

    for i in range(3):
      pot_on = bool(enabled.get(_POT_KEYS[i], True))
      just_reenabled = pot_on and not self._was_enabled[i]
      self._was_enabled[i] = pot_on

      if not pot_on or just_reenabled or self._settled[i] is None:
        # Disabled, freshly re-enabled, or first packet: silently rebaseline.
        self._settled[i] = values[i]
        if i == POT_PATTERN:
          self._pattern_live = False
        continue

      if abs(values[i] - self._settled[i]) <= self._deadband:
        continue  # no movement

      self._settled[i] = values[i]
      if i == POT_BRIGHTNESS:
        events.append(('brightness', values[i]))
      elif i == POT_MENU:
        self._last_move[0] = now
        self._pending_selection = True
        menu = self._menu_provider()
        new_idx = select_index(values[i], len(menu), self._menu_idx)
        if new_idx is not None and new_idx != self._menu_idx:
          self._menu_idx = new_idx
          self._pattern_idx = None  # remap pattern within new category
        if self._menu_idx is not None and menu:
          events.append(('overlay', menu[self._menu_idx][0]))
      elif i == POT_PATTERN:
        self._last_move[1] = now
        self._pending_selection = True
        self._pattern_live = True

    if self._pending_selection:
      moves = [t for t in self._last_move if t is not None]
      if moves and now - max(moves) >= self._settle_s:
        effect = self._resolve_selection()
        self._pending_selection = False
        if effect:
          events.append(('activate', effect))

    return events

  def _resolve_selection(self) -> Optional[str]:
    menu = self._menu_provider()
    if not menu:
      return None
    enabled = self._enabled_provider()
    menu_on = bool(enabled.get(_POT_KEYS[POT_MENU], True))

    menu_idx = self._menu_idx
    if menu_idx is None:
      if not menu_on:
        # Disabled and never legitimately established: nothing to activate.
        return None
      # Menu knob never moved: fall back to its absolute position.
      menu_idx = select_index(self.last_values[POT_MENU], len(menu), None)
    menu_idx = min(menu_idx, len(menu) - 1)
    self._menu_idx = menu_idx
    effect_names = menu[menu_idx][1]
    if not effect_names:
      return None

    if self._pattern_live:
      self._pattern_idx = select_index(
        self.last_values[POT_PATTERN], len(effect_names), self._pattern_idx)
    else:
      # Pattern position isn't trustworthy right now (disabled, or enabled
      # but hasn't moved since it was last (re)enabled): keep the last
      # legitimate index instead of consuming its raw position.
      if self._pattern_idx is None:
        self._pattern_idx = 0
      else:
        self._pattern_idx = min(max(self._pattern_idx, 0), len(effect_names) - 1)
    return effect_names[self._pattern_idx]

  def get_status(self) -> dict:
    menu = self._menu_provider()
    category = None
    if self._menu_idx is not None and menu and 0 <= self._menu_idx < len(menu):
      category = menu[self._menu_idx][0]
    return {
      'values': {k: round(v, 3) for k, v in zip(_POT_KEYS, self.last_values)},
      'enabled': self._enabled_provider(),
      'category': category,
    }
