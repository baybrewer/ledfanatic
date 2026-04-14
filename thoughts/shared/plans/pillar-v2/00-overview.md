# Pillar V2: Setup, Simulator & Effects — Master Plan

## Project Summary

Major feature expansion of the LED pillar controller. Six features, three
implementation phases, designed to be implemented sequentially with clear
contracts between components.

**Goal**: Make the pillar self-configuring, visually previewable, and packed
with effects — all controllable from a phone browser.

---

## Repo Architecture Reference

Before implementing, understand the current architecture:

| Layer | Pattern | Key Files |
|-------|---------|-----------|
| API routes | `pi/app/api/routes/<name>.py` with `create_router(deps, require_auth)` factory | 8 existing modules |
| Schemas | Centralized Pydantic models | `pi/app/api/schemas.py` |
| Dependency injection | `AppDeps` dataclass | `pi/app/api/deps.py` |
| App factory | Mounts routers, starts broadcast | `pi/app/api/server.py` |
| Config SSOT | Hardware geometry | `pi/config/hardware.yaml` |
| Constants | Loaded from YAML at import time | `pi/app/hardware_constants.py` |
| Mapping | Hardcoded serpentine (backlog: config-driven) | `pi/app/mapping/cylinder.py` |
| Effects | Base class + registry dicts | `pi/app/effects/{base,generative,audio_reactive}.py` |
| Diagnostics | Diagnostic effect patterns | `pi/app/diagnostics/patterns.py` |
| Teensy codegen | Syncs hardware.yaml → config.h | `pi/scripts/generate_teensy_config.py` |
| Contracts | Canonical shipped-behavior reference | `docs/current-contracts.md` |
| Consolidated spec | Planning docs mirror | `docs/MASTER_SPEC.md` |

**Current color order**: `hardware.yaml` and `config.h` say **BGR**.
`main.cpp` OctoWS2811 init is `WS2811_GRB`. This is a pre-existing discrepancy
that F1 must resolve (see `01-strip-configuration.md`).

**Current test count**: ~219 tests across 9 test files.

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

**Cross-references to existing docs:**

- `docs/current-contracts.md` §6 backlog items: "Config-driven mapping" and
  "Runtime color-order configuration via CONFIG packet" are already tracked.
  F1 implements both.
- `docs/MASTER_SPEC.md` — must be updated to include V2 plans after implementation.

---

## Implementation Phases

### Phase 1: Foundation (F1 + F2 + F3)

No dependencies between these three — can be developed in parallel.

- **F1** establishes per-strip config infrastructure (schema, API, UI setup page)
- **F2** is a UI-only pass (tooltips, labels, minimal backend: add descriptions to scene list)
- **F3** adds new effects from the user-provided Python file

**Gate**: All existing ~219 tests pass. New tests for strip config and
imported effects. UI is usable without reading source code.

### Phase 2: Smart Setup (F4 + F5)

Both depend on F1 (per-strip config must exist to write to).

- **F4** uses phone camera to auto-detect RGB color order per strip
- **F5** uses phone camera to build 2D spatial map of LED positions

**Gate**: Camera features work on iPhone Safari over local WiFi. Results
persist to config files.

### Phase 3: Preview & Live (F6)

Depends on F3 (needs effects to preview).

- **F6** streams rendered frames to a browser canvas for live preview
- Future: live effect coding (Pixel Blaze style) — NOT in this plan

**Gate**: Simulator shows real-time preview of any active effect in browser.

---

## Shared Architecture Decisions

### 1. Color reordering happens on the Pi (not Teensy)

**Why**: OctoWS2811 applies a single global color order (currently `WS2811_GRB`
in `main.cpp:16`) to all 8 outputs. Per-strip reordering at the Teensy level
would require either modifying OctoWS2811 internals or post-processing the DMA
buffer — both fragile.

**Current state**: `hardware.yaml` and `config.h` record the system color order
as **BGR** (the physical strips' native wire order). The OctoWS2811 init flag
`WS2811_GRB` in `main.cpp` controls how `setPixel(i, r, g, b)` arranges bytes
for the wire. F1 must resolve and document this relationship — see
`01-strip-configuration.md` for the full analysis and permutation table.

**How**: The mapping layer (`cylinder.py`) already processes each strip
independently. Adding a per-strip color channel permutation there is a single
NumPy index operation per strip, negligible at 60fps.

**Data flow**:
```
Effect (RGB) → downsample → brightness/gamma → map_frame_fast()
  └─ for each strip:
       1. spatial mapping (serpentine, truncation for short strips)
       2. color reorder (apply strip's compensation permutation)
  → serialize → COBS frame → Teensy → OctoWS2811 (WS2811_GRB) → wire
```

### 2. Teensy firmware stays unchanged (Phase 1–2)

No protocol changes needed. Frame payload format is identical — the Pi just
sends different byte orderings per strip within the same frame structure.
Variable LED counts handled by zero-padding shorter strips.

If max LEDs per channel increases beyond 344, run
`pi/scripts/generate_teensy_config.py` to update `config.h` and recompile
the Teensy firmware.

### 3. Config file strategy

| File | Role | Mutable at runtime? | Write strategy |
|------|------|---------------------|----------------|
| `hardware.yaml` | Physical layout SSOT (strips, channels, wiring) | Yes (via setup API) | Atomic write via tempfile + rename |
| `spatial_map.json` | 2D LED positions from camera mapping (F5) | Yes (via mapping API) | Atomic write via tempfile + rename |
| `effects.yaml` | Effect defaults and palettes | No (edit manually) | — |
| `system.yaml` | Auth, network, brightness, transport | Partially (brightness) | — |
| `state.json` | Runtime state (scene, presets, FPS, schema_version) | Yes (auto-saved) | Atomic write (existing pattern) |

**Note on YAML runtime writes**: `hardware.yaml` is not normally written at
runtime. The setup API (F1) writes it during explicit user-initiated
configuration. This is acceptable because it's infrequent and user-triggered,
not continuous. The write uses atomic temp-file strategy matching `state.json`.

### 4. UI architecture: subpages via panel switching

The current UI uses tabs (`Live`, `Effects`, `Media`, `Audio`, `Diag`, `System`).
The Setup page is a **sub-panel within the System tab**, toggled by a button.
This avoids adding a 7th top-level tab to the already-full navigation bar.
F6 adds a **Sim tab** as the only new top-level tab.

```
System tab
├── System Info (existing)
├── [Setup] button → toggles Setup sub-panel
│   ├── Strip Configuration table
│   ├── [Auto-detect RGB Order] → camera wizard (F4)
│   └── [Map LED Positions] → camera wizard (F5)
└── System Actions (existing: Restart, Reboot)
```

The camera wizards (F4, F5) share a **reusable `CameraWizard` JS component**
that handles `getUserMedia`, frame capture, and cleanup. Each wizard provides
its own detection/mapping logic as a callback.

### 5. Frame streaming for simulator (F6)

The renderer already produces frames at 60fps. For the simulator, we add an
opt-in WebSocket channel that sends the **logical canvas** (10×172×3 = 5,160
bytes) at a reduced rate (10–15fps). This is ~75KB/s — trivial over local WiFi.

The browser renders this as a 2D grid (unwrapped cylinder view) using a
`<canvas>` element. No WebGL needed for 10×172 pixels.

### 6. Renderer override modes (unified)

F2 camera calibration and F6 preview both need to temporarily override the
renderer's normal output. Instead of adding separate mutable fields, use a
single unified override:

```python
class RenderOverride:
    mode: str  # "normal", "calibration", "preview"
    # calibration fields (F4/F5)
    # preview fields (F6)
```

This keeps the Renderer's state clean (SRP) and prevents conflicting overrides.

### 7. Effect registration pattern (unchanged)

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
| MODIFY | `pi/app/hardware_constants.py` — load per-strip config, expose StripConfig dataclass |
| MODIFY | `pi/app/mapping/cylinder.py` — color reorder + variable LED count |
| ADD | `pi/app/api/routes/config.py` — strip config API (create_router pattern) |
| MODIFY | `pi/app/api/schemas.py` — StripConfigUpdate, StripConfigResponse models |
| MODIFY | `pi/app/api/server.py` — mount config router |
| MODIFY | `pi/app/ui/static/js/app.js` — setup sub-panel UI |
| MODIFY | `pi/app/ui/static/css/app.css` — setup panel styles |
| MODIFY | `pi/app/ui/index.html` — setup panel markup |
| ADD | `pi/tests/test_strip_config.py` — config + mapping tests |

### F2: UI Tooltips
| Action | File |
|--------|------|
| MODIFY | `pi/app/ui/index.html` — `data-tooltip` attributes, fix button labels |
| MODIFY | `pi/app/ui/static/css/app.css` — tooltip + toast styles |
| MODIFY | `pi/app/ui/static/js/app.js` — `initTooltips()`, `showToast()`, load descriptions |
| MODIFY | `pi/app/api/routes/scenes.py` — add `description` field from Effect docstrings |

### F3: Animation Integration
| Action | File |
|--------|------|
| ADD | `pi/app/effects/<new_file>.py` — converted effects |
| MODIFY | `pi/config/effects.yaml` — defaults for new effects |
| MODIFY | `pi/app/main.py` — register new effects |
| ADD | `pi/tests/test_<new_effects>.py` — render smoke tests |

### F4: Camera RGB Detection + F5: Camera Spatial Mapping (shared)
| Action | File |
|--------|------|
| ADD | `pi/app/api/routes/setup.py` — calibration/mapping endpoints (create_router pattern) |
| MODIFY | `pi/app/api/schemas.py` — SetLedsRequest, SaveSpatialMapRequest models |
| MODIFY | `pi/app/api/server.py` — mount setup router |
| MODIFY | `pi/app/core/renderer.py` — RenderOverride for calibration mode |
| MODIFY | `pi/app/ui/static/js/app.js` — shared CameraWizard + detection/mapping logic |
| MODIFY | `pi/app/ui/static/css/app.css` — wizard/modal styles |
| MODIFY | `pi/app/ui/index.html` — wizard modal markup |
| ADD | `pi/config/spatial_map.json` — LED position data (F5) |
| ADD | `pi/app/mapping/spatial.py` — spatial map loader (F5) |
| ADD | `pi/tests/test_spatial_mapping.py` |

### F6: Web Simulator
| Action | File |
|--------|------|
| MODIFY | `pi/app/api/routes/ws.py` — frame subscriber management, binary messages |
| MODIFY | `pi/app/core/renderer.py` — frame_callback hook, preview via RenderOverride |
| ADD | `pi/app/api/routes/simulator.py` — preview endpoints (create_router pattern) |
| MODIFY | `pi/app/api/schemas.py` — PreviewRequest model |
| MODIFY | `pi/app/api/server.py` — mount simulator router |
| MODIFY | `pi/app/ui/static/js/app.js` — simulator canvas + WS binary handling |
| MODIFY | `pi/app/ui/static/css/app.css` — simulator styles |
| MODIFY | `pi/app/ui/index.html` — Sim tab + panel |

---

## Rollout Steps (Per Phase)

### After each phase:

1. Run full test suite: `PYTHONPATH=. pytest tests/ -v` — all ~219+ tests pass
2. Update `docs/current-contracts.md`:
   - New routes added to §1 Route Table
   - New config files added to §3 Config Precedence
   - New WebSocket protocol extensions documented in §2 (F6)
   - Hardware geometry section updated for per-strip config (F1)
   - Backlog items resolved: mark as implemented
3. Update `docs/MASTER_SPEC.md` — consolidate V2 plans
4. If max LEDs per channel changed: run `pi/scripts/generate_teensy_config.py`,
   recompile Teensy firmware, update `config.h` in repo
5. Update `pi/scripts/deploy.sh` rsync if new config files added (spatial_map.json)

---

## Test Strategy

| Layer | Tool | Coverage |
|-------|------|----------|
| Strip config parsing | pytest | Schema validation, per-strip defaults, legacy migration |
| Color permutation | pytest | All 6 orderings × known RGB values, verified against OctoWS2811 behavior |
| Variable LED mapping | pytest | Mixed-length strips, padding, edge cases |
| API endpoints | pytest + httpx | CRUD strip config, auth enforcement |
| Camera detection | Manual | iPhone Safari, controlled lighting |
| Spatial mapping | pytest + manual | Position parsing, normalization; camera manual |
| Effect rendering | pytest | Smoke test: each effect returns correct shape |
| Simulator streaming | Manual | WebSocket frame receipt, canvas rendering |
| UI tooltips | Manual | Every button has visible tooltip on hover/long-press |

**Regression**: All ~219 existing tests must continue to pass at every phase gate.

---

## Out of Scope

- Live effect coding (Pixel Blaze style) — future, after simulator is stable
- RGBW (SK6812) 4-channel support — noted in schema, not implemented
- DotStar/APA102 (SPI protocol) — incompatible with OctoWS2811
- Teensy firmware changes — not needed for Phase 1–2 (unless max channel length changes)
- Multi-device support — single pillar only
- Playlist/scheduling features
