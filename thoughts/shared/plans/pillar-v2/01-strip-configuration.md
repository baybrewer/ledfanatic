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
    pairs: [...]
  wiring:
    even_strip_direction: "bottom_to_top"
    odd_strip_direction: "top_to_bottom"
    seam_position: [9, 0]
  color_order: "BGR"
  octo_pins: [2, 14, 7, 8, 6, 20, 21, 5]
```

**hardware_constants.py** exposes: `STRIPS`, `LEDS_PER_STRIP`, `TOTAL_LEDS`,
`CHANNELS`, `LEDS_PER_CHANNEL`, `COLOR_ORDER`, `OUTPUT_WIDTH`, `HEIGHT`,
`INTERNAL_WIDTH`.

**cylinder.py** uses `from ..hardware_constants import LEDS_PER_STRIP, STRIPS, ...`
(name imports — captured at import time, won't update on reload).

**Teensy firmware** (`main.cpp:16`):
```cpp
const int octoConfig = WS2811_GRB | WS2811_800kHz;
```
OctoWS2811 is initialized with `WS2811_GRB`. This means when
`leds.setPixel(i, r, g, b)` is called, OctoWS2811 rearranges the bytes to
put Green first on the wire: wire output = `[G, R, B]`.

**config.h:58-64**: Defines `COLOR_ORDER_BGR` as 5 and `DEFAULT_COLOR_ORDER`
as `COLOR_ORDER_BGR`. But this define is **not used** by `main.cpp` — the
OctoWS2811 init flag `WS2811_GRB` is the actual runtime behavior.

**Pre-existing SSOT conflict**: `hardware.yaml` and `config.h` say BGR (the
strips' native wire expectation), but OctoWS2811 sends bytes as GRB.
`current-contracts.md` §5 says "GRB (compile-time)". This means:
- The physical strips expect BGR on the wire
- OctoWS2811 outputs GRB on the wire
- Colors are currently wrong unless the strips happen to tolerate it, OR
  the config was changed after the contracts were written

**Resolution for F1**: The `color_order` field in hardware.yaml describes
what the strips expect. OctoWS2811's `WS2811_GRB` flag describes what it
outputs. The mapping layer must compensate for the difference. The permutation
table below derives this correctly.

---

## New hardware.yaml Schema

```yaml
pillar:
  # OctoWS2811 pin assignments (Teensy 4.1)
  octo_pins: [2, 14, 7, 8, 6, 20, 21, 5]

  # OctoWS2811 config flag — what the firmware passes to OctoWS2811 constructor.
  # This determines the byte order on the wire. WS2811_GRB means wire = [G, R, B].
  # DO NOT change without recompiling Teensy firmware.
  octo_color_config: "WS2811_GRB"

  # Per-strip configuration (SSOT for strip properties)
  strips:
    - id: 0
      channel: 0
      position_in_channel: 0   # 0 = first half, 1 = second half
      direction: "up"           # wiring direction: "up" or "down"
      leds: 172
      color_order: "BGR"        # what this strip's LEDs expect on the wire
      chipset: "WS2812B"        # informational: WS2811, WS2812B, WS2813, SK6812
    - id: 1
      channel: 0
      position_in_channel: 1
      direction: "down"
      leds: 172
      color_order: "BGR"
      chipset: "WS2812B"
    - id: 2
      channel: 1
      position_in_channel: 0
      direction: "up"
      leds: 172
      color_order: "BGR"
      chipset: "WS2812B"
    - id: 3
      channel: 1
      position_in_channel: 1
      direction: "down"
      leds: 172
      color_order: "BGR"
      chipset: "WS2812B"
    - id: 4
      channel: 2
      position_in_channel: 0
      direction: "up"
      leds: 172
      color_order: "BGR"
      chipset: "WS2812B"
    - id: 5
      channel: 2
      position_in_channel: 1
      direction: "down"
      leds: 172
      color_order: "BGR"
      chipset: "WS2812B"
    - id: 6
      channel: 3
      position_in_channel: 0
      direction: "up"
      leds: 172
      color_order: "BGR"
      chipset: "WS2812B"
    - id: 7
      channel: 3
      position_in_channel: 1
      direction: "down"
      leds: 172
      color_order: "BGR"
      chipset: "WS2812B"
    - id: 8
      channel: 4
      position_in_channel: 0
      direction: "up"
      leds: 172
      color_order: "BGR"
      chipset: "WS2812B"
    - id: 9
      channel: 4
      position_in_channel: 1
      direction: "down"
      leds: 172
      color_order: "BGR"
      chipset: "WS2812B"

  # Seam (visual wrap boundary for cylindrical mapping)
  seam_strips: [9, 0]
```

**Key design choices:**

1. `channel` and `position_in_channel` replace the implicit `x // 2` arithmetic.
   Physical wiring is now explicit, not assumed.
2. `direction` replaces the even/odd convention. Any strip can be up or down.
3. `octo_color_config` documents the Teensy's compile-time OctoWS2811 config.
4. Per-strip `color_order` says what that strip's LEDs expect on the wire.
5. `chipset` is informational (all supported chipsets use the same OctoWS2811
   timing mode; mixed timing is not supported by OctoWS2811 DMA).

**Backward compatibility:** If the `strips` key is an integer (legacy format),
fall back to generating the per-strip array from the legacy flat fields.

---

## Derived Constants (hardware_constants.py)

Computed at load time from the `strips` array — never manually set.

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
    color_order: str          # "BGR", "GRB", "RGB", etc.
    chipset: str              # "WS2812B", "WS2811", "WS2813", "SK6812"
```

**Module-level constants** (computed from strip config):
```python
STRIP_CONFIG: list[StripConfig]
STRIPS: int           # len(STRIP_CONFIG) = 10
MAX_LEDS_PER_STRIP: int  # max(s.leds) = 172
HEIGHT: int           # = MAX_LEDS_PER_STRIP
OUTPUT_WIDTH: int     # = STRIPS
TOTAL_LEDS: int       # sum(s.leds)
CHANNELS: int         # max(s.channel) + 1
LEDS_PER_CHANNEL: int # max channel sum
OCTO_COLOR_CONFIG: str  # "WS2811_GRB"
INTERNAL_WIDTH: int   # from system.yaml (unchanged)
```

**Import pattern change**: `cylinder.py` currently uses name imports
(`from ..hardware_constants import LEDS_PER_STRIP`). These capture values at
import time and won't update on reload. Two options:

- **Option A (recommended)**: Change cylinder.py to use module attribute access:
  `import ..hardware_constants as hwc` → `hwc.STRIPS`. This lets a future
  `reload()` propagate correctly.
- **Option B**: Don't support reload — require app restart for geometry changes.
  Color-order-only changes can use a separate in-memory update path.

**Recommendation**: Option A for all geometry references. Color order changes
take effect immediately (permutation is looked up per-frame from STRIP_CONFIG).
LED count changes require restart (mapping arrays must be rebuilt).

### Legacy Fallback

```python
def _load_strip_config(pillar: dict) -> list[StripConfig]:
    """Parse per-strip config, with fallback for legacy schema."""
    strips_val = pillar.get('strips', 10)
    if isinstance(strips_val, list):
        # New format: per-strip array
        return [StripConfig(**s) for s in strips_val]
    # Legacy fallback: generate from flat fields
    count = strips_val
    leds = pillar.get('leds_per_strip', 172)
    order = pillar.get('color_order', 'BGR')
    return [
        StripConfig(
            id=i, channel=i // 2, position_in_channel=i % 2,
            direction="up" if i % 2 == 0 else "down",
            leds=leds, color_order=order, chipset="WS2812B",
        )
        for i in range(count)
    ]
```

---

## Color Reorder Permutation

### The Problem

Effects render in RGB internally. The Teensy receives RGB bytes and calls
`leds.setPixel(i, r, g, b)`. OctoWS2811 (configured `WS2811_GRB`) rearranges
these so the wire carries: **[G, R, B]** (Green first, then Red, then Blue).

If a strip's LEDs expect BGR on the wire (as the current strips do), the LED
reads the wire as: `B=wire[0]=G_sent, G=wire[1]=R_sent, R=wire[2]=B_sent`.
So the LED sees `(R=B_sent, G=R_sent, B=G_sent)` — colors are scrambled
unless we pre-compensate.

### Derivation

Given:
- OctoWS2811 outputs wire bytes as `[G_in, R_in, B_in]` (WS2811_GRB config)
- A strip reads wire bytes according to its `color_order`
- We want the strip to display the intended `(R, G, B)` color

For a strip with wire order `[X0, X1, X2]`, it reads:
- `X0 = wire[0] = G_in`
- `X1 = wire[1] = R_in`
- `X2 = wire[2] = B_in`

We need `X0, X1, X2` to map to the correct R, G, B values.

### Permutation Table

For each strip `color_order`, this table shows what to send to the Teensy
(as R_in, G_in, B_in arguments) given an intended RGB pixel `(R, G, B)`:

| Strip order | Wire needed | Solution: send to Teensy as (R_in, G_in, B_in) | Permutation on RGB input |
|-------------|-------------|------------------------------------------------|--------------------------|
| GRB | [G, R, B] | R_in=R, G_in=G, B_in=B | `(0, 1, 2)` identity |
| RGB | [R, G, B] | G_in=R → swap, R_in=G → swap | `(1, 0, 2)` swap R↔G |
| BGR | [B, G, R] | G_in=B, R_in=G, B_in=R | `(1, 2, 0)` rotate |
| BRG | [B, R, G] | G_in=B, R_in=R, B_in=G | `(0, 2, 1)` swap G↔B |
| RBG | [R, B, G] | G_in=R, R_in=B, B_in=G | `(2, 0, 1)` rotate |
| GBR | [G, B, R] | G_in=G, R_in=B, B_in=R | `(2, 1, 0)` swap R↔B |

**IMPORTANT: Verification test required.** The table above is analytically
derived but must be verified with a test that simulates the full pipeline:

```python
def test_color_permutation(strip_order):
    """For each strip order, verify RGB→permute→OctoWS2811→wire→strip = correct."""
    for intended in [(255,0,0), (0,255,0), (0,0,255)]:
        r, g, b = intended
        perm = PERMUTATION_TABLE[strip_order]
        sent = (intended[perm[0]], intended[perm[1]], intended[perm[2]])
        # OctoWS2811 WS2811_GRB puts [G_in, R_in, B_in] on wire
        wire = (sent[1], sent[0], sent[2])  # [G, R, B] of sent values
        # Strip reads wire in its color_order
        ORDER_MAP = {'R': 0, 'G': 1, 'B': 2}
        displayed_r = wire[strip_order.index('R')]  # NOT right — need positional
        # ... (full derivation in test code)
        assert displayed == intended
```

The test is the source of truth. If the table and test disagree, fix the table.

### Implementation

```python
# In cylinder.py or a new pi/app/mapping/color_order.py

PERMUTATION_TABLE = {
    "GRB": (0, 1, 2),  # identity — matches OctoWS2811 config
    "RGB": (1, 0, 2),
    "BGR": (1, 2, 0),
    "BRG": (0, 2, 1),
    "RBG": (2, 0, 1),
    "GBR": (2, 1, 0),
}
```

---

## Mapping Layer Changes (cylinder.py)

### Import pattern change

```python
# OLD (name imports — won't update on reload):
from ..hardware_constants import LEDS_PER_STRIP, STRIPS, CHANNELS, LEDS_PER_CHANNEL

# NEW (module attribute access — updates if module globals change):
from .. import hardware_constants as hwc
```

All references change from `STRIPS` to `hwc.STRIPS`, etc.

### Updated `map_frame_fast()`

```python
def map_frame_fast(logical_frame: np.ndarray) -> np.ndarray:
    """
    Vectorized frame mapping with per-strip color reorder and variable LED count.

    logical_frame: shape (hwc.STRIPS, hwc.MAX_LEDS_PER_STRIP, 3) uint8
    Returns: shape (hwc.CHANNELS, hwc.LEDS_PER_CHANNEL, 3) uint8
    """
    channel_data = np.zeros((hwc.CHANNELS, hwc.LEDS_PER_CHANNEL, 3), dtype=np.uint8)

    for strip in hwc.STRIP_CONFIG:
        col = logical_frame[strip.id, :strip.leds, :]  # truncate to strip length

        # Apply direction
        if strip.direction == "down":
            col = col[::-1]

        # Apply color permutation
        perm = PERMUTATION_TABLE.get(strip.color_order, (0, 1, 2))
        if perm != (0, 1, 2):
            col = col[:, perm]

        # Place in channel buffer
        if strip.position_in_channel == 0:
            channel_data[strip.channel, :strip.leds, :] = col
        else:
            first_strip = next(s for s in hwc.STRIP_CONFIG
                               if s.channel == strip.channel and s.position_in_channel == 0)
            offset = first_strip.leds
            channel_data[strip.channel, offset:offset + strip.leds, :] = col

    return channel_data
```

---

## Variable LED Count Handling

**Canvas size**: Effects still render to `(OUTPUT_WIDTH, HEIGHT, 3)` where
`HEIGHT = MAX_LEDS_PER_STRIP`. For strips shorter than `MAX_LEDS_PER_STRIP`,
the mapping layer truncates — pixels beyond `strip.leds` are discarded.

**Frame payload**: The Teensy expects `CHANNELS × LEDS_PER_CHANNEL × 3` bytes.
Shorter channels are zero-padded. The physical strip ignores the extra
clocked-out data (no LEDs connected to receive it).

**Teensy config**: If max LEDs per channel increases beyond current 344, run
`pi/scripts/generate_teensy_config.py` to regenerate `config.h` and recompile
Teensy firmware. Document this in the UI as a warning when saving.

---

## API Endpoints

### `GET /api/config/strips`

Returns the current strip configuration.

**Response 200:**
```json
{
  "octo_color_config": "WS2811_GRB",
  "strips": [
    {
      "id": 0,
      "channel": 0,
      "position_in_channel": 0,
      "direction": "up",
      "leds": 172,
      "color_order": "BGR",
      "chipset": "WS2812B"
    }
  ]
}
```

### `POST /api/config/strips` [auth required]

Update strip configuration. Validates, saves to hardware.yaml (atomic write),
reloads constants.

**Request body:**
```json
{
  "strips": [
    {"id": 0, "leds": 172, "color_order": "RGB", "chipset": "WS2812B"}
  ]
}
```

Only `leds`, `color_order`, and `chipset` are settable via API. The `channel`,
`position_in_channel`, and `direction` fields are physical wiring — not
changeable without rewiring.

**Validation rules:**
- `leds`: 1–512 (OctoWS2811 practical max per output)
- `color_order`: one of `RGB`, `GRB`, `BRG`, `RBG`, `GBR`, `BGR`
- `chipset`: one of `WS2811`, `WS2812B`, `WS2813`, `SK6812`
- Strip `id` must match an existing strip
- Sum of LEDs in a channel pair must not exceed 1024

**Response 200:**
```json
{
  "status": "ok",
  "strips": [ ... ],
  "restart_required": false,
  "firmware_update_required": false
}
```

`restart_required` = true if LED count changed (mapping arrays must rebuild).
`firmware_update_required` = true if max_leds_per_channel increased beyond
the Teensy's compiled value.

**Response 400:**
```json
{
  "error": "validation_failed",
  "details": [{"strip_id": 3, "field": "color_order", "message": "Invalid: XYZ"}]
}
```

### Implementation: `pi/app/api/routes/config.py`

Follows the repo's `create_router(deps, require_auth)` factory pattern:

```python
from fastapi import APIRouter, Depends
from ..schemas import StripConfigUpdateRequest, StripConfigResponse

def create_router(deps, require_auth) -> APIRouter:
    router = APIRouter(prefix="/api/config", tags=["config"])

    @router.get("/strips")
    async def get_strips():
        ...

    @router.post("/strips", dependencies=[Depends(require_auth)])
    async def update_strips(body: StripConfigUpdateRequest):
        ...

    return router
```

### Pydantic models in `pi/app/api/schemas.py`

```python
class StripUpdate(BaseModel):
    id: int
    leds: Optional[int] = None
    color_order: Optional[str] = None
    chipset: Optional[str] = None

class StripConfigUpdateRequest(BaseModel):
    strips: list[StripUpdate]
```

### Wiring in `server.py`

```python
from .routes import config as config_routes
app.include_router(config_routes.create_router(deps, require_auth))
```

---

## Migration Path

1. On first load with old-format hardware.yaml (top-level `strips` is an int),
   `_load_strip_config()` generates the per-strip array from flat fields.
2. The first `POST /api/config/strips` writes the new schema to hardware.yaml
   using atomic temp-file + rename.
3. Legacy flat fields are removed on save. Per-strip array becomes sole source.
4. `docs/current-contracts.md` §5 is updated to document per-strip schema.
5. If max LEDs per channel changes: run `generate_teensy_config.py`, recompile
   firmware. The API response includes `firmware_update_required` flag.
6. `current-contracts.md` §6 backlog items "Config-driven mapping" and "Runtime
   color-order configuration" are marked as implemented.

---

## Acceptance Criteria

- [ ] hardware.yaml supports per-strip `color_order`, `leds`, and `chipset`
- [ ] Legacy hardware.yaml (flat format) still loads correctly
- [ ] `GET /api/config/strips` returns all strip properties
- [ ] `POST /api/config/strips` validates and saves; bad input returns 400
- [ ] Color reorder applied in mapping — verified by test with all 6 orders
- [ ] Variable LED count works: shorter strips zero-padded, payload unchanged
- [ ] Setup sub-panel in System tab shows editable table
- [ ] Color order changes take effect on next frame (no restart)
- [ ] LED count changes flag `restart_required: true`
- [ ] cylinder.py uses module attribute access (`hwc.STRIPS`) not name imports
- [ ] `docs/current-contracts.md` updated with new routes and schema
- [ ] All ~219 existing tests pass (regression)

---

## Test Plan

### Unit tests: `pi/tests/test_strip_config.py`

```python
def test_load_legacy_format():
    """Legacy hardware.yaml (strips: 10) generates correct per-strip config."""

def test_load_new_format():
    """Per-strip YAML parses correctly into StripConfig list."""

def test_derived_constants():
    """STRIPS, HEIGHT, CHANNELS, LEDS_PER_CHANNEL computed from strips."""

def test_color_permutation_all_orders():
    """For each of 6 color orders, RGB→permute→OctoWS2811→wire→strip = correct."""

def test_color_permutation_bgr_default():
    """BGR default: verify (255,0,0) red pixel displays as red after full pipeline."""

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

def test_post_strips_flags_restart_required():
    """Changing LED count sets restart_required: true."""

def test_post_strips_flags_firmware_update():
    """Increasing max channel LEDs sets firmware_update_required: true."""
```

### Regression

Run full suite: `PYTHONPATH=. pytest tests/ -v` — all ~219+ tests pass.
