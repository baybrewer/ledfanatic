# F3: Animation Integration

## Summary

Import LED animations from a user-provided external Python file into the pillar
controller effect system. Each animation becomes a registered Effect class,
selectable from the Effects tab in the web UI. The animations must be adapted
to the pillar's canvas format (10×172 RGB) and registered using the existing
effect registry pattern.

**No new infrastructure needed** — this follows the existing effect pattern exactly.

---

## Integration Strategy

### Step 1: Analyze the Source File

The user will provide a Python file containing LED animations. Before
implementation, analyze:

1. **Animation functions**: What animations are defined? What are their parameters?
2. **LED model**: How does the source represent LEDs? (1D strip? 2D matrix?
   circular? What coordinate system?)
3. **Frame generation**: Does each animation produce frames as arrays? Generator?
   Callback? Time-based?
4. **Dependencies**: Does it use any external libraries beyond numpy?
5. **Color format**: RGB? HSV? 0–255 or 0.0–1.0?
6. **Timing**: Is timing built into the animation or external?

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
| Animation loop | Handled by renderer — effect just renders one frame per call |

### Step 3: Convert Each Animation to an Effect Class

Template for conversion:

```python
class SourceAnimationName(Effect):
    """Human-readable description of what this animation does."""

    def render(self, t: float, state) -> np.ndarray:
        frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)

        # Adapt source animation logic here:
        # - Use self.width (10) and self.height (172) for dimensions
        # - Use t for time (seconds, monotonic)
        # - Use self.params for configurable parameters
        # - Use self.elapsed(t) for time since effect started
        # - Access audio via state.audio_level, state.audio_bass, etc.
        # - Return (width, height, 3) uint8 array

        return frame
```

### Step 4: Register in Effect Registry

Create a new file `pi/app/effects/<source_name>.py` containing all converted
effects, with a registration dict:

```python
# At bottom of file
EFFECTS = {
    "animation_name_1": AnimationName1,
    "animation_name_2": AnimationName2,
    ...
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
  animation_name_2:
    params:
      density: 0.5
```

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

Source animations often use module-level variables for state. Convert these
to instance variables in `__init__`:

```python
# BAD (source pattern):
hue_offset = 0
def update():
    global hue_offset
    hue_offset += 0.01

# GOOD (pillar pattern):
class HueShift(Effect):
    def render(self, t, state):
        hue_offset = (t * self.params.get('speed', 1.0)) % 1.0
        # ... use hue_offset
```

### 2. Time-Based, Not Frame-Counting

Source animations often count frames. Convert to time-based using `t`:

```python
# BAD: frame_count += 1; position = frame_count % 172
# GOOD: position = int(t * speed) % self.height
```

### 3. NumPy Vectorization

Source animations that loop pixel-by-pixel should be vectorized:

```python
# BAD:
for x in range(width):
    for y in range(height):
        frame[x, y] = compute_color(x, y, t)

# GOOD:
xs = np.arange(self.width)[:, np.newaxis]
ys = np.arange(self.height)[np.newaxis, :]
frame = compute_color_vectorized(xs, ys, t)
```

### 4. Parameter Extraction

Hardcoded constants become `self.params` entries:

```python
# Source: speed = 2.5, density = 0.8
# Pillar: speed = self.params.get('speed', 2.5)
#         density = self.params.get('density', 0.8)
```

### 5. Canvas Dimensions

Source may assume different dimensions. Always use `self.width` and
`self.height`, never hardcoded values:

```python
# BAD: frame = np.zeros((60, 30, 3))
# GOOD: frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)
```

### 6. Color Clamping

Ensure all color values are uint8 (0–255). NumPy operations can produce
float or negative values:

```python
frame = np.clip(frame, 0, 255).astype(np.uint8)
```

---

## Naming Conventions

Effect names in the registry should be `snake_case`, descriptive, and
prefixed if there's ambiguity:

```python
EFFECTS = {
    "meteor_rain": MeteorRain,      # not "effect1" or "MeteorRain"
    "color_chase": ColorChase,
    "breathing_glow": BreathingGlow,
}
```

Effect class names are `PascalCase`, matching the snake_case registry key.

---

## UI Integration

New effects automatically appear in the **Effects** tab because the UI
fetches from `GET /api/scenes/list`, which returns all registered effects.
No UI code changes needed for basic listing.

For grouping: if the imported animations are thematically different from the
built-in effects, consider adding a new section heading in the Effects panel:

```javascript
// In loadEffects():
if (data.imported && data.imported.length > 0) {
  // Add "Imported Effects" heading
  appendHeading('Imported Effects');
  data.imported.forEach(effect => appendEffectButton(effect));
}
```

This requires the scene list endpoint to include an `imported` category.
Alternatively, just add them to the `generative` list — simpler and sufficient.

**Recommendation**: Add them to `generative` unless there are >10 imported
effects, in which case create an `imported` category.

---

## Acceptance Criteria

- [ ] Every animation from the source file has a corresponding Effect class
- [ ] Each converted effect renders correctly at 60fps without errors
- [ ] Each effect returns the correct shape: `(width, height, 3)` uint8
- [ ] Effects use time-based animation (not frame counting)
- [ ] Effects use `self.params` for configurable values
- [ ] Effects are registered and appear in `GET /api/scenes/list`
- [ ] Effects can be activated via `POST /api/scenes/activate`
- [ ] Default parameters are in `effects.yaml`
- [ ] Each effect has a docstring (for F2 tooltips)
- [ ] No new dependencies added without approval

---

## Test Plan

### Automated: `pi/tests/test_<source_name>.py`

For each converted effect:

```python
def test_<effect_name>_renders():
    """Effect returns correct shape and dtype."""
    effect = EffectClass(width=10, height=172)
    frame = effect.render(0.0, mock_state)
    assert frame.shape == (10, 172, 3)
    assert frame.dtype == np.uint8

def test_<effect_name>_no_crash_over_time():
    """Effect doesn't crash over extended time range."""
    effect = EffectClass(width=10, height=172)
    for t in [0, 0.5, 1.0, 10.0, 100.0, 1000.0]:
        frame = effect.render(t, mock_state)
        assert frame.shape == (10, 172, 3)

def test_<effect_name>_respects_params():
    """Effect uses self.params for configuration."""
    effect = EffectClass(width=10, height=172, params={'speed': 0})
    frame1 = effect.render(0.0, mock_state)
    frame2 = effect.render(1.0, mock_state)
    # With speed=0, frames should be identical
    np.testing.assert_array_equal(frame1, frame2)
```

### Mock State

```python
class MockRenderState:
    audio_level = 0.0
    audio_bass = 0.0
    audio_mid = 0.0
    audio_high = 0.0
    audio_beat = False
    audio_bpm = 0.0

mock_state = MockRenderState()
```

### Regression

Full suite: `PYTHONPATH=. pytest tests/ -v` — all tests pass including new ones.

---

## Waiting On

The user will provide the source Python file. This plan is the integration
framework. Once the file is provided:

1. Analyze the file using this plan's Step 1 checklist
2. Map each animation to the pillar model (Step 2)
3. Convert, register, test (Steps 3–5)
4. Iterate with the user on parameter tuning and visual quality
