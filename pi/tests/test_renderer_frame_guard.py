"""
Renderer frame guard: frames reach the transport only in READY and only when
the packed size matches the CONFIG geometry (Task 5, Teensy link reliability).
"""

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

import numpy as np
import pytest

from app.core import renderer as renderer_module
from app.core.brightness import BrightnessEngine
from app.core.renderer import Renderer, RenderState
from app.effects.base import Effect
from app.layout import load_layout, compile_layout, output_config_list
from app.layout.schema import LayoutConfig, MatrixConfig, OutputConfig, LinearSegment
from app.transport.link_state import LinkState


class GreenEffect(Effect):
  def render(self, t, state):
    frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)
    frame[:, :, 1] = 128
    return frame


def _run(coro):
  loop = asyncio.new_event_loop()
  try:
    return loop.run_until_complete(coro)
  finally:
    loop.close()


@pytest.fixture
def env():
  layout = compile_layout(load_layout(Path("config")))
  render_state = RenderState()
  brightness = BrightnessEngine({'manual_cap': 0.8})
  transport = MagicMock()
  transport.send_frame = AsyncMock(return_value=True)
  transport.trace = MagicMock()
  transport.state = LinkState.READY
  renderer = Renderer(transport, render_state, brightness, layout)
  renderer.current_effect = GreenEffect(layout.width, layout.height)
  render_state.current_scene = 'green'
  return renderer, transport, layout


def test_expected_frame_bytes_matches_config_geometry(env):
  renderer, _, layout = env
  assert renderer._expected_frame_bytes == sum(output_config_list(layout)) * 3


def test_short_frame_is_dropped_and_traced(env, monkeypatch):
  renderer, transport, _ = env
  # Simulate a packer/layout disagreement: buffer one LED short of the CONFIG sum
  monkeypatch.setattr(renderer_module, 'pack_frame',
                      lambda frame, layout: b'\x00' * (renderer._expected_frame_bytes - 3))
  _run(renderer._render_frame())
  transport.send_frame.assert_not_awaited()
  transport.trace.event.assert_called_once()
  args, kwargs = transport.trace.event.call_args
  assert args[0] == 'bad_frame_len'
  assert kwargs['got'] == renderer._expected_frame_bytes - 3
  assert kwargs['want'] == renderer._expected_frame_bytes
  # Frame was still rendered (preview keeps updating); it just never hit the wire
  assert renderer.state.frames_rendered == 1
  assert renderer.state.frames_sent == 0


def test_bad_frame_log_is_rate_limited(env, monkeypatch, caplog):
  renderer, _, _ = env
  monkeypatch.setattr(renderer_module, 'pack_frame', lambda frame, layout: b'\x00' * 3)
  with caplog.at_level('ERROR', logger='app.core.renderer'):
    _run(renderer._render_frame())
    _run(renderer._render_frame())
  mismatch_logs = [r for r in caplog.records if 'Frame size mismatch' in r.getMessage()]
  assert len(mismatch_logs) == 1


def test_not_ready_skips_send(env):
  renderer, transport, _ = env
  for state in (LinkState.CLOSED, LinkState.OPEN, LinkState.HELLO_OK, LinkState.CONFIGURED):
    transport.state = state
    _run(renderer._render_frame())
  transport.send_frame.assert_not_awaited()
  transport.trace.event.assert_not_called()
  # Rendering continues so the live preview stays alive while the link comes up
  assert renderer.state.frames_rendered == 4


def test_ready_and_correct_length_sends_once(env):
  renderer, transport, _ = env
  _run(renderer._render_frame())
  transport.send_frame.assert_awaited_once()
  sent = transport.send_frame.await_args.args[0]
  assert len(sent) == renderer._expected_frame_bytes
  transport.trace.event.assert_not_called()
  assert renderer.state.frames_sent == 1


def _small_layout():
  """Two 10-LED columns on one output: a different LED total than config/layout.yaml."""
  config = LayoutConfig(
    version=1,
    matrix=MatrixConfig(width=2, height=10),
    outputs=[OutputConfig(id='ch0', channel=0, color_order='BGR', segments=[
      LinearSegment(id='c0', start=(0, 0), direction='+y', length=10, physical_offset=0),
      LinearSegment(id='c1', start=(1, 9), direction='-y', length=10, physical_offset=10),
    ])],
  )
  return compile_layout(config)


def test_apply_layout_recomputes_expected_bytes(env):
  renderer, transport, layout = env
  before = renderer._expected_frame_bytes
  small = _small_layout()
  want = sum(output_config_list(small)) * 3
  assert want != before, "test needs a layout with a different LED total"
  renderer.apply_layout(small)
  assert renderer._expected_frame_bytes == want
  # A frame rendered for the new geometry must pass the guard — otherwise the
  # stream freezes after every layout change
  renderer.current_effect = GreenEffect(small.width, small.height)
  _run(renderer._render_frame())
  transport.send_frame.assert_awaited_once()
  assert len(transport.send_frame.await_args.args[0]) == want
  transport.trace.event.assert_not_called()


def test_bare_mock_transport_without_trace_still_skips(monkeypatch):
  # A test double with no `trace` attribute must not crash the guard
  layout = compile_layout(load_layout(Path("config")))
  transport = MagicMock(spec=['send_frame', 'state'])
  transport.send_frame = AsyncMock(return_value=True)
  transport.state = LinkState.READY
  renderer = Renderer(transport, RenderState(), BrightnessEngine({}), layout)
  renderer.current_effect = GreenEffect(layout.width, layout.height)
  monkeypatch.setattr(renderer_module, 'pack_frame', lambda frame, layout: b'\x00' * 3)
  _run(renderer._render_frame())
  transport.send_frame.assert_not_awaited()
