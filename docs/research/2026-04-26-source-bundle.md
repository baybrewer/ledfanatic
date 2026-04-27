# Source Code Bundle for Review

**Project:** pillar-controller
**Date:** 2026-04-26 (rev 3 — matches research brief rev 3)
**Purpose:** Key source files for Codex review of language performance and optimization opportunities

**Layout note:** The checked-in default layout (`pi/config/layout.yaml`) is 10x83 = 830 pixels.
Layout is dynamic — the Pi's active layout may differ. All performance analysis should specify the active layout.

**Round 1+2 review findings incorporated:**
1. Performance baseline flagged as unmeasured estimates; prerequisite benchmark step added
2. Effect benchmark harness doesn't cover all registered effects — fix required (see File 7)
3. Media pre-resize corrected: cache must be keyed by (item, width, height, fit) not import-time geometry
4. Coordinate normalization flagged as needing design spec — three coordinate concerns must not be conflated
5. USB baud rate recommendation replaced with end-to-end transport measurement (USB CDC, not raw UART)
6. Pixelblaze comparison softened — different hardware/workloads, approximate comparison only
7. Roadmap split into performance optimization and product ideas (separate decision processes)
8. Source bundle expanded to include layout apply path, media playback, and full benchmark harness

---

## File 1: pi/app/core/renderer.py — Main Render Loop

```python
"""
Core render loop.
Manages the scene -> render -> map -> send pipeline at the target FPS.
"""

import asyncio, logging, time
from typing import Optional
import numpy as np
from ..layout import pack_frame, CompiledLayout, _expand_segment
from ..transport.usb import TeensyTransport
from .brightness import BrightnessEngine

class RenderState:
  """Shared mutable state for the current render."""
  def __init__(self):
    self.target_fps: int = 60
    self.current_scene: Optional[str] = None
    self.blackout: bool = False
    self.gamma: float = 2.2
    self.origin: str = 'bottom_left'
    self.grid_width: int = 0
    self.grid_height: int = 0
    self._audio_lock_free: dict = {
      'level': 0.0, 'bass': 0.0, 'mid': 0.0, 'high': 0.0,
      'beat': False, 'bpm': 0.0, 'spectrum': [0.0] * 16,
    }
    self.actual_fps: float = 0.0
    self.frames_rendered: int = 0
    self.frames_sent: int = 0
    self.frames_dropped: int = 0
    self.last_frame_time_ms: float = 0.0
    self.render_cost_ms: float = 0.0

class Renderer:
  def __init__(self, transport, state, brightness_engine, layout):
    self.transport = transport
    self.state = state
    self.brightness_engine = brightness_engine
    self.layout = layout
    self.effect_registry: dict = {}
    self.current_effect = None
    self._running = False
    self._gamma_lut = _build_gamma_lut(state.gamma)
    self._fps_samples: list[float] = []
    self._fps_window = 60
    self._last_frame_start: float = 0.0
    self._segment_positions: dict = {}
    self._last_logical_frame = np.zeros((layout.width, layout.height, 3), dtype=np.uint8)
    self.state.grid_width = layout.width
    self.state.grid_height = layout.height

  def apply_layout(self, layout: CompiledLayout, layout_config=None):
    """Hot-swap the compiled layout. Thread-safe: next frame picks it up."""
    self.layout = layout
    self._last_logical_frame = np.zeros((layout.width, layout.height, 3), dtype=np.uint8)
    self.state.grid_width = layout.width
    self.state.grid_height = layout.height
    self.state.origin = layout.origin
    if layout_config is not None:
      self._rebuild_segment_cache_from_config(layout_config)
    # Recreate current effect at new dimensions
    if self.state.current_scene and self.state.current_scene in self.effect_registry:
      saved_scene = self.state.current_scene
      self.state.current_scene = None
      self.current_effect = None
      self._set_scene(saved_scene)

  async def run(self):
    """Main render loop."""
    self._running = True
    while self._running:
      frame_start = time.monotonic()
      target_interval = 1.0 / self.state.target_fps
      # FPS measurement
      if self._last_frame_start > 0:
        frame_interval = frame_start - self._last_frame_start
        self._fps_samples.append(frame_interval)
        if len(self._fps_samples) > self._fps_window:
          self._fps_samples.pop(0)
        if self._fps_samples:
          avg_interval = sum(self._fps_samples) / len(self._fps_samples)
          self.state.actual_fps = 1.0 / avg_interval if avg_interval > 0 else 0
      self._last_frame_start = frame_start
      try:
        await self._render_frame()
      except asyncio.CancelledError:
        break
      except Exception as e:
        self.state.frames_dropped += 1
      render_elapsed = time.monotonic() - frame_start
      self.state.render_cost_ms = render_elapsed * 1000
      remaining = target_interval - render_elapsed
      if remaining > 0:
        await asyncio.sleep(remaining)
      else:
        self.state.frames_dropped += 1

  async def _render_frame(self):
    """Render one frame and send to Teensy."""
    from datetime import datetime, timezone
    w, h = self.layout.width, self.layout.height
    if self.state.blackout or self.current_effect is None:
      logical_frame = np.zeros((w, h, 3), dtype=np.uint8)
    else:
      t = time.monotonic()
      internal_frame = self.current_effect.render(t, self.state)
      # Downsample if RENDER_SCALE > 1
      if getattr(self.current_effect, 'RENDER_SCALE', 1) > 1:
        from PIL import Image
        img = Image.fromarray(internal_frame.transpose(1, 0, 2))
        img = img.resize((w, h), Image.LANCZOS)
        logical_frame = np.array(img).transpose(1, 0, 2)
      else:
        logical_frame = internal_frame
      # Brightness + gamma
      effective = self.brightness_engine.get_effective_brightness(datetime.now(timezone.utc))
      logical_frame = (logical_frame * effective).astype(np.uint8)
      logical_frame = self._gamma_lut[logical_frame]
      # [Test pattern handling omitted for brevity]
    self._last_logical_frame = logical_frame
    self.state.frames_rendered += 1
    # Y-flip for bottom-left origin
    if self.layout.origin == 'bottom_left':
      logical_frame = logical_frame[:, ::-1, :]
    # Pack and send
    pixel_bytes = pack_frame(logical_frame, self.layout)
    success = await self.transport.send_frame(pixel_bytes)
    if success:
      self.state.frames_sent += 1
```

---

## File 2: pi/app/layout/packer.py — Frame Packing (Hot Loop)

```python
"""
Layout packer -- converts logical frame to physical output buffers.
Uses precomputed mapping entries from CompiledLayout for O(pixel_count)
per-frame performance with no geometry logic at runtime.
"""
import numpy as np
from .compiler import CompiledLayout

def pack_frame(frame: np.ndarray, layout: CompiledLayout) -> bytes:
    channel_offsets: dict[int, int] = {}
    offset = 0
    for ch in range(8):
        channel_offsets[ch] = offset
        offset += layout.output_sizes.get(ch, 0) * 3
    buf = bytearray(offset)
    for entry in layout.entries:
        rgb = frame[entry.x, entry.y]
        pos = channel_offsets[entry.channel] + entry.pixel_index * 3
        s = entry.swizzle
        buf[pos] = rgb[s[0]]
        buf[pos + 1] = rgb[s[1]]
        buf[pos + 2] = rgb[s[2]]
    return bytes(buf)
```

---

## File 3: pi/app/layout/compiler.py — Layout Compiler (Startup-Only)

```python
"""Startup-only: validates, then produces CompiledLayout with LUTs + flat entry list."""
from dataclasses import dataclass
from typing import Optional

SWIZZLE_MAP = {
    "RGB": (0, 1, 2), "RBG": (0, 2, 1), "GRB": (1, 0, 2),
    "GBR": (1, 2, 0), "BRG": (2, 0, 1), "BGR": (2, 1, 0),
}

@dataclass
class MappingEntry:
    x: int; y: int; channel: int; pixel_index: int; swizzle: tuple[int, int, int]

@dataclass
class CompiledLayout:
    width: int; height: int; origin: str
    forward_lut: list; reverse_lut: dict; entries: list[MappingEntry]
    output_sizes: dict[int, int]; color_swizzle: dict; total_mapped: int

def compile_layout(config) -> CompiledLayout:
    """Compile a validated LayoutConfig into fast-lookup structures."""
    w, h = config.matrix.width, config.matrix.height
    forward_lut = [[None] * h for _ in range(w)]
    reverse_lut, entries, output_sizes, color_swizzle = {}, [], {}, {}
    total_mapped = 0
    for output in config.outputs:
        ch = output.channel
        swizzle = SWIZZLE_MAP.get(output.color_order, (0, 1, 2))
        color_swizzle[ch] = swizzle
        if ch not in reverse_lut: reverse_lut[ch] = {}
        max_idx = 0
        for seg in output.segments:
            if not seg.enabled: continue
            seg_co = getattr(seg, 'color_order', '') or ''
            seg_swizzle = SWIZZLE_MAP.get(seg_co, swizzle) if seg_co else swizzle
            positions = _expand_segment(seg)
            for i, (px, py) in enumerate(positions):
                phys_idx = seg.physical_offset + i
                forward_lut[px][py] = (ch, phys_idx)
                reverse_lut[ch][phys_idx] = (px, py)
                entries.append(MappingEntry(x=px, y=py, channel=ch,
                    pixel_index=phys_idx, swizzle=seg_swizzle))
                total_mapped += 1
                if phys_idx + 1 > max_idx: max_idx = phys_idx + 1
        output_sizes[ch] = max(output_sizes.get(ch, 0), max_idx)
    return CompiledLayout(width=w, height=h, origin=config.matrix.origin,
        forward_lut=forward_lut, reverse_lut=reverse_lut, entries=entries,
        output_sizes=output_sizes, color_swizzle=color_swizzle, total_mapped=total_mapped)
```

---

## File 4: pi/app/api/routes/layout.py — Layout Apply Path (Dynamic Layout)

This is the code path for runtime layout changes. Validates, compiles, sends CONFIG to Teensy (ACK gate), then commits to renderer and disk.

```python
"""Layout API routes -- get, apply, validate, test-segment.
All mutations: validate -> compile -> send CONFIG to Teensy -> ACK gate -> commit."""

@router.post("/apply", dependencies=[Depends(require_auth)])
async def apply_layout(req: LayoutApplyRequest):
    """Replace entire layout config."""
    staged = parse_layout(req.model_dump())     # parse raw JSON -> LayoutConfig
    errors = validate_layout(staged)             # bounds, overlaps, max_pixels
    if errors:
        raise HTTPException(422, detail=errors)
    compiled = compile_layout(staged)            # -> CompiledLayout
    oc = output_config_list(compiled)            # -> [int]*8 for Teensy

    config_ok = await deps.transport.send_config(oc)  # Teensy ACK gate
    if not config_ok:
        raise HTTPException(502, detail="Teensy rejected CONFIG or timed out")

    # ACK received -- commit
    deps.layout_config = staged
    deps.compiled_layout = compiled
    deps.renderer.apply_layout(compiled, staged)  # hot-swap into render loop
    save_layout(staged, deps.config_dir)          # persist to disk
    return {"status": "ok", "width": compiled.width, "height": compiled.height,
            "total_mapped": compiled.total_mapped}
```

---

## File 5: pi/app/transport/usb.py — USB CDC Transport

```python
"""USB Serial transport to Teensy. Connection is USB CDC (virtual serial)."""

class TeensyTransport:
  def __init__(self, reconnect_interval=1.0, handshake_timeout=3.0):
    self._lock = asyncio.Lock()
    # ...

  async def send_frame(self, pixel_data: bytes) -> bool:
    """Send a FRAME packet. Uses asyncio.to_thread for blocking serial write."""
    if not self.connected or not self.serial: return False
    self.frame_id += 1
    timestamp_us = int(time.monotonic() * 1_000_000) & 0xFFFFFFFFFFFFFFFF
    if self._last_config_ack:
      payload = pixel_data  # post-CONFIG: raw bytes
    else:
      # legacy format: header + pixel data
      total_leds = len(pixel_data) // 3
      payload = struct.pack('<BH', 5, total_leds // 5) + pixel_data
    packet = build_packet(PacketType.FRAME, payload,
        frame_id=self.frame_id, timestamp_us=timestamp_us)
    framed = frame_packet(packet)  # COBS encode + 0x00 delimiter
    async with self._lock:
      try:
        await asyncio.to_thread(self.serial.write, framed)
        self.frames_sent += 1; return True
      except (serial.SerialException, OSError):
        self.send_errors += 1; self.connected = False; return False

  async def send_config(self, output_config, timeout=3.0) -> bool:
    """Send CONFIG packet and wait for ACK/NAK. Holds lock for both write and read."""
    # [sends config, waits for ACK within timeout, returns bool]
```

---

## File 6: pi/app/effects/fireworks.py — Particle System (Pure Python Loops)

```python
class SRFireworks(Effect):
    _MAX_SPARKS = 600
    _MAX_ROCKETS = 10

    def render(self, t, state):
        # ... dt calculation, rocket launch on beat ...

        # Update sparks -- PURE PYTHON LOOP
        alive_sparks = []
        for sp in self._sparks:          # Up to 600 iterations
            sp.x += sp.vx * dt; sp.y += sp.vy * dt
            sp.vy += gravity * dt; sp.vx *= 0.98; sp.life -= dt
            if sp.life > 0 and 0 <= sp.y < rows:
                alive_sparks.append(sp)
        self._sparks = alive_sparks

        # Render sparks to frame -- PURE PYTHON LOOP
        frame = np.zeros((cols, rows, 3), dtype=np.float32)
        for sp in self._sparks:
            ix = int(round(sp.x)) % cols; iy = int(round(sp.y))
            if 0 <= ix < cols and 0 <= iy < rows:
                brightness = (sp.life / sp.max_life) ** 0.5
                frame[ix, iy, 0] = min(255, frame[ix, iy, 0] + sp.r * brightness)
                frame[ix, iy, 1] = min(255, frame[ix, iy, 1] + sp.g * brightness)
                frame[ix, iy, 2] = min(255, frame[ix, iy, 2] + sp.b * brightness)

        result = np.clip(frame, 0, 255).astype(np.uint8)
        if self._prev_frame is not None:
            result = np.maximum(result, (self._prev_frame * 0.6).astype(np.uint8))
        self._prev_frame = result.copy()
        return result
```

---

## File 7: pi/tools/bench_effects.py — Benchmark Harness (FULL FILE)

**IMPORTANT:** This harness only covers `EFFECTS`, `AUDIO_EFFECTS`, and `IMPORTED_EFFECTS`. Effects registered ad-hoc in `main.py` (sr_fireworks, tetris, tetris_auto, scrolling_text, animation_switcher) are NOT benchmarked. See File 8 for the gap.

```python
"""Effect benchmark harness -- 10-second full-pipeline timing.

Usage:
  python -m tools.bench_effects                     # all effects
  python -m tools.bench_effects --effect matrix_rain # single effect
  python -m tools.bench_effects --frames 120        # quick pass
"""
import argparse, sys, time
import numpy as np
from unittest.mock import MagicMock
from pathlib import Path

from app.effects.generative import EFFECTS
from app.effects.audio_reactive import AUDIO_EFFECTS
from app.effects.imported import IMPORTED_EFFECTS
from app.layout import load_layout, compile_layout, pack_frame
from app.core.renderer import _build_gamma_lut

# Load and compile layout for benchmarking
_config_dir = Path(__file__).parent.parent / "config"
_layout_config = load_layout(_config_dir)
_layout = compile_layout(_layout_config)
GRID_WIDTH = _layout.width    # From checked-in layout.yaml -- may differ from Pi's active layout
GRID_HEIGHT = _layout.height

def bench_one(name, effect_cls, frames, gamma_lut, state):
  native_w = getattr(effect_cls, 'NATIVE_WIDTH', None) or 40
  try:
    eff = effect_cls(width=native_w, height=GRID_HEIGHT)
  except Exception as e:
    return {'name': name, 'error': str(e)}
  t = time.monotonic()
  render_times, post_times = [], []
  for i in range(frames):
    state.audio_beat = (i % 28 == 0)
    state._audio_lock_free['beat'] = state.audio_beat
    r_start = time.perf_counter()
    try:
      internal_frame = eff.render(t, state)
    except Exception as e:
      return {'name': name, 'error': str(e)}
    r_end = time.perf_counter()
    # Post-processing: downsample + brightness + gamma + pack
    p_start = r_end
    if internal_frame.shape[0] != GRID_WIDTH:
      factor = internal_frame.shape[0] // GRID_WIDTH
      if factor > 1:
        logical = internal_frame.reshape(GRID_WIDTH, factor, GRID_HEIGHT, 3).mean(axis=1).astype(np.uint8)
      else:
        logical = internal_frame[:GRID_WIDTH]
    else:
      logical = internal_frame
    logical = (logical * 0.8).astype(np.uint8)
    logical = gamma_lut[logical]
    _ = pack_frame(logical, _layout)
    p_end = time.perf_counter()
    render_times.append(r_end - r_start)
    post_times.append(p_end - p_start)
    t += 1.0 / 60
  render_ms = [x * 1000 for x in render_times]
  post_ms = [x * 1000 for x in post_times]
  total_ms = [r + p for r, p in zip(render_ms, post_ms)]
  return {
    'name': name, 'width': native_w, 'frames': frames,
    'render_avg_ms': np.mean(render_ms), 'render_p95_ms': np.percentile(render_ms, 95),
    'post_avg_ms': np.mean(post_ms), 'total_avg_ms': np.mean(total_ms),
    'total_p95_ms': np.percentile(total_ms, 95), 'total_max_ms': np.max(total_ms),
    'implied_fps': 1000.0 / np.mean(total_ms) if np.mean(total_ms) > 0 else 9999,
  }

def main():
  parser = argparse.ArgumentParser()
  parser.add_argument('--effect', type=str)
  parser.add_argument('--frames', type=int, default=600)
  parser.add_argument('--csv', action='store_true')
  args = parser.parse_args()

  # LINE 126: ONLY these three dicts -- missing sr_fireworks, tetris, scrolling_text, etc.
  all_effects = {**EFFECTS, **AUDIO_EFFECTS, **IMPORTED_EFFECTS}
  gamma_lut = _build_gamma_lut(2.2)
  state = _make_state()
  # ... runs bench_one for each effect, prints results ...
```

---

## File 8: pi/app/main.py — Effect Registration (lines 131-149)

Shows the gap between what the benchmark harness covers and what the runtime actually registers.

```python
from .effects.generative import EFFECTS
from .effects.audio_reactive import AUDIO_EFFECTS
from .diagnostics.patterns import DIAGNOSTIC_EFFECTS
from .effects.imported import IMPORTED_EFFECTS

# --- These three ARE covered by bench_effects.py ---
for name, cls in EFFECTS.items():
    renderer.register_effect(name, cls)
for name, cls in AUDIO_EFFECTS.items():
    renderer.register_effect(name, cls)

# --- These are NOT covered by bench_effects.py ---
for name, cls in DIAGNOSTIC_EFFECTS.items():
    renderer.register_effect(name, cls)
renderer.register_effect('tetris', Tetris)
renderer.register_effect('tetris_auto', TetrisAutoplay)
renderer.register_effect('sr_fireworks', SRFireworks)
renderer.register_effect('scrolling_text', ScrollingText)

# --- IMPORTED_EFFECTS ARE covered ---
for name, cls in IMPORTED_EFFECTS.items():
    renderer.register_effect(name, cls)
renderer.register_effect('animation_switcher', AnimationSwitcher)
```

---

## File 9: pi/app/effects/base.py — Effect Base Class

```python
class Effect(ABC):
  NATIVE_WIDTH = None
  RENDER_SCALE = 1

  def __init__(self, width: int, height: int, params: Optional[dict] = None):
    self.width = width
    self.height = height
    self.params = params or {}

  @abstractmethod
  def render(self, t: float, state) -> np.ndarray:
    """Returns: np.ndarray of shape (width, height, 3) uint8"""
    pass

  def update_params(self, params: dict):
    self.params.update(params)
```

**Note:** Effects receive width, height, params, and render state. No coordinate grids
or normalized positions. Any coordinate normalization proposal must decide where
that abstraction lives without conflicting with the existing setup/geometry UV system.

---

## File 10: pi/app/effects/media_playback.py — Media Playback (Dynamic Geometry)

Shows why media caching must be geometry-aware: `width` and `height` come from the current layout.

```python
class MediaPlayback(Effect):
  def __init__(self, *args, media_manager=None, **kwargs):
    super().__init__(*args, **kwargs)
    self._frame_cache: dict[int, np.ndarray] = {}  # frame_idx -> raw loaded frame (NOT resized)
    self._fit_mode = self.params.get('fit', 'fill')

  def render(self, t, state):
    # ... frame index calculation ...
    if frame_idx not in self._frame_cache:
      frame = self.media_manager.load_frame(self._item_id, frame_idx)
      if frame is not None:
        self._frame_cache[frame_idx] = frame
        if len(self._frame_cache) > 120:
          oldest = min(self._frame_cache.keys())
          del self._frame_cache[oldest]
    frame = self._frame_cache.get(frame_idx)
    if frame is None:
      return np.zeros((self.width, self.height, 3), dtype=np.uint8)
    # Resize to CURRENT dimensions (from layout at effect instantiation time)
    if frame.shape[0] != self.width or frame.shape[1] != self.height:
      img = Image.fromarray(frame.transpose(1, 0, 2))
      img = img.resize((self.width, self.height), Image.LANCZOS)
      frame = np.array(img).transpose(1, 0, 2)
    return frame
```

**Cache concern:** The cache stores *raw loaded* frames, not resized frames. The PIL LANCZOS resize happens on every `render()` call when dimensions don't match — so the resize cost is paid repeatedly for the same cached original. The proposed fix: cache the *resized* result keyed by `(frame_idx, width, height, fit_mode)`. When layout changes, `renderer.apply_layout()` recreates the effect with new dimensions, discarding the old instance and its cache.

---

## Architecture Diagram

```
Phone/Browser (WiFi)
       |
       v
  [FastAPI Server]  <-- Python, async, port 80
       |
       +-- [Layout API]  <-- POST /api/layout/apply -> validate -> compile
       |       |               -> Teensy CONFIG ACK -> renderer.apply_layout()
       |       |               -> save_layout() to disk
       |
       v
  [Renderer]  <-- 60 FPS loop, layout is hot-swappable
       |
       +-- [Effect.render(t, state)]  <-- NumPy vectorized or Python loops
       |
       +-- [brightness * gamma LUT]   <-- NumPy array ops
       |
       +-- [pack_frame()]             <-- Pure Python loop, N iterations (N = layout pixel count)
       |
       +-- [COBS encode + CRC32]
       |
       v
  [USB CDC Serial]  <-- USB 2.0 FS (baud advisory), asyncio.to_thread
       |
       v
  [Teensy 4.1]  <-- C++ firmware, OctoWS2811 DMA
       |
       v
  [WS2812B strips]  <-- pixel count determined by active layout (default: 830)
```

---

## Comparison: Our Architecture vs Pixelblaze

**Caveat:** This is an approximate comparison across different hardware, programming models, and workloads. Not a controlled benchmark.

| Aspect | pillar-controller | Pixelblaze V3 |
|--------|-------------------|---------------|
| **CPU** | RPi 4 (1.5GHz quad ARM) | ESP32 (240MHz dual Xtensa) |
| **RAM** | 4-8 GB | 520 KB |
| **OS** | Linux | None (bare metal) |
| **Effect language** | Python + NumPy | Custom JS bytecode |
| **Nominal target throughput** | 49,800 px/sec if sustaining 60 FPS at 830px (not measured) | 48,000 px/sec (manufacturer claim) |
| **Max pixels** | Layout-dependent (dynamic, untested at scale) | 5000 (hardware limit) |
| **Audio** | Full FFT + beat detection | Basic via sensor board |
| **Media** | Video/image playback | None |
| **Web UI** | Full control dashboard + setup wizard | Pattern editor |
| **Live edit** | API + WebSocket | Keystroke-level in browser |
| **Pattern model** | Class with render() returning frame array | Expression per-pixel |
| **Layout** | Dynamic, runtime-mutable via API | Static JSON pixel map |
