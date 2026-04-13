# Pillar V2: Setup, Simulator & Effects — Master Plan

## Project Summary

Major feature expansion of the LED pillar controller. Six features, three
implementation phases, designed to be implemented sequentially with clear
contracts between components.

**Goal**: Make the pillar self-configuring, visually previewable, and packed
with effects — all controllable from a phone browser.

---

## Feature Inventory

| ID | Feature | Phase | Priority | Depends On |
|----|---------|-------|----------|------------|
| F1 | Per-strip configuration (RGB order, LED count, chipset) | 1 | P0 | — |
| F2 | UI tooltips & polish | 1 | P0 | — |
| F3 | Animation integration (external Python file) | 1 | P0 | — |
| F4 | Camera-based RGB order auto-detection | 2 | P1 | F1 |
| F5 | Camera-based LED spatial mapping | 2 | P1 | F1 |
| F6 | Web simulator (live effect preview) | 3 | P1 | F3 |

**Planning docs:**

- `01-strip-configuration.md` — F1: per-strip config, schema, mapping, API, UI
- `02-camera-rgb-detection.md` — F4: camera auto-detect RGB order
- `03-camera-spatial-mapping.md` — F5: camera-based 2D position map
- `04-ui-tooltips-polish.md` — F2: tooltips, labels, UX fixes
- `05-animation-integration.md` — F3: import external effects
- `06-web-simulator.md` — F6: browser-based LED preview

---

## Implementation Phases

### Phase 1: Foundation (F1 + F2 + F3)

No dependencies between these three — can be developed in parallel.

- **F1** establishes per-strip config infrastructure (schema, API, UI setup page)
- **F2** is a UI-only pass (tooltips, labels, no backend changes)
- **F3** adds new effects from the user-provided Python file

**Gate**: All existing 120 tests pass. New tests for strip config and
imported effects. UI is usable without reading source code.

### Phase 2: Smart Setup (F4 + F5)

Both depend on F1 (per-strip config must exist to write to).

- **F4** uses phone camera to auto-detect RGB color order per strip
- **F5** uses phone camera to build 2D spatial map of LED positions

**Gate**: Camera features work on iPhone Safari over local WiFi. Results
persist to hardware.yaml / spatial map file.

### Phase 3: Preview & Live (F6)

Depends on F3 (needs effects to preview).

- **F6** streams rendered frames to a browser canvas for live preview
- Future: live effect coding (Pixel Blaze style) — NOT in this plan

**Gate**: Simulator shows real-time preview of any active effect in browser.

---

## Shared Architecture Decisions

### 1. Color reordering happens on the Pi (not Teensy)

**Why**: OctoWS2811 applies a single global color order (currently `WS2811_GRB`)
to all 8 outputs. Per-strip reordering at the Teensy level would require either
modifying OctoWS2811 internals or post-processing the DMA buffer — both fragile.

**How**: The mapping layer (`cylinder.py`) already processes each strip
independently. Adding a per-strip color channel permutation there is a single
NumPy index operation per strip, negligible at 60fps.

**Data flow**:
```
Effect (RGB) → downsample → brightness/gamma → map_frame_fast()
  └─ for each strip:
       1. spatial mapping (serpentine, truncation for short strips)
       2. color reorder (apply strip's permutation: e.g. RGB→GRB, RGB→BRG)
  → serialize → COBS frame → Teensy → OctoWS2811 (WS2811_GRB) → wire
```

The compensation permutation accounts for the difference between what a strip's
LEDs expect on the wire vs. what OctoWS2811's global config (`WS2811_GRB`)
actually outputs. See `01-strip-configuration.md` for the full permutation table.

### 2. Teensy firmware stays unchanged (Phase 1–2)

No protocol changes needed. Frame payload format is identical — the Pi just
sends different byte orderings per strip within the same frame structure.
Variable LED counts handled by zero-padding shorter strips.

### 3. Config file strategy

| File | Role | Mutable at runtime? |
|------|------|---------------------|
| `hardware.yaml` | Physical layout SSOT (strips, channels, wiring) | Yes (via setup API) |
| `spatial_map.json` | 2D LED positions from camera mapping (F5) | Yes (via mapping API) |
| `effects.yaml` | Effect defaults and palettes | No (edit manually) |
| `system.yaml` | Auth, network, brightness, transport | Partially (brightness) |
| `state.json` | Runtime state (scene, presets, FPS) | Yes (auto-saved) |

### 4. UI architecture: subpages via panel switching

The current UI uses tabs (`Live`, `Effects`, `Media`, `Audio`, `Diag`, `System`).
The Setup page is a **sub-panel within the System tab**, toggled by a button.
This avoids adding a 7th top-level tab to the already-full navigation bar.

```
System tab
├── System Info (existing)
├── [Setup] button → toggles Setup sub-panel
│   ├── Strip Configuration table
│   ├── [Auto-detect RGB Order] → camera wizard (F4)
│   └── [Map LED Positions] → camera wizard (F5)
└── System Actions (existing: Restart, Reboot)
```

### 5. Frame streaming for simulator (F6)

The renderer already produces frames at 60fps. For the simulator, we add an
opt-in WebSocket channel that sends the **logical canvas** (10×172×3 = 5,160
bytes) at a reduced rate (10–15fps). This is ~75KB/s — trivial over local WiFi.

The browser renders this as a 2D grid (unwrapped cylinder view) using a
`<canvas>` element. No WebGL needed for 10×172 pixels.

### 6. Effect registration pattern (unchanged)

New effects from F3 follow the existing pattern:
```python
# In the new effects file:
EFFECTS = {"effect_name": EffectClass, ...}

# In main.py at startup:
for name, cls in NEW_EFFECTS.items():
    renderer.register_effect(name, cls)
```

No changes to the renderer, base class, or registration mechanism.

---

## Files Created / Modified Per Feature

### F1: Strip Configuration
| Action | File |
|--------|------|
| MODIFY | `pi/config/hardware.yaml` — per-strip schema |
| MODIFY | `pi/app/hardware_constants.py` — load per-strip config, expose helpers |
| MODIFY | `pi/app/mapping/cylinder.py` — color reorder + variable LED count |
| ADD | `pi/app/api/config_routes.py` — strip config API endpoints |
| MODIFY | `pi/app/api/server.py` — mount config routes |
| MODIFY | `pi/app/ui/static/js/app.js` — setup sub-panel UI |
| MODIFY | `pi/app/ui/static/css/app.css` — setup panel styles |
| MODIFY | `pi/app/ui/index.html` — setup panel markup |
| ADD | `pi/tests/test_strip_config.py` — config + mapping tests |

### F2: UI Tooltips
| Action | File |
|--------|------|
| MODIFY | `pi/app/ui/index.html` — `title` attributes on all buttons |
| MODIFY | `pi/app/ui/static/css/app.css` — tooltip styles |
| MODIFY | `pi/app/ui/static/js/app.js` — custom tooltip component |

### F3: Animation Integration
| Action | File |
|--------|------|
| ADD | `pi/app/effects/<new_file>.py` — converted effects |
| MODIFY | `pi/config/effects.yaml` — defaults for new effects |
| MODIFY | `pi/app/main.py` — register new effects |
| ADD | `pi/tests/test_<new_effects>.py` — render smoke tests |

### F4: Camera RGB Detection
| Action | File |
|--------|------|
| ADD | `pi/app/api/setup_routes.py` — camera setup endpoints |
| MODIFY | `pi/app/api/server.py` — mount setup routes |
| MODIFY | `pi/app/ui/static/js/app.js` — camera wizard UI |
| MODIFY | `pi/app/ui/static/css/app.css` — wizard styles |
| MODIFY | `pi/app/ui/index.html` — wizard modal markup |

### F5: Camera Spatial Mapping
| Action | File |
|--------|------|
| ADD | `pi/config/spatial_map.json` — LED position data |
| ADD | `pi/app/mapping/spatial.py` — spatial map loader |
| MODIFY | `pi/app/api/setup_routes.py` — mapping endpoints |
| MODIFY | `pi/app/ui/static/js/app.js` — mapping wizard UI |
| ADD | `pi/tests/test_spatial_mapping.py` |

### F6: Web Simulator
| Action | File |
|--------|------|
| MODIFY | `pi/app/api/server.py` — frame stream WebSocket |
| MODIFY | `pi/app/core/renderer.py` — frame broadcast hook |
| MODIFY | `pi/app/ui/static/js/app.js` — simulator canvas |
| MODIFY | `pi/app/ui/static/css/app.css` — simulator styles |
| MODIFY | `pi/app/ui/index.html` — simulator panel |

---

## Test Strategy

| Layer | Tool | Coverage |
|-------|------|----------|
| Strip config parsing | pytest | Schema validation, per-strip defaults, migration |
| Color permutation | pytest | All 6 orderings × known RGB values |
| Variable LED mapping | pytest | Mixed-length strips, padding, edge cases |
| API endpoints | pytest + httpx | CRUD strip config, auth enforcement |
| Camera detection | Manual | iPhone Safari, controlled lighting |
| Spatial mapping | pytest + manual | Position parsing, normalization; camera manual |
| Effect rendering | pytest | Smoke test: each effect returns correct shape |
| Simulator streaming | Manual | WebSocket frame receipt, canvas rendering |
| UI tooltips | Manual | Every button has visible tooltip on hover/long-press |

**Regression**: All 120 existing tests must continue to pass at every phase gate.

---

## Out of Scope

- Live effect coding (Pixel Blaze style) — future, after simulator is stable
- RGBW (SK6812) 4-channel support — noted in schema, not implemented
- DotStar/APA102 (SPI protocol) — incompatible with OctoWS2811
- Teensy firmware changes — not needed for Phase 1–2
- Multi-device support — single pillar only
- Playlist/scheduling features
