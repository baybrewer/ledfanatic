"""Tests for cylinder mapping math."""

import numpy as np
import pytest
from app.mapping.cylinder import (
  logical_to_channel, map_frame_fast, serialize_channels,
  wrap_x, downsample_width, N,
)


class TestLogicalToChannel:
  """Verify the serpentine mapping formula."""

  def test_s0_bottom(self):
    ch, idx = logical_to_channel(0, 0)
    assert ch == 0
    assert idx == 0

  def test_s0_top(self):
    ch, idx = logical_to_channel(0, N - 1)
    assert ch == 0
    assert idx == N - 1

  def test_s1_top(self):
    # S1 is wired top→bottom, so logical top (y=N-1) = index N
    ch, idx = logical_to_channel(1, N - 1)
    assert ch == 0
    assert idx == N

  def test_s1_bottom(self):
    # S1 logical bottom (y=0) = index 2*N-1
    ch, idx = logical_to_channel(1, 0)
    assert ch == 0
    assert idx == 2 * N - 1

  def test_s2_bottom(self):
    ch, idx = logical_to_channel(2, 0)
    assert ch == 1
    assert idx == 0

  def test_s3_bottom(self):
    ch, idx = logical_to_channel(3, 0)
    assert ch == 1
    assert idx == 2 * N - 1

  def test_s9_bottom(self):
    ch, idx = logical_to_channel(9, 0)
    assert ch == 4
    assert idx == 2 * N - 1

  def test_all_channels_covered(self):
    """Every logical pixel maps to a unique (channel, index) pair."""
    seen = set()
    for x in range(10):
      for y in range(N):
        ch, idx = logical_to_channel(x, y)
        assert 0 <= ch < 5
        assert 0 <= idx < 2 * N
        key = (ch, idx)
        assert key not in seen, f"Duplicate mapping at ({x}, {y}) -> {key}"
        seen.add(key)
    assert len(seen) == 10 * N

  def test_even_strips_bottom_to_top(self):
    """Even strips (0, 2, 4, 6, 8) map y directly to index."""
    for x in [0, 2, 4, 6, 8]:
      for y in range(N):
        _, idx = logical_to_channel(x, y)
        assert idx == y

  def test_odd_strips_top_to_bottom(self):
    """Odd strips (1, 3, 5, 7, 9) map y=0 to index 2*N-1, y=N-1 to index N."""
    for x in [1, 3, 5, 7, 9]:
      _, idx_bottom = logical_to_channel(x, 0)
      _, idx_top = logical_to_channel(x, N - 1)
      assert idx_bottom == 2 * N - 1
      assert idx_top == N


class TestMapFrameFast:
  def test_output_shape(self):
    frame = np.zeros((10, N, 3), dtype=np.uint8)
    result = map_frame_fast(frame)
    assert result.shape == (5, 2 * N, 3)

  def test_identity_mapping(self):
    """A known pixel should appear at the correct position."""
    frame = np.zeros((10, N, 3), dtype=np.uint8)
    frame[0, 0] = (255, 0, 0)  # S0 bottom -> CH0 index 0
    frame[1, 0] = (0, 255, 0)  # S1 bottom -> CH0 index 2*N-1

    result = map_frame_fast(frame)
    assert tuple(result[0, 0]) == (255, 0, 0)
    assert tuple(result[0, 2 * N - 1]) == (0, 255, 0)

  def test_s1_top_maps_to_ch0_N(self):
    frame = np.zeros((10, N, 3), dtype=np.uint8)
    frame[1, N - 1] = (0, 0, 255)  # S1 top -> CH0 index N
    result = map_frame_fast(frame)
    assert tuple(result[0, N]) == (0, 0, 255)

  def test_all_zeros(self):
    frame = np.zeros((10, N, 3), dtype=np.uint8)
    result = map_frame_fast(frame)
    assert np.all(result == 0)

  def test_bad_shape_raises(self):
    with pytest.raises(AssertionError):
      map_frame_fast(np.zeros((5, N, 3), dtype=np.uint8))


class TestSerializeChannels:
  def test_output_length(self):
    data = np.zeros((5, 2 * N, 3), dtype=np.uint8)
    result = serialize_channels(data)
    assert len(result) == 5 * 2 * N * 3

  def test_byte_order(self):
    """Bytes are RGB-ordered; OctoWS2811 firmware handles GRB reorder."""
    data = np.zeros((5, 2 * N, 3), dtype=np.uint8)
    data[0, 0] = (10, 20, 30)
    result = serialize_channels(data)
    assert result[0] == 10
    assert result[1] == 20
    assert result[2] == 30


class TestWrapX:
  def test_normal(self):
    assert wrap_x(5) == 5

  def test_wrap_positive(self):
    assert wrap_x(10) == 0
    assert wrap_x(11) == 1

  def test_wrap_negative(self):
    assert wrap_x(-1) == 9
    assert wrap_x(-2) == 8


class TestDownsample:
  def test_identity(self):
    frame = np.ones((10, N, 3), dtype=np.uint8) * 128
    result = downsample_width(frame, 10)
    assert result.shape == (10, N, 3)
    assert np.all(result == 128)

  def test_2x_downsample(self):
    frame = np.ones((20, N, 3), dtype=np.uint8) * 100
    result = downsample_width(frame, 10)
    assert result.shape == (10, N, 3)
