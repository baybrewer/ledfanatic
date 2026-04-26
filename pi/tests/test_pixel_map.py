"""
Tests for pixel_map config — loading, validation, and compilation.

TDD: these tests define the contract for pi/app/config/pixel_map.py.
Tests the flat SegmentConfig model (schema v2) and backward compat from v1.
"""

import tempfile
from pathlib import Path

import numpy as np
import pytest
import yaml

from app.config.pixel_map import (
  CompiledPixelMap,
  PixelMapConfig,
  SegmentConfig,
  LineConfig,
  compile_pixel_map,
  load_pixel_map,
  save_pixel_map,
  validate_pixel_map,
)


def _simple_map() -> PixelMapConfig:
  """
  Minimal 2-column, 3-row grid with 2 segments totaling 6 LEDs.

  Segment 0: output 0, col 0 going up — (0,0) -> (0,2) = 3 LEDs, BGR
  Segment 1: output 0, col 1 going down — (1,2) -> (1,0) = 3 LEDs, BGR
  """
  return PixelMapConfig(
    origin="bottom-left",
    teensy_outputs=8,
    teensy_max_leds_per_output=1200,
    teensy_wire_order="BGR",
    teensy_signal_family="ws281x_800khz",
    teensy_octo_pins=[2, 14, 7, 8, 6, 20, 21, 5],
    segments=[
      SegmentConfig(start=(0, 0), end=(0, 2), output=0, color_order="BGR"),
      SegmentConfig(start=(1, 2), end=(1, 0), output=0, color_order="BGR"),
    ],
  )


# ---------------------------------------------------------------------------
# TestSegmentLedCount
# ---------------------------------------------------------------------------

class TestSegmentLedCount:
  """Segment LED counting: vertical, horizontal, and diagonal rejection."""

  def test_vertical_up(self):
    s = SegmentConfig(start=(0, 0), end=(0, 4), output=0)
    assert s.led_count() == 5

  def test_vertical_down(self):
    s = SegmentConfig(start=(3, 10), end=(3, 0), output=0)
    assert s.led_count() == 11

  def test_horizontal_right(self):
    s = SegmentConfig(start=(0, 5), end=(7, 5), output=0)
    assert s.led_count() == 8

  def test_horizontal_left(self):
    s = SegmentConfig(start=(9, 0), end=(2, 0), output=0)
    assert s.led_count() == 8

  def test_diagonal_rejected(self):
    """Segments must be axis-aligned — diagonal raises ValueError."""
    s = SegmentConfig(start=(0, 0), end=(3, 4), output=0)
    with pytest.raises(ValueError, match="axis-aligned"):
      s.led_count()

  def test_single_pixel(self):
    """A segment from (2,5) to (2,5) covers exactly 1 LED."""
    s = SegmentConfig(start=(2, 5), end=(2, 5), output=0)
    assert s.led_count() == 1

  def test_positions_vertical_up(self):
    s = SegmentConfig(start=(0, 0), end=(0, 2), output=0)
    assert s.positions() == [(0, 0), (0, 1), (0, 2)]

  def test_positions_vertical_down(self):
    s = SegmentConfig(start=(1, 2), end=(1, 0), output=0)
    assert s.positions() == [(1, 2), (1, 1), (1, 0)]

  def test_positions_horizontal_right(self):
    s = SegmentConfig(start=(0, 5), end=(3, 5), output=0)
    assert s.positions() == [(0, 5), (1, 5), (2, 5), (3, 5)]

  def test_positions_horizontal_left(self):
    s = SegmentConfig(start=(3, 0), end=(1, 0), output=0)
    assert s.positions() == [(3, 0), (2, 0), (1, 0)]

  def test_positions_diagonal_rejected(self):
    s = SegmentConfig(start=(0, 0), end=(2, 3), output=0)
    with pytest.raises(ValueError, match="axis-aligned"):
      s.positions()


# ---------------------------------------------------------------------------
# TestBackwardCompatAlias
# ---------------------------------------------------------------------------

class TestBackwardCompatAlias:
  """LineConfig is an alias for SegmentConfig."""

  def test_line_config_is_segment_config(self):
    assert LineConfig is SegmentConfig

  def test_line_config_works(self):
    ln = LineConfig(start=(0, 0), end=(0, 5), output=0, color_order="BGR")
    assert ln.led_count() == 6


# ---------------------------------------------------------------------------
# TestValidation
# ---------------------------------------------------------------------------

class TestValidation:
  """Validation catches all structural errors in PixelMapConfig."""

  def test_valid_map_no_errors(self):
    config = _simple_map()
    errors = validate_pixel_map(config)
    assert errors == []

  def test_duplicate_grid_positions(self):
    """Two segments mapping to the same (x,y) is an error."""
    config = PixelMapConfig(segments=[
      SegmentConfig(start=(0, 0), end=(0, 2), output=0),
      SegmentConfig(start=(0, 0), end=(0, 2), output=1),  # duplicate positions!
    ])
    errors = validate_pixel_map(config)
    assert any("duplicate" in e.lower() for e in errors)

  def test_output_overflow(self):
    """Total LEDs on a pin must not exceed teensy_max_leds_per_output."""
    config = PixelMapConfig(
      teensy_max_leds_per_output=5,
      segments=[
        SegmentConfig(start=(0, 0), end=(0, 2), output=0),  # 3
        SegmentConfig(start=(1, 0), end=(1, 2), output=0),  # 3, total=6 > 5
      ],
    )
    errors = validate_pixel_map(config)
    assert any("exceed" in e.lower() or "overflow" in e.lower() for e in errors)

  def test_negative_coordinates(self):
    """Segment coordinates must be non-negative."""
    config = PixelMapConfig(segments=[
      SegmentConfig(start=(-1, 0), end=(-1, 2), output=0),
    ])
    errors = validate_pixel_map(config)
    assert any("negative" in e.lower() or "non-negative" in e.lower() for e in errors)

  def test_invalid_color_order(self):
    """Segments must have a valid color_order from SWIZZLE_MAP."""
    config = PixelMapConfig(segments=[
      SegmentConfig(start=(0, 0), end=(0, 2), output=0, color_order="XYZ"),
    ])
    errors = validate_pixel_map(config)
    assert any("color_order" in e.lower() or "invalid" in e.lower() for e in errors)

  def test_teensy_outputs_must_be_8(self):
    """OctoWS2811 requires exactly 8 outputs."""
    config = _simple_map()
    config.teensy_outputs = 4
    errors = validate_pixel_map(config)
    assert any("teensy_outputs" in e.lower() or "exactly 8" in e.lower() for e in errors)

  def test_segment_output_must_be_in_range(self):
    """Segment output index must be 0-7."""
    config = PixelMapConfig(segments=[
      SegmentConfig(start=(0, 0), end=(0, 2), output=8),
    ])
    errors = validate_pixel_map(config)
    assert any("output" in e.lower() and "range" in e.lower() for e in errors)

  def test_different_color_orders_per_segment(self):
    """Different segments can have different color orders."""
    config = PixelMapConfig(segments=[
      SegmentConfig(start=(0, 0), end=(0, 2), output=0, color_order="BGR"),
      SegmentConfig(start=(1, 2), end=(1, 0), output=0, color_order="GRB"),
    ])
    errors = validate_pixel_map(config)
    assert errors == []
    compiled = compile_pixel_map(config)
    # Segment 0 should be BGR swizzle
    assert compiled.reverse_lut[0][0][2] == (2, 1, 0)  # BGR
    # Segment 1 should be GRB swizzle
    assert compiled.reverse_lut[1][0][2] == (1, 0, 2)  # GRB

  def test_no_strip_id_uniqueness_check(self):
    """No strip IDs exist — segments are identified by index only."""
    # Two segments on the same output is fine (auto-offset)
    config = PixelMapConfig(segments=[
      SegmentConfig(start=(0, 0), end=(0, 2), output=0),
      SegmentConfig(start=(1, 0), end=(1, 2), output=0),
    ])
    errors = validate_pixel_map(config)
    assert errors == []

  def test_multiple_segments_same_output_valid(self):
    """Multiple segments on the same output should be valid if total fits."""
    config = PixelMapConfig(
      teensy_max_leds_per_output=1200,
      segments=[
        SegmentConfig(start=(0, 0), end=(0, 99), output=0),   # 100 LEDs
        SegmentConfig(start=(1, 0), end=(1, 99), output=0),   # 100 LEDs, total=200
      ],
    )
    errors = validate_pixel_map(config)
    assert errors == []


# ---------------------------------------------------------------------------
# TestCompilation
# ---------------------------------------------------------------------------

class TestCompilation:
  """Compilation produces correct LUTs, output config, and segment offsets."""

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
    Grid cell (0,0) -> segment 0, LED 0
    Grid cell (0,1) -> segment 0, LED 1
    Grid cell (0,2) -> segment 0, LED 2
    Grid cell (1,2) -> segment 1, LED 0
    Grid cell (1,1) -> segment 1, LED 1
    Grid cell (1,0) -> segment 1, LED 2
    """
    lut = self.compiled.forward_lut
    # Segment 0: col 0 going up, LED indices 0,1,2
    assert tuple(lut[0, 0]) == (0, 0)
    assert tuple(lut[0, 1]) == (0, 1)
    assert tuple(lut[0, 2]) == (0, 2)
    # Segment 1: col 1 going down from top, LED indices 0,1,2
    assert tuple(lut[1, 2]) == (1, 0)
    assert tuple(lut[1, 1]) == (1, 1)
    assert tuple(lut[1, 0]) == (1, 2)

  def test_reverse_lut(self):
    """reverse_lut[segment_index][led_index] -> (x, y, swizzle_tuple)."""
    rlut = self.compiled.reverse_lut
    assert len(rlut) == 2  # 2 segments
    # Segment 0, LED 0 maps to (0, 0) with BGR swizzle
    x, y, swizzle = rlut[0][0]
    assert (x, y) == (0, 0)
    assert swizzle == (2, 1, 0)  # BGR

    # Segment 1, LED 0 maps to (1, 2)
    x, y, swizzle = rlut[1][0]
    assert (x, y) == (1, 2)

    # Segment 1, LED 2 maps to (1, 0)
    x, y, swizzle = rlut[1][2]
    assert (x, y) == (1, 0)

  def test_output_config(self):
    """output_config is list[int] with 8 entries, LEDs per pin."""
    oc = self.compiled.output_config
    assert len(oc) == 8
    assert oc[0] == 6  # Both segments on pin 0: 3 + 3 = 6
    assert oc[1] == 0
    assert oc[2] == 0

  def test_segment_offsets(self):
    """Segment offsets are auto-calculated sequentially on each output."""
    offsets = self.compiled.segment_offsets
    assert len(offsets) == 2
    assert offsets[0] == 0  # First segment on pin 0: offset 0
    assert offsets[1] == 3  # Second segment on pin 0: offset 3 (after 3 LEDs)

  def test_segment_offsets_multi_output(self):
    """Segments on different outputs each start at offset 0."""
    config = PixelMapConfig(segments=[
      SegmentConfig(start=(0, 0), end=(0, 2), output=0),
      SegmentConfig(start=(1, 0), end=(1, 2), output=1),
    ])
    compiled = compile_pixel_map(config)
    assert compiled.segment_offsets == [0, 0]
    assert compiled.output_config[0] == 3
    assert compiled.output_config[1] == 3

  def test_segment_offsets_three_on_one_pin(self):
    """Three segments on the same pin stack sequentially."""
    config = PixelMapConfig(segments=[
      SegmentConfig(start=(0, 0), end=(0, 4), output=2),   # 5 LEDs
      SegmentConfig(start=(1, 0), end=(1, 4), output=2),   # 5 LEDs
      SegmentConfig(start=(2, 0), end=(2, 4), output=2),   # 5 LEDs
    ])
    compiled = compile_pixel_map(config)
    assert compiled.segment_offsets == [0, 5, 10]
    assert compiled.output_config[2] == 15

  def test_unmapped_cells(self):
    """A grid with unmapped cells should have [-1, -1] in forward_lut."""
    config = PixelMapConfig(segments=[
      SegmentConfig(start=(0, 0), end=(0, 2), output=0),
      # Only map (1,0) and (1,1) — (1,2) is unmapped
      SegmentConfig(start=(1, 0), end=(1, 1), output=0),
    ])
    compiled = compile_pixel_map(config)
    # (1,2) should be unmapped
    assert tuple(compiled.forward_lut[1, 2]) == (-1, -1)

  def test_total_mapped_leds(self):
    assert self.compiled.total_mapped_leds == 6

  def test_empty_config(self):
    """Empty config compiles to zero-size pixel map."""
    config = PixelMapConfig()
    compiled = compile_pixel_map(config)
    assert compiled.width == 0
    assert compiled.height == 0
    assert compiled.total_mapped_leds == 0
    assert compiled.output_config == [0] * 8
    assert compiled.segment_offsets == []

  def test_pixel_overrides_applied(self):
    """pixel_overrides remap individual LEDs to different grid positions."""
    config = _simple_map()
    # Override segment 1, LED 2 (normally at (1,0)) to grid position (2,0)
    config.pixel_overrides = {"1:2": (2, 0)}
    compiled = compile_pixel_map(config)
    # Grid should now be 3 wide
    assert compiled.width == 3
    # (2,0) should map to segment 1, LED 2
    assert tuple(compiled.forward_lut[2, 0]) == (1, 2)


# ---------------------------------------------------------------------------
# TestLoadSaveV2
# ---------------------------------------------------------------------------

class TestLoadSaveV2:
  """Round-trip: save to YAML (schema v2), load, validate, compile."""

  def test_save_load_round_trip(self):
    config = _simple_map()
    with tempfile.TemporaryDirectory() as tmpdir:
      config_dir = Path(tmpdir)
      save_pixel_map(config, config_dir)
      loaded = load_pixel_map(config_dir)

    assert loaded.origin == config.origin
    assert loaded.teensy_outputs == config.teensy_outputs
    assert loaded.teensy_max_leds_per_output == config.teensy_max_leds_per_output
    assert loaded.teensy_wire_order == config.teensy_wire_order
    assert loaded.teensy_signal_family == config.teensy_signal_family
    assert loaded.teensy_octo_pins == config.teensy_octo_pins
    assert len(loaded.segments) == 2

    seg0 = loaded.segments[0]
    assert seg0.start == (0, 0)
    assert seg0.end == (0, 2)
    assert seg0.output == 0
    assert seg0.color_order == "BGR"

    seg1 = loaded.segments[1]
    assert seg1.start == (1, 2)
    assert seg1.end == (1, 0)
    assert seg1.output == 0
    assert seg1.color_order == "BGR"

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

  def test_schema_version_in_saved_yaml(self):
    """Saved YAML should include schema_version: 2."""
    config = _simple_map()
    with tempfile.TemporaryDirectory() as tmpdir:
      config_dir = Path(tmpdir)
      save_pixel_map(config, config_dir)
      with open(config_dir / "pixel_map.yaml") as f:
        data = yaml.safe_load(f)
    assert data["schema_version"] == 2
    assert "segments" in data
    assert "strips" not in data


# ---------------------------------------------------------------------------
# TestBackwardCompatMigration
# ---------------------------------------------------------------------------

class TestBackwardCompatMigration:
  """Loading v1 YAML (strips) auto-migrates to v2 (segments)."""

  def test_migrate_v1_strips_to_segments(self):
    """V1 strips with nested lines become flat segments."""
    v1_data = {
      "origin": "bottom-left",
      "teensy": {
        "outputs": 8,
        "max_leds_per_output": 1200,
        "wire_order": "BGR",
        "signal_family": "ws281x_800khz",
        "octo_pins": [2, 14, 7, 8, 6, 20, 21, 5],
      },
      "strips": [
        {
          "id": 0,
          "output": 0,
          "output_offset": 0,
          "lines": [
            {"start": [0, 0], "end": [0, 2], "color_order": "BGR"},
          ],
        },
        {
          "id": 1,
          "output": 0,
          "output_offset": 3,
          "lines": [
            {"start": [1, 2], "end": [1, 0], "color_order": "BGR"},
          ],
        },
      ],
    }
    with tempfile.TemporaryDirectory() as tmpdir:
      config_dir = Path(tmpdir)
      with open(config_dir / "pixel_map.yaml", "w") as f:
        yaml.dump(v1_data, f)
      loaded = load_pixel_map(config_dir)

    # Should have 2 segments (one line per strip)
    assert len(loaded.segments) == 2
    assert loaded.segments[0].start == (0, 0)
    assert loaded.segments[0].end == (0, 2)
    assert loaded.segments[0].output == 0
    assert loaded.segments[1].start == (1, 2)
    assert loaded.segments[1].end == (1, 0)
    assert loaded.segments[1].output == 0

  def test_migrate_v1_multi_line_strip(self):
    """A v1 strip with multiple lines produces multiple segments."""
    v1_data = {
      "strips": [
        {
          "id": 0,
          "output": 2,
          "output_offset": 0,
          "lines": [
            {"start": [0, 0], "end": [0, 3], "color_order": "BGR"},
            {"start": [1, 3], "end": [1, 0], "color_order": "GRB"},
          ],
        },
      ],
    }
    with tempfile.TemporaryDirectory() as tmpdir:
      config_dir = Path(tmpdir)
      with open(config_dir / "pixel_map.yaml", "w") as f:
        yaml.dump(v1_data, f)
      loaded = load_pixel_map(config_dir)

    assert len(loaded.segments) == 2
    assert loaded.segments[0].output == 2
    assert loaded.segments[0].color_order == "BGR"
    assert loaded.segments[1].output == 2
    assert loaded.segments[1].color_order == "GRB"

  def test_migrate_v1_validates(self):
    """A migrated v1 config should pass validation."""
    v1_data = {
      "strips": [
        {
          "id": 0,
          "output": 0,
          "output_offset": 0,
          "lines": [
            {"start": [0, 0], "end": [0, 2], "color_order": "BGR"},
            {"start": [1, 2], "end": [1, 0], "color_order": "BGR"},
          ],
        },
      ],
    }
    with tempfile.TemporaryDirectory() as tmpdir:
      config_dir = Path(tmpdir)
      with open(config_dir / "pixel_map.yaml", "w") as f:
        yaml.dump(v1_data, f)
      loaded = load_pixel_map(config_dir)

    errors = validate_pixel_map(loaded)
    assert errors == []

  def test_v2_preferred_over_v1(self):
    """If both segments and strips exist, segments wins."""
    data = {
      "segments": [
        {"start": [0, 0], "end": [0, 2], "output": 0, "color_order": "BGR"},
      ],
      "strips": [
        {
          "id": 99, "output": 7, "output_offset": 0,
          "lines": [{"start": [5, 0], "end": [5, 2]}],
        },
      ],
    }
    with tempfile.TemporaryDirectory() as tmpdir:
      config_dir = Path(tmpdir)
      with open(config_dir / "pixel_map.yaml", "w") as f:
        yaml.dump(data, f)
      loaded = load_pixel_map(config_dir)

    # Should use segments, not strips
    assert len(loaded.segments) == 1
    assert loaded.segments[0].output == 0


# ---------------------------------------------------------------------------
# TestDefaultConfig
# ---------------------------------------------------------------------------

class TestDefaultConfig:
  """Validate the shipped pixel_map.yaml (may be v1 or v2) produces correct geometry."""

  @pytest.fixture(autouse=True)
  def load_default(self):
    config_dir = Path(__file__).parent.parent / "config"
    self.config = load_pixel_map(config_dir)
    self.compiled = compile_pixel_map(self.config)

  def test_validates_clean(self):
    errors = validate_pixel_map(self.config)
    assert errors == [], f"Validation errors: {errors}"

  def test_grid_10x83(self):
    assert self.compiled.width == 10
    assert self.compiled.height == 83

  def test_total_leds(self):
    assert self.compiled.total_mapped_leds == 830

  def test_10_segments(self):
    assert len(self.config.segments) == 10

  def test_5_outputs_used(self):
    """5 of the 8 output pins should have LEDs."""
    used_outputs = [i for i, n in enumerate(self.compiled.output_config) if n > 0]
    assert len(used_outputs) == 5

  def test_no_unmapped_cells(self):
    """Every cell in the 10x83 grid should be mapped."""
    unmapped = (self.compiled.forward_lut[:, :, 0] == -1)
    assert not unmapped.any(), "Found unmapped cells in default config"

  def test_output_config_values(self):
    """Each used output should have 166 LEDs (2 segments x 83)."""
    oc = self.compiled.output_config
    for pin in range(5):
      assert oc[pin] == 166, f"Pin {pin}: expected 166, got {oc[pin]}"
    for pin in range(5, 8):
      assert oc[pin] == 0, f"Pin {pin}: expected 0, got {oc[pin]}"

  def test_segment_offsets(self):
    """Each pair of segments on a pin should have offsets [0, 83]."""
    offsets = self.compiled.segment_offsets
    # 10 segments, pairs on outputs 0-4
    for pair_idx in range(5):
      seg_a = pair_idx * 2
      seg_b = pair_idx * 2 + 1
      assert offsets[seg_a] == 0, f"Segment {seg_a} offset should be 0"
      assert offsets[seg_b] == 83, f"Segment {seg_b} offset should be 83"
