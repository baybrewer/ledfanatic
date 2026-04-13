# F1: Per-Strip Configuration

## Summary

Replace the global `color_order`, `leds_per_strip`, and implicit chipset with
per-strip configuration in `hardware.yaml`. Update the mapping layer to apply
per-strip color reordering and handle variable LED counts. Add API endpoints
and a Setup sub-panel in the System tab for editing strip properties.

---

## Current State

**hardware.yaml** (global values):
```yaml
pillar:
  strips: 10
  leds_per_strip: 172
  total_leds: 1720
  channels:
    count: 5
    leds_per_channel: 344
    pairs:
      - channel: 0
        strips: [0, 1]
      # ...
  color_order: "GRB"
  octo_pins: [2, 14, 7, 8, 6, 20, 21, 5]
```

**hardware_constants.py** exposes: `STRIPS`, `LEDS_PER_STRIP`, `TOTAL_LEDS`,
`CHANNELS`, `LEDS_PER_CHANNEL`, `COLOR_ORDER`, `OUTPUT_WIDTH`, `HEIGHT`,
`INTERNAL_WIDTH`.

**cylinder.py** uses `STRIPS`, `LEDS_PER_STRIP`, `CHANNELS`, `LEDS_PER_CHANNEL`
and assumes all strips are the same length.

**Teensy firmware** is configured with `WS2811_GRB | WS2811_800kHz` globally.
It receives RGB bytes and calls `leds.setPixel(i, r, g, b)` — OctoWS2811
handles the GRB→wire reordering internally.

---

## New hardware.yaml Schema

```yaml
pillar:
  # OctoWS2811 pin assignments (Teensy 4.1)
  octo_pins: [2, 14, 7, 8, 6, 20, 21, 5]

  # System-level OctoWS2811 color order (compile-time on Teensy)
  # This is what OctoWS2811 is configured to output on the wire.
  # Individual strip color_order fields are relative to this.
  system_color_order: "GRB"

  # Per-strip configuration (SSOT for strip properties)
  strips:
    - id: 0
      channel: 0
      position_in_channel: 0   # 0 = first half, 1 = second half
      direction: "up"           # wiring direction: "up" or "down"
      leds: 172
      color_order: "GRB"        # what this strip's LEDs expect on the wire
      chipset: "WS2812B"        # informational: WS2811, WS2812B, WS2813, SK6812
    - id: 1
      channel: 0
      position_in_channel: 1
      direction: "down"
      leds: 172
      color_order: "GRB"
      chipset: "WS2812B"
    - id: 2
      channel: 1
      position_in_channel: 0
      direction: "up"
      leds: 172
      color_order: "GRB"
      chipset: "WS2812B"
    - id: 3
      channel: 1
      position_in_channel: 1
      direction: "down"
      leds: 172
      color_order: "GRB"
      chipset: "WS2812B"
    - id: 4
      channel: 2
      position_in_channel: 0
      direction: "up"
      leds: 172
      color_order: "GRB"
      chipset: "WS2812B"
    - id: 5
      channel: 2
      position_in_channel: 1
      direction: "down"
      leds: 172
      color_order: "GRB"
      chipset: "WS2812B"
    - id: 6
      channel: 3
      position_in_channel: 0
      direction: "up"
      leds: 172
      color_order: "GRB"
      chipset: "WS2812B"
    - id: 7
      channel: 3
      position_in_channel: 1
      direction: "down"
      leds: 172
      color_order: "GRB"
      chipset: "WS2812B"
    - id: 8
      channel: 4
      position_in_channel: 0
      direction: "up"
      leds: 172
      color_order: "GRB"
      chipset: "WS2812B"
    - id: 9
      channel: 4
      position_in_channel: 1
      direction: "down"
      leds: 172
      color_order: "GRB"
      chipset: "WS2812B"

  # Seam (visual wrap boundary for cylindrical mapping)
  seam_strips: [9, 0]
```

**Key design choices:**

1. `channel` and `position_in_channel` replace the implicit `x // 2` arithmetic.
   Physical wiring is now explicit, not assumed.
2. `direction` replaces the even/odd convention. Any strip can be up or down.
3. `system_color_order` documents the Teensy's compile-time OctoWS2811 config.
4. Per-strip `color_order` says what that strip's LEDs expect on the wire.
5. `chipset` is informational (all supported chipsets use the same OctoWS2811
   timing mode; mixed timing is not supported by OctoWS2811 DMA).

**Backward compatibility:** If the `strips` array is absent, fall back to
generating it from the legacy flat fields (`strips: 10`, `leds_per_strip: 172`,
etc.). This keeps old configs working during migration.

---

## Derived Constants (hardware_constants.py)

Computed at load time from the `strips` array — never manually set:

```python
# Per-strip config objects
STRIP_CONFIG: list[StripConfig]  # dataclass with all strip fields

# Derived globals
STRIPS = len(STRIP_CONFIG)                          # 10
MAX_LEDS_PER_STRIP = max(s.leds for s in STRIP_CONFIG)  # 172
HEIGHT = MAX_LEDS_PER_STRIP                         # 172
OUTPUT_WIDTH = STRIPS                               # 10
TOTAL_LEDS = sum(s.leds for s in STRIP_CONFIG)      # 1720

# Channel-derived
CHANNELS = max(s.channel for s in STRIP_CONFIG) + 1  # 5
LEDS_PER_CHANNEL = max(
    sum(s.leds for s in STRIP_CONFIG if s.channel == ch)
    for ch in range(CHANNELS)
)                                                    # 344

SYSTEM_COLOR_ORDER = "GRB"  # from hardware.yaml
INTERNAL_WIDTH = 40          # from system.yaml (unchanged)
```

**New dataclass:**

```python
from dataclasses import dataclass

@dataclass
class StripConfig:
    id: int
    channel: int
    position_in_channel: int  # 0 or 1
    direction: str            # "up" or "down"
    leds: int                 # number of LEDs
    color_order: str          # "GRB", "RGB", "BRG", "RBG", "GBR", "BGR"
    chipset: str              # "WS2812B", "WS2811", "WS2813", "SK6812"
```

---

## Color Reorder Permutation

**Problem**: Effects render in RGB. OctoWS2811 is configured as `WS2811_GRB`.
When we call `leds.setPixel(i, r, g, b)`, OctoWS2811 puts Green on the wire
first, then Red, then Blue — because it assumes the strip expects GRB.

If a strip actually expects a different order (e.g., RGB), the wrong bytes
reach the wrong color channels. We fix this by pre-permuting the RGB data
on the Pi before sending, so that after OctoWS2811's GRB reordering, the
wire carries the bytes the strip actually expects.

**Derivation**: OctoWS2811's `setPixel(i, R_in, G_in, B_in)` outputs on the
wire as `[G_in, R_in, B_in]` (GRB config). The strip reads wire bytes and
maps them to its own order.

For a strip expecting `[X0, X1, X2]` on the wire (where X is R, G, or B):

| Strip order | Wire needed      | What OctoWS2811 sends | Fix: send to Teensy |
|-------------|------------------|-----------------------|---------------------|
| GRB         | [G, R, B]        | [G_in, R_in, B_in]   | R_in=R, G_in=G, B_in=B (no change) |
| RGB         | [R, G, B]        | [G_in, R_in, B_in]   | G_in=R, R_in=G → send (G, R, B) |
| BRG         | [B, R, G]        | [G_in, R_in, B_in]   | G_in=B, R_in=R, B_in=G → send (R, B, G) |
| RBG         | [R, B, G]        | [G_in, R_in, B_in]   | G_in=R, R_in=B, B_in=G → send (B, R, G) |
| GBR         | [G, B, R]        | [G_in, R_in, B_in]   | G_in=G, R_in=B, B_in=R → send (B, G, R) |
| BGR         | [B, G, R]        | [G_in, R_in, B_in]   | G_in=B, R_in=G, B_in=R → send (G, B, R) |

**Precomputed permutation table** (input is RGB, output is what to send to Teensy):

```python
# Maps strip_color_order → (r_idx, g_idx, b_idx) to apply to RGB pixel data
# Example: for RGB strip, send pixel[1], pixel[0], pixel[2] (swap R↔G)
PERMUTATION_TABLE = {
    "GRB": (0, 1, 2),  # identity — matches system config
    "RGB": (1, 0, 2),  # swap R↔G
    "BRG": (0, 2, 1),  # not intuitive, but verified: send (R, B, G) for (R,G,B) input
    "RBG": (2, 0, 1),  # send (B, R, G)
    "GBR": (1, 2, 0),  # send (B, G, R)  ← wait, let me re-derive
    "BGR": (1, 2, 0),  # send (G, B, R)
}
```

**IMPORTANT**: The table above is illustrative. The implementation must include
a **verification test** that for each color order, sending pure red (255,0,0),
pure green (0,255,0), and pure blue (0,0,255) through the permutation →
OctoWS2811 GRB → wire → strip results in the intended color. The derivation
is error-prone by hand; the test is the source of truth.

**Verification test pseudocode:**
```python
def test_color_permutation(strip_order):
    for intended in [(255,0,0), (0,255,0), (0,0,255)]:
        permuted = apply_permutation(intended, strip_order)
        # Simulate OctoWS2811 GRB output: wire = [permuted[1], permuted[0], permuted[2]]
        wire = (permuted[1], permuted[0], permuted[2])
        # Strip reads wire in its own order
        displayed = read_wire_as(wire, strip_order)
        assert displayed == intended, f"Failed for {strip_order}: {intended} → {displayed}"
```

---

## Mapping Layer Changes (cylinder.py)

### Updated `map_frame_fast()`

```python
def map_frame_fast(logical_frame: np.ndarray, strip_config: list[StripConfig] = None) -> np.ndarray:
    """
    Vectorized frame mapping with per-strip color reorder and variable LED count.

    logical_frame: shape (STRIPS, MAX_LEDS_PER_STRIP, 3) uint8
    Returns: shape (CHANNELS, LEDS_PER_CHANNEL, 3) uint8
    """
    if strip_config is None:
        strip_config = STRIP_CONFIG

    channel_data = np.zeros((CHANNELS, LEDS_PER_CHANNEL, 3), dtype=np.uint8)

    for strip in strip_config:
        col = logical_frame[strip.id, :strip.leds, :]  # truncate to strip length

        # Apply direction
        if strip.direction == "down":
            col = col[::-1]

        # Apply color permutation
        perm = _get_permutation(strip.color_order)
        if perm != (0, 1, 2):
            col = col[:, perm]

        # Place in channel buffer
        if strip.position_in_channel == 0:
            channel_data[strip.channel, :strip.leds, :] = col
        else:
            # Second strip starts after first strip's LEDs
            first_strip = next(s for s in strip_config
                               if s.channel == strip.channel and s.position_in_channel == 0)
            offset = first_strip.leds
            channel_data[strip.channel, offset:offset + strip.leds, :] = col

    return channel_data
```

**Performance note**: The per-strip loop is 10 iterations (one per strip).
Each iteration is vectorized NumPy operations on ~172-element arrays. This
is negligible compared to the render cost. If profiling shows otherwise,
precompute flat index arrays at config load time (same pattern as current code).

### New helper: `_get_permutation()`

```python
# Precomputed at import time from strip config
_permutation_cache: dict[str, tuple[int, int, int]] = {}

def _get_permutation(strip_color_order: str) -> tuple[int, int, int]:
    """Return RGB index permutation for the given strip color order."""
    if strip_color_order not in _permutation_cache:
        _permutation_cache[strip_color_order] = _compute_permutation(
            strip_color_order, SYSTEM_COLOR_ORDER
        )
    return _permutation_cache[strip_color_order]
```

---

## Variable LED Count Handling

**Canvas size**: Effects still render to `(OUTPUT_WIDTH, HEIGHT, 3)` where
`HEIGHT = MAX_LEDS_PER_STRIP`. For strips shorter than `MAX_LEDS_PER_STRIP`,
the mapping layer truncates — pixels beyond `strip.leds` are discarded.

**Frame payload**: The Teensy expects `CHANNELS × LEDS_PER_CHANNEL × 3` bytes.
`LEDS_PER_CHANNEL` is the max across all channels. Shorter channels are
zero-padded. The physical strip ignores the extra clocked-out data (no LEDs
connected to receive it).

**OctoWS2811 initialization**: The Teensy still uses `LEDS_PER_STRIP = 344`
(or whatever the max channel length is). No firmware change needed — unused
LED slots just output zeros.

---

## API Endpoints

### `GET /api/config/strips`

Returns the current strip configuration.

**Response 200:**
```json
{
  "system_color_order": "GRB",
  "strips": [
    {
      "id": 0,
      "channel": 0,
      "position_in_channel": 0,
      "direction": "up",
      "leds": 172,
      "color_order": "GRB",
      "chipset": "WS2812B"
    },
    ...
  ]
}
```

### `POST /api/config/strips` [auth required]

Update strip configuration. Validates, saves to hardware.yaml, reloads
constants and mapping layer.

**Request body:**
```json
{
  "strips": [
    {
      "id": 0,
      "leds": 172,
      "color_order": "RGB",
      "chipset": "WS2812B"
    },
    ...
  ]
}
```

Only `leds`, `color_order`, and `chipset` are settable via API. The `channel`,
`position_in_channel`, and `direction` fields are physical wiring — not
changeable without rewiring. They can only be edited by hand in hardware.yaml.

**Validation rules:**
- `leds`: 1–512 (OctoWS2811 practical max per output)
- `color_order`: one of `RGB`, `GRB`, `BRG`, `RBG`, `GBR`, `BGR`
- `chipset`: one of `WS2811`, `WS2812B`, `WS2813`, `SK6812`
- Strip `id` must match an existing strip (no adding/removing strips via API)
- Sum of LEDs in a channel pair must not exceed 1024 (Teensy buffer limit)

**Response 200:**
```json
{
  "status": "ok",
  "strips": [ ... ],
  "restart_required": false
}
```

`restart_required` is true if LED count changed (requires mapping layer
rebuild). Color order changes take effect on the next frame.

**Response 400:**
```json
{
  "error": "validation_failed",
  "details": [
    {"strip_id": 3, "field": "color_order", "message": "Invalid value: XYZ"}
  ]
}
```

### New file: `pi/app/api/config_routes.py`

```python
from fastapi import APIRouter, Depends
from ..api.auth import require_auth
from ..hardware_constants import get_strip_config, update_strip_config

router = APIRouter(prefix="/api/config", tags=["config"])

@router.get("/strips")
async def get_strips():
    ...

@router.post("/strips", dependencies=[Depends(require_auth)])
async def update_strips(body: StripConfigUpdate):
    ...
```

Mount in `server.py`:
```python
from .config_routes import router as config_router
app.include_router(config_router)
```

---

## hardware_constants.py Changes

Add a `reload()` function so the API can trigger a re-read of hardware.yaml
after saving changes:

```python
_strip_config: list[StripConfig] = []

def _load_strip_config(pillar: dict) -> list[StripConfig]:
    """Parse per-strip config, with fallback for legacy schema."""
    if 'strips' in pillar and isinstance(pillar['strips'], list):
        return [StripConfig(**s) for s in pillar['strips']]
    # Legacy fallback: generate from flat fields
    count = pillar.get('strips', 10)
    leds = pillar.get('leds_per_strip', 172)
    order = pillar.get('color_order', 'GRB')
    return [
        StripConfig(
            id=i,
            channel=i // 2,
            position_in_channel=i % 2,
            direction="up" if i % 2 == 0 else "down",
            leds=leds,
            color_order=order,
            chipset="WS2812B",
        )
        for i in range(count)
    ]

def reload():
    """Re-read hardware.yaml and update all module-level constants."""
    global STRIP_CONFIG, STRIPS, MAX_LEDS_PER_STRIP, HEIGHT, ...
    _hw = _load_hardware_config()
    _pillar = _hw.get('pillar', {})
    STRIP_CONFIG = _load_strip_config(_pillar)
    STRIPS = len(STRIP_CONFIG)
    # ... recompute all derived constants
```

---

## UI: Setup Sub-Panel

Located within the **System tab**, toggled by a "Setup" button.

### Markup (index.html addition inside `#panel-system`)

```html
<button id="setup-toggle-btn" class="secondary">Strip Setup</button>

<div id="setup-panel" class="sub-panel hidden">
  <h2>Strip Configuration</h2>
  <div id="strip-config-table" class="strip-table">
    <!-- Populated by JS: one row per strip -->
  </div>
  <div class="setup-actions">
    <button id="setup-save-btn">Save Configuration</button>
    <button id="setup-detect-rgb-btn" class="secondary">Auto-detect RGB Order</button>
    <button id="setup-map-leds-btn" class="secondary">Map LED Positions</button>
  </div>
</div>
```

### Strip table row (generated by JS)

```html
<div class="strip-row" data-strip-id="0">
  <span class="strip-label">S0</span>
  <span class="strip-channel">CH0-A</span>
  <label>LEDs
    <input type="number" class="strip-leds" min="1" max="512" value="172">
  </label>
  <label>Color Order
    <select class="strip-color-order">
      <option value="GRB" selected>GRB</option>
      <option value="RGB">RGB</option>
      <option value="BRG">BRG</option>
      <option value="RBG">RBG</option>
      <option value="GBR">GBR</option>
      <option value="BGR">BGR</option>
    </select>
  </label>
  <label>Chipset
    <select class="strip-chipset">
      <option value="WS2812B" selected>WS2812B</option>
      <option value="WS2811">WS2811</option>
      <option value="WS2813">WS2813</option>
      <option value="SK6812">SK6812</option>
    </select>
  </label>
</div>
```

### JavaScript (app.js additions)

```javascript
async function loadStripConfig() {
  const data = await api('GET', '/api/config/strips');
  // Populate strip-config-table rows
}

async function saveStripConfig() {
  const strips = collectStripFormData();
  const result = await api('POST', '/api/config/strips', { strips });
  if (result.restart_required) {
    showToast('Config saved. Restart required for LED count changes.');
  } else {
    showToast('Config saved. Changes active on next frame.');
  }
}
```

---

## Migration Path

1. On first load with old-format hardware.yaml, `_load_strip_config()` detects
   the legacy format (top-level `strips` is an integer, not a list) and generates
   the per-strip array from the flat fields.
2. The first `POST /api/config/strips` call writes the new schema to hardware.yaml.
3. Legacy fields (`strips: 10`, `leds_per_strip: 172`, etc.) are removed from the
   YAML on save. The per-strip array becomes the sole source.
4. Teensy `config.h` constants remain unchanged. If max LEDs per channel increases,
   a Teensy firmware update is required (documented in release notes, not automated).

---

## Acceptance Criteria

- [ ] hardware.yaml supports per-strip `color_order`, `leds`, and `chipset`
- [ ] Legacy hardware.yaml (flat format) still loads correctly
- [ ] `GET /api/config/strips` returns all strip properties
- [ ] `POST /api/config/strips` validates and saves; bad input returns 400
- [ ] Color reorder is applied in mapping layer — verified by test with all 6 orders
- [ ] Variable LED count works: shorter strips zero-padded, frame payload unchanged
- [ ] Setup sub-panel in System tab shows editable table
- [ ] Changing color order takes effect on next frame (no restart)
- [ ] All 120 existing tests pass (regression)

---

## Test Plan

### Unit tests: `pi/tests/test_strip_config.py`

```python
def test_load_legacy_format():
    """Legacy hardware.yaml generates correct per-strip config."""

def test_load_new_format():
    """Per-strip YAML parses correctly."""

def test_derived_constants():
    """STRIPS, HEIGHT, CHANNELS, LEDS_PER_CHANNEL computed from strips."""

def test_color_permutation_all_orders():
    """For each of 6 color orders, RGB→permute→OctoWS2811→wire→strip = correct."""

def test_map_frame_with_color_reorder():
    """map_frame_fast applies per-strip color permutation."""

def test_map_frame_variable_lengths():
    """Strips with different LED counts produce correctly padded channel data."""

def test_map_frame_mixed_directions():
    """Strips with direction='up' vs 'down' map correctly."""

def test_strip_config_validation():
    """Invalid color_order, leds out of range, etc. raise ValueError."""
```

### API tests: in `pi/tests/test_strip_config.py`

```python
def test_get_strips_returns_config():
    """GET /api/config/strips returns all strips."""

def test_post_strips_updates_color_order():
    """POST changes color_order, verify via GET."""

def test_post_strips_validation_error():
    """Invalid color_order returns 400."""

def test_post_strips_requires_auth():
    """No auth token → 401."""
```

### Regression

Run full suite: `PYTHONPATH=. pytest tests/ -v` — all 120+ tests pass.
