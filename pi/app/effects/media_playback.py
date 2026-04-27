"""
Media playback effect — plays cached media items as LED frames.
Scales video/image frames to fit the matrix dimensions.
"""

import time
import numpy as np
from typing import Optional
from PIL import Image

from .base import Effect
from ..media.manager import MediaManager


class MediaPlayback(Effect):
  """Plays a cached media item (image, GIF, or video)."""

  def __init__(self, *args, media_manager: Optional[MediaManager] = None, **kwargs):
    super().__init__(*args, **kwargs)
    self.media_manager = media_manager
    self._item_id: Optional[str] = self.params.get('item_id')
    self._loop = self.params.get('loop', True)
    self._speed = self.params.get('speed', 1.0)
    self._frame_cache: dict[int, np.ndarray] = {}
    self._frame_count = 0
    self._fps = 30
    self._fit_mode = self.params.get('fit', 'fill')  # fill, fit, stretch

    if self._item_id and self.media_manager:
      item = self.media_manager.items.get(self._item_id)
      if item:
        self._frame_count = item.frame_count
        self._fps = item.fps

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)

    if not self._item_id or not self.media_manager:
      return np.zeros((self.width, self.height, 3), dtype=np.uint8)

    if self._frame_count == 0:
      return np.zeros((self.width, self.height, 3), dtype=np.uint8)

    # Calculate current frame index
    frame_time = elapsed * self._speed
    frame_idx = int(frame_time * self._fps)

    if self._loop:
      frame_idx = frame_idx % self._frame_count
    else:
      frame_idx = min(frame_idx, self._frame_count - 1)

    # Load from cache or disk
    if frame_idx not in self._frame_cache:
      frame = self.media_manager.load_frame(self._item_id, frame_idx)
      if frame is not None:
        self._frame_cache[frame_idx] = frame
        # Keep cache bounded
        if len(self._frame_cache) > 120:
          oldest = min(self._frame_cache.keys())
          del self._frame_cache[oldest]

    frame = self._frame_cache.get(frame_idx)
    if frame is None:
      return np.zeros((self.width, self.height, 3), dtype=np.uint8)

    # Scale frame to fit matrix dimensions
    # frame is (w, h, 3) — check if it needs resizing
    if frame.shape[0] != self.width or frame.shape[1] != self.height:
      # Transpose to PIL (h, w, 3), resize, transpose back
      img = Image.fromarray(frame.transpose(1, 0, 2))
      img = img.resize((self.width, self.height), Image.LANCZOS)
      frame = np.array(img).transpose(1, 0, 2)

    return frame


MEDIA_EFFECTS = {
  'media_playback': MediaPlayback,
}
