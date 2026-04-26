import pytest
from app.layout.schema import LayoutConfig, OutputConfig, LinearSegment, parse_layout


def test_parse_minimal_layout():
    """Minimal layout with one output, one linear segment."""
    raw = {
        "version": 1,
        "matrix": {"width": 10, "height": 83, "origin": "bottom_left"},
        "outputs": [
            {
                "id": "ch0",
                "channel": 0,
                "color_order": "BGR",
                "segments": [
                    {
                        "id": "col_0",
                        "start": {"x": 0, "y": 0},
                        "direction": "+y",
                        "length": 83,
                        "physical_offset": 0,
                    }
                ],
            }
        ],
    }
    config = parse_layout(raw)
    assert config.matrix.width == 10
    assert config.matrix.height == 83
    assert len(config.outputs) == 1
    assert len(config.outputs[0].segments) == 1
    seg = config.outputs[0].segments[0]
    assert seg.id == "col_0"
    assert seg.direction == "+y"
    assert seg.length == 83
    assert seg.physical_offset == 0


def test_parse_explicit_segment():
    """Explicit segment with point list."""
    raw = {
        "version": 1,
        "matrix": {"width": 3, "height": 1, "origin": "bottom_left"},
        "outputs": [
            {
                "id": "ch0",
                "channel": 0,
                "color_order": "RGB",
                "segments": [
                    {
                        "id": "custom",
                        "type": "explicit",
                        "points": [{"x": 2, "y": 0}, {"x": 0, "y": 0}, {"x": 1, "y": 0}],
                        "physical_offset": 0,
                    }
                ],
            }
        ],
    }
    config = parse_layout(raw)
    seg = config.outputs[0].segments[0]
    assert seg.type == "explicit"
    assert len(seg.points) == 3
    assert seg.points[0] == (2, 0)


def test_parse_direction_variants():
    """All four direction values are accepted."""
    for d in ("+x", "-x", "+y", "-y"):
        raw = {
            "version": 1,
            "matrix": {"width": 5, "height": 5, "origin": "bottom_left"},
            "outputs": [
                {
                    "id": "ch0",
                    "channel": 0,
                    "color_order": "BGR",
                    "segments": [
                        {"id": "s", "start": {"x": 0, "y": 0}, "direction": d, "length": 5, "physical_offset": 0}
                    ],
                }
            ],
        }
        config = parse_layout(raw)
        assert config.outputs[0].segments[0].direction == d


def test_reject_invalid_direction():
    """Invalid direction raises ValueError."""
    raw = {
        "version": 1,
        "matrix": {"width": 5, "height": 5, "origin": "bottom_left"},
        "outputs": [
            {
                "id": "ch0",
                "channel": 0,
                "color_order": "BGR",
                "segments": [
                    {"id": "s", "start": {"x": 0, "y": 0}, "direction": "up", "length": 5, "physical_offset": 0}
                ],
            }
        ],
    }
    with pytest.raises((ValueError, KeyError)):
        parse_layout(raw)
