"""
Persistence migration tests.

Verify that unversioned (legacy) state.json and media metadata.json files
load and upgrade correctly when schema_version is introduced.
"""

import json

import pytest

from app.core.state import StateManager, STATE_SCHEMA_VERSION
from app.media.manager import MediaManager, MEDIA_SCHEMA_VERSION


class TestStateMigration:
    """state.json versioning and migration."""

    def test_fresh_state_has_schema_version(self, tmp_path):
        """New state files are created with current schema_version."""
        mgr = StateManager(config_dir=tmp_path)
        mgr.force_save()
        with open(tmp_path / "state.json") as f:
            data = json.load(f)
        assert data['schema_version'] == STATE_SCHEMA_VERSION

    def test_legacy_unversioned_state_loads(self, tmp_path):
        """A legacy state.json without schema_version loads and migrates."""
        legacy = {
            'current_scene': 'fire',
            'current_params': {'speed': 2.0},
            'blackout': False,
            'scenes': {'sunset': {'effect': 'gradient', 'params': {}}},
            'playlists': {},
            'brightness_manual_cap': 0.6,
            'brightness_auto_enabled': False,
            'target_fps': 45,
            'last_updated': '2025-01-01T00:00:00',
        }
        with open(tmp_path / "state.json", 'w') as f:
            json.dump(legacy, f)

        mgr = StateManager(config_dir=tmp_path)
        mgr.load()

        # All values preserved
        assert mgr.current_scene == 'fire'
        assert mgr.current_params == {'speed': 2.0}
        assert mgr.brightness_manual_cap == 0.6
        assert mgr.target_fps == 45
        assert 'sunset' in mgr.list_scenes()

    def test_legacy_state_saved_with_version(self, tmp_path):
        """After loading legacy state, saving writes schema_version."""
        legacy = {'current_scene': 'fire', 'blackout': False}
        with open(tmp_path / "state.json", 'w') as f:
            json.dump(legacy, f)

        mgr = StateManager(config_dir=tmp_path)
        mgr.load()
        mgr.force_save()

        with open(tmp_path / "state.json") as f:
            data = json.load(f)
        assert data['schema_version'] == STATE_SCHEMA_VERSION

    def test_current_version_loads_without_migration(self, tmp_path):
        """State with current version loads directly without migration."""
        state = {
            'schema_version': STATE_SCHEMA_VERSION,
            'current_scene': 'rainbow_rotate',
            'current_params': {},
            'blackout': False,
            'scenes': {},
            'playlists': {},
            'last_updated': '2025-06-01T00:00:00',
        }
        with open(tmp_path / "state.json", 'w') as f:
            json.dump(state, f)

        mgr = StateManager(config_dir=tmp_path)
        mgr.load()
        assert mgr.current_scene == 'rainbow_rotate'

    def test_empty_legacy_state_loads(self, tmp_path):
        """Completely empty legacy state still loads."""
        with open(tmp_path / "state.json", 'w') as f:
            json.dump({}, f)

        mgr = StateManager(config_dir=tmp_path)
        mgr.load()
        assert mgr.current_scene is None

    def test_get_full_state_includes_version(self, tmp_path):
        """get_full_state() includes schema_version."""
        mgr = StateManager(config_dir=tmp_path)
        mgr.load()
        full = mgr.get_full_state()
        assert 'schema_version' in full
        assert full['schema_version'] == STATE_SCHEMA_VERSION

    def test_v1_to_v2_migration(self, tmp_path):
        """v1 state migrates to v2 with layer fields; scene preserved."""
        sm = StateManager(config_dir=tmp_path)
        sm._state = {
            'schema_version': 1,
            'current_scene': 'rainbow_rotate',
            'current_params': {'speed': 0.5},
        }
        sm._migrate(sm._state)
        assert sm._state['schema_version'] == 2
        # R10-H1: migration does NOT convert scene to layers
        assert sm._state['current_layers'] == []
        assert sm._state['render_mode'] == 'single'
        # Original scene preserved
        assert sm._state['current_scene'] == 'rainbow_rotate'
        assert sm._state['current_params'] == {'speed': 0.5}

    def test_v0_to_v2_migration(self, tmp_path):
        """Unversioned state migrates through v1 to v2."""
        sm = StateManager(config_dir=tmp_path)
        sm._state = {
            'current_scene': 'fire',
            'current_params': {},
        }
        sm._migrate(sm._state)
        assert sm._state['schema_version'] == 2
        assert sm._state['current_layers'] == []
        assert sm._state['render_mode'] == 'single'
        assert sm._state['current_scene'] == 'fire'

    def test_v1_file_loads_with_layer_fields(self, tmp_path):
        """A v1 state.json on disk loads and gets layer fields."""
        v1_state = {
            'schema_version': 1,
            'current_scene': 'gradient',
            'current_params': {'colors': ['blue']},
            'blackout': False,
            'scenes': {},
            'playlists': {},
            'last_updated': '2026-04-01T00:00:00',
        }
        with open(tmp_path / "state.json", 'w') as f:
            json.dump(v1_state, f)

        mgr = StateManager(config_dir=tmp_path)
        mgr.load()
        assert mgr.current_scene == 'gradient'
        assert mgr.current_layers == []
        assert mgr._state['render_mode'] == 'single'
        assert mgr._state['schema_version'] == 2


class TestMediaMetadataMigration:
    """media metadata.json versioning and migration."""

    def test_legacy_metadata_loads(self, tmp_path):
        """Legacy metadata without schema_version loads correctly."""
        cache_dir = tmp_path / "cache"
        item_dir = cache_dir / "abc123"
        item_dir.mkdir(parents=True)

        legacy_meta = {
            'id': 'abc123',
            'name': 'test.png',
            'type': 'image',
            'frame_count': 1,
            'fps': 1,
            'width': 40,
            'height': 172,
        }
        with open(item_dir / "metadata.json", 'w') as f:
            json.dump(legacy_meta, f)

        mgr = MediaManager(media_dir=tmp_path / "media", cache_dir=cache_dir)
        mgr.scan_library()
        assert 'abc123' in mgr.items
        assert mgr.items['abc123'].name == 'test.png'

    def test_versioned_metadata_loads(self, tmp_path):
        """Current versioned metadata loads correctly."""
        cache_dir = tmp_path / "cache"
        item_dir = cache_dir / "xyz789"
        item_dir.mkdir(parents=True)

        meta = {
            'schema_version': MEDIA_SCHEMA_VERSION,
            'id': 'xyz789',
            'name': 'video.mp4',
            'type': 'video',
            'frame_count': 100,
            'fps': 30,
            'width': 40,
            'height': 172,
        }
        with open(item_dir / "metadata.json", 'w') as f:
            json.dump(meta, f)

        mgr = MediaManager(media_dir=tmp_path / "media", cache_dir=cache_dir)
        mgr.scan_library()
        assert 'xyz789' in mgr.items

    def test_import_writes_versioned_metadata(self, tmp_path):
        """New imports write schema_version to metadata.json."""
        from PIL import Image

        cache_dir = tmp_path / "cache"
        media_dir = tmp_path / "media"
        mgr = MediaManager(media_dir=media_dir, cache_dir=cache_dir)

        img = Image.new('RGB', (50, 50), color=(255, 0, 0))
        img_path = tmp_path / "test.png"
        img.save(img_path)

        import asyncio
        item = asyncio.get_event_loop().run_until_complete(
            mgr.import_file(img_path, "test.png")
        )
        assert item is not None

        meta_path = cache_dir / item.id / "metadata.json"
        with open(meta_path) as f:
            meta = json.load(f)
        assert meta['schema_version'] == MEDIA_SCHEMA_VERSION
