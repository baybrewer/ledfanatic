# Strip Mapping — Live Strip-to-Channel Configuration

## Goal

Replace the channel-only setup UI and hardcoded legacy mapper with a live strip mapping table. Each strip is assigned to a channel with direction, offset, LED count, and color order. Changes apply immediately. A test button per strip lights it up for physical identification.

## Current State

- Legacy mapper (`cylinder.py`) hardcodes 10 strips → 5 channels, serpentine, BGR. Works but unconfigurable.
- Channel-only model (`ChannelConfig/ChannelInstallation`) saves color order + LED count per channel but doesn't connect to the renderer (output plan disabled because it broke mapping).
- Compiled plan mapper (`runtime_mapper.py`) already handles per-strip direction, color order swizzle, and channel placement — just needs correct strip data.
- `compile_output_plan` in `runtime_plan.py` already works with strip-oriented data.

## Design

### Data Model

`installation.yaml` stores a list of strips:

```yaml
schema_version: 3
strips:
  - id: 0
    channel: 0
    offset: 0
    direction: bottom_to_top
    led_count: 172
    color_order: BGR
  - id: 1
    channel: 0
    offset: 172
    direction: top_to_bottom
    led_count: 172
    color_order: BGR
  # ... more strips
```

Each strip has:
- `id` — auto-assigned sequential integer (0-based)
- `channel` — OctoWS2811 output channel (0–7)
- `offset` — LED offset within the channel (where this strip's data starts in the channel buffer)
- `direction` — `bottom_to_top` or `top_to_bottom`
- `led_count` — number of LEDs (1–1100)
- `color_order` — RGB, RBG, GRB, GBR, BRG, BGR

Removed from old model (YAGNI): chipset, label, enabled, logical_order, output_slot, geometry_mode, spatial_profile_id, profile_name.

`logical_order` is implicit from the strip's position in the list (strip index = logical column).

### Migration

- Schema v3 (this format): load directly
- Schema v2 (channel-only): synthesize 2 strips per active channel (paired serpentine, offset 0 and 172) to match legacy layout
- Schema v1 or missing strips key (old strip format): map `output_channel` → `channel`, `output_slot * physical_leds_per_strip` → `offset`, carry over direction/led_count/color_order
- No file: synthesize default 10 strips matching legacy layout

### Validation

Per-strip:
- `channel` must be 0–7
- `offset` must be >= 0
- `led_count` must be 1–1100
- `offset + led_count` must not exceed max LEDs per channel (1100)
- `color_order` must be valid
- `direction` must be valid

Cross-strip:
- No two strips on the same channel may have overlapping LED ranges (`offset` to `offset + led_count`)

### API

**`GET /api/setup/strips`**
Returns current strip list:
```json
{
  "strips": [
    {"id": 0, "channel": 0, "offset": 0, "direction": "bottom_to_top", "led_count": 172, "color_order": "BGR"},
    ...
  ]
}
```

**`POST /api/setup/strips/{id}`** (auth required)
Update one strip. Partial updates allowed:
```json
{"channel": 1, "direction": "top_to_bottom"}
```
Validates, recompiles output plan, hot-applies, persists. Returns full strip list.

**`POST /api/setup/strips`** (auth required)
Add a new strip:
```json
{"channel": 0, "offset": 344, "direction": "bottom_to_top", "led_count": 172, "color_order": "BGR"}
```
Auto-assigns next `id`. Returns full strip list.

**`DELETE /api/setup/strips/{id}`** (auth required)
Remove a strip. Re-numbers remaining strip IDs sequentially. Returns full strip list.

**`POST /api/setup/strips/{id}/test`** (auth required)
Activates a test pattern on just that strip — a gradient (e.g., red at bottom, blue at top) so the user can physically identify which strip it is. Clears after 5 seconds or when another test is triggered.

### Backend Flow

On each strip change (POST/DELETE):
1. Validate strip config + cross-strip overlap check
2. Update in-memory installation
3. Compile output plan via existing `compile_output_plan()` (the original strip-oriented function, not `compile_channel_plan`)
4. Hot-apply: `renderer.apply_output_plan(plan)`
5. Persist to `installation.yaml`
6. Return full strip list

### Compiled Plan Integration

The existing `compile_output_plan()` in `runtime_plan.py` takes an installation with `.strips` (list of StripConfig-like objects). The new `StripMapping` dataclass needs to be compatible — either reuse the existing function with adapted data, or update it to work with the new simpler strip model.

Key fields the compiler needs from each strip:
- `output_channel` → `channel`
- `output_offset` → `offset`
- `direction`
- `installed_led_count` → `led_count`
- `color_order`
- `logical_order` → strip's index in the list
- `enabled` → always True (removed strips don't exist)

### UI (Setup Tab)

A table of strips. Each row:

| Strip | Channel | Offset | Direction | LEDs | Color Order | Test |
|-------|---------|--------|-----------|------|-------------|------|
| 0 | [0-7] | [num] | [↑/↓] | [num] | [dropdown] | [btn] |
| 1 | [0-7] | [num] | [↑/↓] | [num] | [dropdown] | [btn] |
| ... | | | | | | |

- Channel: dropdown 0–7
- Offset: number input
- Direction: dropdown (↑ Bottom→Top / ↓ Top→Bottom)
- LEDs: number input (1–1100)
- Color Order: dropdown (RGB, RBG, GRB, GBR, BRG, BGR)
- Test: button, lights up that strip with identifying gradient

Below the table:
- "Add Strip" button
- Validation errors shown inline

Each change debounced 300ms → POST → immediate apply.

### Test Pattern

When "Test" is clicked for strip N:
- Renderer temporarily overrides the output with a test frame where strip N shows a red→blue vertical gradient and all other strips are black
- Auto-clears after 5 seconds, restoring the current effect
- If another test is clicked, it replaces the current one

Implementation: the setup route sets a `_test_strip` flag on the renderer (or a dedicated test pattern service). The render loop checks this flag and injects the test frame before mapping.

### Files Changed

- `pi/app/config/installation.py` — rewrite again: `StripMapping` dataclass, `StripInstallation`, migration v1→v3 and v2→v3, load/save
- `pi/app/mapping/runtime_plan.py` — update `compile_output_plan` or add adapter to work with new strip model
- `pi/app/api/routes/setup.py` — rewrite: CRUD strip endpoints + test pattern
- `pi/app/api/schemas.py` — `StripConfigRequest`
- `pi/app/main.py` — re-enable `apply_output_plan`, remove channel plan
- `pi/app/core/renderer.py` — add test strip pattern support
- `pi/app/ui/static/index.html` — strip table HTML
- `pi/app/ui/static/js/app.js` — strip table JS
- `pi/app/ui/static/css/app.css` — strip table styles
- `pi/tests/test_strip_mapping.py` — tests for new model + migration

### Files Removed

- `compile_channel_plan` from `runtime_plan.py` (unused)

## Not In Scope

- Pixelblaze-style coordinate mapping (separate project)
- 3D spatial coordinates per LED
- Visual strip layout editor (drag-and-drop)
- Chipset selection per strip
