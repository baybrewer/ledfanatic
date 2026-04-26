import pytest
from app.layout.schema import LayoutConfig, MatrixConfig, OutputConfig, LinearSegment, ExplicitSegment
from app.layout.compiler import validate_layout


def _make_config(outputs, width=10, height=83):
    return LayoutConfig(
        version=1,
        matrix=MatrixConfig(width=width, height=height),
        outputs=outputs,
    )


class TestValidation:
    def test_valid_config_no_errors(self):
        """A correct serpentine pair on one output passes validation."""
        config = _make_config([
            OutputConfig(id="ch0", channel=0, color_order="BGR", segments=[
                LinearSegment(id="col0", start=(0, 0), direction="+y", length=83, physical_offset=0),
                LinearSegment(id="col1", start=(1, 82), direction="-y", length=83, physical_offset=83),
            ])
        ])
        errors = validate_layout(config)
        assert errors == []

    def test_segment_out_of_bounds(self):
        """Segment extending outside matrix bounds is rejected."""
        config = _make_config([
            OutputConfig(id="ch0", channel=0, segments=[
                LinearSegment(id="s", start=(0, 0), direction="+y", length=100, physical_offset=0),
            ])
        ], height=83)
        errors = validate_layout(config)
        assert any("bounds" in e.lower() for e in errors)

    def test_duplicate_logical_pixel(self):
        """Two segments mapping to the same logical pixel is rejected."""
        config = _make_config([
            OutputConfig(id="ch0", channel=0, segments=[
                LinearSegment(id="a", start=(0, 0), direction="+y", length=5, physical_offset=0),
                LinearSegment(id="b", start=(0, 0), direction="+y", length=5, physical_offset=5),
            ])
        ])
        errors = validate_layout(config)
        assert any("duplicate" in e.lower() or "collision" in e.lower() for e in errors)

    def test_duplicate_physical_index(self):
        """Two segments with overlapping physical offsets on same output is rejected."""
        config = _make_config([
            OutputConfig(id="ch0", channel=0, segments=[
                LinearSegment(id="a", start=(0, 0), direction="+y", length=5, physical_offset=0),
                LinearSegment(id="b", start=(1, 0), direction="+y", length=5, physical_offset=3),
            ])
        ])
        errors = validate_layout(config)
        assert any("physical" in e.lower() or "overlap" in e.lower() for e in errors)

    def test_exceeds_max_pixels(self):
        """Total pixels on one output exceeding max_pixels is rejected."""
        config = _make_config([
            OutputConfig(id="ch0", channel=0, max_pixels=100, segments=[
                LinearSegment(id="s", start=(0, 0), direction="+y", length=83, physical_offset=0),
                LinearSegment(id="s2", start=(1, 0), direction="+y", length=83, physical_offset=83),
            ])
        ])
        errors = validate_layout(config)
        assert any("max" in e.lower() or "exceed" in e.lower() for e in errors)

    def test_disabled_segment_skipped(self):
        """Disabled segments are excluded from validation."""
        config = _make_config([
            OutputConfig(id="ch0", channel=0, segments=[
                LinearSegment(id="a", start=(0, 0), direction="+y", length=83, physical_offset=0),
                LinearSegment(id="b", start=(0, 0), direction="+y", length=83, physical_offset=83, enabled=False),
            ])
        ])
        errors = validate_layout(config)
        assert errors == []

    def test_explicit_segment_out_of_bounds(self):
        """Explicit segment with points outside matrix is rejected."""
        config = _make_config([
            OutputConfig(id="ch0", channel=0, segments=[
                ExplicitSegment(id="e", points=((0, 0), (99, 99)), physical_offset=0),
            ])
        ], width=10, height=10)
        errors = validate_layout(config)
        assert any("bounds" in e.lower() for e in errors)
