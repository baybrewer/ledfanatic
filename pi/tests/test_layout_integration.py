"""Integration tests: layout.yaml -> parse -> validate -> compile -> pack."""

import numpy as np
import pytest
from pathlib import Path

from app.layout import load_layout, save_layout, compile_layout, validate_layout, pack_frame, output_config_list


class TestDefaultLayout:
    """Validate the shipped layout.yaml produces correct geometry."""

    @pytest.fixture(autouse=True)
    def setup(self):
        config_dir = Path(__file__).parent.parent / "config"
        self.config = load_layout(config_dir)
        errors = validate_layout(self.config)
        assert errors == [], f"Validation errors: {errors}"
        self.compiled = compile_layout(self.config)

    def test_grid_dimensions(self):
        assert self.compiled.width == 10
        assert self.compiled.height == 83

    def test_total_mapped_leds(self):
        assert self.compiled.total_mapped == 830

    def test_output_config(self):
        oc = output_config_list(self.compiled)
        assert oc == [166, 166, 166, 166, 166, 0, 0, 0]

    def test_serpentine_direction(self):
        """Even columns go up (+y), odd columns go down (-y)."""
        assert self.compiled.forward_lut[0][0] == (0, 0)
        assert self.compiled.forward_lut[0][82] == (0, 82)
        assert self.compiled.forward_lut[1][82] == (0, 83)
        assert self.compiled.forward_lut[1][0] == (0, 165)

    def test_pack_solid_frame(self):
        """Solid white frame packs to all-white buffer."""
        frame = np.full((10, 83, 3), 255, dtype=np.uint8)
        buf = pack_frame(frame, self.compiled)
        assert len(buf) == 2490
        assert all(b == 255 for b in buf)

    def test_no_unmapped_cells(self):
        """Every cell in 10x83 grid should be mapped."""
        for x in range(10):
            for y in range(83):
                assert self.compiled.forward_lut[x][y] is not None, \
                    f"Unmapped cell at ({x}, {y})"


class TestSaveLoadRoundTrip:
    """Verify save_layout produces YAML that load_layout can reconstruct."""

    def test_round_trip(self, tmp_path):
        from app.layout.schema import (
            LayoutConfig, MatrixConfig, OutputConfig, LinearSegment, ExplicitSegment,
        )
        original = LayoutConfig(
            version=1,
            matrix=MatrixConfig(width=5, height=10, origin="bottom_left"),
            outputs=[
                OutputConfig(id="ch0", channel=0, color_order="BGR", segments=[
                    LinearSegment(id="seg_a", start=(0, 0), direction="+y", length=10, physical_offset=0),
                    ExplicitSegment(id="seg_b", points=((1, 0), (1, 1)), physical_offset=10),
                ]),
            ],
        )
        save_layout(original, tmp_path)
        loaded = load_layout(tmp_path)
        assert loaded.matrix.width == 5
        assert loaded.matrix.height == 10
        assert len(loaded.outputs) == 1
        assert len(loaded.outputs[0].segments) == 2
        assert loaded.outputs[0].segments[0].id == "seg_a"
        assert loaded.outputs[0].segments[1].id == "seg_b"
        assert loaded.outputs[0].segments[1].points == ((1, 0), (1, 1))
