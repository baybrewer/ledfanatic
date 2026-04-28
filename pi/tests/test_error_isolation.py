"""
Integration tests for effect error isolation.

Verifies that a crashing effect returns a black frame instead of
killing the render loop, and that the renderer recovers gracefully.
"""

import asyncio
import numpy as np
from unittest.mock import MagicMock, AsyncMock
from app.effects.base import Effect
from app.core.renderer import Renderer, RenderState
from app.core.brightness import BrightnessEngine
from app.layout import load_layout, compile_layout
from pathlib import Path


class CrashingEffect(Effect):
  """Effect that always crashes."""
  def render(self, t, state):
    raise RuntimeError("Effect exploded")


class WorkingEffect(Effect):
  """Effect that returns green frame."""
  def render(self, t, state):
    frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)
    frame[:, :, 1] = 128
    return frame


def test_renderer_isolates_crashing_effect():
  """_render_frame() should catch effect crash and continue."""
  layout_config = load_layout(Path("config"))
  layout = compile_layout(layout_config)
  state = RenderState()
  brightness = BrightnessEngine({})
  transport = MagicMock()
  transport.send_frame = AsyncMock(return_value=True)
  renderer = Renderer(transport, state, brightness, layout)
  renderer.current_effect = CrashingEffect(layout.width, layout.height)
  state.current_scene = "crasher"
  loop = asyncio.new_event_loop()
  loop.run_until_complete(renderer._render_frame())
  loop.close()
  assert transport.send_frame.called
  assert state.frames_rendered == 1


def test_renderer_continues_after_crash():
  """After a crash, switching to working effect should work normally."""
  layout_config = load_layout(Path("config"))
  layout = compile_layout(layout_config)
  state = RenderState()
  brightness = BrightnessEngine({})
  transport = MagicMock()
  transport.send_frame = AsyncMock(return_value=True)
  renderer = Renderer(transport, state, brightness, layout)
  renderer.current_effect = CrashingEffect(layout.width, layout.height)
  state.current_scene = "crasher"
  loop = asyncio.new_event_loop()
  loop.run_until_complete(renderer._render_frame())
  renderer.current_effect = WorkingEffect(layout.width, layout.height)
  state.current_scene = "worker"
  loop.run_until_complete(renderer._render_frame())
  loop.close()
  assert state.frames_rendered == 2
  assert state.frames_sent >= 1
