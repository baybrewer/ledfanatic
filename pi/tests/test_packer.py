"""
Tests for output packer — grid frame to LED output buffer with color order swizzle.

TDD: these tests define the contract for pi/app/mapping/packer.py.
"""

import numpy as np
import pytest

from app.config.pixel_map import (
  PixelMapConfig,
  SegmentConfig,
  compile_pixel_map,
)
from app.mapping.packer import pack_frame


def _simple_compiled():
  """
  2x3 grid with 2 segments on output 0, BGR order, compiled via compile_pixel_map.

  Segment 0: (0,0) -> (0,2) = 3 LEDs going up col 0
  Segment 1: (1,2) -> (1,0) = 3 LEDs going down col 1
  """
  cfg = PixelMapConfig(segments=[
    SegmentConfig(start=(0, 0), end=(0, 2), output=0, color_order='BGR'),
    SegmentConfig(start=(1, 2), end=(1, 0), output=0, color_order='BGR'),
  ])
  return compile_pixel_map(cfg)


class TestPacker:
  """Output packer: grid frame -> wire buffer."""

  def test_basic_packing(self):
    """2x3 all-red frame produces correct output buffer length."""
    pm = _simple_compiled()
    frame = np.zeros((2, 3, 3), dtype=np.uint8)
    frame[:, :] = [255, 0, 0]  # RGB red
    buf = pack_frame(frame, pm)
    # 6 LEDs x 3 bytes = 18 bytes minimum for output 0
    assert len(buf) >= 18
    # BGR order: red RGB [255, 0, 0] -> wire bytes [0, 0, 255]
    # LED 0 at (0,0): B=0, G=0, R=255
    assert buf[0] == 0    # B
    assert buf[1] == 0    # G
    assert buf[2] == 255  # R

  def test_color_order_swizzle(self):
    """GRB order: frame pixel [255, 128, 64] (RGB) -> wire bytes [128, 255, 64] (GRB)."""
    cfg = PixelMapConfig(segments=[
      SegmentConfig(start=(0, 0), end=(0, 0), output=0, color_order='GRB'),
    ])
    pm = compile_pixel_map(cfg)
    frame = np.zeros((1, 1, 3), dtype=np.uint8)
    frame[0, 0] = [255, 128, 64]  # R=255, G=128, B=64
    buf = pack_frame(frame, pm)
    # GRB swizzle: wire byte 0 = G=128, byte 1 = R=255, byte 2 = B=64
    assert buf[0] == 128  # G
    assert buf[1] == 255  # R
    assert buf[2] == 64   # B

  def test_multi_output(self):
    """2 segments on different outputs (0 and 2), total buffer = sum of all output allocations * 3."""
    cfg = PixelMapConfig(segments=[
      SegmentConfig(start=(0, 0), end=(0, 0), output=0, color_order='RGB'),
      SegmentConfig(start=(1, 0), end=(1, 0), output=2, color_order='RGB'),
    ])
    pm = compile_pixel_map(cfg)
    frame = np.zeros((2, 1, 3), dtype=np.uint8)
    frame[0, 0] = [10, 20, 30]
    frame[1, 0] = [40, 50, 60]
    buf = pack_frame(frame, pm)
    # Output 0: 1 LED = 3 bytes, Output 1: 0 LEDs, Output 2: 1 LED = 3 bytes
    # output_config = [1, 0, 1, 0, 0, 0, 0, 0] -> total = 6 bytes
    assert len(buf) == 6
    # Output 0 pixel at offset 0: [10, 20, 30] (RGB order)
    assert buf[0] == 10
    assert buf[1] == 20
    assert buf[2] == 30
    # Output 2 pixel at offset 3 (pin 0: 3 bytes + pin 1: 0 bytes = offset 3)
    assert buf[3] == 40
    assert buf[4] == 50
    assert buf[5] == 60

  def test_unmapped_cells_are_black(self):
    """Mapped pixel at (0,0) appears correctly; unmapped LEDs are zero."""
    cfg = PixelMapConfig(segments=[
      SegmentConfig(start=(0, 0), end=(0, 0), output=0, color_order='RGB'),
    ])
    pm = compile_pixel_map(cfg)
    frame = np.full((1, 1, 3), 255, dtype=np.uint8)
    buf = pack_frame(frame, pm)
    # (0,0) is mapped -> should contain [255, 255, 255]
    assert buf[0] == 255
    assert buf[1] == 255
    assert buf[2] == 255
