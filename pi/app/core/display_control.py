"""
Display control — the one place that mutates brightness, blackout and the
active scene, so HTTP routes, physical pots and integrations (Home Assistant)
behave identically.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class DisplayController:
  def __init__(self, renderer, render_state, state_manager, brightness_engine,
               transport, media_manager=None):
    self.renderer = renderer
    self.render_state = render_state
    self.state_manager = state_manager
    self.brightness_engine = brightness_engine
    self.transport = transport
    self.media_manager = media_manager

  def set_manual_brightness(self, value: float) -> float:
    """Set the manual brightness cap (0-1).

    Brightness is applied only in the renderer; the Teensy master brightness
    stays pinned at full (see pin_teensy_brightness) so it isn't applied twice.
    """
    value = max(0.0, min(1.0, float(value)))
    self.brightness_engine.manual_cap = value
    self.state_manager.brightness_manual_cap = value
    return value

  async def pin_teensy_brightness(self):
    await self.transport.send_brightness(1.0)

  async def set_blackout(self, enabled: bool):
    self.render_state.blackout = bool(enabled)
    self.state_manager.blackout = bool(enabled)
    await self.transport.send_blackout(bool(enabled))

  def activate_and_persist(self, effect: str, params: Optional[dict]) -> Optional[dict]:
    """Activate a scene and remember it. Returns resolved params, or None if unknown.

    params=None means "use this effect's last-known params".
    """
    if params is None:
      params = self.state_manager.get_effect_params(effect) or None
    if not self.renderer.activate_scene(effect, params, media_manager=self.media_manager):
      return None
    # Resolve to actual effect params (merged with yaml defaults)
    resolved = params if params is not None else dict(
      getattr(self.renderer.current_effect, 'params', {}) or {}
    )
    # Filter out internal keys like '_effect_registry'
    resolved = {k: v for k, v in resolved.items() if not k.startswith('_')}
    self.state_manager.current_scene = effect
    self.state_manager.current_params = resolved
    # Persist per-effect params so switching back restores them
    self.state_manager.set_effect_params(effect, resolved)
    return resolved
