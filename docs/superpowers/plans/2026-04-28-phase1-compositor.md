# Phase 1: Error Isolation + Compositor Foundation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add effect error isolation and a layer-based compositor so multiple effects can be blended into a single output frame, with persistent scene storage and layout hot-swap support.

**Architecture:** The compositor sits between effects and the renderer. It manages a list of Layer objects, each with its own effect instance. On each frame, it renders all enabled layers, blends them using the specified blend mode, and returns a single (width, height, 3) uint8 frame. The renderer calls the compositor instead of a single effect.

**Tech Stack:** Python 3.13 / NumPy / FastAPI / Pydantic

**Codex Review:** Rev 6 addressed 18 total findings across 6 rounds.

Round 1 (6 findings):
- R1-H1: Added state.json schema_version v2 migration + persistent layer storage
- R1-H2: Fixed blend mode opacity — canonical `mode(base, top)` then `alpha_blend(base, result, opacity)`
- R1-H3: Error isolation test exercises `_render_frame()` with mocked transport
- R1-M4: Added Pydantic request models, reorder endpoint
- R1-M5: Added `compositor.apply_layout()` for layout hot-swap
- R1-M6: Wired `compositor_ms` into RenderState and system API

Round 2 (3 findings):
- R2-H1: Compositor uses shared `_create_effect()` honoring RENDER_SCALE, YAML param merging, animation_switcher wiring
- R2-M2: Crash isolation test has healthy + crashing + healthy layers, proves healthy layers survive
- R2-M3: Pydantic models use `Field(ge/le)` for opacity, `Literal` for blend_mode, `Field(ge=0)` for indices

Round 3 (2 findings):
- R3-H1: Boot-time restore path hydrates compositor from persisted `current_layers` in main.py
- R3-M2: All Compositor construction sites (API route, from_dict, boot restore) pass `effects_config`

Round 4 (3 findings):
- R4-H1: Explicit mode exclusion — activate_scene clears compositor; removing all layers clears compositor
- R4-H2: First /layers/add bootstraps layer 0 from current scene, clears current_effect
- R4-M3: Boot restore always uses compositor for any non-empty current_layers (preserves opacity/blend)

Round 5 (2 findings):
- R5-H1: activate_scene clears compositor only AFTER successful activation (no state loss on failure)
- R5-M2: Boot restore REPLACES existing startup block (not appended after); clears current_effect in compositor path

Round 6 (2 findings):
- R6-H1: Entering compositor clears current_scene/current_params; leaving clears all stale state
- R6-H2: Mode-switch centralized in Renderer.activate_scene() — all activation paths (scenes, media, diagnostics, startup) exit compositor automatically

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `pi/app/core/compositor.py` | Layer model, blend modes, compositor class, layout rebind |
| Create | `pi/tests/test_compositor.py` | Unit tests for compositor, blend modes (incl. opacity for all modes) |
| Create | `pi/tests/test_error_isolation.py` | Integration test: crashing effect in `_render_frame()` |
| Modify | `pi/app/core/renderer.py:320-340` | Error isolation, compositor integration, compositor_ms |
| Modify | `pi/app/core/renderer.py:RenderState` | Add compositor_ms field + to_dict() |
| Modify | `pi/app/core/state.py` | schema_version v2, persist layers, v1→v2 migration |
| Modify | `pi/app/api/routes/scenes.py` | Layer CRUD + reorder endpoints with Pydantic models |

---

### Task 1: Blend Mode Functions (Correct Opacity Semantics)

**Files:**
- Create: `pi/app/core/compositor.py`
- Create: `pi/tests/test_compositor.py`

**Codex H2 fix:** All blend modes follow the canonical rule:
`final = alpha_blend(base, mode_fn(base, top), opacity)`
This means opacity controls how much of the blended result shows, consistently across all modes.

- [ ] **Step 1: Write failing tests for blend modes (including opacity for every mode)**

```python
# pi/tests/test_compositor.py
import numpy as np
from app.core.compositor import blend, BLEND_MODES


def _frame(r, g, b, w=4, h=4):
    f = np.zeros((w, h, 3), dtype=np.uint8)
    f[:, :] = [r, g, b]
    return f


class TestBlendModes:
    def test_normal_full_opacity(self):
        result = blend(_frame(255, 0, 0), _frame(0, 0, 255), 1.0, 'normal')
        assert np.array_equal(result[0, 0], [0, 0, 255])

    def test_normal_half_opacity(self):
        result = blend(_frame(200, 0, 0), _frame(0, 0, 200), 0.5, 'normal')
        assert result[0, 0, 0] == 100
        assert result[0, 0, 2] == 100

    def test_normal_zero_opacity(self):
        base = _frame(255, 0, 0)
        result = blend(base, _frame(0, 0, 255), 0.0, 'normal')
        assert np.array_equal(result, base)

    def test_add_full_opacity(self):
        result = blend(_frame(100, 50, 0), _frame(100, 50, 200), 1.0, 'add')
        assert result[0, 0, 0] == 200
        assert result[0, 0, 2] == 200

    def test_add_clamps(self):
        result = blend(_frame(200, 0, 0), _frame(200, 0, 0), 1.0, 'add')
        assert result[0, 0, 0] == 255

    def test_add_half_opacity(self):
        # add(100, 100) = 200, then alpha_blend(100, 200, 0.5) = 150
        result = blend(_frame(100, 0, 0), _frame(100, 0, 0), 0.5, 'add')
        assert 148 <= result[0, 0, 0] <= 152

    def test_screen_full_opacity(self):
        result = blend(_frame(128, 0, 0), _frame(128, 0, 0), 1.0, 'screen')
        # screen: 1 - (1-0.502)(1-0.502) = 0.752 → 192
        assert 190 <= result[0, 0, 0] <= 194

    def test_screen_half_opacity(self):
        # screen(128,128) ≈ 192, alpha_blend(128, 192, 0.5) ≈ 160
        result = blend(_frame(128, 0, 0), _frame(128, 0, 0), 0.5, 'screen')
        assert 158 <= result[0, 0, 0] <= 162

    def test_multiply_full_opacity(self):
        result = blend(_frame(128, 255, 0), _frame(128, 128, 0), 1.0, 'multiply')
        assert 63 <= result[0, 0, 0] <= 65  # 128*128/255 ≈ 64

    def test_multiply_half_opacity(self):
        # multiply(128,128) ≈ 64, alpha_blend(128, 64, 0.5) ≈ 96
        result = blend(_frame(128, 0, 0), _frame(128, 0, 0), 0.5, 'multiply')
        assert 94 <= result[0, 0, 0] <= 98

    def test_max_full_opacity(self):
        result = blend(_frame(100, 200, 50), _frame(200, 100, 50), 1.0, 'max')
        assert result[0, 0, 0] == 200
        assert result[0, 0, 1] == 200

    def test_max_half_opacity(self):
        # max(100, 200) = 200, alpha_blend(100, 200, 0.5) = 150
        result = blend(_frame(100, 0, 0), _frame(200, 0, 0), 0.5, 'max')
        assert 148 <= result[0, 0, 0] <= 152

    def test_unknown_mode_falls_back_to_normal(self):
        result = blend(_frame(255, 0, 0), _frame(0, 0, 255), 1.0, 'bogus')
        assert np.array_equal(result[0, 0], [0, 0, 255])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd pi && PYTHONPATH=. pytest tests/test_compositor.py -v`
Expected: ImportError — compositor.py doesn't exist yet

- [ ] **Step 3: Implement blend mode functions with canonical opacity rule**

```python
# pi/app/core/compositor.py
"""
Compositor — layer-based effect compositing with blend modes.

All blend modes follow the canonical opacity rule:
  result = alpha_blend(base, mode_fn(base, top), opacity)
This ensures consistent opacity behavior across all modes.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


def _alpha_blend(base: np.ndarray, result: np.ndarray, opacity: float) -> np.ndarray:
    """Apply opacity: mix base and result by opacity factor."""
    if opacity >= 1.0:
        return result
    if opacity <= 0.0:
        return base.copy()
    return (base.astype(np.float32) * (1 - opacity) + result.astype(np.float32) * opacity).astype(np.uint8)


def _mode_normal(base: np.ndarray, top: np.ndarray) -> np.ndarray:
    return top


def _mode_add(base: np.ndarray, top: np.ndarray) -> np.ndarray:
    return np.clip(base.astype(np.uint16) + top.astype(np.uint16), 0, 255).astype(np.uint8)


def _mode_screen(base: np.ndarray, top: np.ndarray) -> np.ndarray:
    a = base.astype(np.float32) / 255.0
    b = top.astype(np.float32) / 255.0
    return ((1.0 - (1.0 - a) * (1.0 - b)) * 255).astype(np.uint8)


def _mode_multiply(base: np.ndarray, top: np.ndarray) -> np.ndarray:
    return (base.astype(np.float32) * top.astype(np.float32) / 255.0).astype(np.uint8)


def _mode_max(base: np.ndarray, top: np.ndarray) -> np.ndarray:
    return np.maximum(base, top)


BLEND_MODES = {
    'normal': _mode_normal,
    'add': _mode_add,
    'screen': _mode_screen,
    'multiply': _mode_multiply,
    'max': _mode_max,
}


def blend(base: np.ndarray, top: np.ndarray, opacity: float, mode: str = 'normal') -> np.ndarray:
    """Apply blend mode then opacity. Canonical rule for all modes."""
    mode_fn = BLEND_MODES.get(mode, _mode_normal)
    blended = mode_fn(base, top)
    return _alpha_blend(base, blended, opacity)
```

- [ ] **Step 4: Run tests**

Run: `cd pi && PYTHONPATH=. pytest tests/test_compositor.py -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add pi/app/core/compositor.py pi/tests/test_compositor.py
git commit -m "feat: add blend mode functions with canonical opacity semantics"
```

---

### Task 2: Layer Model and Compositor Class

**Files:**
- Modify: `pi/app/core/compositor.py`
- Modify: `pi/tests/test_compositor.py`

- [ ] **Step 1: Write failing tests for Layer and Compositor**

```python
# Append to pi/tests/test_compositor.py
from unittest.mock import MagicMock
from app.core.compositor import Layer, Compositor


def _make_effect_cls(r, g, b):
    """Create a simple effect class that fills with a solid color."""
    class SolidEffect:
        def __init__(self, width, height, params=None):
            self.width = width
            self.height = height
        def render(self, t, state):
            return np.full((self.width, self.height, 3), [r, g, b], dtype=np.uint8)
        def update_params(self, p):
            pass
    return SolidEffect


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
        registry = {
            'solid_red': _make_effect_cls(255, 0, 0),
            'solid_blue': _make_effect_cls(0, 0, 255),
        }
        return Compositor(width, height, registry)

    def test_empty_returns_black(self):
        comp = self._make_compositor()
        frame = comp.render(0, MagicMock())
        assert frame.shape == (10, 20, 3)
        assert np.all(frame == 0)

    def test_single_layer(self):
        comp = self._make_compositor()
        comp.add_layer(Layer(effect_name='solid_red'))
        frame = comp.render(0, MagicMock())
        assert frame[0, 0, 0] == 255
        assert frame[0, 0, 2] == 0

    def test_two_layers_add(self):
        comp = self._make_compositor()
        comp.add_layer(Layer(effect_name='solid_red'))
        comp.add_layer(Layer(effect_name='solid_blue', blend_mode='add'))
        frame = comp.render(0, MagicMock())
        assert frame[0, 0, 0] == 255
        assert frame[0, 0, 2] == 255

    def test_disabled_layer_skipped(self):
        comp = self._make_compositor()
        comp.add_layer(Layer(effect_name='solid_red'))
        comp.add_layer(Layer(effect_name='solid_blue', enabled=False))
        frame = comp.render(0, MagicMock())
        assert frame[0, 0, 0] == 255
        assert frame[0, 0, 2] == 0

    def test_opacity(self):
        comp = self._make_compositor()
        comp.add_layer(Layer(effect_name='solid_red'))
        comp.add_layer(Layer(effect_name='solid_blue', opacity=0.5))
        frame = comp.render(0, MagicMock())
        # normal blend at 0.5: red*0.5 + blue*0.5
        assert 125 <= frame[0, 0, 0] <= 130
        assert 125 <= frame[0, 0, 2] <= 130

    def test_remove_layer(self):
        comp = self._make_compositor()
        comp.add_layer(Layer(effect_name='solid_red'))
        assert len(comp.layers) == 1
        comp.remove_layer(0)
        assert len(comp.layers) == 0

    def test_reorder_layer(self):
        comp = self._make_compositor()
        comp.add_layer(Layer(effect_name='solid_red'))
        comp.add_layer(Layer(effect_name='solid_blue'))
        comp.move_layer(1, 0)
        assert comp.layers[0].effect_name == 'solid_blue'
        assert comp.layers[1].effect_name == 'solid_red'

    def test_crashing_layer_isolated_other_layers_survive(self):
        """A crashing layer must not prevent healthy layers from rendering."""
        comp = self._make_compositor()
        comp.add_layer(Layer(effect_name='solid_red'))          # layer 0: healthy
        comp.add_layer(Layer(effect_name='solid_blue'))         # layer 1: will crash
        comp.add_layer(Layer(effect_name='solid_red', blend_mode='add'))  # layer 2: healthy
        # Inject crasher into layer 1 only
        class Crasher:
            def __init__(self, *a, **kw): pass
            def render(self, t, state): raise RuntimeError("boom")
            def update_params(self, p): pass
        comp._effect_instances[1] = Crasher()
        frame = comp.render(0, MagicMock())
        assert frame.shape == (10, 20, 3)
        # Layer 0 (red) + layer 2 (red, add) should produce red=255
        # Layer 1 crash is skipped, blue absent
        assert frame[0, 0, 0] == 255  # red from layers 0+2
        assert frame[0, 0, 2] == 0    # no blue — crasher skipped

    def test_compositor_ms_tracked(self):
        comp = self._make_compositor()
        comp.add_layer(Layer(effect_name='solid_red'))
        comp.render(0, MagicMock())
        assert comp.compositor_ms >= 0

    def test_apply_layout_recreates_instances(self):
        comp = self._make_compositor(width=10, height=20)
        comp.add_layer(Layer(effect_name='solid_red'))
        frame1 = comp.render(0, MagicMock())
        assert frame1.shape == (10, 20, 3)
        # Change layout
        comp.apply_layout(5, 40)
        frame2 = comp.render(0, MagicMock())
        assert frame2.shape == (5, 40, 3)

    def test_to_dict(self):
        comp = self._make_compositor()
        comp.add_layer(Layer(effect_name='solid_red', opacity=0.8))
        comp.add_layer(Layer(effect_name='solid_blue', blend_mode='add'))
        d = comp.to_dict()
        assert len(d['layers']) == 2
        assert d['layers'][0]['opacity'] == 0.8
        assert d['layers'][1]['blend_mode'] == 'add'
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd pi && PYTHONPATH=. pytest tests/test_compositor.py::TestCompositor -v`
Expected: ImportError for Layer, Compositor

- [ ] **Step 3: Implement Layer and Compositor**

Append to `pi/app/core/compositor.py`:

```python
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

    def __init__(self, width: int, height: int, effect_registry: dict,
                 effects_config: Optional[dict] = None):
        self.width = width
        self.height = height
        self._effect_registry = effect_registry
        self._effects_config = effects_config or {}
        self.layers: list[Layer] = []
        self._effect_instances: list[Optional[object]] = []
        self.compositor_ms: float = 0.0

    def add_layer(self, layer: Layer, index: Optional[int] = None) -> int:
        if index is None:
            self.layers.append(layer)
            idx = len(self.layers) - 1
        else:
            self.layers.insert(index, layer)
            idx = index
        self._rebuild_instances()
        return idx

    def remove_layer(self, index: int):
        if 0 <= index < len(self.layers):
            self.layers.pop(index)
            self._rebuild_instances()

    def move_layer(self, from_idx: int, to_idx: int):
        if 0 <= from_idx < len(self.layers):
            layer = self.layers.pop(from_idx)
            to_idx = min(to_idx, len(self.layers))
            self.layers.insert(to_idx, layer)
            self._rebuild_instances()

    def update_layer(self, index: int, **kwargs):
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

    def apply_layout(self, width: int, height: int):
        """Rebuild all effect instances at new dimensions (layout hot-swap)."""
        self.width = width
        self.height = height
        self._rebuild_instances()

    def _create_effect(self, effect_name: str, params: dict) -> Optional[object]:
        """Create an effect instance honoring RENDER_SCALE, YAML param merge,
        and animation_switcher's _effect_registry injection — same contract as
        renderer._set_scene() so layered mode preserves all existing behavior."""
        cls = self._effect_registry.get(effect_name)
        if cls is None:
            logger.warning(f"Unknown effect: {effect_name}")
            return None
        try:
            # Merge: YAML config defaults < caller params (mirrors renderer._set_scene)
            merged = dict(params)
            if self._effects_config:
                for section in ('effects', 'audio_effects'):
                    section_data = self._effects_config.get(section, {})
                    if effect_name in section_data:
                        yaml_params = section_data[effect_name].get('params', {})
                        merged = {**yaml_params, **params}
                        break
            # AnimationSwitcher needs effect_registry
            if effect_name == 'animation_switcher':
                merged['_effect_registry'] = self._effect_registry
            # Honor RENDER_SCALE
            width = self.width
            height = self.height
            render_scale = getattr(cls, 'RENDER_SCALE', 1)
            if render_scale > 1:
                width *= render_scale
                height *= render_scale
            instance = cls(width=width, height=height, params=merged)
            instance._compositor_render_scale = render_scale
            return instance
        except Exception as e:
            logger.error(f"Failed to create effect '{effect_name}': {e}")
            return None

    def _rebuild_instances(self):
        self._effect_instances = [
            self._create_effect(layer.effect_name, layer.params)
            for layer in self.layers
        ]

    def render(self, t: float, state) -> np.ndarray:
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
                # Downsample if effect uses RENDER_SCALE > 1
                scale = getattr(instance, '_compositor_render_scale', 1)
                if scale > 1:
                    from PIL import Image
                    img = Image.fromarray(frame.transpose(1, 0, 2))
                    img = img.resize((self.width, self.height), Image.LANCZOS)
                    frame = np.array(img).transpose(1, 0, 2)
            except Exception as e:
                logger.error(f"Layer {i} '{layer.effect_name}' crashed: {e}", exc_info=True)
                continue
            result = blend(result, frame, layer.opacity, layer.blend_mode)

        self.compositor_ms = (time.perf_counter() - start) * 1000
        return result

    def to_dict(self) -> dict:
        return {'layers': [l.to_dict() for l in self.layers]}

    @staticmethod
    def from_dict(data: dict, width: int, height: int, effect_registry: dict,
                  effects_config: Optional[dict] = None) -> 'Compositor':
        comp = Compositor(width, height, effect_registry, effects_config=effects_config)
        for ld in data.get('layers', []):
            comp.add_layer(Layer(
                effect_name=ld['effect_name'],
                params=ld.get('params', {}),
                opacity=ld.get('opacity', 1.0),
                blend_mode=ld.get('blend_mode', 'normal'),
                enabled=ld.get('enabled', True),
            ))
        return comp
```

- [ ] **Step 4: Run tests**

Run: `cd pi && PYTHONPATH=. pytest tests/test_compositor.py -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add pi/app/core/compositor.py pi/tests/test_compositor.py
git commit -m "feat: add Layer model and Compositor with error isolation, layout rebind"
```

---

### Task 3: Error Isolation Integration Test

**Files:**
- Create: `pi/tests/test_error_isolation.py`
- Modify: `pi/app/core/renderer.py`

**Codex H3 fix:** Test exercises the actual `_render_frame()` path, not just calling render() directly.

- [ ] **Step 1: Write integration test for renderer error isolation**

```python
# pi/tests/test_error_isolation.py
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

    # Mock transport to capture sent frames
    transport = MagicMock()
    transport.send_frame = AsyncMock(return_value=True)

    renderer = Renderer(transport, state, brightness, layout)
    renderer.current_effect = CrashingEffect(layout.width, layout.height)
    state.current_scene = "crasher"

    # Run one frame — should NOT raise
    loop = asyncio.new_event_loop()
    loop.run_until_complete(renderer._render_frame())
    loop.close()

    # Should have sent a frame (black fallback)
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

    # First: crashing effect
    renderer.current_effect = CrashingEffect(layout.width, layout.height)
    state.current_scene = "crasher"
    loop = asyncio.new_event_loop()
    loop.run_until_complete(renderer._render_frame())

    # Switch to working effect
    renderer.current_effect = WorkingEffect(layout.width, layout.height)
    state.current_scene = "worker"
    loop.run_until_complete(renderer._render_frame())
    loop.close()

    assert state.frames_rendered == 2
    assert state.frames_sent >= 1
```

- [ ] **Step 2: Add error isolation to renderer._render_frame()**

In `pi/app/core/renderer.py`, wrap the effect.render() call:

```python
      effect_start = time.perf_counter()
      try:
          internal_frame = self.current_effect.render(t, self.state)
      except Exception as e:
          logger.error(f"Effect '{self.state.current_scene}' crashed: {e}", exc_info=True)
          internal_frame = np.zeros((w, h, 3), dtype=np.uint8)
      self.state.effect_render_ms = (time.perf_counter() - effect_start) * 1000
```

- [ ] **Step 3: Run tests**

Run: `cd pi && PYTHONPATH=. pytest tests/test_error_isolation.py -v`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add pi/tests/test_error_isolation.py pi/app/core/renderer.py
git commit -m "feat: isolate effect render errors in _render_frame()"
```

---

### Task 4: Wire compositor_ms into RenderState and API

**Files:**
- Modify: `pi/app/core/renderer.py`

**Codex M6 fix:** compositor_ms exposed via status API for profiler workspace.

- [ ] **Step 1: Add compositor_ms to RenderState**

```python
# In RenderState.__init__, after send_ms:
    self.compositor_ms: float = 0.0

# In RenderState.to_dict(), add:
      'compositor_ms': round(self.compositor_ms, 2),
```

- [ ] **Step 2: Thread compositor_ms from compositor to RenderState in _render_frame()**

```python
# After compositor renders, copy timing:
    if self.compositor and self.compositor.layers:
        ...
        self.state.compositor_ms = self.compositor.compositor_ms
    else:
        self.state.compositor_ms = 0.0
```

- [ ] **Step 3: Run tests**

Run: `cd pi && PYTHONPATH=. pytest tests/ -v -q`
Expected: No regressions

- [ ] **Step 4: Commit**

```bash
git add pi/app/core/renderer.py
git commit -m "feat: expose compositor_ms in RenderState and system API"
```

---

### Task 5: Persistent Layer Storage (State Migration)

**Files:**
- Modify: `pi/app/core/state.py`

**Codex H1 fix:** state.json schema_version v2 stores layer stack. v1→v2 migration preserves single-effect scenes.

- [ ] **Step 1: Add layers property to StateManager**

```python
# In StateManager, add property:
@property
def current_layers(self) -> list[dict]:
    return self._data.get('current_layers', [])

@current_layers.setter
def current_layers(self, layers: list[dict]):
    self._data['current_layers'] = layers
    self.mark_dirty()
```

- [ ] **Step 2: Add v1→v2 migration**

```python
def _migrate(self, data: dict) -> dict:
    version = data.get('schema_version', 0)
    if version < 1:
        # ... existing v0→v1 migration ...
        data['schema_version'] = 1
    if version < 2:
        # v1→v2: convert single scene to layer format
        scene = data.get('current_scene')
        params = data.get('current_params', {})
        if scene:
            data['current_layers'] = [{
                'effect_name': scene,
                'params': params,
                'opacity': 1.0,
                'blend_mode': 'normal',
                'enabled': True,
            }]
        else:
            data['current_layers'] = []
        data['schema_version'] = 2
    return data
```

- [ ] **Step 3: Write test for migration**

```python
# In pi/tests/test_state.py or test_migrations.py
def test_v1_to_v2_migration():
    from app.core.state import StateManager
    sm = StateManager(config_dir=Path("/tmp/test_state"))
    sm._data = {
        'schema_version': 1,
        'current_scene': 'rainbow_rotate',
        'current_params': {'speed': 0.5},
    }
    sm._migrate(sm._data)
    assert sm._data['schema_version'] == 2
    assert len(sm._data['current_layers']) == 1
    assert sm._data['current_layers'][0]['effect_name'] == 'rainbow_rotate'
    assert sm._data['current_layers'][0]['opacity'] == 1.0
```

- [ ] **Step 4: Run tests**

Run: `cd pi && PYTHONPATH=. pytest tests/test_state.py tests/test_migrations.py -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add pi/app/core/state.py pi/tests/
git commit -m "feat: state.json v2 schema with persistent layer storage"
```

---

### Task 6: Layer CRUD API with Pydantic Models

**Files:**
- Modify: `pi/app/api/routes/scenes.py`

**Codex M4 fix:** Typed Pydantic request models, reorder endpoint included.

- [ ] **Step 1: Define Pydantic models**

```python
# At top of pi/app/api/routes/scenes.py (or in schemas.py)
from pydantic import BaseModel, Field
from typing import Optional, Literal

BlendMode = Literal['normal', 'add', 'screen', 'multiply', 'max']

class LayerAddRequest(BaseModel):
    effect_name: str
    params: dict = Field(default_factory=dict)
    opacity: float = Field(default=1.0, ge=0.0, le=1.0)
    blend_mode: BlendMode = 'normal'
    enabled: bool = True

class LayerUpdateRequest(BaseModel):
    opacity: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    blend_mode: Optional[BlendMode] = None
    enabled: Optional[bool] = None
    params: Optional[dict] = None

class LayerReorderRequest(BaseModel):
    from_index: int = Field(ge=0)
    to_index: int = Field(ge=0)
```

- [ ] **Step 2: Add mode exclusion to activate_scene**

**R6-H2 fix:** Instead of patching individual routes, centralize mode-switch
logic inside `Renderer.activate_scene()` itself. This ensures ALL activation
paths (scenes, media, diagnostics, startup) properly exit compositor mode.

In `pi/app/core/renderer.py`, modify `activate_scene()`:
```python
def activate_scene(self, scene_name, params=None, media_manager=None) -> bool:
    """Unified scene activation. Clears compositor on success (R6-H2)."""
    # ... existing activation logic ...
    if success:
        # R4-H1 + R5-H1 + R6-H2: centralized compositor teardown on any
        # successful single-effect activation — no route-level patching needed
        if self.compositor:
            self.compositor = None
        # R6-H1: clear stale single-effect fields are updated by _set_scene
        # so current_scene/current_params always reflect the active state
    return success
```

The route no longer needs mode-exit logic — `renderer.activate_scene()` handles it.
No changes needed in media routes, diagnostic routes, or startup — they all call
`renderer.activate_scene()` which now owns the mode transition.

- [ ] **Step 3: Add layer CRUD + reorder endpoints**

```python
@router.get("/layers")
async def get_layers():
    if deps.renderer.compositor:
        return deps.renderer.compositor.to_dict()
    if deps.render_state.current_scene:
        return {'layers': [{'effect_name': deps.render_state.current_scene,
                           'params': deps.state_manager.current_params or {},
                           'opacity': 1.0, 'blend_mode': 'normal', 'enabled': True}]}
    return {'layers': []}

@router.post("/layers/add", dependencies=[Depends(require_auth)])
async def add_layer(req: LayerAddRequest):
    from app.core.compositor import Layer, Compositor
    if deps.renderer.compositor is None:
        # R4-H2 fix: bootstrap compositor from current scene so we don't drop it
        deps.renderer.compositor = Compositor(
            deps.compiled_layout.width, deps.compiled_layout.height,
            deps.renderer.effect_registry,
            effects_config=deps.renderer.effects_config)
        # Seed layer 0 from the currently active single-effect scene
        if deps.render_state.current_scene:
            base_layer = Layer(
                effect_name=deps.render_state.current_scene,
                params=deps.state_manager.current_params or {},
            )
            deps.renderer.compositor.add_layer(base_layer)
        # R4-H1 + R6-H1 fix: clear ALL single-effect state — compositor owns rendering
        deps.renderer.current_effect = None
        deps.render_state.current_scene = None
        deps.state_manager.current_scene = None
        deps.state_manager.current_params = None
    layer = Layer(**req.model_dump())
    idx = deps.renderer.compositor.add_layer(layer)
    deps.state_manager.current_layers = deps.renderer.compositor.to_dict()['layers']
    return {'status': 'ok', 'index': idx, 'layers': deps.renderer.compositor.to_dict()['layers']}

@router.post("/layers/{index}/remove", dependencies=[Depends(require_auth)])
async def remove_layer(index: int):
    if deps.renderer.compositor:
        deps.renderer.compositor.remove_layer(index)
        layers = deps.renderer.compositor.to_dict()['layers']
        deps.state_manager.current_layers = layers
        # R4-H1 + R6-H1 fix: if all layers removed, clear everything
        if not layers:
            deps.renderer.compositor = None
            deps.state_manager.current_scene = None
            deps.state_manager.current_params = None
        return {'status': 'ok', 'layers': layers}
    return {'error': 'no compositor active'}

@router.post("/layers/{index}/update", dependencies=[Depends(require_auth)])
async def update_layer(index: int, req: LayerUpdateRequest):
    if deps.renderer.compositor:
        updates = {k: v for k, v in req.model_dump().items() if v is not None}
        deps.renderer.compositor.update_layer(index, **updates)
        deps.state_manager.current_layers = deps.renderer.compositor.to_dict()['layers']
        return {'status': 'ok', 'layers': deps.renderer.compositor.to_dict()['layers']}
    return {'error': 'no compositor active'}

@router.post("/layers/reorder", dependencies=[Depends(require_auth)])
async def reorder_layer(req: LayerReorderRequest):
    if deps.renderer.compositor:
        deps.renderer.compositor.move_layer(req.from_index, req.to_index)
        deps.state_manager.current_layers = deps.renderer.compositor.to_dict()['layers']
        return {'status': 'ok', 'layers': deps.renderer.compositor.to_dict()['layers']}
    return {'error': 'no compositor active'}
```

- [ ] **Step 3: Run full test suite**

Run: `cd pi && PYTHONPATH=. pytest tests/ -v -q`
Expected: No regressions

- [ ] **Step 4: Deploy and test on Pi**

```bash
bash pi/scripts/deploy.sh ledfanatic.local
```

- [ ] **Step 5: Commit**

```bash
git add pi/app/api/routes/scenes.py
git commit -m "feat: layer CRUD API with Pydantic models and reorder endpoint"
```

---

### Task 7: Integration — Compositor in Renderer + Layout Hot-Swap + Boot Restore

**Files:**
- Modify: `pi/app/core/renderer.py`
- Modify: `pi/app/main.py`

**Codex fixes:** R1-M5 (layout hot-swap), R3-H1 (boot-time layer restore), R3-M2 (effects_config threading).

- [ ] **Step 1: Add compositor integration to renderer**

In `Renderer.__init__`:
```python
self.compositor = None  # Optional — set when using layers
```

In `Renderer._render_frame()`, before the current effect render block:
```python
    if self.compositor and self.compositor.layers:
        effect_start = time.perf_counter()
        try:
            internal_frame = self.compositor.render(t, self.state)
        except Exception as e:
            logger.error(f"Compositor crashed: {e}", exc_info=True)
            internal_frame = np.zeros((w, h, 3), dtype=np.uint8)
        self.state.effect_render_ms = (time.perf_counter() - effect_start) * 1000
        self.state.compositor_ms = self.compositor.compositor_ms
    elif self.current_effect is not None:
        # existing single-effect path with error isolation
```

In `Renderer.apply_layout()`, add:
```python
    if self.compositor:
        self.compositor.apply_layout(layout.width, layout.height)
```

- [ ] **Step 2: Replace existing startup scene block in main.py**

In `pi/app/main.py`, REPLACE the existing startup scene block (the `# Startup scene` section) with a unified restore that handles both layered and single-effect modes. This is a replacement, not an addition — avoids the R5-M2 conflict of running both paths.

```python
  # Startup scene — unified restore (R3-H1 + R4-M3 + R5-M2 fix)
  # Replaces the previous single-effect startup block entirely.
  saved_layers = state_manager.current_layers
  if saved_layers:
      # Restore via compositor to preserve opacity/blend/enabled semantics
      from app.core.compositor import Compositor
      renderer.compositor = Compositor.from_dict(
          {'layers': saved_layers},
          compiled_layout.width, compiled_layout.height,
          renderer.effect_registry,
          effects_config=effects_conf,
      )
      # R5-M2 fix: clear current_effect so it doesn't linger behind compositor
      renderer.current_effect = None
      logger.info(f"Restored {len(saved_layers)} layer(s) from state.json")
  else:
      # Legacy: no layers persisted, use current_scene
      startup = state_manager.current_scene or display_conf.get('startup_scene', 'rainbow_rotate')
      if not renderer.activate_scene(startup, state_manager.current_params, media_manager=media_manager):
          fallback = display_conf.get('startup_scene', 'rainbow_rotate')
          logger.warning(f"Failed to restore scene '{startup}', falling back to '{fallback}'")
          renderer.activate_scene(fallback)
```

- [ ] **Step 3: Run full test suite + deploy**

Run: `cd pi && PYTHONPATH=. pytest tests/ -v -q`
Deploy: `bash pi/scripts/deploy.sh ledfanatic.local`

- [ ] **Step 3: Manual verification — two-layer compositing on Pi**

Test via curl: add two layers, verify FPS > 55, verify both effects visible.

- [ ] **Step 4: Commit and tag**

```bash
git add pi/app/core/renderer.py
git commit -m "feat: integrate compositor into renderer with layout hot-swap"
git tag v1.2.0-compositor
```

---

## Acceptance Criteria

| Criterion | Test | Codex Finding |
|-----------|------|---------------|
| Effect crash doesn't kill render | test_error_isolation.py | H3 ✓ |
| 5 blend modes correct with opacity | test_compositor.py::TestBlendModes (13 tests) | H2 ✓ |
| Canonical opacity rule for all modes | test_compositor.py::test_*_half_opacity | H2 ✓ |
| Empty compositor returns black | test_compositor.py::test_empty_returns_black | — |
| Disabled layer skipped | test_compositor.py::test_disabled_layer_skipped | — |
| Crashing layer isolated, others survive | test_compositor.py::test_crashing_layer_isolated_other_layers_survive | R2-M2 ✓ |
| Layout hot-swap recreates instances | test_compositor.py::test_apply_layout | M5 ✓ |
| compositor_ms in API | RenderState.to_dict() | M6 ✓ |
| Layers persist in state.json v2 | test_state/test_migrations | H1 ✓ |
| v1→v2 migration preserves scenes | test_v1_to_v2_migration | H1 ✓ |
| Layer CRUD uses Pydantic models | scenes.py LayerAddRequest etc | M4 ✓ |
| Reorder endpoint exists | POST /layers/reorder | M4 ✓ |
| Two-layer scene at 59+ FPS | Manual Pi verification | — |
| RENDER_SCALE honored in layers | Manual test with supersampled effect | R2-H1 ✓ |
| YAML param defaults merged in layers | Code review of _create_effect | R2-H1 ✓ |
| Pydantic rejects invalid opacity/blend | 422 on bad request | R2-M3 ✓ |
| Layers restored on boot from state.json | Reboot Pi, verify layers active | R3-H1 ✓ |
| effects_config passed to all Compositor sites | Code review of API + from_dict + boot | R3-M2 ✓ |
| activate_scene clears compositor | Test: activate after layers, verify single-effect | R4-H1 ✓ |
| First /layers/add preserves current scene | Test: add layer while scene active, both visible | R4-H2 ✓ |
| Remove all layers clears compositor cleanly | Test: remove all, then activate_scene works | R4-H1 ✓ |
| Single-layer with opacity restores correctly | Reboot with opacity=0.5 layer, verify dim | R4-M3 ✓ |
| Mode switch centralized in activate_scene | Media/diag/startup all exit compositor | R6-H2 ✓ |
| No stale state after mode transitions | Enter layers, exit, verify clean state | R6-H1 ✓ |
| Existing single-effect mode unchanged | Full regression suite | — |
