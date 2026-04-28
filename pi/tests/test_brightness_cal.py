"""Tests for per-segment brightness calibration."""
import numpy as np
import pytest

from app.layout.schema import LinearSegment, BrightnessCal, parse_layout
from app.layout.compiler import compile_layout


def test_default_brightness_cal():
  seg = LinearSegment(id='s1', start=(0, 0), direction='+y', length=10, physical_offset=0)
  assert seg.brightness_cal == BrightnessCal()
  assert seg.brightness_cal.r == (1.0, 1.0, 1.0)


def test_brightness_cal_validation():
  with pytest.raises(ValueError):
    BrightnessCal(r=(1.0, 1.0))  # only 2 values


def test_parse_layout_with_cal():
  raw = {
    'version': 1,
    'matrix': {'width': 1, 'height': 5},
    'outputs': [{
      'id': 'out0', 'channel': 0,
      'segments': [{
        'id': 'seg0', 'type': 'linear',
        'start': {'x': 0, 'y': 0}, 'direction': '+y', 'length': 5,
        'physical_offset': 0,
        'brightness_cal': {'r': [0.8, 0.9, 1.0], 'g': [1.0, 1.0, 1.0], 'b': [0.7, 0.85, 1.0]},
      }],
    }],
  }
  config = parse_layout(raw)
  seg = config.outputs[0].segments[0]
  assert seg.brightness_cal.r == (0.8, 0.9, 1.0)
  assert seg.brightness_cal.b[0] == 0.7


def test_compiled_layout_has_cal_luts():
  raw = {
    'version': 1,
    'matrix': {'width': 1, 'height': 5},
    'outputs': [{
      'id': 'out0', 'channel': 0,
      'segments': [{
        'id': 'seg0', 'type': 'linear',
        'start': {'x': 0, 'y': 0}, 'direction': '+y', 'length': 5,
        'physical_offset': 0,
        'brightness_cal': {'r': [0.5, 0.75, 1.0], 'g': [1.0, 1.0, 1.0], 'b': [1.0, 1.0, 1.0]},
      }],
    }],
  }
  config = parse_layout(raw)
  layout = compile_layout(config)
  assert layout.cal_luts.shape == (1, 3, 256)
  identity = np.arange(256, dtype=np.uint8)
  assert not np.array_equal(layout.cal_luts[0, 0], identity)  # red corrected
  assert np.array_equal(layout.cal_luts[0, 1], identity)  # green is identity
  assert layout.cal_seg_idx_expanded.shape == layout.pack_src.shape
  assert layout.cal_logical_ch.shape == layout.pack_src.shape


def test_pack_applies_correction():
  from app.layout.packer import pack_frame
  raw = {
    'version': 1,
    'matrix': {'width': 1, 'height': 2},
    'outputs': [{
      'id': 'out0', 'channel': 0, 'color_order': 'RGB',
      'segments': [{
        'id': 'seg0', 'type': 'linear',
        'start': {'x': 0, 'y': 0}, 'direction': '+y', 'length': 2,
        'physical_offset': 0,
        'brightness_cal': {'r': [0.5, 0.5, 0.5], 'g': [1.0, 1.0, 1.0], 'b': [1.0, 1.0, 1.0]},
      }],
    }],
  }
  config = parse_layout(raw)
  layout = compile_layout(config)
  frame = np.full((1, 2, 3), 200, dtype=np.uint8)  # all channels = 200
  packed = pack_frame(frame, layout)
  # Red should be dimmed (multiplier ~0.5), green and blue should be ~200
  data = np.frombuffer(packed, dtype=np.uint8)
  # With RGB color order, bytes are R,G,B,R,G,B
  assert data[0] < 150  # red dimmed
  assert data[1] >= 190  # green ~200
  assert data[2] >= 190  # blue ~200
