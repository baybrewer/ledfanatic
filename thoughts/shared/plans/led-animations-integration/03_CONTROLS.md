# Phase 3 — Per-Effect Parameter Controls

## Goal

Each animation shows its **actual parameters** (from the source `PARAMS` list) in the Effects tab UI. NOT a blanket "speed + palette" — each effect defines its own controls. Parameters are sent via the existing `/api/scenes/activate` params mechanism.

**Critical rule:** Generate UI controls from each effect's `PARAMS` class attribute, not from assumptions. Some effects have no speed param (Spectrum, VUMeter, BeatPulse, BassFire, Spectrogram, StrobeChaos). Some have no standard palette (Fireplace uses fire palette, Feldstein2 has 17 custom palettes). The UI must be data-driven from metadata.

## API changes

### Enrich `/api/effects/catalog` response

Add per-effect parameter metadata derived from each class's `PARAMS` attribute:

```json
{
  "effects": {
    "aurora_borealis": {
      "name": "aurora_borealis",
      "label": "Aurora Borealis",
      "group": "imported_ambient",
      "description": "Shimmering northern lights with flowing curtains",
      "params": [
        {"name": "speed", "label": "Speed", "min": 0.05, "max": 2.0, "step": 0.05, "default": 0.4, "type": "slider"},
        {"name": "wave", "label": "Wave", "min": 0.2, "max": 3.0, "step": 0.1, "default": 1.0, "type": "slider"},
        {"name": "bright", "label": "Brightness", "min": 0.2, "max": 1.0, "step": 0.05, "default": 0.9, "type": "slider"}
      ],
      "palettes": ["Rainbow", "Ocean", "Sunset", "Forest", "Lava", "Ice", "Neon", "Cyberpunk", "Pastel", "Vapor"],
      "default_palette": 0
    },
    "feldstein_og": {
      "params": [
        {"name": "speed", "label": "Speed", "min": 0.1, "max": 3.0, "step": 0.1, "default": 1.0, "type": "slider"},
        {"name": "fade", "label": "Fade/Dark", "min": 10, "max": 200, "step": 5, "default": 40, "type": "slider"},
        {"name": "palette", "label": "Palette", "min": 0, "max": 16, "step": 1, "default": 0, "type": "slider"}
      ],
      "palettes": ["Original", "Rainbow", "Ocean", "Fire", "Acid", "Pastel", "Monochrome", "Sunset", "Aurora", "Cyberpunk", "Deep Sea", "Ember", "Neon", "Forest", "Vapor", "Blood Moon", "Ice Storm"],
      "default_palette": 0,
      "palette_type": "custom_feldstein"
    },
    "spectrum": {
      "params": [
        {"name": "gain", "label": "Gain", "min": 0.5, "max": 5.0, "step": 0.1, "default": 2.0, "type": "slider"},
        {"name": "decay", "label": "Decay", "min": 0.5, "max": 0.99, "step": 0.01, "default": 0.92, "type": "slider"}
      ],
      "palettes": ["Rainbow", "Ocean", "Sunset", "Forest", "Lava", "Ice", "Neon", "Cyberpunk", "Pastel", "Vapor"],
      "default_palette": 0
    }
  }
}
```

**Key:** Params and palettes come from each class's metadata, not assumed. Feldstein OG has 17 custom palettes. Fireplace has 16 params and uses fire palette (no standard palette selector). Sound effects without `speed` don't show a speed slider.
```

### Activate with params

```json
POST /api/scenes/activate
{
  "effect": "aurora_borealis",
  "params": {"speed": 2.0, "palette": "Ocean"}
}
```

The `palette` param is handled by the effect's `__init__` to set `self.palette_idx`.

## UI changes — Effects tab

### Current state
- Grid of buttons, click to activate
- No parameter controls

### New state

```
┌─────────────────────────────────────────┐
│ Effects                                  │
│                                          │
│ ▼ Classic (5)                            │
│  [Rainbow Cycle] [Feldstein] [Feld OG]  │
│  [Brett's Fav] [Fireplace]              │
│                                          │
│ ▼ Ambient (12)                           │
│  [Aurora] [Plasma] [Lava Lamp] ...      │
│                                          │
│ ▼ Sound-Reactive (10)                    │
│  [Spectrum] [Beat Pulse] ...            │
│                                          │
│ ▼ Built-in (existing effects)            │
│  [Fire] [Rainbow Rotate] ...            │
│                                          │
│ ── Active: Aurora Borealis ──            │
│ Speed    [═══════●════] 1.5             │
│ Wave     [══●═════════] 0.8             │
│ Palette  [▼ Ocean        ]              │
│                                          │
└─────────────────────────────────────────┘
```

### Implementation

**HTML additions:**
- Collapsible category headers (click to expand/collapse)
- Active effect control panel below the grid
- Slider inputs with live value display
- Palette dropdown (populated from catalog metadata)

**JS additions:**
- `loadEffectParams(effectName)` — fetch catalog, render sliders + dropdown
- Slider `input` event → debounced re-activate with new params
- Palette dropdown `change` → re-activate with `{palette: selectedName}`

**CSS additions:**
- `.effect-category` collapsible header
- `.param-slider` styling matching existing brightness slider
- `.palette-select` dropdown styling

## Important: loadEffects() refactor required

The current `loadEffects()` in `app.js` rebuilds the Effects tab from `/api/scenes/list` on every call. This must be refactored to:

1. Fetch from `/api/effects/catalog` (richer metadata with params/palettes)
2. Store result in an in-memory `effectsCatalog` object
3. Render category sections + active controls from that state
4. Only re-fetch on tab activation, not on every activate call
5. After activating an effect, update the active highlight without re-fetching

This prevents the param controls from being destroyed every time an effect is activated.

## Param change flow (state-preserving)

1. User moves speed slider
2. JS debounces (100ms)
3. `POST /api/scenes/activate {effect: current, params: {speed: newVal, palette: currentPalette}}`
4. Renderer detects same effect is already active → calls `effect.update_params(merged)` instead of re-creating
5. Scalar params take effect on next frame; structural params (count, density) trigger controlled resize in the effect's `update_params()` override
6. Fire buffers, particle lists, and trail buffers are preserved

**Note:** Switching to a DIFFERENT effect still re-creates. Only same-effect param changes are state-preserving.

## Tests

- Catalog response includes params and palettes for imported effects
- Activate with custom speed produces different output than default
- Palette param switches palette
- Existing effects without params still work (empty params array)

## Gate

- Every imported effect shows speed slider and palette dropdown (where applicable)
- Parameter changes take effect within 1 frame
- No UI regression on existing effects
