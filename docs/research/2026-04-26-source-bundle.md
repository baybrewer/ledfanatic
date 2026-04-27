# Source Code Bundle for Review

**Project:** pillar-controller
**Date:** 2026-04-26 (revised after Codex round-1 review)
**Purpose:** Key source files for Codex review of language performance and optimization opportunities

**Layout note:** The checked-in default layout (`pi/config/layout.yaml`) is 10x83 = 830 pixels.
Layout is dynamic — the Pi's active layout may differ. All performance analysis should specify the active layout.

**Round 1 review findings incorporated:**
1. Performance baseline now flagged as unmeasured estimates; prerequisite benchmark step added
2. Effect benchmark harness (`pi/tools/bench_effects.py`) doesn't cover all registered effects — fix required
3. Media pre-resize corrected: cache must be keyed by (item, width, height, fit) not import-time geometry
4. Coordinate normalization flagged as needing design work to avoid mixing calibration/rendering/authoring concerns
5. USB baud rate recommendation replaced with end-to-end transport measurement (USB CDC, not raw UART)

---

## File 1: pi/app/core/renderer.py — Main Render Loop

```python
"""
Core render loop.

Manages the scene -> render -> map -> send pipeline at the target FPS.
"""

import asyncio
import logging
import time
from typing import Optional

import numpy as np

from ..layout import pack_frame, CompiledLayout, _expand_segment
from ..transport.usb import TeensyTransport
from .brightness import BrightnessEngine

logger = logging.getLogger(__name__)


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

    # Audio modulation (updated by audio worker via snapshot)
    self._audio_lock_free: dict = {
      'level': 0.0, 'bass': 0.0, 'mid': 0.0, 'high': 0.0,
      'beat': False, 'bpm': 0.0, 'spectrum': [0.0] * 16,
    }

    # Stats
    self.actual_fps: float = 0.0
    self.frames_rendered: int = 0
    self.frames_sent: int = 0
    self.frames_dropped: int = 0
    self.last_frame_time_ms: float = 0.0
    self.render_cost_ms: float = 0.0

  def update_audio(self, snapshot: dict):
    self._audio_lock_free = snapshot

  @property
  def audio_level(self) -> float:
    return self._audio_lock_free.get('level', 0.0)

  @property
  def audio_bass(self) -> float:
    return self._audio_lock_free.get('bass', 0.0)

  @property
  def audio_mid(self) -> float:
    return self._audio_lock_free.get('mid', 0.0)

  @property
  def audio_high(self) -> float:
    return self._audio_lock_free.get('high', 0.0)

  @property
  def audio_beat(self) -> bool:
    return self._audio_lock_free.get('beat', False)

  @property
  def audio_bpm(self) -> float:
    return self._audio_lock_free.get('bpm', 0.0)

  @property
  def audio_spectrum(self) -> list:
    return self._audio_lock_free.get('spectrum', [0.0] * 16)

  def to_dict(self) -> dict:
    return {
      'target_fps': self.target_fps,
      'actual_fps': round(self.actual_fps, 1),
      'current_scene': self.current_scene,
      'blackout': self.blackout,
      'frames_rendered': self.frames_rendered,
      'frames_sent': self.frames_sent,
      'frames_dropped': self.frames_dropped,
      'last_frame_time_ms': round(self.last_frame_time_ms, 2),
      'render_cost_ms': round(self.render_cost_ms, 2),
      'audio_level': round(self.audio_level, 3),
      'audio_bass': round(self.audio_bass, 3),
      'audio_mid': round(self.audio_mid, 3),
      'audio_high': round(self.audio_high, 3),
      'audio_beat': self.audio_beat,
      'audio_spectrum': [round(v, 3) for v in self.audio_spectrum],
    }


def _build_gamma_lut(gamma: float) -> np.ndarray:
  lut = np.zeros(256, dtype=np.uint8)
  for i in range(256):
    lut[i] = int(pow(i / 255.0, gamma) * 255.0 + 0.5)
  return lut


def _hue_to_rgb(hue_deg: float) -> tuple[int, int, int]:
  h = (hue_deg % 360) / 60
  i = int(h)
  f = h - i
  q = int(255 * (1 - f))
  t = int(255 * f)
  if i == 0: return (255, t, 0)
  if i == 1: return (q, 255, 0)
  if i == 2: return (0, 255, t)
  if i == 3: return (0, q, 255)
  if i == 4: return (t, 0, 255)
  return (255, 0, q)


class Renderer:
  def __init__(self, transport: TeensyTransport, state: RenderState,
               brightness_engine: BrightnessEngine, layout: CompiledLayout):
    self.transport = transport
    self.state = state
    self.brightness_engine = brightness_engine
    self.layout = layout
    self.effect_registry: dict = {}
    self.current_effect = None
    self._test_segment_id: Optional[str] = None
    self._test_mode: Optional[str] = None
    self._test_strip_until: float = 0.0
    self._probe_strip: int = -1
    self._probe_led: int = -1
    self._running = False
    self._gamma_lut = _build_gamma_lut(state.gamma)
    self._fps_samples: list[float] = []
    self._fps_window = 60
    self._last_frame_start: float = 0.0
    self._segment_positions: dict[str, list[tuple[int, int]]] = {}
    self._last_logical_frame = np.zeros((layout.width, layout.height, 3), dtype=np.uint8)
    self.state.grid_width = layout.width
    self.state.grid_height = layout.height
    self.state.origin = layout.origin

  async def run(self):
    """Main render loop."""
    self._running = True
    logger.info(f"Render loop started at {self.state.target_fps} FPS target")

    while self._running:
      frame_start = time.monotonic()
      target_interval = 1.0 / self.state.target_fps

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
        logger.error(f"Render error: {e}", exc_info=True)
        self.state.frames_dropped += 1

      render_elapsed = time.monotonic() - frame_start
      self.state.render_cost_ms = render_elapsed * 1000
      self.state.last_frame_time_ms = render_elapsed * 1000

      remaining = target_interval - render_elapsed
      if remaining > 0:
        await asyncio.sleep(remaining)
      else:
        self.state.frames_dropped += 1

  async def _render_frame(self):
    """Render one frame and send to Teensy."""
    from datetime import datetime, timezone

    w = self.layout.width
    h = self.layout.height

    if self.state.blackout or self.current_effect is None:
      logical_frame = np.zeros((w, h, 3), dtype=np.uint8)
      self._last_logical_frame = logical_frame
    else:
      t = time.monotonic()
      internal_frame = self.current_effect.render(t, self.state)

      # Downsample if effect uses RENDER_SCALE > 1
      if self.current_effect and getattr(self.current_effect, 'RENDER_SCALE', 1) > 1:
        from PIL import Image
        img = Image.fromarray(internal_frame.transpose(1, 0, 2))
        img = img.resize((w, h), Image.LANCZOS)
        logical_frame = np.array(img).transpose(1, 0, 2)
      else:
        logical_frame = internal_frame

      # Apply effective brightness
      effective = self.brightness_engine.get_effective_brightness(
        datetime.now(timezone.utc)
      )
      logical_frame = (logical_frame * effective).astype(np.uint8)

      # Apply gamma
      logical_frame = self._gamma_lut[logical_frame]

      # [Test pattern handling omitted for brevity]

      self._last_logical_frame = logical_frame

    self.state.frames_rendered += 1

    # Flip y-axis when origin is bottom-left
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
    """
    Pack a (width, height, 3) uint8 frame into contiguous output buffer.

    Returns bytes laid out as: [channel_0_data][channel_1_data]...[channel_7_data]
    where each channel's data is output_sizes[ch] * 3 bytes.
    """
    channel_offsets: dict[int, int] = {}
    offset = 0
    for ch in range(8):
        channel_offsets[ch] = offset
        offset += layout.output_sizes.get(ch, 0) * 3

    total_bytes = offset
    buf = bytearray(total_bytes)

    for entry in layout.entries:
        rgb = frame[entry.x, entry.y]
        pos = channel_offsets[entry.channel] + entry.pixel_index * 3
        s = entry.swizzle
        buf[pos] = rgb[s[0]]
        buf[pos + 1] = rgb[s[1]]
        buf[pos + 2] = rgb[s[2]]

    return bytes(buf)


def output_config_list(layout: CompiledLayout) -> list[int]:
    """Return 8-entry list of LEDs per output channel (for Teensy CONFIG packet)."""
    return [layout.output_sizes.get(ch, 0) for ch in range(8)]
```

---

## File 3: pi/app/layout/compiler.py — Layout Compiler (Startup-Only)

```python
"""
Layout compiler -- validates layout config and compiles to mapping tables.

Startup-only: validates all rules, then produces a CompiledLayout with
precomputed forward/reverse LUTs and a flat mapping entry list for
fast per-frame packing.
"""

from dataclasses import dataclass, field
from typing import Optional

from .schema import (
    LayoutConfig, OutputConfig, LinearSegment, ExplicitSegment, Segment,
    VALID_DIRECTIONS,
)


def _expand_segment(seg) -> list[tuple[int, int]]:
    """Expand any segment type into its (x, y) positions."""
    if isinstance(seg, ExplicitSegment):
        return [(x, y) for x, y in seg.points]
    # LinearSegment
    x, y = seg.start
    dx, dy = 0, 0
    if seg.direction == "+x": dx = 1
    elif seg.direction == "-x": dx = -1
    elif seg.direction == "+y": dy = 1
    elif seg.direction == "-y": dy = -1
    return [(x + dx * i, y + dy * i) for i in range(seg.length)]


SWIZZLE_MAP = {
    "RGB": (0, 1, 2), "RBG": (0, 2, 1), "GRB": (1, 0, 2),
    "GBR": (1, 2, 0), "BRG": (2, 0, 1), "BGR": (2, 1, 0),
}


@dataclass
class MappingEntry:
    """One logical-to-physical mapping."""
    x: int
    y: int
    channel: int
    pixel_index: int
    swizzle: tuple[int, int, int]


@dataclass
class CompiledLayout:
    """Precomputed mapping tables for fast rendering."""
    width: int
    height: int
    origin: str
    forward_lut: list[list[Optional[tuple[int, int]]]]
    reverse_lut: dict[int, dict[int, Optional[tuple[int, int]]]]
    entries: list[MappingEntry]
    output_sizes: dict[int, int]
    color_swizzle: dict[int, tuple[int, int, int]]
    total_mapped: int


def compile_layout(config) -> CompiledLayout:
    """Compile a validated LayoutConfig into fast-lookup structures."""
    w, h = config.matrix.width, config.matrix.height
    forward_lut = [[None] * h for _ in range(w)]
    reverse_lut = {}
    entries = []
    output_sizes = {}
    color_swizzle = {}
    total_mapped = 0

    for output in config.outputs:
        ch = output.channel
        swizzle = SWIZZLE_MAP.get(output.color_order, (0, 1, 2))
        color_swizzle[ch] = swizzle
        if ch not in reverse_lut:
            reverse_lut[ch] = {}
        max_idx = 0
        for seg in output.segments:
            if not seg.enabled:
                continue
            seg_co = getattr(seg, 'color_order', '') or ''
            seg_swizzle = SWIZZLE_MAP.get(seg_co, swizzle) if seg_co else swizzle
            positions = _expand_segment(seg)
            for i, (px, py) in enumerate(positions):
                phys_idx = seg.physical_offset + i
                forward_lut[px][py] = (ch, phys_idx)
                reverse_lut[ch][phys_idx] = (px, py)
                entries.append(MappingEntry(
                    x=px, y=py, channel=ch,
                    pixel_index=phys_idx, swizzle=seg_swizzle,
                ))
                total_mapped += 1
                if phys_idx + 1 > max_idx:
                    max_idx = phys_idx + 1
        output_sizes[ch] = max(output_sizes.get(ch, 0), max_idx)

    return CompiledLayout(
        width=w, height=h, origin=config.matrix.origin,
        forward_lut=forward_lut, reverse_lut=reverse_lut,
        entries=entries, output_sizes=output_sizes,
        color_swizzle=color_swizzle, total_mapped=total_mapped,
    )
```

---

## File 4: pi/app/transport/usb.py — USB Serial Transport

```python
"""USB Serial transport to Teensy."""

import asyncio
import logging
import time
import struct
from typing import Optional

import serial
import serial.tools.list_ports

from ..models.protocol import (
  PacketType, build_packet, verify_packet, frame_packet,
  build_hello_payload, build_frame_payload, build_blackout_payload,
  build_config_payload, output_config_to_list,
  parse_caps_payload, parse_stats_payload,
  cobs_encode, cobs_decode, PROTOCOL_VERSION,
)

logger = logging.getLogger(__name__)

TEENSY_VID = 0x16C0
TEENSY_PID = 0x0483


class TeensyTransport:
  def __init__(self, reconnect_interval=1.0, handshake_timeout=3.0):
    self.reconnect_interval = reconnect_interval
    self.handshake_timeout = handshake_timeout
    self.serial = None
    self.connected = False
    self.caps = None
    self.frame_id = 0
    self._rx_buffer = bytearray()
    self._lock = asyncio.Lock()
    self._last_config_ack = None
    self._on_connect_callback = None
    self.frames_sent = 0
    self.send_errors = 0
    self.reconnect_count = 0

  async def send_frame(self, pixel_data: bytes) -> bool:
    """Send a FRAME packet."""
    if not self.connected or not self.serial:
      return False

    self.frame_id += 1
    timestamp_us = int(time.monotonic() * 1_000_000) & 0xFFFFFFFFFFFFFFFF

    if self._last_config_ack:
      payload = pixel_data
    else:
      total_leds = len(pixel_data) // 3
      channels = 5
      leds_per_ch = total_leds // channels if channels > 0 else 0
      payload = struct.pack('<BH', channels, leds_per_ch) + pixel_data

    packet = build_packet(
      PacketType.FRAME, payload,
      frame_id=self.frame_id, timestamp_us=timestamp_us,
    )
    framed = frame_packet(packet)

    async with self._lock:
      try:
        await asyncio.to_thread(self.serial.write, framed)
        self.frames_sent += 1
        return True
      except (serial.SerialException, OSError) as e:
        self.send_errors += 1
        logger.error(f"Frame send failed: {e}")
        self.connected = False
        return False
```

---

## File 5: pi/app/effects/fireworks.py — Particle System (Performance Bottleneck)

```python
"""Sound-reactive fireworks -- launches on beats, explodes into sparks."""

import math
import random
import time
import numpy as np
from .base import Effect


class _Spark:
  __slots__ = ('x', 'y', 'vx', 'vy', 'r', 'g', 'b', 'life', 'max_life')

  def __init__(self, x, y, vx, vy, r, g, b, life):
    self.x, self.y = x, y
    self.vx, self.vy = vx, vy
    self.r, self.g, self.b = r, g, b
    self.life = life
    self.max_life = life


class _Rocket:
  __slots__ = ('x', 'y', 'vy', 'target_y', 'r', 'g', 'b', 'trail')

  def __init__(self, x, target_y, height, r, g, b):
    self.x = x
    self.y = float(height - 1)
    self.vy = 0.0
    self.target_y = target_y
    self.r, self.g, self.b = r, g, b
    self.trail = []


class SRFireworks(Effect):
    """Sound-reactive fireworks -- beat launches rockets, bass controls intensity."""
    CATEGORY = "sound"
    DISPLAY_NAME = "SR Fireworks"
    _MAX_SPARKS = 600
    _MAX_ROCKETS = 10

    def render(self, t, state):
        # ... setup omitted for brevity ...
        dt = min(t - self._last_t, 0.05)
        self._last_t = t

        # Update sparks -- PURE PYTHON LOOP (bottleneck)
        alive_sparks = []
        for sp in self._sparks:          # Up to 600 iterations
            sp.x += sp.vx * dt
            sp.y += sp.vy * dt
            sp.vy += gravity * dt
            sp.vx *= 0.98
            sp.life -= dt
            if sp.life > 0 and 0 <= sp.y < rows:
                alive_sparks.append(sp)
        self._sparks = alive_sparks

        # Render sparks -- PURE PYTHON LOOP (bottleneck)
        frame = np.zeros((cols, rows, 3), dtype=np.float32)
        for sp in self._sparks:          # Up to 600 iterations
            ix = int(round(sp.x)) % cols
            iy = int(round(sp.y))
            if 0 <= ix < cols and 0 <= iy < rows:
                brightness = (sp.life / sp.max_life) ** 0.5
                frame[ix, iy, 0] = min(255, frame[ix, iy, 0] + sp.r * brightness)
                frame[ix, iy, 1] = min(255, frame[ix, iy, 1] + sp.g * brightness)
                frame[ix, iy, 2] = min(255, frame[ix, iy, 2] + sp.b * brightness)

        # Temporal fade
        result = np.clip(frame, 0, 255).astype(np.uint8)
        if self._prev_frame is not None:
            result = np.maximum(result, (self._prev_frame * 0.6).astype(np.uint8))
        self._prev_frame = result.copy()
        return result
```

---

## File 6: pi/app/effects/imported/ambient_a.py — Aurora Borealis (NumPy-Vectorized Effect)

```python
class AuroraBorealis(Effect):
  """Northern-lights curtain with noise-driven shimmer."""
  CATEGORY = "ambient"
  DISPLAY_NAME = "Aurora Borealis"
  PALETTE_SUPPORT = True

  PARAMS = [
    _Param("Speed", "speed", 0.05, 2.0, 0.05, 0.4),
    _Param("Wave", "wave", 0.2, 3.0, 0.1, 1.0),
    _Param("Bright", "bright", 0.2, 1.0, 0.05, 0.9),
  ]

  def render(self, t, state):
    dt_ms = self._calc_dt_ms(t)
    speed = self.params.get("speed", 0.4)
    wave = self.params.get("wave", 1.0)
    bright = self.params.get("bright", 0.9)
    pal_idx = self.params.get("palette", 0)

    self._t += dt_ms * 0.001 * speed
    tt = self._t
    cols = self.width
    rows = self.height

    # (cols, 1) and (1, rows) grids for broadcasting
    x_g = np.arange(cols, dtype=np.float64)[:, np.newaxis]
    y_g = np.arange(rows, dtype=np.float64)[np.newaxis, :]

    # ALL NUMPY VECTORIZED -- this is already near C speed
    curtain = (perlin_grid(y_g * 0.3, x_g * 0.008 * wave, tt * 0.5) + 1.0) * 0.5
    w = (np.sin(x_g * 0.02 * wave + tt * 2 + y_g * 0.8) + 1) * 0.5
    shimmer = (perlin_grid(y_g * 0.5 + 100, x_g * 0.02, tt * 3) + 1.0) * 0.5 * 0.4
    v = np.clip(curtain * w * bright + shimmer * curtain * bright * 0.5, 0.0, 1.0)

    hue = curtain * 0.8 + 0.1
    rgb = pal_color_grid(pal_idx % NUM_PALETTES, hue)
    self.buf.data = (rgb.astype(np.float32) * v[..., np.newaxis]).clip(0, 255).astype(np.uint8)
    return self.buf.get_frame()
```

---

## File 7: pi/tools/bench_effects.py — Benchmark Harness (INCOMPLETE COVERAGE)

**Note:** This harness only covers `EFFECTS`, `AUDIO_EFFECTS`, and `IMPORTED_EFFECTS`.
Effects registered ad-hoc in main.py (sr_fireworks, tetris, tetris_auto, scrolling_text,
animation_switcher) are NOT benchmarked. This is a gap that must be fixed.

```python
from app.effects.generative import EFFECTS
from app.effects.audio_reactive import AUDIO_EFFECTS
from app.effects.imported import IMPORTED_EFFECTS
from app.layout import load_layout, compile_layout, pack_frame

# Loads layout from config/ — uses checked-in layout.yaml (may differ from Pi's active layout)
_config_dir = Path(__file__).parent.parent / "config"
_layout_config = load_layout(_config_dir)
_layout = compile_layout(_layout_config)
GRID_WIDTH = _layout.width
GRID_HEIGHT = _layout.height

# Line 126: only these three dicts — missing sr_fireworks, tetris, scrolling_text, etc.
all_effects = {**EFFECTS, **AUDIO_EFFECTS, **IMPORTED_EFFECTS}
```

## File 8: pi/app/main.py — Effect Registration (lines 131-149)

```python
# These three are covered by bench_effects.py:
for name, cls in EFFECTS.items():
    renderer.register_effect(name, cls)
for name, cls in AUDIO_EFFECTS.items():
    renderer.register_effect(name, cls)
for name, cls in DIAGNOSTIC_EFFECTS.items():
    renderer.register_effect(name, cls)

# These are NOT covered by bench_effects.py:
renderer.register_effect('tetris', Tetris)
renderer.register_effect('tetris_auto', TetrisAutoplay)
renderer.register_effect('sr_fireworks', SRFireworks)
renderer.register_effect('scrolling_text', ScrollingText)

for name, cls in IMPORTED_EFFECTS.items():
    renderer.register_effect(name, cls)
renderer.register_effect('animation_switcher', AnimationSwitcher)
```

## File 9: pi/app/effects/base.py — Effect Base Class

```python
class Effect(ABC):
  """Base class for all effects."""
  NATIVE_WIDTH = None
  RENDER_SCALE = 1

  def __init__(self, width: int, height: int, params: Optional[dict] = None):
    self.width = width
    self.height = height
    self.params = params or {}
    self._start_time: Optional[float] = None

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

## Architecture Diagram

```
Phone/Browser (WiFi)
       |
       v
  [FastAPI Server]  <-- Python, async, port 80
       |
       v
  [Renderer]  <-- 60 FPS loop
       |
       +-- [Effect.render(t, state)]  <-- NumPy vectorized (fast) or Python loops (slow)
       |
       +-- [brightness * gamma LUT]   <-- NumPy array ops (fast)
       |
       +-- [pack_frame()]             <-- Pure Python loop, N iterations (N = layout pixel count)
       |
       +-- [COBS encode + CRC32]      <-- ~0.3ms
       |
       v
  [USB CDC Serial]  <-- USB 2.0 FS (baud advisory), asyncio.to_thread
       |
       v
  [Teensy 4.1]  <-- C++ firmware, OctoWS2811 DMA
       |
       v
  [5 x WS2812B strips]  <-- 830 LEDs (default layout; dynamic)
```

---

## Comparison: Our Architecture vs Pixelblaze

| Aspect | pillar-controller | Pixelblaze V3 |
|--------|-------------------|---------------|
| **CPU** | RPi 4 (1.5GHz quad ARM) | ESP32 (240MHz dual Xtensa) |
| **RAM** | 4-8 GB | 520 KB |
| **OS** | Linux | None (bare metal) |
| **Effect language** | Python + NumPy | Custom JS bytecode |
| **Throughput** | ~49,800 px/sec at default layout (60fps x 830) | 48,000 px/sec |
| **Max pixels** | Layout-dependent (dynamic) | 5000 (hardware limit) |
| **Audio** | Full FFT + beat detection | Basic via sensor board |
| **Media** | Video/image playback | None |
| **Web UI** | Full control dashboard | Pattern editor |
| **Live edit** | API + WebSocket | Keystroke-level |
| **Pattern model** | Class with render() returning frame | Expression per-pixel |
| **Compilation** | Python interpreted + NumPy C | Bytecode compiled |
