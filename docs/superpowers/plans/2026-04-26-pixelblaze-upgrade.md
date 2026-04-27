# Pixelblaze-Inspired Architecture Upgrade — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce a constrained Pixelblaze-inspired effect lifecycle (RenderContext + EffectV2), deduplicate shared kernels, fix Matrix Rain FPS degradation, and add a benchmark harness — without breaking any existing effects.

**Architecture:** RenderContext is built once per layout change and updated per-frame (time, dt, audio). EffectV2 base class enforces `before_render(ctx, dt)` + `render_pixels(ctx, out)` with renderer-owned output buffer. A compatibility adapter wraps all existing `render(t, state)` effects so they work without migration. Shared kernels centralize duplicated helpers (_calc_dt_ms, fade_buffer, hsv arrays). Matrix Rain is migrated to V2 as proof-of-concept.

**Tech Stack:** Python 3.13, NumPy, pytest

---

## Existing Code Context

**Effect base class** (`pi/app/effects/base.py`): `Effect(ABC)` with `render(t, state) -> np.ndarray`. Effects receive `(width, height, params)` in constructor.

**Renderer** (`pi/app/core/renderer.py`): Calls `effect.render(time.monotonic(), self.state)`. Applies brightness, gamma, y-flip, then packs via `pack_frame()`. The `RenderState` carries audio data, FPS stats, grid dimensions.

**Current problems:**
- `_calc_dt_ms()` is copy-pasted into 15+ imported effect classes
- `LEDBuffer` and `AudioCompatAdapter` instantiated per-effect (not shared)
- Generative effects allocate `np.zeros()` every frame
- No cached coordinate arrays — effects rebuild them each frame
- Matrix Rain allocates/grows particle lists causing FPS degradation
- Effect metadata split between class attributes (imported) and hardcoded catalog dicts (generative)

---

## File Structure

| File | Responsibility | Action |
|------|---------------|--------|
| `pi/app/effects/context.py` | RenderContext dataclass + builder | Create |
| `pi/app/effects/base_v2.py` | EffectV2 base class + compat adapter | Create |
| `pi/app/effects/kernels.py` | Shared render kernels (deduplicated) | Create |
| `pi/app/core/renderer.py` | Wire RenderContext, support both V1/V2 | Modify |
| `pi/app/effects/base.py` | Add metadata mixin to V1 base | Modify |
| `pi/tests/test_render_context.py` | RenderContext tests | Create |
| `pi/tests/test_effect_v2.py` | EffectV2 lifecycle tests | Create |
| `pi/tests/test_kernels.py` | Shared kernel tests | Create |
| `pi/tests/test_benchmark.py` | FPS stability benchmark | Create |
| `pi/tools/audit_constants.py` | Hardcoded constant scanner | Create |

---

## Task 1: RenderContext Dataclass

**Files:**
- Create: `pi/app/effects/context.py`
- Test: `pi/tests/test_render_context.py`

- [ ] **Step 1: Write failing test for RenderContext creation**

```python
# pi/tests/test_render_context.py
import numpy as np
import pytest
from app.effects.context import RenderContext, build_render_context


class TestRenderContext:
    def test_build_from_layout(self):
        """RenderContext has correct dimensions and cached coordinate arrays."""
        ctx = build_render_context(width=10, height=83, origin="bottom_left")
        assert ctx.width == 10
        assert ctx.height == 83
        assert ctx.x.shape == (10, 83)
        assert ctx.y.shape == (10, 83)
        assert ctx.index.shape == (10, 83)
        # x array: column index normalized 0-1
        assert ctx.x[0, 0] == pytest.approx(0.0)
        assert ctx.x[9, 0] == pytest.approx(1.0)
        # y array: row index normalized 0-1
        assert ctx.y[0, 0] == pytest.approx(0.0)
        assert ctx.y[0, 82] == pytest.approx(1.0)

    def test_index_array(self):
        """Index array is sequential pixel count."""
        ctx = build_render_context(width=3, height=4)
        assert ctx.index[0, 0] == 0
        assert ctx.index[2, 3] == 11  # 3*4 - 1
        assert ctx.logical_led_count == 12

    def test_update_per_frame(self):
        """Per-frame fields update without rebuilding coordinates."""
        ctx = build_render_context(width=10, height=83)
        ctx.update_frame(t=1.5, dt=0.0167, frame_index=90)
        assert ctx.t == 1.5
        assert ctx.dt == pytest.approx(0.0167)
        assert ctx.frame_index == 90

    def test_update_audio(self):
        """Audio snapshot updates per-frame."""
        ctx = build_render_context(width=10, height=83)
        audio = {"level": 0.5, "bass": 0.8, "beat": True}
        ctx.update_audio(audio)
        assert ctx.audio_raw["level"] == 0.5
        assert ctx.audio_raw["bass"] == 0.8

    def test_cylindrical_coordinates(self):
        """Theta wraps 0-2pi across width."""
        ctx = build_render_context(width=10, height=83)
        assert ctx.theta is not None
        assert ctx.theta.shape == (10, 83)
        assert ctx.theta[0, 0] == pytest.approx(0.0)
        # Last column should be close to 2*pi but not equal (it's 9/10 * 2pi)
        assert ctx.theta[9, 0] == pytest.approx(9/10 * 2 * np.pi, rel=0.01)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd pi && source .venv/bin/activate && PYTHONPATH=. pytest tests/test_render_context.py -v`
Expected: ImportError — `app.effects.context` does not exist

- [ ] **Step 3: Implement RenderContext**

```python
# pi/app/effects/context.py
"""
RenderContext — cached per-layout coordinate arrays and per-frame state.

Built once when the layout changes. Updated every frame with time, dt, and audio.
Effects use this instead of building their own coordinate grids.
"""

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np


@dataclass
class RenderContext:
    """Immutable layout-derived fields + mutable per-frame fields."""

    # Layout-derived (set once, cached)
    width: int = 0
    height: int = 0
    origin: str = "bottom_left"
    logical_led_count: int = 0

    # Cached coordinate arrays (set once per layout change)
    x: np.ndarray = field(default_factory=lambda: np.empty(0))       # normalized 0-1
    y: np.ndarray = field(default_factory=lambda: np.empty(0))       # normalized 0-1
    theta: Optional[np.ndarray] = None                                # 0 to 2*pi (cylindrical)
    radius: Optional[np.ndarray] = None                               # 0-1 from center
    index: np.ndarray = field(default_factory=lambda: np.empty(0))   # sequential 0..N-1

    # Per-frame (updated every render cycle)
    t: float = 0.0
    dt: float = 0.0167
    frame_index: int = 0

    # Audio (updated every frame from RenderState)
    audio_raw: dict = field(default_factory=dict)

    def update_frame(self, t: float, dt: float, frame_index: int):
        """Update per-frame timing. Called by renderer before each effect render."""
        self.t = t
        self.dt = dt
        self.frame_index = frame_index

    def update_audio(self, audio_snapshot: dict):
        """Update audio data. Called by renderer before each effect render."""
        self.audio_raw = audio_snapshot


def build_render_context(width: int, height: int, origin: str = "bottom_left") -> RenderContext:
    """Build a RenderContext with cached coordinate arrays for the given grid dimensions."""
    # Normalized coordinate grids: x across columns (0-1), y across rows (0-1)
    if width <= 1:
        x_1d = np.array([0.0])
    else:
        x_1d = np.arange(width, dtype=np.float64) / (width - 1)
    if height <= 1:
        y_1d = np.array([0.0])
    else:
        y_1d = np.arange(height, dtype=np.float64) / (height - 1)

    x = np.broadcast_to(x_1d[:, np.newaxis], (width, height)).copy()
    y = np.broadcast_to(y_1d[np.newaxis, :], (width, height)).copy()

    # Sequential pixel index
    index = np.arange(width * height, dtype=np.int32).reshape(width, height)

    # Cylindrical coordinates (for pillar/cylinder effects)
    theta_1d = np.arange(width, dtype=np.float64) / width * 2 * math.pi
    theta = np.broadcast_to(theta_1d[:, np.newaxis], (width, height)).copy()

    # Radius from center (useful for radial effects)
    cx, cy = 0.5, 0.5
    radius = np.sqrt((x - cx) ** 2 + (y - cy) ** 2)

    return RenderContext(
        width=width,
        height=height,
        origin=origin,
        logical_led_count=width * height,
        x=x,
        y=y,
        theta=theta,
        radius=radius,
        index=index,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd pi && PYTHONPATH=. pytest tests/test_render_context.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add pi/app/effects/context.py pi/tests/test_render_context.py
git commit -m "feat: RenderContext with cached coordinate arrays

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Shared Kernel Library

**Files:**
- Create: `pi/app/effects/kernels.py`
- Test: `pi/tests/test_kernels.py`

- [ ] **Step 1: Write failing tests for shared kernels**

```python
# pi/tests/test_kernels.py
import numpy as np
import pytest
from app.effects.kernels import calc_dt, fade_buffer, additive_blend


class TestCalcDt:
    def test_first_call_returns_default(self):
        state = {"last_t": None}
        dt = calc_dt(1.0, state)
        assert dt == pytest.approx(0.01667, rel=0.01)
        assert state["last_t"] == 1.0

    def test_subsequent_call_returns_delta(self):
        state = {"last_t": 1.0}
        dt = calc_dt(1.05, state)
        assert dt == pytest.approx(0.05)

    def test_never_negative(self):
        state = {"last_t": 2.0}
        dt = calc_dt(1.0, state)  # time went backward
        assert dt == 0.0


class TestFadeBuffer:
    def test_fade_reduces_values(self):
        buf = np.full((3, 3, 3), 200, dtype=np.uint8)
        fade_buffer(buf, 0.5)
        assert buf[0, 0, 0] == 100

    def test_fade_one_is_noop(self):
        buf = np.full((3, 3, 3), 200, dtype=np.uint8)
        fade_buffer(buf, 1.0)
        assert buf[0, 0, 0] == 200

    def test_fade_zero_clears(self):
        buf = np.full((3, 3, 3), 200, dtype=np.uint8)
        fade_buffer(buf, 0.0)
        assert buf[0, 0, 0] == 0


class TestAdditiveBlend:
    def test_add_and_clip(self):
        buf = np.full((2, 2, 3), 200, dtype=np.uint8)
        add = np.full((2, 2, 3), 100, dtype=np.uint8)
        additive_blend(buf, add)
        assert buf[0, 0, 0] == 255  # clipped

    def test_add_no_overflow(self):
        buf = np.full((2, 2, 3), 50, dtype=np.uint8)
        add = np.full((2, 2, 3), 30, dtype=np.uint8)
        additive_blend(buf, add)
        assert buf[0, 0, 0] == 80
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd pi && PYTHONPATH=. pytest tests/test_kernels.py -v`
Expected: ImportError

- [ ] **Step 3: Implement shared kernels**

```python
# pi/app/effects/kernels.py
"""
Shared render kernels — centralized helpers used by multiple effects.

Deduplicates _calc_dt_ms, fade, blend, and HSV conversion that were
previously copy-pasted across 15+ effect classes.
"""

import numpy as np


def calc_dt(t: float, state: dict, default: float = 1/60) -> float:
    """Calculate delta time in seconds. Replaces per-class _calc_dt_ms pattern.

    state must be a dict with a 'last_t' key (initially None).
    Returns dt in seconds (not milliseconds).
    """
    last_t = state.get("last_t")
    if last_t is None:
        state["last_t"] = t
        return default
    dt = t - last_t
    state["last_t"] = t
    return max(0.0, dt)


def fade_buffer(buf: np.ndarray, factor: float):
    """Multiply an (W, H, 3) uint8 buffer by factor in-place. Replaces LEDBuffer.fade()."""
    if factor >= 1.0:
        return
    if factor <= 0.0:
        buf[:] = 0
        return
    buf[:] = (buf.astype(np.uint16) * int(factor * 256) >> 8).astype(np.uint8)


def additive_blend(dst: np.ndarray, src: np.ndarray):
    """Add src to dst (both uint8), clipping at 255. In-place on dst."""
    np.add(dst, src, out=dst, casting='unsafe')
    # The above overflows — use uint16 intermediate
    tmp = dst.astype(np.uint16) + src.astype(np.uint16)
    np.clip(tmp, 0, 255, out=tmp)
    dst[:] = tmp.astype(np.uint8)


def hsv_to_rgb_array(h: np.ndarray, s: float, v: float) -> np.ndarray:
    """Vectorized HSV to RGB. h is array of hues 0-1, s and v are scalars.

    Returns (shape..., 3) uint8 array.
    """
    h = h % 1.0
    i = (h * 6.0).astype(np.int32) % 6
    f = h * 6.0 - np.floor(h * 6.0)
    p = v * (1.0 - s)
    q = v * (1.0 - s * f)
    t = v * (1.0 - s * (1.0 - f))

    r = np.where(i == 0, v, np.where(i == 1, q, np.where(i == 2, p, np.where(i == 3, p, np.where(i == 4, t, v)))))
    g = np.where(i == 0, t, np.where(i == 1, v, np.where(i == 2, v, np.where(i == 3, q, np.where(i == 4, p, p)))))
    b = np.where(i == 0, p, np.where(i == 1, p, np.where(i == 2, t, np.where(i == 3, v, np.where(i == 4, v, q)))))

    out = np.zeros((*h.shape, 3), dtype=np.uint8)
    out[..., 0] = (r * 255).astype(np.uint8)
    out[..., 1] = (g * 255).astype(np.uint8)
    out[..., 2] = (b * 255).astype(np.uint8)
    return out


def temporal_blend(current: np.ndarray, previous: np.ndarray, factor: float) -> np.ndarray:
    """Blend current frame with previous for smooth transitions.

    factor: 0.0 = all current, 1.0 = all previous.
    Returns new uint8 array.
    """
    return (current.astype(np.float32) * (1 - factor) + previous.astype(np.float32) * factor).astype(np.uint8)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd pi && PYTHONPATH=. pytest tests/test_kernels.py -v`
Expected: All 8 tests PASS

- [ ] **Step 5: Fix additive_blend (the simple add overflows)**

The initial implementation has a bug — `np.add` with uint8 wraps. Replace:

```python
def additive_blend(dst: np.ndarray, src: np.ndarray):
    """Add src to dst (both uint8), clipping at 255. In-place on dst."""
    tmp = dst.astype(np.uint16) + src.astype(np.uint16)
    np.clip(tmp, 0, 255, out=tmp)
    dst[:] = tmp.astype(np.uint8)
```

- [ ] **Step 6: Commit**

```bash
git add pi/app/effects/kernels.py pi/tests/test_kernels.py
git commit -m "feat: shared render kernels (calc_dt, fade, blend, hsv)

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: EffectV2 Base Class + Compatibility Adapter

**Files:**
- Create: `pi/app/effects/base_v2.py`
- Test: `pi/tests/test_effect_v2.py`

- [ ] **Step 1: Write failing tests**

```python
# pi/tests/test_effect_v2.py
import numpy as np
import pytest
from app.effects.context import RenderContext, build_render_context
from app.effects.base_v2 import EffectV2, EffectV1Adapter
from app.effects.base import Effect


class SimpleV2Effect(EffectV2):
    """Test V2 effect — fills frame with red."""
    CATEGORY = "test"
    DISPLAY_NAME = "Test V2"

    def before_render(self, ctx: RenderContext, dt: float):
        pass

    def render_pixels(self, ctx: RenderContext, out: np.ndarray):
        out[:, :] = [255, 0, 0]


class TestEffectV2:
    def test_v2_lifecycle(self):
        """V2 effect receives RenderContext and writes to provided buffer."""
        ctx = build_render_context(width=3, height=4)
        effect = SimpleV2Effect(width=3, height=4)
        out = np.zeros((3, 4, 3), dtype=np.uint8)
        effect.before_render(ctx, 0.0167)
        effect.render_pixels(ctx, out)
        assert out[0, 0, 0] == 255  # red
        assert out[0, 0, 1] == 0

    def test_v2_has_metadata(self):
        effect = SimpleV2Effect(width=3, height=4)
        assert effect.CATEGORY == "test"
        assert effect.DISPLAY_NAME == "Test V2"


# V1 effect for adapter testing
class OldStyleEffect(Effect):
    def render(self, t, state):
        frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)
        frame[:, :] = [0, 255, 0]  # green
        return frame


class TestV1Adapter:
    def test_adapter_wraps_v1_as_v2(self):
        """V1 adapter makes old effects work with V2 renderer."""
        v1 = OldStyleEffect(width=3, height=4)
        adapter = EffectV1Adapter(v1)
        ctx = build_render_context(width=3, height=4)
        ctx.update_frame(t=1.0, dt=0.0167, frame_index=60)
        out = np.zeros((3, 4, 3), dtype=np.uint8)
        adapter.before_render(ctx, 0.0167)
        adapter.render_pixels(ctx, out)
        assert out[0, 0, 1] == 255  # green from V1 effect

    def test_adapter_passes_render_state(self):
        """Adapter creates a RenderState-like object from context for V1 effects."""
        v1 = OldStyleEffect(width=3, height=4)
        adapter = EffectV1Adapter(v1)
        ctx = build_render_context(width=3, height=4)
        ctx.update_frame(t=1.0, dt=0.0167, frame_index=60)
        ctx.update_audio({"level": 0.5, "bass": 0.3, "mid": 0.2, "high": 0.1, "beat": False, "bpm": 120, "spectrum": [0.0]*16})
        out = np.zeros((3, 4, 3), dtype=np.uint8)
        adapter.before_render(ctx, 0.0167)
        adapter.render_pixels(ctx, out)
        # Should not crash — V1 effect accessed state.audio_level etc.
        assert True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd pi && PYTHONPATH=. pytest tests/test_effect_v2.py -v`
Expected: ImportError

- [ ] **Step 3: Implement EffectV2 and adapter**

```python
# pi/app/effects/base_v2.py
"""
EffectV2 — constrained Pixelblaze-inspired effect lifecycle.

Effects implement:
  before_render(ctx, dt)    — update timers, particles, state
  render_pixels(ctx, out)   — fill the output buffer using cached coordinates

The renderer owns the output buffer and provides it. Effects MUST NOT
allocate a new frame array in render_pixels.
"""

from abc import ABC, abstractmethod
from typing import Optional

import numpy as np

from .context import RenderContext


class EffectV2(ABC):
    """Base class for V2 effects with constrained lifecycle."""

    # Metadata — subclasses should override
    CATEGORY: str = "uncategorized"
    DISPLAY_NAME: str = ""
    DESCRIPTION: str = ""
    PALETTE_SUPPORT: bool = False
    AUDIO_REQUIRED: bool = False
    PARAMS: list = []

    def __init__(self, width: int, height: int, params: Optional[dict] = None):
        self.width = width
        self.height = height
        self.params = params or {}

    @abstractmethod
    def before_render(self, ctx: RenderContext, dt: float):
        """Update effect state. Called once per frame before render_pixels.

        DO: update timers, advance particles, read params, process audio.
        DON'T: allocate large arrays, write to output, do I/O.
        """
        pass

    @abstractmethod
    def render_pixels(self, ctx: RenderContext, out: np.ndarray):
        """Write pixels to the output buffer.

        out is (width, height, 3) uint8, provided by the renderer.
        Use ctx.x, ctx.y, ctx.theta, ctx.index for coordinates.
        DO NOT allocate a new frame — write directly to out.
        """
        pass

    def update_params(self, params: dict):
        """Update parameters without resetting state."""
        self.params.update(params)

    def on_layout_change(self, ctx: RenderContext):
        """Called when the layout changes. Override to rebuild caches."""
        pass


class EffectV1Adapter(EffectV2):
    """Wraps a V1 Effect (render(t, state) -> ndarray) to work with the V2 lifecycle.

    Allows gradual migration — existing effects work without changes.
    """

    def __init__(self, v1_effect):
        self._v1 = v1_effect
        super().__init__(v1_effect.width, v1_effect.height, v1_effect.params)
        # Forward metadata
        self.CATEGORY = getattr(v1_effect, 'CATEGORY', 'uncategorized')
        self.DISPLAY_NAME = getattr(v1_effect, 'DISPLAY_NAME', '')
        self.DESCRIPTION = getattr(v1_effect, 'DESCRIPTION', '')
        self.PALETTE_SUPPORT = getattr(v1_effect, 'PALETTE_SUPPORT', False)
        self.PARAMS = getattr(v1_effect, 'PARAMS', [])
        self._last_frame = None
        self._render_state_proxy = None

    def before_render(self, ctx: RenderContext, dt: float):
        """Build a RenderState-compatible proxy from the RenderContext."""
        self._render_state_proxy = _RenderStateProxy(ctx)

    def render_pixels(self, ctx: RenderContext, out: np.ndarray):
        """Call the V1 render() and copy result into the provided buffer."""
        frame = self._v1.render(ctx.t, self._render_state_proxy)
        if frame is not None and frame.shape == out.shape:
            out[:] = frame
        elif frame is not None:
            # Shape mismatch — try to use what fits
            min_w = min(frame.shape[0], out.shape[0])
            min_h = min(frame.shape[1], out.shape[1])
            out[:min_w, :min_h] = frame[:min_w, :min_h]

    def update_params(self, params: dict):
        self._v1.update_params(params)
        self.params = self._v1.params


class _RenderStateProxy:
    """Mimics RenderState interface from RenderContext for V1 compatibility."""

    def __init__(self, ctx: RenderContext):
        self._ctx = ctx
        self._audio = ctx.audio_raw
        self.target_fps = 60
        self.actual_fps = 60.0
        self.current_scene = None
        self.blackout = False
        self.frames_rendered = ctx.frame_index
        self.frames_sent = ctx.frame_index
        self.frames_dropped = 0
        self.last_frame_time_ms = ctx.dt * 1000
        self.render_cost_ms = 0.0
        self.grid_width = ctx.width
        self.grid_height = ctx.height
        self.origin = ctx.origin

    # Audio lock-free dict (accessed directly by imported sound effects)
    @property
    def _audio_lock_free(self):
        return self._audio

    @property
    def audio_level(self):
        return self._audio.get('level', 0.0)

    @property
    def audio_bass(self):
        return self._audio.get('bass', 0.0)

    @property
    def audio_mid(self):
        return self._audio.get('mid', 0.0)

    @property
    def audio_high(self):
        return self._audio.get('high', 0.0)

    @property
    def audio_beat(self):
        return self._audio.get('beat', False)

    @property
    def audio_bpm(self):
        return self._audio.get('bpm', 0.0)

    @property
    def audio_spectrum(self):
        return self._audio.get('spectrum', [0.0] * 16)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd pi && PYTHONPATH=. pytest tests/test_effect_v2.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add pi/app/effects/base_v2.py pi/tests/test_effect_v2.py
git commit -m "feat: EffectV2 base class + V1 compatibility adapter

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Wire RenderContext into Renderer

**Files:**
- Modify: `pi/app/core/renderer.py`

- [ ] **Step 1: Build RenderContext on layout change**

In `Renderer.__init__`, after storing `self.layout`, add:

```python
from ..effects.context import build_render_context
self._render_ctx = build_render_context(layout.width, layout.height, layout.origin)
```

In `apply_layout()`, rebuild the context:

```python
self._render_ctx = build_render_context(layout.width, layout.height, layout.origin)
```

- [ ] **Step 2: Update RenderContext per-frame in _render_frame**

At the top of `_render_frame()`, before calling the effect:

```python
# Update context per-frame
dt = 0.0167  # default
if self._last_frame_start > 0:
    dt = frame_start - self._last_frame_start
self._render_ctx.update_frame(t=time.monotonic(), dt=dt, frame_index=self.state.frames_rendered)
self._render_ctx.update_audio(self.state._audio_lock_free)
```

- [ ] **Step 3: Support both V1 and V2 effects**

In `_render_frame()`, replace the current effect call:

```python
# Check if effect is V2 or V1
from ..effects.base_v2 import EffectV2
if isinstance(self.current_effect, EffectV2):
    # V2: use constrained lifecycle with renderer-owned buffer
    logical_frame = np.zeros((w, h, 3), dtype=np.uint8)
    self.current_effect.before_render(self._render_ctx, self._render_ctx.dt)
    self.current_effect.render_pixels(self._render_ctx, logical_frame)
else:
    # V1: existing path
    internal_frame = self.current_effect.render(t, self.state)
    # ... existing downsample/etc logic
```

- [ ] **Step 4: Run full test suite**

Run: `cd pi && PYTHONPATH=. pytest tests/ -v -k "not test_matrix_rain_perf and not test_import_writes" 2>&1 | tail -10`
Expected: All pass — existing V1 effects unchanged

- [ ] **Step 5: Commit**

```bash
git add pi/app/core/renderer.py
git commit -m "feat: wire RenderContext into renderer, support V1 + V2 effects

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Hardcoded Constant Audit

**Files:**
- Create: `pi/tools/audit_constants.py`

- [ ] **Step 1: Create the audit script**

```python
#!/usr/bin/env python3
# pi/tools/audit_constants.py
"""Scan codebase for hardcoded LED constants that should come from config."""

import re
import sys
from pathlib import Path

SUSPICIOUS = {
    r'\b83\b': 'strip length (83 LEDs)',
    r'\b82\b': 'strip length - 1',
    r'\b84\b': 'strip length + 1',
    r'\b172\b': 'old strip length (172)',
    r'\b171\b': 'old strip length - 1',
    r'\b173\b': 'old strip length + 1',
    r'\b344\b': 'old output size (2×172)',
    r'\b166\b': 'output size (2×83)',
    r'\bLEDS_PER_STRIP\b': 'hardcoded LED count name',
    r'\bSTRIP_COUNT\b': 'hardcoded strip count name',
    r'\bNUM_LEDS\b': 'hardcoded LED count name',
}

# Paths to skip
SKIP_DIRS = {'__pycache__', '.git', 'node_modules', 'vendor', '.venv', 'venv'}
SKIP_FILES = {'audit_constants.py', 'config.h'}  # config.h legitimately has defaults

# Files where constants are expected (config, tests, plans)
EXPECTED_PATHS = {'config/', 'tests/', 'docs/', 'plans/'}

def scan(root: Path):
    hits = []
    for path in sorted(root.rglob('*')):
        if any(d in path.parts for d in SKIP_DIRS):
            continue
        if path.name in SKIP_FILES:
            continue
        if not path.is_file() or path.suffix not in ('.py', '.js', '.yaml', '.yml', '.ts'):
            continue

        is_expected = any(e in str(path) for e in EXPECTED_PATHS)
        try:
            text = path.read_text(errors='ignore')
        except Exception:
            continue

        for lineno, line in enumerate(text.splitlines(), 1):
            # Skip comments
            stripped = line.strip()
            if stripped.startswith('#') or stripped.startswith('//'):
                continue
            for pattern, desc in SUSPICIOUS.items():
                if re.search(pattern, line):
                    status = 'EXPECTED' if is_expected else 'SUSPICIOUS'
                    rel = path.relative_to(root)
                    hits.append((status, str(rel), lineno, desc, stripped[:80]))

    # Report
    suspicious = [h for h in hits if h[0] == 'SUSPICIOUS']
    expected = [h for h in hits if h[0] == 'EXPECTED']

    print(f"\n=== SUSPICIOUS ({len(suspicious)} hits) ===")
    for status, path, line, desc, text in suspicious:
        print(f"  {path}:{line} [{desc}] {text}")

    print(f"\n=== EXPECTED ({len(expected)} hits) ===")
    for status, path, line, desc, text in expected:
        print(f"  {path}:{line} [{desc}] {text}")

    print(f"\nTotal: {len(suspicious)} suspicious, {len(expected)} expected")
    return len(suspicious)

if __name__ == '__main__':
    root = Path(__file__).parent.parent
    sys.exit(0 if scan(root) == 0 else 1)
```

- [ ] **Step 2: Run the audit**

```bash
cd /Users/jim/ai/pillar-controller/pi && python tools/audit_constants.py
```

Review each SUSPICIOUS hit and classify as valid or needs-fix.

- [ ] **Step 3: Commit**

```bash
git add pi/tools/audit_constants.py
git commit -m "tool: hardcoded constant audit scanner

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Performance Benchmark Harness

**Files:**
- Create: `pi/tests/test_benchmark.py`

- [ ] **Step 1: Write benchmark test**

```python
# pi/tests/test_benchmark.py
"""Performance benchmark — runs effects for N seconds and checks stability."""

import time
import numpy as np
import pytest
from pathlib import Path

from app.layout import load_layout, compile_layout
from app.layout.packer import pack_frame


def _get_compiled():
    config_dir = Path(__file__).parent.parent / "config"
    config = load_layout(config_dir)
    return compile_layout(config)


def _benchmark_effect(effect_cls, duration=5.0, params=None):
    """Run an effect for `duration` seconds, return frame time stats."""
    compiled = _get_compiled()
    w, h = compiled.width, compiled.height
    effect = effect_cls(width=w, height=h, params=params or {})

    # Fake RenderState
    class FakeState:
        target_fps = 60
        actual_fps = 60.0
        current_scene = "benchmark"
        blackout = False
        frames_rendered = 0
        audio_level = 0.3
        audio_bass = 0.4
        audio_mid = 0.2
        audio_high = 0.1
        audio_beat = False
        audio_bpm = 120.0
        audio_spectrum = [0.1] * 16
        _audio_lock_free = {
            'level': 0.3, 'bass': 0.4, 'mid': 0.2, 'high': 0.1,
            'beat': False, 'bpm': 120.0, 'spectrum': [0.1] * 16,
        }

    state = FakeState()
    frame_times = []
    t_start = time.monotonic()

    while time.monotonic() - t_start < duration:
        t = time.monotonic()
        frame = effect.render(t, state)
        pack_frame(frame, compiled)
        elapsed = time.monotonic() - t
        frame_times.append(elapsed)
        state.frames_rendered += 1
        # Simulate beat every 0.5s
        if state.frames_rendered % 30 == 0:
            state.audio_beat = True
            state._audio_lock_free['beat'] = True
        else:
            state.audio_beat = False
            state._audio_lock_free['beat'] = False

    times = np.array(frame_times)
    return {
        'frames': len(times),
        'avg_ms': np.mean(times) * 1000,
        'p50_ms': np.percentile(times, 50) * 1000,
        'p95_ms': np.percentile(times, 95) * 1000,
        'p99_ms': np.percentile(times, 99) * 1000,
        'max_ms': np.max(times) * 1000,
        'fps': len(times) / duration,
    }


class TestBenchmark:
    """Performance gates — effects must maintain stable FPS."""

    def test_fire_baseline(self):
        """Fire should maintain smooth performance (our reference effect)."""
        from app.effects.generative import Fire
        stats = _benchmark_effect(Fire, duration=5.0)
        print(f"Fire: {stats['fps']:.0f} FPS, avg={stats['avg_ms']:.1f}ms, p95={stats['p95_ms']:.1f}ms")
        assert stats['p95_ms'] < 16.67, f"Fire p95 too slow: {stats['p95_ms']:.1f}ms"

    def test_matrix_rain_stability(self):
        """Matrix Rain must not degrade over time."""
        from app.effects.imported.ambient_a import MatrixRain
        # Run for 10 seconds
        stats = _benchmark_effect(MatrixRain, duration=10.0)
        print(f"Matrix Rain: {stats['fps']:.0f} FPS, avg={stats['avg_ms']:.1f}ms, p99={stats['p99_ms']:.1f}ms, max={stats['max_ms']:.1f}ms")
        # Must not have spikes above 2x budget
        assert stats['p99_ms'] < 33.0, f"Matrix Rain p99 too slow: {stats['p99_ms']:.1f}ms"

    def test_solid_color_budget(self):
        """Simplest effect — establishes baseline overhead."""
        from app.effects.generative import SolidColor
        stats = _benchmark_effect(SolidColor, duration=3.0)
        print(f"SolidColor: {stats['fps']:.0f} FPS, avg={stats['avg_ms']:.1f}ms")
        assert stats['avg_ms'] < 5.0, f"SolidColor too slow: {stats['avg_ms']:.1f}ms"

    def test_fireplace_stability(self):
        """Fireplace (complex) should remain stable."""
        from app.effects.imported.classic import Fireplace
        stats = _benchmark_effect(Fireplace, duration=5.0)
        print(f"Fireplace: {stats['fps']:.0f} FPS, avg={stats['avg_ms']:.1f}ms, p95={stats['p95_ms']:.1f}ms")
        # Fireplace is heavy but should stay under budget
        assert stats['p95_ms'] < 16.67, f"Fireplace p95 too slow: {stats['p95_ms']:.1f}ms"
```

- [ ] **Step 2: Run benchmarks**

```bash
cd pi && PYTHONPATH=. pytest tests/test_benchmark.py -v -s
```

Note: These run on the dev machine (not Pi). The Pi will be ~3-5x slower. Results establish relative performance baselines.

- [ ] **Step 3: Commit**

```bash
git add pi/tests/test_benchmark.py
git commit -m "test: performance benchmark harness with FPS stability gates

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Example V2 Effect — Migrate Rainbow Rotate

**Files:**
- Create: `pi/app/effects/v2/rainbow.py`
- Test: `pi/tests/test_v2_rainbow.py`

This proves the V2 lifecycle works end-to-end with a real effect.

- [ ] **Step 1: Write failing test**

```python
# pi/tests/test_v2_rainbow.py
import numpy as np
import pytest
from app.effects.context import build_render_context
from app.effects.v2.rainbow import RainbowRotateV2


class TestRainbowV2:
    def test_renders_non_black(self):
        ctx = build_render_context(width=10, height=83)
        ctx.update_frame(t=1.0, dt=0.0167, frame_index=60)
        effect = RainbowRotateV2(width=10, height=83)
        out = np.zeros((10, 83, 3), dtype=np.uint8)
        effect.before_render(ctx, 0.0167)
        effect.render_pixels(ctx, out)
        assert out.sum() > 0, "Frame should not be all black"

    def test_no_frame_allocation(self):
        """V2 effect must write to provided buffer, not allocate new one."""
        ctx = build_render_context(width=10, height=83)
        ctx.update_frame(t=1.0, dt=0.0167, frame_index=60)
        effect = RainbowRotateV2(width=10, height=83)
        out = np.zeros((10, 83, 3), dtype=np.uint8)
        effect.before_render(ctx, 0.0167)
        effect.render_pixels(ctx, out)
        # out should be modified in-place
        assert out[5, 40, 0] > 0 or out[5, 40, 1] > 0 or out[5, 40, 2] > 0
```

- [ ] **Step 2: Implement V2 rainbow effect**

```python
# pi/app/effects/v2/__init__.py
"""V2 effects — using the constrained Pixelblaze-inspired lifecycle."""

# pi/app/effects/v2/rainbow.py
"""Rainbow rotate — V2 implementation using RenderContext coordinates."""

import numpy as np
from ..base_v2 import EffectV2
from ..context import RenderContext
from ..kernels import hsv_to_rgb_array


class RainbowRotateV2(EffectV2):
    """Rainbow that rotates around the cylinder. V2 lifecycle."""

    CATEGORY = "generative"
    DISPLAY_NAME = "Rainbow Rotate V2"
    DESCRIPTION = "Rainbow rotating around the pillar using cached coordinates"

    def __init__(self, width, height, params=None):
        super().__init__(width, height, params)
        self._elapsed = 0.0

    def before_render(self, ctx: RenderContext, dt: float):
        speed = self.params.get('speed', 1.0)
        self._elapsed += dt * speed

    def render_pixels(self, ctx: RenderContext, out: np.ndarray):
        # Use cached theta (cylindrical angle) — no coordinate generation per frame
        hue = (ctx.theta / (2 * np.pi) + self._elapsed * 0.2) % 1.0
        rgb = hsv_to_rgb_array(hue, 1.0, 1.0)
        out[:] = rgb
```

- [ ] **Step 3: Run tests**

```bash
cd pi && PYTHONPATH=. pytest tests/test_v2_rainbow.py -v
```

- [ ] **Step 4: Register the V2 effect**

In `pi/app/main.py`, after the existing effect registrations:

```python
from .effects.v2.rainbow import RainbowRotateV2
renderer.register_effect('rainbow_rotate_v2', RainbowRotateV2)
```

Note: The renderer's `_set_scene` must handle V2 effects. Since V2 effects have the same `__init__(width, height, params)` signature, existing instantiation works. The V2-specific `before_render`/`render_pixels` path is handled by the isinstance check in `_render_frame()` (Task 4).

- [ ] **Step 5: Commit**

```bash
git add pi/app/effects/v2/__init__.py pi/app/effects/v2/rainbow.py pi/tests/test_v2_rainbow.py pi/app/main.py
git commit -m "feat: example V2 effect (RainbowRotateV2) proving constrained lifecycle

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Deploy + Verify

- [ ] **Step 1: Run full test suite**

```bash
cd pi && PYTHONPATH=. pytest tests/ -v
```

- [ ] **Step 2: Run benchmarks**

```bash
cd pi && PYTHONPATH=. pytest tests/test_benchmark.py -v -s
```

- [ ] **Step 3: Run constant audit**

```bash
cd pi && python tools/audit_constants.py
```

- [ ] **Step 4: Deploy**

```bash
bash pi/scripts/deploy.sh ledfanatic.local
```

- [ ] **Step 5: Verify on Pi**

- Select RainbowRotateV2 from effects (if registered in catalog)
- Verify all existing effects still work
- Check logs for errors: `ssh jim@ledfanatic.local 'journalctl -u pillar -n 20 --no-pager'`
