"""
Cylinder mapping engine.

Converts a logical 10x172 RGB canvas into 5x344 electrical channel data
for the serpentine-wired LED pillar.

Logical model:
  - width = 10 columns (strips S0-S9)
  - height = 172 rows
  - row 0 = bottom, row 171 = top

Electrical model:
  - 5 channels, each 344 LEDs
  - CH0: S0 (bottom→top) + S1 (top→bottom)
  - CH1: S2 (bottom→top) + S3 (top→bottom)
  - etc.
"""

import numpy as np
from typing import Optional

from ..hardware_constants import LEDS_PER_STRIP, STRIPS, CHANNELS, LEDS_PER_CHANNEL


N = LEDS_PER_STRIP


def logical_to_channel(x: int, y: int) -> tuple[int, int]:
  """
  Convert logical pixel (x, y) to (channel, index).

  x: logical strip column 0..9
  y: logical row 0..171 (0 = bottom)
  Returns: (channel 0..4, index 0..343)
  """
  channel = x // 2
  if x % 2 == 0:
    # First strip in pair: bottom→top, index = y
    index = y
  else:
    # Second strip in pair: top→bottom, index = (2*N - 1) - y
    index = (2 * N - 1) - y
  return channel, index


def build_lookup_table() -> np.ndarray:
  """
  Build a precomputed lookup table for fast mapping.

  Returns array of shape (10, 172, 2) where [x, y] = (channel, index).
  """
  lut = np.zeros((STRIPS, N, 2), dtype=np.int32)
  for x in range(STRIPS):
    for y in range(N):
      ch, idx = logical_to_channel(x, y)
      lut[x, y, 0] = ch
      lut[x, y, 1] = idx
  return lut


# Precompute channel and index arrays for vectorized mapping
_channels = np.zeros((STRIPS, N), dtype=np.int32)
_indices = np.zeros((STRIPS, N), dtype=np.int32)
for _x in range(STRIPS):
  for _y in range(N):
    _ch, _idx = logical_to_channel(_x, _y)
    _channels[_x, _y] = _ch
    _indices[_x, _y] = _idx


def map_frame(logical_frame: np.ndarray) -> np.ndarray:
  """
  Map a logical frame (10, 172, 3) RGB to channel data (5, 344, 3).

  Uses precomputed lookup for speed.
  """
  assert logical_frame.shape == (10, N, 3), f"Expected (10, {N}, 3), got {logical_frame.shape}"

  channel_data = np.zeros((5, 2 * N, 3), dtype=np.uint8)

  for x in range(10):
    for y in range(N):
      ch = _channels[x, y]
      idx = _indices[x, y]
      channel_data[ch, idx] = logical_frame[x, y]

  return channel_data


def map_frame_fast(logical_frame: np.ndarray) -> np.ndarray:
  """
  Vectorized frame mapping using precomputed flat indices.

  logical_frame: shape (10, 172, 3) uint8
  Returns: shape (5, 344, 3) uint8
  """
  assert logical_frame.shape == (10, N, 3), f"Expected (10, {N}, 3), got {logical_frame.shape}"

  channel_data = np.zeros((5, 2 * N, 3), dtype=np.uint8)

  # Process each channel pair
  for pair_idx in range(5):
    even_strip = pair_idx * 2
    odd_strip = pair_idx * 2 + 1

    # Even strip: bottom→top maps directly to indices 0..171
    channel_data[pair_idx, :N, :] = logical_frame[even_strip, :, :]

    # Odd strip: top→bottom maps to indices 172..343 (reversed)
    channel_data[pair_idx, N:, :] = logical_frame[odd_strip, ::-1, :]

  return channel_data


def serialize_channels(channel_data: np.ndarray) -> bytes:
  """
  Serialize channel data to bytes for USB transport.

  channel_data: shape (5, 344, 3) uint8
  Returns: channel-major RGB bytes, 5 * 344 * 3 = 5160 bytes
  """
  return channel_data.tobytes()


def wrap_x(x: int) -> int:
  """Horizontal wrap for cylindrical seam."""
  return x % 10


def downsample_width(supersampled: np.ndarray, output_width: int = 10) -> np.ndarray:
  """
  Downsample a supersampled canvas to the physical output width.

  supersampled: shape (internal_width, 172, 3)
  Returns: shape (10, 172, 3)
  """
  internal_width = supersampled.shape[0]
  if internal_width == output_width:
    return supersampled

  # Simple box-filter downsampling
  ratio = internal_width / output_width
  result = np.zeros((output_width, N, 3), dtype=np.uint8)
  for x in range(output_width):
    start = int(x * ratio)
    end = int((x + 1) * ratio)
    result[x] = np.mean(supersampled[start:end], axis=0).astype(np.uint8)
  return result
