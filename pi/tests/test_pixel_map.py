"""
Tests for pixel_map config — loading, validation, and compilation.

TDD: these tests define the contract for pi/app/config/pixel_map.py.
"""

import tempfile
from pathlib import Path

import numpy as np
import pytest
import yaml

from app.config.pixel_map import (
  CompiledPixelMap,
  PixelMapConfig,
  ScanlineConfig,
  SegmentConfig,
  StripConfig,
  compile_pixel_map,
  load_pixel_map,
  save_pixel_map,
  validate_pixel_map,
)


def _simple_map() -> PixelMapConfig:
  """
  Minimal 2-column, 3-row grid with 1 strip of 6 LEDs.

  Strip 0: output 0, offset 0, 6 LEDs, BGR color order.
    Scanline 0: col 0 going up — (0,0) → (0,2) = 3 LEDs
    Scanline 1: col 1 going down — (1,2) → (1,0) = 3 LEDs
  Segments: one segment covering all 6 LEDs, BGR.
  """
  return PixelMapConfig(
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
          ScanlineConfig(start=(0, 0), end=(0, 2)),   # col 0, up
          ScanlineConfig(start=(1, 2), end=(1, 0)),   # col 1, down
        ],
        pixel_overrides={},
      ),
    ],
  )


# ---------------------------------------------------------------------------
# TestScanlineLedCount
# ---------------------------------------------------------------------------

class TestScanlineLedCount:
  """Scanline LED counting: vertical, horizontal, and diagonal rejection."""

  def test_vertical_up(self):
    s = ScanlineConfig(start=(0, 0), end=(0, 4))
    assert s.led_count() == 5  # abs(0)+abs(4)+1

  def test_vertical_down(self):
    s = ScanlineConfig(start=(3, 10), end=(3, 0))
    assert s.led_count() == 11

  def test_horizontal_right(self):
    s = ScanlineConfig(start=(0, 5), end=(7, 5))
    assert s.led_count() == 8

  def test_horizontal_left(self):
    s = ScanlineConfig(start=(9, 0), end=(2, 0))
    assert s.led_count() == 8

  def test_diagonal_rejected(self):
    """Scanlines must be axis-aligned — diagonal raises ValueError."""
    s = ScanlineConfig(start=(0, 0), end=(3, 4))
    with pytest.raises(ValueError, match="axis-aligned"):
      s.led_count()

  def test_single_pixel(self):
    """A scanline from (2,5) to (2,5) covers exactly 1 LED."""
    s = ScanlineConfig(start=(2, 5), end=(2, 5))
    assert s.led_count() == 1

  def test_positions_vertical_up(self):
    s = ScanlineConfig(start=(0, 0), end=(0, 2))
    assert s.positions() == [(0, 0), (0, 1), (0, 2)]

  def test_positions_vertical_down(self):
    s = ScanlineConfig(start=(1, 2), end=(1, 0))
    assert s.positions() == [(1, 2), (1, 1), (1, 0)]

  def test_positions_horizontal_right(self):
    s = ScanlineConfig(start=(0, 5), end=(3, 5))
    assert s.positions() == [(0, 5), (1, 5), (2, 5), (3, 5)]

  def test_positions_horizontal_left(self):
    s = ScanlineConfig(start=(3, 0), end=(1, 0))
    assert s.positions() == [(3, 0), (2, 0), (1, 0)]

  def test_positions_diagonal_rejected(self):
    s = ScanlineConfig(start=(0, 0), end=(2, 3))
    with pytest.raises(ValueError, match="axis-aligned"):
      s.positions()


# ---------------------------------------------------------------------------
# TestValidation
# ---------------------------------------------------------------------------

class TestValidation:
  """Validation catches all structural errors in PixelMapConfig."""

  def test_valid_map_no_errors(self):
    config = _simple_map()
    errors = validate_pixel_map(config)
    assert errors == []

  def test_scanline_total_mismatch(self):
    """Scanline LED total must equal strip total_leds."""
    config = _simple_map()
    config.strips[0].total_leds = 10  # actual scanlines sum to 6
    errors = validate_pixel_map(config)
    assert any("total_leds" in e.lower() or "mismatch" in e.lower() for e in errors)

  def test_duplicate_grid_positions(self):
    """Two scanlines mapping to the same (x,y) is an error."""
    config = _simple_map()
    # Make both scanlines cover the same column
    config.strips[0].scanlines = [
      ScanlineConfig(start=(0, 0), end=(0, 2)),
      ScanlineConfig(start=(0, 0), end=(0, 2)),  # duplicate!
    ]
    errors = validate_pixel_map(config)
    assert any("duplicate" in e.lower() for e in errors)

  def test_output_overflow(self):
    """Strip offset + total_leds must not exceed teensy_max_leds_per_output."""
    config = _simple_map()
    config.strips[0].output_offset = 1198  # 1198 + 6 = 1204 > 1200
    errors = validate_pixel_map(config)
    assert any("overflow" in e.lower() or "exceed" in e.lower() for e in errors)

  def test_segment_coverage_gap(self):
    """Segments must cover all LEDs with no gaps."""
    config = _simple_map()
    config.strips[0].segments = [
      SegmentConfig(range_start=0, range_end=3, color_order="BGR"),
      # gap at index 4
      SegmentConfig(range_start=5, range_end=5, color_order="BGR"),
    ]
    errors = validate_pixel_map(config)
    assert any("coverage" in e.lower() or "gap" in e.lower() for e in errors)

  def test_negative_coordinates(self):
    """Scanline coordinates must be non-negative."""
    config = _simple_map()
    config.strips[0].scanlines[0] = ScanlineConfig(start=(-1, 0), end=(-1, 2))
    errors = validate_pixel_map(config)
    assert any("negative" in e.lower() or "non-negative" in e.lower() for e in errors)

  def test_segment_range_exceeds_total_leds(self):
    """Segment range_end must be < total_leds."""
    config = _simple_map()
    config.strips[0].segments = [
      SegmentConfig(range_start=0, range_end=9, color_order="BGR"),  # 9 >= 6
    ]
    errors = validate_pixel_map(config)
    assert any("range" in e.lower() or "exceed" in e.lower() or "bound" in e.lower() for e in errors)

  def test_overlapping_output_ranges(self):
    """Two strips on the same output pin must not have overlapping LED ranges."""
    config = PixelMapConfig(
      origin="bottom-left",
      teensy_outputs=8,
      teensy_max_leds_per_output=1200,
      teensy_wire_order="BGR",
      teensy_signal_family="ws281x_800khz",
      teensy_octo_pins=[2, 14, 7, 8, 6, 20, 21, 5],
      strips=[
        StripConfig(
          id=0, output=0, output_offset=0, total_leds=3,
          segments=[SegmentConfig(range_start=0, range_end=2, color_order="BGR")],
          scanlines=[ScanlineConfig(start=(0, 0), end=(0, 2))],
          pixel_overrides={},
        ),
        StripConfig(
          id=1, output=0, output_offset=2, total_leds=3,  # overlaps: [2..4] vs [0..2]
          segments=[SegmentConfig(range_start=0, range_end=2, color_order="BGR")],
          scanlines=[ScanlineConfig(start=(1, 0), end=(1, 2))],
          pixel_overrides={},
        ),
      ],
    )
    errors = validate_pixel_map(config)
    assert any("overlap" in e.lower() for e in errors)

  def test_non_overlapping_output_ranges(self):
    """Adjacent but non-overlapping ranges on the same pin should be valid."""
    config = PixelMapConfig(
      origin="bottom-left",
      teensy_outputs=8,
      teensy_max_leds_per_output=1200,
      teensy_wire_order="BGR",
      teensy_signal_family="ws281x_800khz",
      teensy_octo_pins=[2, 14, 7, 8, 6, 20, 21, 5],
      strips=[
        StripConfig(
          id=0, output=0, output_offset=0, total_leds=3,
          segments=[SegmentConfig(range_start=0, range_end=2, color_order="BGR")],
          scanlines=[ScanlineConfig(start=(0, 0), end=(0, 2))],
          pixel_overrides={},
        ),
        StripConfig(
          id=1, output=0, output_offset=3, total_leds=3,  # adjacent: [3..5] vs [0..2]
          segments=[SegmentConfig(range_start=0, range_end=2, color_order="BGR")],
          scanlines=[ScanlineConfig(start=(1, 0), end=(1, 2))],
          pixel_overrides={},
        ),
      ],
    )
    errors = validate_pixel_map(config)
    assert not any("overlap" in e.lower() for e in errors)


# ---------------------------------------------------------------------------
# TestCompilation
# ---------------------------------------------------------------------------

class TestCompilation:
  """Compilation produces correct LUTs and output config."""

  def setup_method(self):
    self.config = _simple_map()
    self.compiled = compile_pixel_map(self.config)

  def test_grid_dimensions(self):
    assert self.compiled.width == 2
    assert self.compiled.height == 3

  def test_forward_lut_shape(self):
    """forward_lut is (width, height, 2) int16."""
    assert self.compiled.forward_lut.shape == (2, 3, 2)
    assert self.compiled.forward_lut.dtype == np.int16

  def test_forward_lut_values(self):
    """
    Grid cell (0,0) → strip 0, LED 0
    Grid cell (0,1) → strip 0, LED 1
    Grid cell (0,2) → strip 0, LED 2
    Grid cell (1,2) → strip 0, LED 3
    Grid cell (1,1) → strip 0, LED 4
    Grid cell (1,0) → strip 0, LED 5
    """
    lut = self.compiled.forward_lut
    # Col 0, going up: LED indices 0,1,2
    assert tuple(lut[0, 0]) == (0, 0)
    assert tuple(lut[0, 1]) == (0, 1)
    assert tuple(lut[0, 2]) == (0, 2)
    # Col 1, going down from top: LED indices 3,4,5
    assert tuple(lut[1, 2]) == (0, 3)
    assert tuple(lut[1, 1]) == (0, 4)
    assert tuple(lut[1, 0]) == (0, 5)

  def test_reverse_lut(self):
    """reverse_lut[strip_id][led_index] → (x, y, swizzle_tuple)."""
    rlut = self.compiled.reverse_lut
    assert len(rlut) > 0
    # LED 0 maps to (0, 0) with BGR swizzle
    x, y, swizzle = rlut[0][0]
    assert (x, y) == (0, 0)
    assert swizzle == (2, 1, 0)  # BGR

    # LED 3 maps to (1, 2)
    x, y, swizzle = rlut[0][3]
    assert (x, y) == (1, 2)

    # LED 5 maps to (1, 0)
    x, y, swizzle = rlut[0][5]
    assert (x, y) == (1, 0)

  def test_output_config(self):
    """output_config maps output index → list of (strip_id, offset, count)."""
    oc = self.compiled.output_config
    assert 0 in oc
    assert oc[0] == [(0, 0, 6)]

  def test_unmapped_cells(self):
    """A grid with an unmapped cell should have [-1, -1] in forward_lut."""
    # Create a config with a 3x3 grid but only map part of it
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
          total_leds=3,
          segments=[SegmentConfig(range_start=0, range_end=2, color_order="BGR")],
          scanlines=[ScanlineConfig(start=(0, 0), end=(0, 2))],
          pixel_overrides={},
        ),
        StripConfig(
          id=1,
          output=0,
          output_offset=3,
          total_leds=2,
          segments=[SegmentConfig(range_start=0, range_end=1, color_order="BGR")],
          # Only map (1,0) and (1,1) — (1,2) is unmapped
          scanlines=[ScanlineConfig(start=(1, 0), end=(1, 1))],
          pixel_overrides={},
        ),
      ],
    )
    compiled = compile_pixel_map(config)
    # (1,2) should be unmapped
    assert tuple(compiled.forward_lut[1, 2]) == (-1, -1)

  def test_total_mapped_leds(self):
    assert self.compiled.total_mapped_leds == 6

  def test_pixel_overrides_applied(self):
    """pixel_overrides remap individual LEDs to different grid positions."""
    config = _simple_map()
    # Override LED 5 (normally at (1,0)) to grid position (0,0)
    # But (0,0) is already mapped, so let's override to a new position
    # First, extend the grid by making LED 5 go to (2,0) instead
    config.strips[0].pixel_overrides = {5: (2, 0)}
    compiled = compile_pixel_map(config)
    # Grid should now be 3 wide
    assert compiled.width == 3
    # (2,0) should map to strip 0, LED 5
    assert tuple(compiled.forward_lut[2, 0]) == (0, 5)


# ---------------------------------------------------------------------------
# TestLoadFromYaml
# ---------------------------------------------------------------------------

class TestLoadFromYaml:
  """Round-trip: save to YAML, load, validate, compile."""

  def test_load_from_yaml(self):
    config = _simple_map()
    with tempfile.TemporaryDirectory() as tmpdir:
      config_dir = Path(tmpdir)
      save_pixel_map(config, config_dir)

      loaded = load_pixel_map(config_dir)

    # Verify structural equivalence
    assert loaded.origin == config.origin
    assert loaded.teensy_outputs == config.teensy_outputs
    assert loaded.teensy_max_leds_per_output == config.teensy_max_leds_per_output
    assert loaded.teensy_wire_order == config.teensy_wire_order
    assert loaded.teensy_signal_family == config.teensy_signal_family
    assert loaded.teensy_octo_pins == config.teensy_octo_pins
    assert len(loaded.strips) == len(config.strips)

    strip = loaded.strips[0]
    assert strip.id == 0
    assert strip.output == 0
    assert strip.output_offset == 0
    assert strip.total_leds == 6
    assert len(strip.scanlines) == 2
    assert strip.scanlines[0].start == (0, 0)
    assert strip.scanlines[0].end == (0, 2)
    assert strip.scanlines[1].start == (1, 2)
    assert strip.scanlines[1].end == (1, 0)
    assert len(strip.segments) == 1
    assert strip.segments[0].range_start == 0
    assert strip.segments[0].range_end == 5
    assert strip.segments[0].color_order == "BGR"

  def test_load_validates_successfully(self):
    """A loaded config should pass validation."""
    config = _simple_map()
    with tempfile.TemporaryDirectory() as tmpdir:
      config_dir = Path(tmpdir)
      save_pixel_map(config, config_dir)
      loaded = load_pixel_map(config_dir)

    errors = validate_pixel_map(loaded)
    assert errors == []

  def test_load_compiles_successfully(self):
    """A loaded config should compile without error."""
    config = _simple_map()
    with tempfile.TemporaryDirectory() as tmpdir:
      config_dir = Path(tmpdir)
      save_pixel_map(config, config_dir)
      loaded = load_pixel_map(config_dir)

    compiled = compile_pixel_map(loaded)
    assert compiled.width == 2
    assert compiled.height == 3
    assert compiled.total_mapped_leds == 6


# ---------------------------------------------------------------------------
# TestDefaultConfig
# ---------------------------------------------------------------------------

class TestDefaultConfig:
  """Validate the shipped pixel_map.yaml matches the 10×172 pillar."""

  @pytest.fixture(autouse=True)
  def load_default(self):
    config_dir = Path(__file__).parent.parent / "config"
    self.config = load_pixel_map(config_dir)
    self.compiled = compile_pixel_map(self.config)

  def test_validates_clean(self):
    errors = validate_pixel_map(self.config)
    assert errors == [], f"Validation errors: {errors}"

  def test_grid_10x172(self):
    assert self.compiled.width == 10
    assert self.compiled.height == 172

  def test_total_leds(self):
    assert self.compiled.total_mapped_leds == 1720

  def test_10_strips(self):
    assert len(self.config.strips) == 10

  def test_5_outputs(self):
    assert len(self.compiled.output_config) == 5

  def test_no_unmapped_cells(self):
    """Every cell in the 10×172 grid should be mapped."""
    unmapped = (self.compiled.forward_lut[:, :, 0] == -1)
    assert not unmapped.any(), "Found unmapped cells in default config"
