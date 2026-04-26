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


from app.layout.compiler import compile_layout, CompiledLayout, MappingEntry


class TestCompilation:
    def test_compiled_dimensions(self):
        config = _make_config([
            OutputConfig(id="ch0", channel=0, color_order="BGR", segments=[
                LinearSegment(id="col0", start=(0, 0), direction="+y", length=83, physical_offset=0),
            ])
        ])
        compiled = compile_layout(config)
        assert compiled.width == 10
        assert compiled.height == 83

    def test_forward_lut(self):
        config = _make_config([
            OutputConfig(id="ch0", channel=0, color_order="BGR", segments=[
                LinearSegment(id="s", start=(0, 0), direction="+y", length=3, physical_offset=0),
            ])
        ], width=1, height=3)
        compiled = compile_layout(config)
        assert compiled.forward_lut[0][0] == (0, 0)
        assert compiled.forward_lut[0][1] == (0, 1)
        assert compiled.forward_lut[0][2] == (0, 2)

    def test_reverse_lut(self):
        config = _make_config([
            OutputConfig(id="ch0", channel=0, color_order="BGR", segments=[
                LinearSegment(id="s", start=(2, 5), direction="-y", length=3, physical_offset=10),
            ])
        ], width=3, height=6)
        compiled = compile_layout(config)
        assert compiled.reverse_lut[0][10] == (2, 5)
        assert compiled.reverse_lut[0][11] == (2, 4)
        assert compiled.reverse_lut[0][12] == (2, 3)

    def test_mapping_entries(self):
        config = _make_config([
            OutputConfig(id="ch0", channel=0, color_order="BGR", segments=[
                LinearSegment(id="s", start=(0, 0), direction="+y", length=3, physical_offset=0),
            ])
        ], width=1, height=3)
        compiled = compile_layout(config)
        assert len(compiled.entries) == 3
        e = compiled.entries[0]
        assert e.x == 0
        assert e.y == 0
        assert e.channel == 0
        assert e.pixel_index == 0

    def test_output_sizes(self):
        config = _make_config([
            OutputConfig(id="ch0", channel=0, segments=[
                LinearSegment(id="a", start=(0, 0), direction="+y", length=83, physical_offset=0),
                LinearSegment(id="b", start=(1, 82), direction="-y", length=83, physical_offset=83),
            ]),
            OutputConfig(id="ch1", channel=1, segments=[
                LinearSegment(id="c", start=(2, 0), direction="+y", length=83, physical_offset=0),
            ]),
        ])
        compiled = compile_layout(config)
        assert compiled.output_sizes[0] == 166
        assert compiled.output_sizes[1] == 83

    def test_color_swizzle_per_output(self):
        config = _make_config([
            OutputConfig(id="ch0", channel=0, color_order="BGR", segments=[
                LinearSegment(id="s", start=(0, 0), direction="+y", length=1, physical_offset=0),
            ]),
            OutputConfig(id="ch1", channel=1, color_order="GRB", segments=[
                LinearSegment(id="s2", start=(1, 0), direction="+y", length=1, physical_offset=0),
            ]),
        ], width=2, height=1)
        compiled = compile_layout(config)
        assert compiled.color_swizzle[0] == (2, 1, 0)
        assert compiled.color_swizzle[1] == (1, 0, 2)

    def test_unmapped_pixels_are_none(self):
        config = _make_config([
            OutputConfig(id="ch0", channel=0, segments=[
                LinearSegment(id="s", start=(0, 0), direction="+y", length=1, physical_offset=0),
            ])
        ], width=2, height=2)
        compiled = compile_layout(config)
        assert compiled.forward_lut[0][0] is not None
        assert compiled.forward_lut[1][0] is None
        assert compiled.forward_lut[0][1] is None
