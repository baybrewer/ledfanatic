"""
Persistent state manager.

Handles saving/loading of scenes, presets, and system configuration.
Uses atomic writes for crash safety and debounced saves to reduce disk churn.
"""

import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional
from datetime import datetime

logger = logging.getLogger(__name__)

STATE_SCHEMA_VERSION = 2


class StateManager:
  def __init__(self, config_dir: Path):
    self.config_dir = config_dir
    self.state_file = self.config_dir / "state.json"
    # Only structural defaults here. Display values (brightness_manual_cap,
    # target_fps, gamma) are intentionally omitted so config file values
    # aren't overridden by hardcoded defaults on first load.
    self._state: dict = {
      'schema_version': STATE_SCHEMA_VERSION,
      'current_scene': None,
      'current_params': {},
      'blackout': False,
      'scenes': {},
      'playlists': {},
      'current_layers': [],
      'render_mode': 'single',
      'last_updated': None,
    }
    self._dirty = False
    self._flush_interval = 1.0  # seconds
    self.config_dir.mkdir(parents=True, exist_ok=True)

  def load(self):
    """Load state from disk, migrating legacy (unversioned) files."""
    if self.state_file.exists():
      try:
        with open(self.state_file) as f:
          saved = json.load(f)
        self._migrate(saved)
        self._state.update(saved)
        logger.info(f"State loaded from {self.state_file}")
      except Exception as e:
        logger.error(f"Failed to load state: {e}")

  def _migrate(self, data: dict):
    """Migrate legacy state data to the current schema."""
    version = data.get('schema_version', 0)
    if version < 1:
      # Legacy v0 -> v1: just stamp the version
      data['schema_version'] = 1
      version = 1
      logger.info("Migrated state.json from unversioned to v1")
    if version < 2:
      # v1 -> v2: add layer support fields (R10-H1: do NOT convert scenes to layers)
      data['current_layers'] = []
      data['render_mode'] = 'single'
      data['schema_version'] = 2
      logger.info("Migrated state.json from v1 to v2")

  def _atomic_write(self):
    """Atomically save state to disk."""
    self._state['last_updated'] = datetime.now().isoformat()
    try:
      fd, tmp_path = tempfile.mkstemp(dir=self.config_dir, suffix='.tmp')
      with os.fdopen(fd, 'w') as f:
        json.dump(self._state, f, indent=2)
      os.replace(tmp_path, self.state_file)
    except Exception as e:
      logger.error(f"Failed to save state: {e}")

  def mark_dirty(self):
    """Mark state as needing a save. Actual write happens on next flush."""
    self._dirty = True

  def flush(self):
    """Write to disk if dirty. Call periodically from a background task."""
    if self._dirty:
      self._atomic_write()
      self._dirty = False

  def force_save(self):
    """Immediate write regardless of dirty flag. Use on shutdown."""
    self._atomic_write()
    self._dirty = False

  async def flush_loop(self):
    """Background task: periodically flush dirty state."""
    while True:
      await asyncio.sleep(self._flush_interval)
      self.flush()

  # --- Properties with debounced save ---

  @property
  def current_scene(self) -> Optional[str]:
    return self._state.get('current_scene')

  @current_scene.setter
  def current_scene(self, value: str):
    self._state['current_scene'] = value
    self.mark_dirty()

  @property
  def current_params(self) -> dict:
    return self._state.get('current_params', {})

  @current_params.setter
  def current_params(self, value: dict):
    self._state['current_params'] = value
    self.mark_dirty()

  @property
  def current_layers(self) -> list[dict]:
    return self._state.get('current_layers', [])

  @current_layers.setter
  def current_layers(self, layers: list[dict]):
    self._state['current_layers'] = layers
    self.mark_dirty()

  # --- Per-effect param memory ---

  def get_effect_params(self, effect_name: str) -> dict:
    """Return saved params for a specific effect, or empty dict if none."""
    store = self._state.get('effect_params', {})
    return dict(store.get(effect_name, {}))

  def set_effect_params(self, effect_name: str, params: dict):
    """Save params for a specific effect. Also used to remember each effect's last state."""
    if 'effect_params' not in self._state:
      self._state['effect_params'] = {}
    self._state['effect_params'][effect_name] = dict(params)
    self.mark_dirty()

  @property
  def brightness_manual_cap(self) -> Optional[float]:
    return self._state.get('brightness_manual_cap')

  @brightness_manual_cap.setter
  def brightness_manual_cap(self, value: float):
    self._state['brightness_manual_cap'] = max(0.0, min(1.0, value))
    self.mark_dirty()

  @property
  def brightness_auto_enabled(self) -> Optional[bool]:
    return self._state.get('brightness_auto_enabled')

  @brightness_auto_enabled.setter
  def brightness_auto_enabled(self, value: bool):
    self._state['brightness_auto_enabled'] = value
    self.mark_dirty()

  @property
  def target_fps(self) -> Optional[int]:
    return self._state.get('target_fps')

  @target_fps.setter
  def target_fps(self, value: int):
    self._state['target_fps'] = max(1, min(90, value))
    self.mark_dirty()

  @property
  def audio_bass_sensitivity(self) -> Optional[float]:
    return self._state.get('audio_bass_sensitivity')

  @audio_bass_sensitivity.setter
  def audio_bass_sensitivity(self, value: float):
    self._state['audio_bass_sensitivity'] = max(0.1, min(3.0, value))
    self.mark_dirty()

  @property
  def audio_mid_sensitivity(self) -> Optional[float]:
    return self._state.get('audio_mid_sensitivity')

  @audio_mid_sensitivity.setter
  def audio_mid_sensitivity(self, value: float):
    self._state['audio_mid_sensitivity'] = max(0.1, min(3.0, value))
    self.mark_dirty()

  @property
  def audio_treble_sensitivity(self) -> Optional[float]:
    return self._state.get('audio_treble_sensitivity')

  @audio_treble_sensitivity.setter
  def audio_treble_sensitivity(self, value: float):
    self._state['audio_treble_sensitivity'] = max(0.1, min(3.0, value))
    self.mark_dirty()

  # --- Scene presets ---

  def save_scene(self, name: str, effect: str, params: dict):
    if 'scenes' not in self._state:
      self._state['scenes'] = {}
    self._state['scenes'][name] = {
      'effect': effect,
      'params': params,
      'saved_at': datetime.now().isoformat(),
    }
    self.mark_dirty()
    logger.info(f"Scene saved: {name}")

  def load_scene(self, name: str) -> Optional[dict]:
    return self._state.get('scenes', {}).get(name)

  def delete_scene(self, name: str) -> bool:
    if name in self._state.get('scenes', {}):
      del self._state['scenes'][name]
      self.mark_dirty()
      return True
    return False

  def list_scenes(self) -> dict:
    return self._state.get('scenes', {})

  # --- Playlists ---

  def save_playlist(self, name: str, items: list[dict]):
    if 'playlists' not in self._state:
      self._state['playlists'] = {}
    self._state['playlists'][name] = {
      'items': items,
      'saved_at': datetime.now().isoformat(),
    }
    self.mark_dirty()

  def load_playlist(self, name: str) -> Optional[dict]:
    return self._state.get('playlists', {}).get(name)

  def list_playlists(self) -> dict:
    return self._state.get('playlists', {})

  def get_full_state(self) -> dict:
    return dict(self._state)
