"""
Media import, transcode, and cache manager.

Handles upload of images, GIFs, and video files.
Transcodes to pillar-native cached format for deterministic playback.
"""

import asyncio
import json
import logging
import shutil
import uuid
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

MEDIA_DIR = Path("/opt/pillar/media")
CACHE_DIR = Path("/opt/pillar/cache")
VIRTUAL_WIDTH = 40
HEIGHT = 172
MAX_UPLOAD_MB = 50


class MediaItem:
  def __init__(self, item_id: str, name: str, media_type: str,
               frame_count: int, fps: float, width: int, height: int):
    self.id = item_id
    self.name = name
    self.type = media_type
    self.frame_count = frame_count
    self.fps = fps
    self.width = width
    self.height = height

  def to_dict(self) -> dict:
    return {
      'id': self.id,
      'name': self.name,
      'type': self.type,
      'frame_count': self.frame_count,
      'fps': self.fps,
      'width': self.width,
      'height': self.height,
    }


class MediaManager:
  def __init__(self, media_dir: Optional[Path] = None, cache_dir: Optional[Path] = None):
    self.media_dir = media_dir or MEDIA_DIR
    self.cache_dir = cache_dir or CACHE_DIR
    self.items: dict[str, MediaItem] = {}
    self._ensure_dirs()

  def _ensure_dirs(self):
    self.media_dir.mkdir(parents=True, exist_ok=True)
    self.cache_dir.mkdir(parents=True, exist_ok=True)

  def scan_library(self):
    """Scan cache directory for existing items."""
    for meta_path in self.cache_dir.glob("*/metadata.json"):
      try:
        with open(meta_path) as f:
          meta = json.load(f)
        item = MediaItem(
          item_id=meta['id'],
          name=meta['name'],
          media_type=meta['type'],
          frame_count=meta['frame_count'],
          fps=meta.get('fps', 30),
          width=meta.get('width', VIRTUAL_WIDTH),
          height=meta.get('height', HEIGHT),
        )
        self.items[item.id] = item
      except Exception as e:
        logger.warning(f"Failed to load {meta_path}: {e}")
    logger.info(f"Loaded {len(self.items)} cached media items")

  async def import_file(self, file_path: Path, original_name: str) -> Optional[MediaItem]:
    """Import and transcode a media file."""
    suffix = file_path.suffix.lower()
    item_id = str(uuid.uuid4())[:8]

    try:
      if suffix in ('.png', '.jpg', '.jpeg', '.bmp'):
        return await self._import_image(file_path, item_id, original_name)
      elif suffix == '.gif':
        return await self._import_gif(file_path, item_id, original_name)
      elif suffix in ('.mp4', '.mov', '.avi', '.webm', '.mkv'):
        return await self._import_video(file_path, item_id, original_name)
      else:
        logger.warning(f"Unsupported media type: {suffix}")
        return None
    except Exception as e:
      logger.error(f"Import failed for {original_name}: {e}", exc_info=True)
      return None

  async def _import_image(self, path: Path, item_id: str, name: str) -> MediaItem:
    """Import a static image."""
    img = Image.open(path).convert('RGB')
    img = img.resize((VIRTUAL_WIDTH, HEIGHT), Image.Resampling.LANCZOS)

    cache_path = self.cache_dir / item_id
    cache_path.mkdir(parents=True, exist_ok=True)

    frame = np.array(img, dtype=np.uint8)
    # Image is (H, W, 3), we want (W, H, 3) for our column-major model
    frame = np.transpose(frame, (1, 0, 2))
    np.save(cache_path / "frame_0000.npy", frame)

    meta = {
      'id': item_id,
      'name': name,
      'type': 'image',
      'frame_count': 1,
      'fps': 1,
      'width': VIRTUAL_WIDTH,
      'height': HEIGHT,
    }
    with open(cache_path / "metadata.json", 'w') as f:
      json.dump(meta, f)

    item = MediaItem(
      item_id=meta['id'], name=meta['name'], media_type=meta['type'],
      frame_count=meta['frame_count'], fps=meta['fps'],
      width=meta['width'], height=meta['height'],
    )
    self.items[item_id] = item
    logger.info(f"Imported image: {name} -> {item_id}")
    return item

  async def _import_gif(self, path: Path, item_id: str, name: str) -> MediaItem:
    """Import an animated GIF."""
    img = Image.open(path)
    cache_path = self.cache_dir / item_id
    cache_path.mkdir(parents=True, exist_ok=True)

    frames = []
    frame_idx = 0
    try:
      while True:
        frame = img.convert('RGB').resize((VIRTUAL_WIDTH, HEIGHT), Image.Resampling.LANCZOS)
        arr = np.array(frame, dtype=np.uint8)
        arr = np.transpose(arr, (1, 0, 2))
        np.save(cache_path / f"frame_{frame_idx:04d}.npy", arr)
        frames.append(frame_idx)
        frame_idx += 1
        img.seek(img.tell() + 1)
    except EOFError:
      pass

    # Estimate FPS from GIF duration
    duration = img.info.get('duration', 100)
    fps = min(60, 1000.0 / max(10, duration))

    meta = {
      'id': item_id,
      'name': name,
      'type': 'gif',
      'frame_count': len(frames),
      'fps': fps,
      'width': VIRTUAL_WIDTH,
      'height': HEIGHT,
    }
    with open(cache_path / "metadata.json", 'w') as f:
      json.dump(meta, f)

    item = MediaItem(
      item_id=meta['id'], name=meta['name'], media_type=meta['type'],
      frame_count=meta['frame_count'], fps=meta['fps'],
      width=meta['width'], height=meta['height'],
    )
    self.items[item_id] = item
    logger.info(f"Imported GIF: {name} ({len(frames)} frames @ {fps:.1f} FPS) -> {item_id}")
    return item

  async def _import_video(self, path: Path, item_id: str, name: str) -> MediaItem:
    """Import video using ffmpeg/PyAV."""
    cache_path = self.cache_dir / item_id
    cache_path.mkdir(parents=True, exist_ok=True)

    try:
      import av
    except ImportError:
      logger.error("PyAV not installed — video import unavailable")
      raise ImportError("PyAV required for video import")

    container = av.open(str(path))
    stream = container.streams.video[0]
    fps = float(stream.average_rate or 30)
    target_fps = min(60, fps)

    frame_idx = 0
    for av_frame in container.decode(video=0):
      img = av_frame.to_image().convert('RGB')
      img = img.resize((VIRTUAL_WIDTH, HEIGHT), Image.Resampling.LANCZOS)
      arr = np.array(img, dtype=np.uint8)
      arr = np.transpose(arr, (1, 0, 2))
      np.save(cache_path / f"frame_{frame_idx:04d}.npy", arr)
      frame_idx += 1

    container.close()

    meta = {
      'id': item_id,
      'name': name,
      'type': 'video',
      'frame_count': frame_idx,
      'fps': target_fps,
      'width': VIRTUAL_WIDTH,
      'height': HEIGHT,
    }
    with open(cache_path / "metadata.json", 'w') as f:
      json.dump(meta, f)

    item = MediaItem(
      item_id=meta['id'], name=meta['name'], media_type=meta['type'],
      frame_count=meta['frame_count'], fps=meta['fps'],
      width=meta['width'], height=meta['height'],
    )
    self.items[item_id] = item
    logger.info(f"Imported video: {name} ({frame_idx} frames @ {target_fps:.1f} FPS) -> {item_id}")
    return item

  def load_frame(self, item_id: str, frame_idx: int) -> Optional[np.ndarray]:
    """Load a cached frame."""
    cache_path = self.cache_dir / item_id / f"frame_{frame_idx:04d}.npy"
    if cache_path.exists():
      return np.load(cache_path)
    return None

  def delete_item(self, item_id: str) -> bool:
    """Delete a media item and its cache."""
    if item_id not in self.items:
      return False
    cache_path = self.cache_dir / item_id
    if cache_path.exists():
      shutil.rmtree(cache_path)
    del self.items[item_id]
    return True

  def list_items(self) -> list[dict]:
    return [item.to_dict() for item in self.items.values()]
