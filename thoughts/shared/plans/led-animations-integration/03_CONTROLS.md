# Phase 3 — Speed/Palette UI Controls

## Goal

Every animation gets a speed slider and palette selector in the Effects tab UI. Parameters are sent via the existing `/api/scenes/activate` params mechanism.

## API changes

### Enrich `/api/effects/catalog` response

Add per-effect parameter metadata:

```json
{
  "effects": {
    "aurora_borealis": {
      "name": "aurora_borealis",
      "label": "Aurora Borealis",
      "group": "imported_ambient",
      "description": "Shimmering northern lights",
      "params": [
        {"name": "speed", "label": "Speed", "min": 0.1, "max": 5.0, "step": 0.1, "default": 1.0, "type": "slider"},
        {"name": "wave", "label": "Wave", "min": 0.5, "max": 4.0, "step": 0.1, "default": 1.5, "type": "slider"},
        {"name": "bright", "label": "Brightness", "min": 0.3, "max": 1.5, "step": 0.05, "default": 1.0, "type": "slider"}
      ],
      "palettes": ["Rainbow", "Ocean", "Sunset", "Forest", "Lava", "Ice", "Neon", "Cyberpunk", "Pastel", "Vapor"],
      "current_palette": "Rainbow"
    }
  }
}
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

## Param change flow

1. User moves speed slider
2. JS debounces (100ms)
3. `POST /api/scenes/activate {effect: current, params: {speed: newVal, palette: currentPalette}}`
4. Renderer re-creates effect with merged params
5. Effect picks up new speed on next render

## Tests

- Catalog response includes params and palettes for imported effects
- Activate with custom speed produces different output than default
- Palette param switches palette
- Existing effects without params still work (empty params array)

## Gate

- Every imported effect shows speed slider and palette dropdown (where applicable)
- Parameter changes take effect within 1 frame
- No UI regression on existing effects
