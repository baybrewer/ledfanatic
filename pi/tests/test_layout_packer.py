"""Tests for the layout packer — logical frame to physical output buffers."""

import numpy as np
import pytest

from app.layout.schema import (
    LayoutConfig, MatrixConfig, OutputConfig, LinearSegment, ExplicitSegment,
)
from app.layout.compiler import compile_layout, validate_layout
from app.layout.packer import pack_frame, output_config_list


def _compile(outputs, width=10, height=83):
    config = LayoutConfig(
        version=1,
        matrix=MatrixConfig(width=width, height=height),
        outputs=outputs,
    )
    assert validate_layout(config) == []
    return compile_layout(config)


class TestPacker:
    def test_basic_bgr_packing(self):
        """Red pixel in RGB frame -> BGR wire bytes [0, 0, 255]."""
        compiled = _compile([
            OutputConfig(id="ch0", channel=0, color_order="BGR", segments=[
                LinearSegment(id="s", start=(0, 0), direction="+y", length=1, physical_offset=0),
            ])
        ], width=1, height=1)
        frame = np.zeros((1, 1, 3), dtype=np.uint8)
        frame[0, 0] = [255, 0, 0]  # RGB red
        buf = pack_frame(frame, compiled)
        assert buf[0] == 0    # B
        assert buf[1] == 0    # G
        assert buf[2] == 255  # R

    def test_grb_swizzle(self):
        """GRB color order: [255, 128, 64] RGB -> [128, 255, 64] wire."""
        compiled = _compile([
            OutputConfig(id="ch0", channel=0, color_order="GRB", segments=[
                LinearSegment(id="s", start=(0, 0), direction="+y", length=1, physical_offset=0),
            ])
        ], width=1, height=1)
        frame = np.zeros((1, 1, 3), dtype=np.uint8)
        frame[0, 0] = [255, 128, 64]
        buf = pack_frame(frame, compiled)
        assert buf[0] == 128  # G
        assert buf[1] == 255  # R
        assert buf[2] == 64   # B

    def test_multi_output_layout(self):
        """Two outputs produce contiguous buffer: ch0 then ch1."""
        compiled = _compile([
            OutputConfig(id="ch0", channel=0, color_order="RGB", segments=[
                LinearSegment(id="a", start=(0, 0), direction="+y", length=1, physical_offset=0),
            ]),
            OutputConfig(id="ch1", channel=1, color_order="RGB", segments=[
                LinearSegment(id="b", start=(1, 0), direction="+y", length=1, physical_offset=0),
            ]),
        ], width=2, height=1)
        frame = np.zeros((2, 1, 3), dtype=np.uint8)
        frame[0, 0] = [10, 20, 30]
        frame[1, 0] = [40, 50, 60]
        buf = pack_frame(frame, compiled)
        assert len(buf) == 6
        assert buf[0:3] == bytes([10, 20, 30])
        assert buf[3:6] == bytes([40, 50, 60])

    def test_unmapped_pixels_are_black(self):
        """Physical LEDs with no logical mapping emit zeros."""
        compiled = _compile([
            OutputConfig(id="ch0", channel=0, color_order="RGB", segments=[
                LinearSegment(id="s", start=(0, 0), direction="+y", length=1, physical_offset=2),
            ])
        ], width=1, height=1)
        frame = np.full((1, 1, 3), 200, dtype=np.uint8)
        buf = pack_frame(frame, compiled)
        assert len(buf) == 9
        assert buf[0:6] == bytes([0, 0, 0, 0, 0, 0])
        assert buf[6:9] == bytes([200, 200, 200])

    def test_serpentine_pair(self):
        """Two segments on same output: col0 up, col1 down."""
        compiled = _compile([
            OutputConfig(id="ch0", channel=0, color_order="RGB", segments=[
                LinearSegment(id="col0", start=(0, 0), direction="+y", length=3, physical_offset=0),
                LinearSegment(id="col1", start=(1, 2), direction="-y", length=3, physical_offset=3),
            ])
        ], width=2, height=3)
        frame = np.zeros((2, 3, 3), dtype=np.uint8)
        frame[0, 0] = [255, 0, 0]
        frame[0, 1] = [0, 255, 0]
        frame[0, 2] = [0, 0, 255]
        frame[1, 2] = [255, 255, 255]
        frame[1, 1] = [0, 255, 255]
        frame[1, 0] = [255, 255, 0]
        buf = pack_frame(frame, compiled)
        assert len(buf) == 18
        assert buf[0:3] == bytes([255, 0, 0])
        assert buf[3:6] == bytes([0, 255, 0])
        assert buf[6:9] == bytes([0, 0, 255])
        assert buf[9:12] == bytes([255, 255, 255])
        assert buf[12:15] == bytes([0, 255, 255])
        assert buf[15:18] == bytes([255, 255, 0])

    def test_output_config_list(self):
        """output_config_list() returns 8-entry list for Teensy CONFIG."""
        compiled = _compile([
            OutputConfig(id="ch0", channel=0, segments=[
                LinearSegment(id="a", start=(0, 0), direction="+y", length=83, physical_offset=0),
                LinearSegment(id="b", start=(1, 82), direction="-y", length=83, physical_offset=83),
            ]),
            OutputConfig(id="ch2", channel=2, segments=[
                LinearSegment(id="c", start=(2, 0), direction="+y", length=50, physical_offset=0),
            ]),
        ])
        oc = output_config_list(compiled)
        assert len(oc) == 8
        assert oc[0] == 166
        assert oc[1] == 0
        assert oc[2] == 50
        assert oc[3] == 0
