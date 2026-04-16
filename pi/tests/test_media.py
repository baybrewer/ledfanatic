"""Tests for media manager."""

import json

import numpy as np
import pytest
from PIL import Image

from app.media.manager import MediaManager, MediaItem, HEIGHT


class TestMediaItem:
  def test_media_item_construction(self):
    item = MediaItem(
      item_id="abc123",
      name="test.png",
      media_type="image",
      frame_count=1,
      fps=1.0,
      width=40,
      height=172,
    )
    assert item.id == "abc123"
    assert item.name == "test.png"
    assert item.type == "image"
    assert item.frame_count == 1
    assert item.fps == 1.0
    assert item.width == 40
    assert item.height == 172

  def test_media_item_to_dict(self):
    item = MediaItem(
      item_id="abc123",
      name="test.png",
      media_type="image",
      frame_count=1,
      fps=1.0,
      width=40,
      height=172,
    )
    d = item.to_dict()
    expected_keys = {'id', 'name', 'type', 'frame_count', 'fps', 'width', 'height'}
    assert set(d.keys()) == expected_keys
    assert d['type'] == 'image'
    assert d['id'] == 'abc123'

  def test_metadata_key_consistency(self):
    """Verify metadata dict uses 'media_type' key in constructor
    and 'type' in to_dict output, matching scan_library expectations."""
    meta = {
      'id': 'xyz',
      'name': 'foo.png',
      'type': 'image',
      'frame_count': 1,
      'fps': 1,
      'width': 40,
      'height': 172,
    }
    # scan_library constructs with explicit keyword args
    item = MediaItem(
      item_id=meta['id'],
      name=meta['name'],
      media_type=meta['type'],
      frame_count=meta['frame_count'],
      fps=meta['fps'],
      width=meta['width'],
      height=meta['height'],
    )
    assert item.type == 'image'
    assert item.to_dict()['type'] == 'image'


class TestScanLibrary:
  def test_scan_library_empty(self, tmp_path):
    media_dir = tmp_path / "media"
    cache_dir = tmp_path / "cache"
    mgr = MediaManager(media_dir=media_dir, cache_dir=cache_dir)
    mgr.scan_library()
    assert len(mgr.items) == 0


class TestImportImage:
  @pytest.mark.asyncio
  async def test_import_image(self, tmp_path):
    media_dir = tmp_path / "media"
    cache_dir = tmp_path / "cache"
    mgr = MediaManager(media_dir=media_dir, cache_dir=cache_dir)

    # Create a small test PNG
    img = Image.new('RGB', (100, 100), color=(255, 0, 0))
    img_path = tmp_path / "test_input.png"
    img.save(img_path)

    item = await mgr.import_file(img_path, "test_input.png")
    assert item is not None
    assert item.name == "test_input.png"
    assert item.type == "image"
    assert item.frame_count == 1
    assert item.width == 40
    assert item.height == HEIGHT

    # Verify item is tracked
    assert item.id in mgr.items

    # Verify cached frame exists
    frame = mgr.load_frame(item.id, 0)
    assert frame is not None
    assert frame.shape == (40, HEIGHT, 3)


class TestMediaDelete:
  @pytest.mark.asyncio
  async def test_media_delete(self, tmp_path):
    media_dir = tmp_path / "media"
    cache_dir = tmp_path / "cache"
    mgr = MediaManager(media_dir=media_dir, cache_dir=cache_dir)

    # Import a test image
    img = Image.new('RGB', (50, 50), color=(0, 255, 0))
    img_path = tmp_path / "delete_me.png"
    img.save(img_path)

    item = await mgr.import_file(img_path, "delete_me.png")
    assert item is not None
    item_id = item.id

    # Delete it
    assert mgr.delete_item(item_id) is True
    assert item_id not in mgr.items

    # Verify cache dir removed
    assert not (cache_dir / item_id).exists()

    # Double-delete returns False
    assert mgr.delete_item(item_id) is False
