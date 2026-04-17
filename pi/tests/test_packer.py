"""
Tests for output packer — grid frame to LED output buffer with color order swizzle.

TDD: these tests define the contract for pi/app/mapping/packer.py.
"""

import numpy as np
import pytest

from app.config.pixel_map import (
  CompiledPixelMap,
  PixelMapConfig,
  ScanlineConfig,
  SegmentConfig,
  StripConfig,
  compile_pixel_map,
)
from app.mapping.packer import pack_frame


def _simple_compiled() -> CompiledPixelMap:
  """
  2x3 grid with 1 strip of 6 LEDs (BGR order), compiled via compile_pixel_map.

  Strip 0: output 0, offset 0, 6 LEDs, BGR color order.
    Scanline 0: col 0 going up — (0,0) → (0,2) = 3 LEDs
    Scanline 1: col 1 going down — (1,2) → (1,0) = 3 LEDs
  """
  config = PixelMapConfig(
    origin="bottom-left",
    teensy_outputs=8,
    teensy_max_leds_per_output=1200,
    teensy_wire_order="BGR",
    teensy_signal_family="ws281x_800khz",
    teensy_octo_pins=[2, 14, 7, 8, 6, 20, 21, 5],
    strips=[
      StripConfig(
        id=0,
        output=0,
        output_offset=0,
        total_leds=6,
        segments=[
          SegmentConfig(range_start=0, range_end=5, color_order="BGR"),
        ],
        scanlines=[
          ScanlineConfig(start=(0, 0), end=(0, 2)),
          ScanlineConfig(start=(1, 2), end=(1, 0)),
        ],
        pixel_overrides={},
      ),
    ],
  )
  return compile_pixel_map(config)


class TestPacker:
  """Output packer: grid frame → wire buffer."""

  def test_basic_packing(self):
    """2x3 all-red frame produces correct output buffer length."""
    pm = _simple_compiled()
    frame = np.zeros((2, 3, 3), dtype=np.uint8)
    frame[:, :] = [255, 0, 0]  # RGB red
    buf = pack_frame(frame, pm)
    # 6 LEDs x 3 bytes = 18 bytes minimum for output 0
    assert len(buf) >= 18
    # BGR order: red RGB [255, 0, 0] → wire bytes [0, 0, 255]
    # LED 0 at (0,0): B=0, G=0, R=255
    assert buf[0] == 0    # B
    assert buf[1] == 0    # G
    assert buf[2] == 255  # R

  def test_color_order_swizzle(self):
    """GRB order: frame pixel [255, 128, 64] (RGB) → wire bytes [128, 255, 64] (GRB)."""
    config = PixelMapConfig(
      origin="bottom-left",
      teensy_outputs=8,
      teensy_max_leds_per_output=1200,
      teensy_wire_order="GRB",
      teensy_signal_family="ws281x_800khz",
      teensy_octo_pins=[2, 14, 7, 8, 6, 20, 21, 5],
      strips=[
        StripConfig(
          id=0,
          output=0,
          output_offset=0,
          total_leds=1,
          segments=[SegmentConfig(range_start=0, range_end=0, color_order="GRB")],
          scanlines=[ScanlineConfig(start=(0, 0), end=(0, 0))],
          pixel_overrides={},
        ),
      ],
    )
    pm = compile_pixel_map(config)
    frame = np.zeros((1, 1, 3), dtype=np.uint8)
    frame[0, 0] = [255, 128, 64]  # R=255, G=128, B=64
    buf = pack_frame(frame, pm)
    # GRB swizzle: wire byte 0 = G=128, byte 1 = R=255, byte 2 = B=64
    assert buf[0] == 128  # G
    assert buf[1] == 255  # R
    assert buf[2] == 64   # B

  def test_multi_output(self):
    """2 strips on different outputs (0 and 2), total buffer = sum of all output allocations * 3."""
    config = PixelMapConfig(
      origin="bottom-left",
      teensy_outputs=8,
      teensy_max_leds_per_output=1200,
      teensy_wire_order="RGB",
      teensy_signal_family="ws281x_800khz",
      teensy_octo_pins=[2, 14, 7, 8, 6, 20, 21, 5],
      strips=[
        StripConfig(
          id=0,
          output=0,
          output_offset=0,
          total_leds=1,
          segments=[SegmentConfig(range_start=0, range_end=0, color_order="RGB")],
          scanlines=[ScanlineConfig(start=(0, 0), end=(0, 0))],
          pixel_overrides={},
        ),
        StripConfig(
          id=1,
          output=2,
          output_offset=0,
          total_leds=1,
          segments=[SegmentConfig(range_start=0, range_end=0, color_order="RGB")],
          scanlines=[ScanlineConfig(start=(1, 0), end=(1, 0))],
          pixel_overrides={},
        ),
      ],
    )
    pm = compile_pixel_map(config)
    frame = np.zeros((2, 1, 3), dtype=np.uint8)
    frame[0, 0] = [10, 20, 30]
    frame[1, 0] = [40, 50, 60]
    buf = pack_frame(frame, pm)
    # Output 0: 1 LED = 3 bytes, Output 1: 0 LEDs, Output 2: 1 LED = 3 bytes
    # Total = (1 + 0 + 1) * 3 = 6 bytes for outputs 0-2 only
    # But buffer should cover all outputs 0 through max used (2), so:
    # pin 0: 1 LED, pin 1: 0 LEDs, pin 2: 1 LED → 3 + 0 + 3 = 6 bytes
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
    config = PixelMapConfig(
      origin="bottom-left",
      teensy_outputs=8,
      teensy_max_leds_per_output=1200,
      teensy_wire_order="RGB",
      teensy_signal_family="ws281x_800khz",
      teensy_octo_pins=[2, 14, 7, 8, 6, 20, 21, 5],
      strips=[
        StripConfig(
          id=0,
          output=0,
          output_offset=0,
          total_leds=1,
          segments=[SegmentConfig(range_start=0, range_end=0, color_order="RGB")],
          scanlines=[ScanlineConfig(start=(0, 0), end=(0, 0))],
          pixel_overrides={},
        ),
      ],
    )
    pm = compile_pixel_map(config)
    frame = np.full((1, 1, 3), 255, dtype=np.uint8)
    buf = pack_frame(frame, pm)
    # (0,0) is mapped → should contain [255, 255, 255]
    assert buf[0] == 255
    assert buf[1] == 255
    assert buf[2] == 255
