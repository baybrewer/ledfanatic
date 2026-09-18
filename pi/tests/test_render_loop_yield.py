"""
Regression test: the render loop must never starve the event loop.

Bug: when a frame renders slower than the target interval AND the transport
is disconnected (send_frame returns without awaiting anything), run() skipped
its sleep and busy-spun — freezing HTTP, transport reconnect, and pot polling
until restart (observed live 2026-09-18: 145% CPU, dead API, watchdog-faded
LEDs). Even asyncio timeouts can't fire on a starved loop, so this test
bounds itself by having the effect stop the renderer after a fixed number of
frames rather than relying on timers.
"""

import asyncio
import time

import numpy as np
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

from app.core.renderer import Renderer, RenderState
from app.core.brightness import BrightnessEngine
from app.effects.base import Effect
from app.layout import load_layout, compile_layout


class SlowSelfStoppingEffect(Effect):
  """Renders slower than the 90 FPS frame budget; stops the renderer itself
  after MAX_FRAMES so the test terminates even when the loop is starved."""

  MAX_FRAMES = 25  # ~0.5s of wall time at 0.02s per render

  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    self.frames = 0
    self.stopper = None  # set by the test

  def render(self, t, state):
    time.sleep(0.02)
    self.frames += 1
    if self.frames >= self.MAX_FRAMES and self.stopper:
      self.stopper()
    return np.zeros((self.width, self.height, 3), dtype=np.uint8)


def test_overloaded_render_loop_still_yields():
  layout_config = load_layout(Path("config"))
  layout = compile_layout(layout_config)
  state = RenderState()
  state.target_fps = 90
  brightness = BrightnessEngine({})
  transport = MagicMock()
  # Disconnected-transport behavior: returns immediately, no real await
  transport.send_frame = AsyncMock(return_value=False)
  renderer = Renderer(transport, state, brightness, layout)
  effect = SlowSelfStoppingEffect(layout.width, layout.height)
  effect.stopper = renderer.stop
  renderer.current_effect = effect
  state.current_scene = "slow"

  ticks = 0

  async def scenario():
    nonlocal ticks

    async def sibling():
      nonlocal ticks
      while True:
        await asyncio.sleep(0.01)
        ticks += 1

    sibling_task = asyncio.create_task(sibling())
    await renderer.run()  # returns after MAX_FRAMES via the effect's stopper
    sibling_task.cancel()
    await asyncio.gather(sibling_task, return_exceptions=True)

  asyncio.run(scenario())

  # ~0.5s of over-budget rendering. With the starvation bug the sibling task
  # never gets scheduled (0 ticks). With the fix it runs between frames.
  assert ticks >= 10, f"event loop starved: sibling ran {ticks} times during ~0.5s"
