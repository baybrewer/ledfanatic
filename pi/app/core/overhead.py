"""
Overhead light service — SSOT for what "the overhead light" means.

Exposes Overhead mode as one light (on/off, brightness, white color
temperature, RGB color, effect) for the UI tab and Home Assistant. Knows
nothing about HTTP or MQTT; all mutation goes through DisplayController.
"""

import re
from dataclasses import dataclass, asdict, field
from typing import Optional

from ..effects.overhead import OVERHEAD_EFFECTS

WHITE_EFFECT = 'oh_white'
COLOR_EFFECT = 'oh_color'
DEFAULT_EFFECT = WHITE_EFFECT

_HEX_RE = re.compile(r'^#[0-9a-fA-F]{6}$')


def rgb_to_hex(rgb) -> str:
  return '#{:02X}{:02X}{:02X}'.format(*(max(0, min(255, int(c))) for c in rgb))


def hex_to_rgb(value: str) -> tuple:
  value = value.lstrip('#')
  return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


@dataclass(frozen=True)
class OverheadCommand:
  on: Optional[bool] = None
  brightness: Optional[float] = None   # 0-1
  effect: Optional[str] = None         # effect name (oh_*)
  kelvin: Optional[int] = None
  rgb: Optional[tuple] = None
  params: Optional[dict] = None        # extra effect params (speed, saturation...)


@dataclass(frozen=True)
class OverheadState:
  on: bool
  brightness: float
  effect: str
  kelvin: int
  rgb: tuple
  color_mode: str
  params: dict = field(default_factory=dict)

  def to_dict(self) -> dict:
    data = asdict(self)
    data['rgb'] = list(self.rgb)
    return data


class OverheadService:
  def __init__(self, display, effect_catalog):
    self.display = display
    self.catalog = effect_catalog

  # --- Read side ---

  def effects(self) -> list:
    """(name, label) for every overhead effect, in module order."""
    result = []
    for name in OVERHEAD_EFFECTS:
      meta = self.catalog.get_meta(name) if self.catalog else None
      result.append((name, meta.label if meta else name))
    return result

  def is_overhead_showing(self) -> bool:
    renderer = self.display.renderer
    if renderer.compositor and renderer.compositor.layers:
      return False
    return self.display.render_state.current_scene in OVERHEAD_EFFECTS

  def snapshot(self) -> OverheadState:
    showing = self.is_overhead_showing()
    sm = self.display.state_manager
    if showing:
      effect = self.display.render_state.current_scene
      live = getattr(self.display.renderer.current_effect, 'params', {}) or {}
      params = {k: v for k, v in live.items() if not k.startswith('_')}
    else:
      effect = self._last_effect()
      params = self._stored_params(effect)
    kelvin = int(self._stored_params(WHITE_EFFECT).get('kelvin'))
    color = self._stored_params(COLOR_EFFECT).get('color')
    return OverheadState(
      on=showing and not self.display.render_state.blackout,
      brightness=float(self.display.brightness_engine.manual_cap),
      effect=effect,
      kelvin=kelvin,
      rgb=hex_to_rgb(color),
      color_mode='color_temp' if effect == WHITE_EFFECT else 'rgb',
      params=params,
    )

  # --- Write side ---

  async def apply(self, cmd: OverheadCommand) -> OverheadState:
    if cmd.effect is not None and cmd.effect not in OVERHEAD_EFFECTS:
      raise ValueError(f"Unknown overhead effect: {cmd.effect}")

    showing = self.is_overhead_showing()

    if cmd.on is False:
      # HA controls Overhead only: "off" must never kill a festival scene.
      if showing:
        await self.display.set_blackout(True)
      return self.snapshot()

    if cmd.brightness is not None:
      self.display.set_manual_brightness(cmd.brightness)

    # Remember white/color settings even when another effect is the target,
    # so "make it warmer" then "turn on White" lands where the user expects.
    if cmd.kelvin is not None:
      self._store(WHITE_EFFECT, {'kelvin': cmd.kelvin})
    if cmd.rgb is not None:
      self._store(COLOR_EFFECT, {'color': rgb_to_hex(cmd.rgb)})

    target = cmd.effect
    if target is None:
      if cmd.kelvin is not None:
        target = WHITE_EFFECT
      elif cmd.rgb is not None:
        target = COLOR_EFFECT

    wants_light = (cmd.on is True or target is not None or cmd.params)
    if not wants_light:
      return self.snapshot()

    if target is None:
      target = self.display.render_state.current_scene if showing else self._last_effect()

    params = self._stored_params(target)
    if cmd.params:
      params.update(self._validate(target, cmd.params))
    if self.display.activate_and_persist(target, params) is None:
      raise ValueError(f"Failed to activate overhead effect: {target}")
    self.display.state_manager.last_overhead_effect = target
    if self.display.render_state.blackout:
      await self.display.set_blackout(False)
    return self.snapshot()

  # --- Helpers ---

  def _last_effect(self) -> str:
    last = self.display.state_manager.last_overhead_effect
    return last if last in OVERHEAD_EFFECTS else DEFAULT_EFFECT

  def _param_meta(self, effect: str) -> dict:
    meta = self.catalog.get_meta(effect) if self.catalog else None
    return {p['name']: p for p in (meta.params if meta else ())}

  def _stored_params(self, effect: str) -> dict:
    """Catalog defaults overlaid with the user's saved params."""
    params = {name: p.get('default') for name, p in self._param_meta(effect).items()}
    params.update(self.display.state_manager.get_effect_params(effect))
    return params

  def _store(self, effect: str, updates: dict):
    params = self._stored_params(effect)
    params.update(self._validate(effect, updates))
    self.display.state_manager.set_effect_params(effect, params)
    # Apply live if this effect is on screen right now
    if self.is_overhead_showing() and self.display.render_state.current_scene == effect:
      self.display.renderer.current_effect.update_params(params)

  def _validate(self, effect: str, updates: dict) -> dict:
    """Clamp to catalog param ranges; drop unknown keys and bad values."""
    meta = self._param_meta(effect)
    clean = {}
    for key, value in updates.items():
      spec = meta.get(key)
      if spec is None:
        continue
      if spec.get('type') == 'color':
        if isinstance(value, str) and _HEX_RE.match(value):
          clean[key] = value.upper()
        continue
      try:
        num = float(value)
      except (TypeError, ValueError):
        continue
      num = max(float(spec['min']), min(float(spec['max']), num))
      clean[key] = int(round(num)) if key == 'kelvin' else num
    return clean
