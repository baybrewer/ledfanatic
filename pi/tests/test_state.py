"""Tests for state manager."""

import json

import pytest
from app.core.state import StateManager


class TestLoadMissing:
  def test_load_missing_file(self, tmp_path):
    """No state file on disk -> defaults are used."""
    mgr = StateManager(config_dir=tmp_path)
    mgr.load()
    assert mgr.current_scene == 'rainbow_rotate'
    assert mgr.brightness_manual_cap == 0.8
    assert mgr.target_fps == 60


class TestSaveAndLoad:
  def test_save_and_load(self, tmp_path):
    mgr = StateManager(config_dir=tmp_path)
    mgr.current_scene = 'fire'
    mgr.brightness_manual_cap = 0.5
    mgr.force_save()

    mgr2 = StateManager(config_dir=tmp_path)
    mgr2.load()
    assert mgr2.current_scene == 'fire'
    assert mgr2.brightness_manual_cap == 0.5


class TestAtomicWrite:
  def test_atomic_write(self, tmp_path):
    mgr = StateManager(config_dir=tmp_path)
    mgr.force_save()
    state_file = tmp_path / "state.json"
    assert state_file.exists()

    with open(state_file) as f:
      data = json.load(f)
    assert 'current_scene' in data


class TestDebouncedSave:
  def test_mark_dirty_and_flush(self, tmp_path):
    """Setting a property marks dirty; flush writes to disk."""
    mgr = StateManager(config_dir=tmp_path)
    mgr.brightness_manual_cap = 0.3
    assert mgr._dirty is True

    # File not written yet (debounced)
    state_file = tmp_path / "state.json"
    assert not state_file.exists()

    # Flush writes
    mgr.flush()
    assert state_file.exists()
    with open(state_file) as f:
      data = json.load(f)
    assert data['brightness_manual_cap'] == 0.3
    assert mgr._dirty is False


class TestForceSave:
  def test_force_save_writes(self, tmp_path):
    mgr = StateManager(config_dir=tmp_path)
    mgr.force_save()
    state_file = tmp_path / "state.json"
    assert state_file.exists()
    with open(state_file) as f:
      data = json.load(f)
    assert data['last_updated'] is not None


class TestSceneCrud:
  def test_scene_crud(self, tmp_path):
    mgr = StateManager(config_dir=tmp_path)

    mgr.save_scene('sunset', 'gradient', {'colors': ['orange', 'red']})
    scene = mgr.load_scene('sunset')
    assert scene is not None
    assert scene['effect'] == 'gradient'
    assert scene['params'] == {'colors': ['orange', 'red']}

    scenes = mgr.list_scenes()
    assert 'sunset' in scenes

    assert mgr.delete_scene('sunset') is True
    assert mgr.load_scene('sunset') is None
    assert 'sunset' not in mgr.list_scenes()

    assert mgr.delete_scene('nonexistent') is False
