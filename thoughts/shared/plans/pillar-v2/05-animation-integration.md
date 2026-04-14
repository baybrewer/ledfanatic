# F3: Animation Integration

## Summary

Import LED animations from a user-provided external Python file into the pillar
controller effect system. Each animation becomes a registered Effect class,
selectable from the Effects tab in the web UI.

**No new infrastructure needed** — follows the existing effect pattern exactly.

---

## Integration Strategy

### Step 1: Analyze the Source File

Before implementation, analyze:

1. **Animation functions**: What animations are defined? Parameters?
2. **LED model**: 1D strip? 2D matrix? Coordinate system?
3. **Frame generation**: Arrays? Generator? Callback? Time-based?
4. **Dependencies**: External libraries beyond numpy?
5. **Color format**: RGB? HSV? 0–255 or 0.0–1.0?
6. **Timing**: Built-in or external?

### Step 2: Map Source Model to Pillar Model

| Source Concept | Pillar Equivalent |
|----------------|-------------------|
| LED array / strip | Logical canvas column (one strip) |
| 2D matrix | `(width, height, 3)` numpy array |
| Frame | Return value of `Effect.render()` |
| Time parameter | `t` argument (monotonic seconds) |
| Color (RGB 0-255) | Direct match |
| Color (HSV) | Convert using `hsv_to_rgb()` from `base.py` |
| Color (float 0-1) | Multiply by 255, cast to uint8 |
| Animation loop | Handled by renderer — effect renders one frame per call |

### Step 3: Convert Each Animation to an Effect Class

```python
class SourceAnimationName(Effect):
    """Human-readable description of what this animation does."""

    def render(self, t: float, state) -> np.ndarray:
        frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)
        # Adapt source logic using self.width, self.height, t, self.params
        return frame
```

### Step 4: Register in Effect Registry

Create `pi/app/effects/<source_name>.py` with a registration dict:

```python
EFFECTS = {
    "animation_name_1": AnimationName1,
    "animation_name_2": AnimationName2,
}
```

Register in `pi/app/main.py`:

```python
from .effects.<source_name> import EFFECTS as NEW_EFFECTS

for name, cls in NEW_EFFECTS.items():
    renderer.register_effect(name, cls)
```

### Step 5: Add Default Parameters to effects.yaml

```yaml
effects:
  animation_name_1:
    params:
      speed: 1.0
      color: "#FF6600"
```

**How the merge works**: In `main.py:104-105`, the effects.yaml is loaded and
set as `renderer.effects_config`. When `renderer._set_scene()` is called, it
looks up the effect name in `effects_config['effects']` (or `'audio_effects'`)
and merges: `yaml_params < caller_params`. This means effects.yaml provides
defaults, and API calls can override per-activation.

---

## File Placement

| File | Purpose |
|------|---------|
| `pi/app/effects/<source_name>.py` | Converted effect classes |
| `pi/config/effects.yaml` | Default parameters (append to existing) |
| `pi/app/main.py` | Import and register new effects |
| `pi/tests/test_<source_name>.py` | Smoke tests for new effects |

---

## Conversion Rules

### 1. No Global State

Convert module-level variables to time-based computation:
```python
# BAD: global hue_offset; hue_offset += 0.01
# GOOD: hue_offset = (t * speed) % 1.0
```

### 2. Time-Based, Not Frame-Counting

```python
# BAD: frame_count += 1; position = frame_count % 172
# GOOD: position = int(t * speed) % self.height
```

### 3. NumPy Vectorization

```python
# BAD: for x in range(width): for y in range(height): ...
# GOOD: xs, ys = np.meshgrid(np.arange(w), np.arange(h), indexing='ij')
```

### 4. Parameter Extraction

Hardcoded constants → `self.params.get('name', default)`.

### 5. Canvas Dimensions

Always use `self.width` and `self.height`, never hardcoded values.

### 6. Color Clamping

```python
frame = np.clip(frame, 0, 255).astype(np.uint8)
```

### 7. Docstrings Required

Every effect class must have a docstring (used for tooltips in F2):
```python
class MeteorRain(Effect):
    """Meteors falling down the pillar with glowing trails"""
```

---

## Naming Conventions

- Registry keys: `snake_case` (e.g., `"meteor_rain"`)
- Class names: `PascalCase` (e.g., `MeteorRain`)
- Descriptive, no abbreviations, no generic names like "effect1"

---

## UI Integration

New effects automatically appear in the **Effects** tab — the UI fetches from
`GET /api/scenes/list`, which returns all registered effects. The response
format includes the effect's type and description:

```json
{
  "effects": {
    "meteor_rain": {"type": "generative", "description": "Meteors falling..."},
    ...
  }
}
```

No UI code changes needed for basic listing. If there are many imported effects
(>10), consider adding a new category in the response and a section heading.

---

## Acceptance Criteria

- [ ] Every animation from the source file has a corresponding Effect class
- [ ] Each effect renders correctly at 60fps without errors
- [ ] Each effect returns `(width, height, 3)` uint8
- [ ] Effects use time-based animation (not frame counting)
- [ ] Effects use `self.params` for configurable values
- [ ] Effects registered and appear in `GET /api/scenes/list`
- [ ] Effects activatable via `POST /api/scenes/activate`
- [ ] Default parameters in `effects.yaml`
- [ ] Each effect has a non-empty docstring
- [ ] No new dependencies added without approval
- [ ] All ~219 existing tests pass (regression)

---

## Test Plan

### Automated: `pi/tests/test_<source_name>.py`

```python
class MockRenderState:
    audio_level = 0.0
    audio_bass = 0.0
    audio_mid = 0.0
    audio_high = 0.0
    audio_beat = False
    audio_bpm = 0.0

mock_state = MockRenderState()

def test_<effect>_renders():
    effect = EffectClass(width=10, height=172)
    frame = effect.render(0.0, mock_state)
    assert frame.shape == (10, 172, 3)
    assert frame.dtype == np.uint8

def test_<effect>_no_crash_over_time():
    effect = EffectClass(width=10, height=172)
    for t in [0, 0.5, 1.0, 10.0, 100.0, 1000.0]:
        frame = effect.render(t, mock_state)
        assert frame.shape == (10, 172, 3)

def test_<effect>_respects_params():
    effect = EffectClass(width=10, height=172, params={'speed': 0})
    f1 = effect.render(0.0, mock_state)
    f2 = effect.render(1.0, mock_state)
    np.testing.assert_array_equal(f1, f2)
```

---

## Waiting On

The user will provide the source Python file. This plan is the integration
framework. Once provided: analyze → map → convert → register → test.
