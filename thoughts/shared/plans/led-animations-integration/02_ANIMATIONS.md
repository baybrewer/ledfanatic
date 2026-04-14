# Phase 2 — Port All 27 Animations

## Goal

Port every animation class from `led_sim.py` as a repo-native `Effect` subclass. Each animation uses `LEDBuffer` + engine modules, no Pygame.

## File layout

```
pi/app/effects/
  imported/
    __init__.py          # registers all ported effects into ALL three surfaces
    classic.py           # 5 classic animations
    ambient.py           # 12 ambient animations
    sound.py             # 10 sound-reactive animations
```

## Critical: Triple registration

Imported effects must be registered in **all three surfaces** or they'll be invisible:

1. **`renderer.effect_registry`** — so `activate_scene()` works
2. **`EffectCatalogService`** — so `/api/effects/catalog` and `/api/scenes/list` include them
3. **`PreviewService` lookup** — currently hardcoded to `EFFECTS + AUDIO_EFFECTS`

**Fix in Phase 2:**
- `imported/__init__.py` exports `IMPORTED_EFFECTS = {name: class}` dict
- `main.py` registers all into renderer
- `main.py` registers metadata into catalog service
- `PreviewService.start()` must be updated to also check `IMPORTED_EFFECTS`
  (or better: look up from `renderer.effect_registry` instead of hardcoded dicts)

## Critical: AudioCompatAdapter injection

The current render path passes raw `RenderState` to `effect.render(t, state)`.
Sound-reactive imported effects need the richer `AudioSnapshot` surface (`bands`, `beat_energy`, `drop`, `is_phrase`, etc.).

**Solution:** Each imported sound effect wraps the adapter internally:

```python
class ImportedSoundEffect(Effect):
    def __init__(self, ...):
        super().__init__(...)
        self._audio_adapter = AudioCompatAdapter()

    def render(self, t, state):
        # Adapt raw RenderState audio into rich snapshot
        raw = state._audio_lock_free
        audio = self._audio_adapter.adapt(raw, t)
        # Use audio.bands, audio.drop, etc.
        self._update(dt_ms, audio)
        return self.buf.get_frame()
```

This avoids modifying the renderer's core path.

## Critical: Audio adapter fixes needed before port

The adapter must match the source simulator's contract:

| Field | Source behavior | Current adapter | Fix needed |
|-------|----------------|-----------------|------------|
| `drop` | Boolean onset event (True for ~2-3s burst, then False) | Float accumulator (0-1) | Add `drop_event: bool` (onset trigger) alongside `drop: float` (intensity) |
| `_time` | Used by VUMeter/BeatPulse for breakdown sine | Not exposed | Add `_time` alias for `time_s` |
| `drop_intensity` | 0-1+ magnitude of drop | Not exposed | Add field |

## Critical: Stateful effects — no re-create on param change

Many effects maintain internal state (fire buffers, particle lists, trail buffers, scroll positions). The current renderer destroys and recreates the effect on every `activate_scene()` call.

**Solution:** Add `update_params(params)` method to Effect base class:

```python
class Effect(ABC):
    def update_params(self, params: dict):
        """Update parameters without resetting state. Override for custom behavior."""
        self.params.update(params)
```

The renderer checks if the active effect matches the requested effect name; if so, calls `update_params()` instead of re-creating.

The `/api/scenes/activate` route already sends `{effect, params}`. The renderer just needs:
```python
if scene_name == self.state.current_scene and self.current_effect:
    self.current_effect.update_params(merged)
    return True
```

## Adapter pattern (updated)

```python
class PortedEffect(Effect):
    CATEGORY = "imported_classic"
    DISPLAY_NAME = "Rainbow Cycle"
    DESCRIPTION = "Fills every LED with a single palette color that advances over time"
    PALETTE_SUPPORT = True
    PARAMS = [...]  # FROM THE ACTUAL SOURCE, not assumed

    def __init__(self, width=10, height=172, params=None):
        super().__init__(width, height, params)
        self.buf = LEDBuffer(width, height)
        self._last_t = None
        # Initialize from ACTUAL source defaults

    def render(self, t, state):
        if self._last_t is None:
            self._last_t = t
        dt_ms = max(0, (t - self._last_t) * 1000)
        self._last_t = t
        self._update(dt_ms, state)
        return self.buf.get_frame()

    def update_params(self, params):
        """Update without resetting internal state.

        IMPORTANT: Effects with structural params (particle counts, star arrays,
        scroll buffers) MUST override this method to handle resizing.
        The base implementation only handles scalar params safely.
        """
        for key, val in params.items():
            if key == 'palette' and self.PALETTE_SUPPORT:
                self._set_palette(val)
            elif key in self._SCALAR_PARAMS:
                setattr(self, key.upper(), val)
            # Structural params (count, density, particles, etc.) handled by override

    # Each effect class defines which params are safe for scalar update
    _SCALAR_PARAMS = set()  # Override per class
```

## Persistent framebuffer requirement

Several effects do NOT clear their buffer each frame — they fade or accumulate:
- `FlowField` — fades buffer by factor, draws particle trails
- `FeldsteinEquation` — accumulates into prior pixels
- `SoundRipples` — fades buffer
- `SoundWorm` — fades buffer
- `ParticleBurst` — fades buffer

The `LEDBuffer` class must support `fade(factor)` and `add_led()` (additive blending). The buffer must persist across frames — it is NOT cleared automatically.

Each effect decides whether to `clear()` or `fade()` at the start of its update.

## Param metadata — DO NOT hand-write, extract from source

**Critical rule:** Do NOT maintain hand-written param tables in this plan document. They drift from the source and cause wrong implementations.

Instead, each ported effect class MUST define its params by reading the vendored source's `Param(...)` entries directly:

```python
# In each ported effect class, define PARAMS from the source's exact values:
# Reference: pi/app/effects/imported/vendor/led_sim_reference.py
# Search for the class name, find its `params = [Param(...), ...]` block
# Copy label, attr, lo, hi, step, default exactly.
```

### What the implementer must do per effect:

1. Open `vendor/led_sim_reference.py`
2. Find the animation class (e.g., `class Aurora(AnimBase):`)
3. Copy its `params = [Param(...)]` entries exactly
4. Copy its `has_palette` flag and default palette index
5. Copy its `__init__` defaults for each param attribute
6. For Feldstein2: note it has 17 custom `_FELD_PALETTES`, selected by integer `PALETTE` param — NOT the standard 10
7. For Fireplace: note it has 16 params and uses fire palette — NOT the standard palettes
8. For sound effects: note which `audio.xxx` fields are accessed in `update()` — these are the actual audio deps

### Summary of special cases (verified against source)

| Effect | Special handling |
|--------|-----------------|
| Feldstein2 | 17 custom palettes via integer PALETTE param (0-16), not standard palette system |
| Fireplace | 16 params, fire palette only, no standard palette, no speed param |
| Spectrum, VUMeter, BeatPulse, BassFire, StrobeChaos | No speed param — only gain/decay/intensity |
| VUMeter, BeatPulse | Use `audio._time` for breakdown sine |
| All sound effects | Use `audio.drop` as boolean event trigger |
| OceanWaves | Default palette idx 1 (Ocean) |
| MatrixRain | Default palette idx 3 (Forest) |
| Nebula | Default palette idx 9 (Vapor) |

## Registration in main.py — COMPLETE wiring

The current codebase has a gap: `AppDeps.effect_catalog` is optional and never populated. Routes fall back to a freshly-built default catalog that won't include imported effects.

**Required changes:**

1. **`main.py`** — create ONE shared `EffectCatalogService`, register all imported effects into it, and pass it through `create_app()`:

```python
from .effects.catalog import EffectCatalogService, EffectMeta
from .effects.imported import IMPORTED_EFFECTS

# Create shared catalog (picks up built-in effects automatically)
effect_catalog = EffectCatalogService()

# Register all imported effects into ALL THREE surfaces
for name, cls in IMPORTED_EFFECTS.items():
    # 1. Renderer registry (for activate_scene)
    renderer.register_effect(name, cls)

    # 2. Catalog service (for /api/effects/catalog and /api/scenes/list)
    meta = EffectMeta(
        name=name, label=cls.DISPLAY_NAME, group=cls.CATEGORY,
        description=cls.DESCRIPTION,
        audio_requires=getattr(cls, 'AUDIO_REQUIRES', ()),
    )
    effect_catalog.register_imported(name, meta)

# Pass catalog into create_app so AppDeps.effect_catalog is populated
app = create_app(..., effect_catalog=effect_catalog, ...)
```

2. **`server.py create_app()`** — accept `effect_catalog` parameter and store in `AppDeps`

3. **`preview/service.py start()`** — look up from `renderer.effect_registry` instead of hardcoded `EFFECTS + AUDIO_EFFECTS`

This ensures imported effects appear in ALL surfaces: live activation, catalog API, scenes list, and preview.

## Update PreviewService to use renderer registry

In `pi/app/preview/service.py`, change `start()` to look up from `renderer.effect_registry` instead of hardcoded `EFFECTS + AUDIO_EFFECTS`:

```python
def start(self, effect_name, ...):
    if effect_name not in self._renderer.effect_registry:
        raise ValueError(f"Unknown effect: {effect_name}")
    effect_cls = self._renderer.effect_registry[effect_name]
```

## Tests

- Every animation returns `(10, 172, 3)` uint8
- Time continuity: render 10 frames, no crash
- Palette switching works for palette-capable effects
- Param update via `update_params()` preserves state
- Sound animations degrade gracefully with zero audio
- Persistent-buffer effects produce visible trails after 10+ frames
- Fire has 16 controllable params
- Feldstein2 has 17 custom palettes

## Gate

- All 27 animations render without error
- Registered in all three surfaces (renderer, catalog, preview)
- Sound effects receive adapted audio via AudioCompatAdapter
- Param changes don't reset stateful animations
- Effects requiring `update_params()` overrides (structural param changes):
  - Fireflies (Count resizes particle array)
  - FlowField (Particles resizes particle array)
  - Starfield (Density resizes star array)
  - MatrixRain (Density resizes drop array)
  - ParticleBurst (Count changes burst size)
  - LavaLamp (Blobs resizes blob array)
  - Kaleidoscope (Segments changes symmetry calculation)
  - Moire (Centers resizes center array)
  - Fireplace (most params are scalars, but NOISE_OCTAVES affects computation)
  - Feldstein2 (Palette param switches between 17 custom palettes)
