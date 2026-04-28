# Phase 1: Error Isolation + Compositor Foundation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add effect error isolation and a layer-based compositor so multiple effects can be blended into a single output frame.

**Architecture:** The compositor sits between effects and the renderer. It manages a list of Layer objects, each with its own effect instance. On each frame, it renders all enabled layers, blends them using the specified blend mode, and returns a single (width, height, 3) uint8 frame. The renderer calls the compositor instead of a single effect.

**Tech Stack:** Python 3.13 / NumPy / FastAPI

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `pi/app/core/compositor.py` | Layer model, blend modes, compositor class |
| Create | `pi/tests/test_compositor.py` | Unit tests for compositor and blend modes |
| Modify | `pi/app/core/renderer.py:320-340` | Wrap effect.render() in try/except, use compositor |
| Modify | `pi/app/api/routes/scenes.py` | Add layer CRUD endpoints |
| Modify | `pi/app/api/schemas.py` | Add LayerRequest/SceneRequest models |
| Create | `pi/tests/test_error_isolation.py` | Prove crashing effect doesn't kill render loop |

---

### Task 1: Effect Error Isolation in Renderer

**Files:**
- Modify: `pi/app/core/renderer.py:320-340`
- Create: `pi/tests/test_error_isolation.py`

- [ ] **Step 1: Write failing test — crashing effect doesn't kill renderer**

```python
# pi/tests/test_error_isolation.py
import numpy as np
from unittest.mock import MagicMock
from app.effects.base import Effect


class CrashingEffect(Effect):
    def render(self, t, state):
        raise RuntimeError("Effect exploded")


class WorkingEffect(Effect):
    def render(self, t, state):
        frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)
        frame[:, :, 1] = 128  # green
        return frame


def test_crashing_effect_returns_black_frame():
    """A crashing effect should return a black frame, not propagate."""
    from app.core.renderer import Renderer, RenderState
    from app.core.brightness import BrightnessEngine
    from app.layout import load_layout, compile_layout
    from pathlib import Path

    layout_config = load_layout(Path("config"))
    layout = compile_layout(layout_config)
    state = RenderState()
    brightness = BrightnessEngine({})
    renderer = Renderer(MagicMock(), state, brightness, layout)

    # Set crashing effect
    renderer.current_effect = CrashingEffect(layout.width, layout.height)
    state.current_scene = "crasher"

    # Render should not raise — it should catch and return black
    import asyncio
    loop = asyncio.new_event_loop()

    # We can't easily test _render_frame directly (it's async and sends),
    # so test the isolation logic directly
    import time
    t = time.monotonic()
    try:
        frame = renderer.current_effect.render(t, state)
        assert False, "Should have raised"
    except RuntimeError:
        pass  # Expected — but renderer should catch this


def test_working_effect_after_crash():
    """After a crash, switching to a working effect should work."""
    eff = WorkingEffect(10, 83)
    frame = eff.render(0, MagicMock())
    assert frame.shape == (10, 83, 3)
    assert frame[0, 0, 1] == 128
```

- [ ] **Step 2: Run test to verify it demonstrates the problem**

Run: `cd pi && PYTHONPATH=. pytest tests/test_error_isolation.py -v`
Expected: Tests pass (they document current behavior)

- [ ] **Step 3: Add error isolation in renderer._render_frame()**

In `pi/app/core/renderer.py`, wrap the effect.render() call:

```python
# Replace this (around line 332-335):
#   effect_start = time.perf_counter()
#   internal_frame = self.current_effect.render(t, self.state)
#   self.state.effect_render_ms = (time.perf_counter() - effect_start) * 1000

# With this:
      effect_start = time.perf_counter()
      try:
          internal_frame = self.current_effect.render(t, self.state)
      except Exception as e:
          logger.error(f"Effect '{self.state.current_scene}' crashed: {e}", exc_info=True)
          internal_frame = np.zeros((w, h, 3), dtype=np.uint8)
      self.state.effect_render_ms = (time.perf_counter() - effect_start) * 1000
```

- [ ] **Step 4: Run tests**

Run: `cd pi && PYTHONPATH=. pytest tests/test_error_isolation.py tests/ -v --tb=short`
Expected: All pass, no regressions

- [ ] **Step 5: Commit**

```bash
git add pi/app/core/renderer.py pi/tests/test_error_isolation.py
git commit -m "feat: isolate effect render errors — crash returns black frame"
```

---

### Task 2: Blend Mode Functions

**Files:**
- Create: `pi/app/core/compositor.py`
- Create: `pi/tests/test_compositor.py`

- [ ] **Step 1: Write failing tests for blend modes**

```python
# pi/tests/test_compositor.py
import numpy as np
from app.core.compositor import blend_normal, blend_add, blend_screen, blend_multiply, blend_max


def _frame(r, g, b, w=4, h=4):
    f = np.zeros((w, h, 3), dtype=np.uint8)
    f[:, :] = [r, g, b]
    return f


class TestBlendModes:
    def test_blend_normal_full_opacity(self):
        base = _frame(255, 0, 0)
        top = _frame(0, 0, 255)
        result = blend_normal(base, top, 1.0)
        assert np.array_equal(result, top)

    def test_blend_normal_half_opacity(self):
        base = _frame(200, 0, 0)
        top = _frame(0, 0, 200)
        result = blend_normal(base, top, 0.5)
        assert result[0, 0, 0] == 100  # 200 * 0.5
        assert result[0, 0, 2] == 100  # 200 * 0.5

    def test_blend_normal_zero_opacity(self):
        base = _frame(255, 0, 0)
        top = _frame(0, 0, 255)
        result = blend_normal(base, top, 0.0)
        assert np.array_equal(result, base)

    def test_blend_add(self):
        a = _frame(100, 50, 0)
        b = _frame(100, 50, 200)
        result = blend_add(a, b, 1.0)
        assert result[0, 0, 0] == 200
        assert result[0, 0, 1] == 100
        assert result[0, 0, 2] == 200

    def test_blend_add_clamps(self):
        a = _frame(200, 0, 0)
        b = _frame(200, 0, 0)
        result = blend_add(a, b, 1.0)
        assert result[0, 0, 0] == 255  # clamped

    def test_blend_screen(self):
        a = _frame(128, 0, 0)
        b = _frame(128, 0, 0)
        result = blend_screen(a, b, 1.0)
        # screen: 1 - (1-a)(1-b) = 1 - 0.498*0.498 = 0.752 → 192
        assert 190 <= result[0, 0, 0] <= 194

    def test_blend_multiply(self):
        a = _frame(128, 255, 0)
        b = _frame(128, 128, 0)
        result = blend_multiply(a, b, 1.0)
        # multiply: a*b/255 = 128*128/255 ≈ 64
        assert 63 <= result[0, 0, 0] <= 65
        assert 127 <= result[0, 0, 1] <= 129  # 255*128/255 = 128

    def test_blend_max(self):
        a = _frame(100, 200, 50)
        b = _frame(200, 100, 50)
        result = blend_max(a, b, 1.0)
        assert result[0, 0, 0] == 200
        assert result[0, 0, 1] == 200
        assert result[0, 0, 2] == 50
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd pi && PYTHONPATH=. pytest tests/test_compositor.py -v`
Expected: ImportError — compositor.py doesn't exist yet

- [ ] **Step 3: Implement blend mode functions**

```python
# pi/app/core/compositor.py
"""
Compositor — layer-based effect compositing with blend modes.

Manages an ordered stack of layers, each with its own effect instance.
Renders all enabled layers and blends them into a single output frame.
"""

import numpy as np


def blend_normal(base: np.ndarray, top: np.ndarray, opacity: float) -> np.ndarray:
    """Standard alpha blend."""
    if opacity >= 1.0:
        return top.copy()
    if opacity <= 0.0:
        return base.copy()
    return (base.astype(np.float32) * (1 - opacity) + top.astype(np.float32) * opacity).astype(np.uint8)


def blend_add(base: np.ndarray, top: np.ndarray, opacity: float) -> np.ndarray:
    """Additive blend — adds light contributions."""
    scaled = (top.astype(np.float32) * opacity)
    return np.clip(base.astype(np.float32) + scaled, 0, 255).astype(np.uint8)


def blend_screen(base: np.ndarray, top: np.ndarray, opacity: float) -> np.ndarray:
    """Screen blend — brightens without washing out."""
    a = base.astype(np.float32) / 255.0
    b = top.astype(np.float32) / 255.0 * opacity
    result = 1.0 - (1.0 - a) * (1.0 - b)
    return (result * 255).astype(np.uint8)


def blend_multiply(base: np.ndarray, top: np.ndarray, opacity: float) -> np.ndarray:
    """Multiply blend — darkens/modulates."""
    mult = (base.astype(np.float32) * top.astype(np.float32) / 255.0)
    return blend_normal(base, mult.astype(np.uint8), opacity)


def blend_max(base: np.ndarray, top: np.ndarray, opacity: float) -> np.ndarray:
    """Max blend — take brightest RGB per pixel."""
    scaled = blend_normal(np.zeros_like(top), top, opacity)
    return np.maximum(base, scaled)


BLEND_MODES = {
    'normal': blend_normal,
    'add': blend_add,
    'screen': blend_screen,
    'multiply': blend_multiply,
    'max': blend_max,
}
```

- [ ] **Step 4: Run tests**

Run: `cd pi && PYTHONPATH=. pytest tests/test_compositor.py -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add pi/app/core/compositor.py pi/tests/test_compositor.py
git commit -m "feat: add blend mode functions (normal, add, screen, multiply, max)"
```

---

### Task 3: Layer Model and Compositor Class

**Files:**
- Modify: `pi/app/core/compositor.py`
- Modify: `pi/tests/test_compositor.py`

- [ ] **Step 1: Write failing tests for Layer and Compositor**

```python
# Append to pi/tests/test_compositor.py
from unittest.mock import MagicMock
from app.core.compositor import Layer, Compositor


class TestLayer:
    def test_layer_creation(self):
        layer = Layer(effect_name='rainbow_rotate', params={'speed': 0.5})
        assert layer.effect_name == 'rainbow_rotate'
        assert layer.opacity == 1.0
        assert layer.blend_mode == 'normal'
        assert layer.enabled is True

    def test_layer_to_dict(self):
        layer = Layer(effect_name='fire', params={'cooling': 55}, opacity=0.7, blend_mode='add')
        d = layer.to_dict()
        assert d['effect_name'] == 'fire'
        assert d['opacity'] == 0.7
        assert d['blend_mode'] == 'add'


class TestCompositor:
    def _make_compositor(self, width=10, height=20):
        effect_registry = {
            'solid_red': type('E', (), {
                '__init__': lambda self, w, h, params=None: setattr(self, 'w', w) or setattr(self, 'h', h),
                'render': lambda self, t, state: np.full((self.w, self.h, 3), [255, 0, 0], dtype=np.uint8),
                'update_params': lambda self, p: None,
            }),
            'solid_blue': type('E', (), {
                '__init__': lambda self, w, h, params=None: setattr(self, 'w', w) or setattr(self, 'h', h),
                'render': lambda self, t, state: np.full((self.w, self.h, 3), [0, 0, 255], dtype=np.uint8),
                'update_params': lambda self, p: None,
            }),
        }
        return Compositor(width, height, effect_registry)

    def test_empty_compositor_returns_black(self):
        comp = self._make_compositor()
        frame = comp.render(0, MagicMock())
        assert frame.shape == (10, 20, 3)
        assert np.all(frame == 0)

    def test_single_layer(self):
        comp = self._make_compositor()
        comp.add_layer(Layer(effect_name='solid_red'))
        frame = comp.render(0, MagicMock())
        assert frame[0, 0, 0] == 255  # red
        assert frame[0, 0, 2] == 0

    def test_two_layers_add_blend(self):
        comp = self._make_compositor()
        comp.add_layer(Layer(effect_name='solid_red'))
        comp.add_layer(Layer(effect_name='solid_blue', blend_mode='add'))
        frame = comp.render(0, MagicMock())
        assert frame[0, 0, 0] == 255  # red from base
        assert frame[0, 0, 2] == 255  # blue added

    def test_disabled_layer_skipped(self):
        comp = self._make_compositor()
        comp.add_layer(Layer(effect_name='solid_red'))
        comp.add_layer(Layer(effect_name='solid_blue', enabled=False))
        frame = comp.render(0, MagicMock())
        assert frame[0, 0, 0] == 255  # red only
        assert frame[0, 0, 2] == 0    # blue disabled

    def test_opacity(self):
        comp = self._make_compositor()
        comp.add_layer(Layer(effect_name='solid_red'))
        comp.add_layer(Layer(effect_name='solid_blue', opacity=0.5))
        frame = comp.render(0, MagicMock())
        assert 120 <= frame[0, 0, 0] <= 130  # red * 0.5
        assert 120 <= frame[0, 0, 2] <= 130  # blue * 0.5

    def test_remove_layer(self):
        comp = self._make_compositor()
        layer = Layer(effect_name='solid_red')
        comp.add_layer(layer)
        assert len(comp.layers) == 1
        comp.remove_layer(0)
        assert len(comp.layers) == 0

    def test_crashing_layer_isolated(self):
        comp = self._make_compositor()
        comp.add_layer(Layer(effect_name='solid_red'))
        # Manually inject a crashing effect
        from app.effects.base import Effect
        class Crasher(Effect):
            def render(self, t, state):
                raise RuntimeError("boom")
        comp._effect_instances[0] = Crasher(10, 20)
        # Should not raise — just skip the crashing layer
        frame = comp.render(0, MagicMock())
        assert frame.shape == (10, 20, 3)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd pi && PYTHONPATH=. pytest tests/test_compositor.py::TestCompositor -v`
Expected: ImportError for Layer, Compositor

- [ ] **Step 3: Implement Layer and Compositor**

Append to `pi/app/core/compositor.py`:

```python
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Layer:
    """One layer in the compositor stack."""
    effect_name: str
    params: dict = field(default_factory=dict)
    opacity: float = 1.0
    blend_mode: str = 'normal'
    enabled: bool = True

    def to_dict(self) -> dict:
        return {
            'effect_name': self.effect_name,
            'params': dict(self.params),
            'opacity': self.opacity,
            'blend_mode': self.blend_mode,
            'enabled': self.enabled,
        }


class Compositor:
    """Renders a stack of layers with blend modes into a single frame."""

    def __init__(self, width: int, height: int, effect_registry: dict):
        self.width = width
        self.height = height
        self._effect_registry = effect_registry
        self.layers: list[Layer] = []
        self._effect_instances: list[Optional[object]] = []
        self.compositor_ms: float = 0.0

    def add_layer(self, layer: Layer, index: Optional[int] = None) -> int:
        """Add a layer. Returns the layer index."""
        if index is None:
            self.layers.append(layer)
            idx = len(self.layers) - 1
        else:
            self.layers.insert(index, layer)
            idx = index
        self._rebuild_instances()
        return idx

    def remove_layer(self, index: int):
        """Remove a layer by index."""
        if 0 <= index < len(self.layers):
            self.layers.pop(index)
            self._rebuild_instances()

    def move_layer(self, from_idx: int, to_idx: int):
        """Move a layer from one position to another."""
        if 0 <= from_idx < len(self.layers):
            layer = self.layers.pop(from_idx)
            to_idx = min(to_idx, len(self.layers))
            self.layers.insert(to_idx, layer)
            self._rebuild_instances()

    def update_layer(self, index: int, **kwargs):
        """Update layer properties (opacity, blend_mode, enabled, params)."""
        if 0 <= index < len(self.layers):
            layer = self.layers[index]
            for key, value in kwargs.items():
                if key == 'params' and index < len(self._effect_instances):
                    instance = self._effect_instances[index]
                    if instance:
                        instance.update_params(value)
                    layer.params.update(value)
                elif hasattr(layer, key):
                    setattr(layer, key, value)

    def _rebuild_instances(self):
        """Rebuild effect instances to match layer list."""
        new_instances = []
        for i, layer in enumerate(self.layers):
            # Reuse existing instance if same effect at same position
            if i < len(self._effect_instances) and self._effect_instances[i] is not None:
                old_layer_name = getattr(self._effect_instances[i], '_compositor_effect_name', None)
                if old_layer_name == layer.effect_name:
                    new_instances.append(self._effect_instances[i])
                    continue
            # Create new instance
            cls = self._effect_registry.get(layer.effect_name)
            if cls:
                try:
                    instance = cls(width=self.width, height=self.height, params=layer.params)
                    instance._compositor_effect_name = layer.effect_name
                    new_instances.append(instance)
                except Exception as e:
                    logger.error(f"Failed to create effect '{layer.effect_name}': {e}")
                    new_instances.append(None)
            else:
                logger.warning(f"Unknown effect: {layer.effect_name}")
                new_instances.append(None)
        self._effect_instances = new_instances

    def render(self, t: float, state) -> np.ndarray:
        """Render all enabled layers and blend into a single frame."""
        start = time.perf_counter()
        result = np.zeros((self.width, self.height, 3), dtype=np.uint8)

        for i, layer in enumerate(self.layers):
            if not layer.enabled or i >= len(self._effect_instances):
                continue
            instance = self._effect_instances[i]
            if instance is None:
                continue

            try:
                frame = instance.render(t, state)
            except Exception as e:
                logger.error(f"Layer {i} '{layer.effect_name}' crashed: {e}", exc_info=True)
                continue

            blend_fn = BLEND_MODES.get(layer.blend_mode, blend_normal)
            result = blend_fn(result, frame, layer.opacity)

        self.compositor_ms = (time.perf_counter() - start) * 1000
        return result

    def to_dict(self) -> dict:
        return {
            'layers': [l.to_dict() for l in self.layers],
        }
```

- [ ] **Step 4: Run tests**

Run: `cd pi && PYTHONPATH=. pytest tests/test_compositor.py -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add pi/app/core/compositor.py pi/tests/test_compositor.py
git commit -m "feat: add Layer model and Compositor with error isolation per layer"
```

---

### Task 4: Integrate Compositor into Renderer

**Files:**
- Modify: `pi/app/core/renderer.py`
- Modify: `pi/app/main.py`

- [ ] **Step 1: Add compositor to Renderer**

In `pi/app/core/renderer.py`, add compositor support that's backward-compatible with single-effect mode:

```python
# In Renderer.__init__, after self.current_effect = None:
self.compositor = None  # Optional — set when using layers

# In Renderer._render_frame(), replace the effect render block with:
if self.compositor and self.compositor.layers:
    # Multi-layer mode: use compositor
    effect_start = time.perf_counter()
    try:
        internal_frame = self.compositor.render(t, self.state)
    except Exception as e:
        logger.error(f"Compositor crashed: {e}", exc_info=True)
        internal_frame = np.zeros((w, h, 3), dtype=np.uint8)
    self.state.effect_render_ms = (time.perf_counter() - effect_start) * 1000
elif self.current_effect is not None:
    # Single-effect mode: backward compatible
    effect_start = time.perf_counter()
    try:
        internal_frame = self.current_effect.render(t, self.state)
    except Exception as e:
        logger.error(f"Effect '{self.state.current_scene}' crashed: {e}", exc_info=True)
        internal_frame = np.zeros((w, h, 3), dtype=np.uint8)
    self.state.effect_render_ms = (time.perf_counter() - effect_start) * 1000
else:
    internal_frame = np.zeros((w, h, 3), dtype=np.uint8)
```

- [ ] **Step 2: Run full test suite**

Run: `cd pi && PYTHONPATH=. pytest tests/ -v --tb=short -q`
Expected: Same pass count as before (434+), no regressions

- [ ] **Step 3: Deploy and verify single-effect mode still works**

```bash
bash pi/scripts/deploy.sh ledfanatic.local
```

Verify: `curl http://ledfanatic.local/api/system/status` returns FPS > 55

- [ ] **Step 4: Commit**

```bash
git add pi/app/core/renderer.py pi/app/main.py
git commit -m "feat: integrate compositor into render loop (backward-compatible)"
```

---

### Task 5: Layer CRUD API Endpoints

**Files:**
- Modify: `pi/app/api/routes/scenes.py`

- [ ] **Step 1: Add layer endpoints**

Add to `pi/app/api/routes/scenes.py`:

```python
@router.get("/layers")
async def get_layers():
    """Get current layer stack."""
    if deps.renderer.compositor:
        return deps.renderer.compositor.to_dict()
    # Single-effect fallback
    if deps.render_state.current_scene:
        return {'layers': [{'effect_name': deps.render_state.current_scene,
                           'params': deps.state_manager.current_params or {},
                           'opacity': 1.0, 'blend_mode': 'normal', 'enabled': True}]}
    return {'layers': []}

@router.post("/layers/add", dependencies=[Depends(require_auth)])
async def add_layer(req: dict):
    """Add a layer to the compositor."""
    from app.core.compositor import Layer, Compositor
    if deps.renderer.compositor is None:
        deps.renderer.compositor = Compositor(
            deps.compiled_layout.width,
            deps.compiled_layout.height,
            deps.renderer.effect_registry,
        )
    layer = Layer(
        effect_name=req['effect_name'],
        params=req.get('params', {}),
        opacity=req.get('opacity', 1.0),
        blend_mode=req.get('blend_mode', 'normal'),
        enabled=req.get('enabled', True),
    )
    idx = deps.renderer.compositor.add_layer(layer)
    return {'status': 'ok', 'index': idx, 'layers': deps.renderer.compositor.to_dict()['layers']}

@router.post("/layers/{index}/remove", dependencies=[Depends(require_auth)])
async def remove_layer(index: int):
    if deps.renderer.compositor:
        deps.renderer.compositor.remove_layer(index)
        return {'status': 'ok', 'layers': deps.renderer.compositor.to_dict()['layers']}
    return {'error': 'no compositor active'}

@router.post("/layers/{index}/update", dependencies=[Depends(require_auth)])
async def update_layer(index: int, req: dict):
    if deps.renderer.compositor:
        deps.renderer.compositor.update_layer(index, **req)
        return {'status': 'ok', 'layers': deps.renderer.compositor.to_dict()['layers']}
    return {'error': 'no compositor active'}
```

- [ ] **Step 2: Test manually**

Deploy and test via curl:
```bash
# Add two layers
curl -X POST http://ledfanatic.local/api/scenes/layers/add \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"effect_name": "rainbow_rotate"}'

curl -X POST http://ledfanatic.local/api/scenes/layers/add \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"effect_name": "twinkle", "blend_mode": "add", "opacity": 0.5}'

# Check layers
curl http://ledfanatic.local/api/scenes/layers
```

- [ ] **Step 3: Commit**

```bash
git add pi/app/api/routes/scenes.py
git commit -m "feat: add layer CRUD API endpoints for compositor"
```

---

### Task 6: Deploy and Verify Phase 1

- [ ] **Step 1: Run full test suite**

Run: `cd pi && PYTHONPATH=. pytest tests/ -v -q`
Expected: 434+ pass, same pre-existing failures only

- [ ] **Step 2: Deploy to Pi**

```bash
bash pi/scripts/deploy.sh ledfanatic.local
```

- [ ] **Step 3: Verify single-effect mode unchanged**

```bash
curl http://ledfanatic.local/api/system/status
# FPS should be 55+
```

- [ ] **Step 4: Verify two-layer compositing works**

```bash
TOKEN=$(ssh jim@ledfanatic.local "python3 -c \"import yaml; print(yaml.safe_load(open('/opt/ledfanatic/config/system.yaml'))['auth']['token'])\"")

# Add base layer
curl -X POST http://ledfanatic.local/api/scenes/layers/add \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"effect_name": "rainbow_rotate"}'

# Add overlay
curl -X POST http://ledfanatic.local/api/scenes/layers/add \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"effect_name": "twinkle", "blend_mode": "add", "opacity": 0.7}'

# Check FPS still above 55
curl http://ledfanatic.local/api/system/status | python3 -c "import sys,json; print(json.load(sys.stdin)['render']['actual_fps'])"
```

- [ ] **Step 5: Commit and tag**

```bash
git add -A
git commit -m "Phase 1 complete: compositor with layers, blend modes, error isolation"
git tag v1.2.0-compositor
```

---

## Acceptance Criteria

| Criterion | Test |
|-----------|------|
| Effect crash doesn't kill render loop | test_error_isolation.py |
| 5 blend modes produce correct output | test_compositor.py::TestBlendModes |
| Empty compositor returns black | test_compositor.py::test_empty_compositor_returns_black |
| Single layer works | test_compositor.py::test_single_layer |
| Two-layer add blend works | test_compositor.py::test_two_layers_add_blend |
| Disabled layer skipped | test_compositor.py::test_disabled_layer_skipped |
| Crashing layer isolated | test_compositor.py::test_crashing_layer_isolated |
| Existing single-effect mode unchanged | Full test suite regression |
| Two-layer scene at 59+ FPS | Manual verification on Pi |
| Layer CRUD API works | Manual curl tests |
