"""Animation Switcher — meta-effect that cycles through a playlist with cross-fade."""

import logging
import random
import time
from typing import Optional

import numpy as np

from .base import Effect
from ..hardware_constants import LEDS_PER_STRIP

logger = logging.getLogger(__name__)


class AnimationSwitcher(Effect):
  """Meta-effect: cycles through a playlist of effects with cross-fade transitions."""

  DISPLAY_NAME = "Animation Switcher"
  CATEGORY = "special"
  DESCRIPTION = "Automatically cycles through selected animations with smooth cross-fade transitions"

  def __init__(self, width=10, height=LEDS_PER_STRIP, params=None):
    super().__init__(width, height, params)
    self._interval = self.params.get('interval', 15)
    self._fade_duration = self.params.get('fade_duration', 2.0)
    self._shuffle = self.params.get('shuffle', False)
    self._playlist = []  # assigned after _effect_registry is set below
    self._playlist_params = self.params.get('playlist_params', {})
    self._effect_registry = self.params.get('_effect_registry', {})

    self._current_idx = 0
    self._current_effect = None
    self._next_effect = None
    self._phase = 'playing'  # 'playing' | 'fading'
    self._phase_timer = 0.0
    self._last_t = None

    self._playlist = self._sanitize_playlist(self.params.get('playlist', []))
    if self._shuffle and len(self._playlist) > 1:
      random.shuffle(self._playlist)

    self._activate_current()

  def _activate_current(self):
    """Create the effect instance for the current playlist index."""
    if not self._playlist or not self._effect_registry:
      return
    name = self._playlist[self._current_idx % len(self._playlist)]
    if name in self._effect_registry:
      params = self._playlist_params.get(name, {})
      cls = self._effect_registry[name]
      self._current_effect = cls(width=self.width, height=self.height, params=params)

  def _activate_next(self):
    """Create the next effect for cross-fade."""
    if not self._playlist or not self._effect_registry:
      return
    next_idx = (self._current_idx + 1) % len(self._playlist)
    name = self._playlist[next_idx]
    if name in self._effect_registry:
      params = self._playlist_params.get(name, {})
      cls = self._effect_registry[name]
      self._next_effect = cls(width=self.width, height=self.height, params=params)

  def render(self, t, state):
    if self._last_t is None:
      self._last_t = t
    dt = t - self._last_t
    self._last_t = t
    self._phase_timer += dt

    if not self._playlist:
      return np.zeros((self.width, self.height, 3), dtype=np.uint8)

    if self._phase == 'playing':
      if self._phase_timer >= self._interval:
        # Start cross-fade
        self._activate_next()
        self._phase = 'fading'
        self._phase_timer = 0.0

      if self._current_effect:
        return self._current_effect.render(t, state)
      return np.zeros((self.width, self.height, 3), dtype=np.uint8)

    elif self._phase == 'fading':
      blend = min(1.0, self._phase_timer / max(0.1, self._fade_duration))

      frame_a = self._current_effect.render(t, state) if self._current_effect else np.zeros((self.width, self.height, 3), dtype=np.uint8)
      frame_b = self._next_effect.render(t, state) if self._next_effect else np.zeros((self.width, self.height, 3), dtype=np.uint8)

      # Cross-fade blend
      blended = (frame_a.astype(np.float32) * (1 - blend) + frame_b.astype(np.float32) * blend).astype(np.uint8)

      if blend >= 1.0:
        # Fade complete — promote next to current
        self._current_idx = (self._current_idx + 1) % len(self._playlist)
        self._current_effect = self._next_effect
        self._next_effect = None
        self._phase = 'playing'
        self._phase_timer = 0.0

      return blended

    return np.zeros((self.width, self.height, 3), dtype=np.uint8)

  def get_switcher_status(self):
    """Return current switcher state for the API."""
    current_name = self._playlist[self._current_idx % len(self._playlist)] if self._playlist else None
    next_idx = (self._current_idx + 1) % len(self._playlist) if self._playlist else 0
    next_name = self._playlist[next_idx] if self._playlist else None
    return {
      'active': True,
      'current': current_name,
      'next': next_name if self._phase == 'fading' else None,
      'phase': self._phase,
      'progress': min(1.0, self._phase_timer / max(0.1, self._fade_duration)) if self._phase == 'fading' else 0,
      'time_remaining': max(0, self._interval - self._phase_timer) if self._phase == 'playing' else 0,
      'playlist': list(self._playlist),
      'interval': self._interval,
      'fade_duration': self._fade_duration,
    }

  def _sanitize_playlist(self, raw):
    """Drop any entries not present in the current effect registry.

    Prevents stale/renamed/removed effect names from producing silent black
    frames or confusing status output.
    """
    if not raw:
      return []
    if not self._effect_registry:
      return list(raw)
    return [name for name in raw if name in self._effect_registry]

  def update_params(self, params):
    """Update switcher params. Playlist changes reset position to 0."""
    if 'interval' in params:
      self._interval = params['interval']
    if 'fade_duration' in params:
      self._fade_duration = params['fade_duration']
    if 'shuffle' in params:
      self._shuffle = params['shuffle']
    if '_effect_registry' in params and params['_effect_registry']:
      self._effect_registry = params['_effect_registry']
    if 'playlist' in params:
      new_playlist = self._sanitize_playlist(list(params['playlist'] or []))
      if new_playlist != self._playlist:
        self._playlist = new_playlist
        self._current_idx = 0
        self._phase = 'playing'
        self._phase_timer = 0.0
        self._current_effect = None
        self._next_effect = None
        self._activate_current()
    self.params.update(params)
