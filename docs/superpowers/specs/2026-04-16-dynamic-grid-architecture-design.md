# Dynamic Grid Architecture Redesign

## Goal

Replace all hardcoded LED geometry (10 strips, 172 LEDs, 5 channels, 344 per channel) with a fully dynamic pixel map. Any arrangement of LED strips maps to an arbitrary rectangular grid. Effects render to whatever grid dimensions the pixel map defines. Teensy is configured at runtime from the pixel map — no reflash needed.

## Architecture

The **pixel map** (`pi/config/pixel_map.yaml`) is the single source of truth for all LED geometry. It replaces `hardware.yaml` geometry sections, `installation.yaml`, and the `cylinder.py` legacy mapper. From it, the system derives grid dimensions, forward/reverse lookup tables, per-segment color order, and Teensy output configuration.

**Data flow:**
```
pixel_map.yaml
  → compile at startup (or on change via Setup UI)
  → forward LUT: grid[x][y] → (strip_id, led_index)
  → reverse LUT: (strip_id, led_index) → (x, y, color_order)
  → grid dimensions: width = max(x)+1, height = max(y)+1
  → teensy config: active outputs, LEDs per output
```

**Render pipeline:**
```
Effect.render(t, state) → frame (W*scale × H*scale × 3)
  → downsample to (W × H × 3)        [only if RENDER_SCALE > 1]
  → brightness / gamma
  → pack_frame(frame, reverse_lut)    → output buffer (per-output LED data)
  → serialize → USB → Teensy
```

No supersampling step, no `INTERNAL_WIDTH`, no downsample-to-10, unless an individual effect declares `RENDER_SCALE > 1`.

## Pixel Map Data Model

File: `pi/config/pixel_map.yaml`

```yaml
schema_version: 1
origin: bottom_left          # bottom_left (default) or top_left

teensy:
  outputs: 8                 # OctoWS2811 pins available
  max_leds_per_output: 1200  # electrical maximum per pin

strips:
  - id: 0
    output: 0                # Teensy output pin (0-7)
    output_offset: 0         # LED offset on that output (for daisy-chaining)
    total_leds: 300
    segments:                # color order regions within the strip
      - range: [0, 299]
        color_order: BGR
    scanlines:
      - start: [0, 0]
        end: [0, 99]        # 100 LEDs, column 0, going up
      - start: [1, 99]
        end: [1, 0]         # 100 LEDs, column 1, going down (S-pattern)
      - start: [2, 0]
        end: [2, 99]        # 100 LEDs, column 2, going up

  - id: 1
    output: 0
    output_offset: 300       # daisy-chained after strip 0 on same output
    total_leds: 200
    segments:
      - range: [0, 149]
        color_order: BGR
      - range: [150, 199]
        color_order: GRB     # spliced repair section
    scanlines:
      - start: [3, 0]
        end: [3, 99]
      - start: [4, 99]
        end: [4, 0]
```

### Scanline Rules

- Scanlines must be axis-aligned: horizontal (same y, x changes) or vertical (same x, y changes)
- LED assignment is sequential within the strip: first scanline gets LEDs 0..N-1, second gets N..M-1, etc.
- Sum of all scanline LED counts must equal `total_leds`
- Start and end coordinates are inclusive
- No diagonal scanlines

### Derived Values

- **Grid width**: `max(x across all scanlines) + 1`
- **Grid height**: `max(y across all scanlines) + 1`
- **Forward LUT**: `grid[x][y] → (strip_id, led_index)` or None (unmapped cell)
- **Reverse LUT**: `(strip_id, led_index) → (x, y, color_order)` — used by output packer
- **Teensy config**: for each output pin (0-7), the maximum `output_offset + total_leds` across all strips on that pin = LEDs needed on that output. Unused pins get 0.

### Validation Rules

- No two LEDs may map to the same (x, y) grid position
- LED indices within a strip must be contiguous (scanlines must cover 0..total_leds-1)
- `output_offset + total_leds` must not exceed `max_leds_per_output` (1200)
- Each segment range must be within [0, total_leds-1] with no gaps or overlaps
- Scanline coordinates must be non-negative integers
- Output pin must be in range [0, outputs-1]

### Origin Handling

- `bottom_left` (default): y=0 is the physical bottom. Effects that "grow up" (spectrum bars) increase in y. Effects that "fall down" (rain) decrease in y.
- `top_left`: y=0 is the physical top. Standard screen coordinates.
- The origin setting is stored in the pixel map and passed to effects as `state.origin` so effects can adjust direction if needed. Most effects don't care — they just render to the grid.

## Compiled Pixel Map

At startup (and on pixel map changes via the Setup UI), the YAML is compiled into an in-memory `CompiledPixelMap` object:

```python
@dataclass(frozen=True)
class CompiledPixelMap:
    width: int                    # grid columns
    height: int                   # grid rows
    origin: str                   # "bottom_left" or "top_left"
    forward_lut: np.ndarray       # (width, height, 2) → [strip_id, led_index] or [-1, -1]
    reverse_lut: list[list[tuple]]  # reverse_lut[strip_id][led_index] → (x, y, color_order_idx)
    output_config: list[int]      # LEDs per output pin [0..7]
    strips: tuple                 # frozen strip metadata
    total_mapped_leds: int        # total LEDs with grid positions
```

The `forward_lut` is a numpy array for fast lookup during raster-mode rendering (future). The `reverse_lut` is a list-of-lists for fast iteration during output packing. Both are built once and frozen.

## Output Packing

Replaces `cylinder.py`, `map_frame_compiled()`, and `serialize_channels_compiled()`.

**New module: `pi/app/mapping/packer.py`**

```python
def pack_frame(frame: np.ndarray, pixel_map: CompiledPixelMap) -> bytes:
    """Pack a (width, height, 3) rendered frame into output buffer for Teensy.

    For each mapped LED:
      1. Look up (x, y, color_order) from reverse_lut
      2. Read frame[x, y] → RGB
      3. Apply color_order swizzle → reordered bytes
      4. Write to output_buffer at correct (output_pin, output_offset + led_index)

    Returns serialized bytes ready for USB transport.
    """
```

The output buffer is shaped as contiguous bytes: output 0 LEDs first, then output 1, etc. Same COBS-framed protocol to Teensy, just dynamically sized.

### Color Order Swizzle

Each segment defines a color order (e.g., BGR, GRB). The Teensy's OctoWS2811 library expects a specific wire order. The swizzle is precomputed per-segment at compile time as a 3-element permutation index, applied during `pack_frame()` — same concept as the current `precontroller_swizzle`, just per-segment instead of per-strip.

## Teensy Protocol Changes

### New CONFIG Packet

Added to the existing COBS-framed protocol:

| Field | Type | Description |
|-------|------|-------------|
| packet_type | u8 | New type: `CONFIG` (0x10) |
| active_outputs | u8 | Number of outputs with LEDs (1-8) |
| leds_per_output | u16[8] | LED count for each output pin (0 = unused) |

**Flow:**
1. Pi connects to Teensy over USB
2. Pi sends CONFIG packet with current pixel map's output config
3. Teensy validates (total RAM check), reallocates OctoWS2811 buffers
4. Teensy responds with ACK (success) or NAK (reason: out of memory, invalid)
5. Pi starts sending FRAME packets sized to match the config
6. Teensy stores config in EEPROM for power-cycle persistence (Pi re-sends on reconnect anyway)

**Frame packet sizing:**
- Current: fixed `5 * 344 * 3 = 5160` bytes per frame
- New: `sum(leds_per_output[i] * 3)` bytes per frame (dynamic)
- Teensy validates frame size against current config, drops mismatched frames

### Backward Compatibility

The Teensy firmware should handle both:
- Old fixed-size frames (if no CONFIG received, use EEPROM config or defaults)
- New CONFIG + dynamic frames

This allows incremental rollout — update Pi first, Teensy picks up the config.

## Effect Migration

~50 effects need mechanical changes. No algorithm changes.

### What Changes

1. **Remove `from ..mapping.cylinder import N`** from every effect file
2. **Remove hardcoded defaults** — `width=10, height=172` → `width, height` (required params, no defaults)
3. **Replace `N` with `self.height`** everywhere
4. **Remove `NATIVE_WIDTH`** — effects render at grid dimensions
5. **`_resample_16_to_10()` → `_resample_bins(n_bins, target_width)`** — dynamic FFT bin resampling for spectrum effects
6. **`LEDBuffer(cols=10, rows=172)` → `LEDBuffer(cols, rows)`** — required params
7. **Add `RENDER_SCALE` class attribute** — default 1 in base class, effects set higher for supersampling
8. **Diagnostic patterns** — remove hardcoded 5-channel/344-LED assumptions, use pixel map config

### What Stays the Same

- Effect render signature: `render(t, state) → np.ndarray(width, height, 3)`
- All effect algorithms (fire, rain, plasma, noise, etc.)
- Palette system, audio adapter, param handling
- Animation switcher (passes grid dimensions to child effects)

### RENDER_SCALE

Per-effect class attribute controlling optional supersampling:

```python
class BaseEffect:
    RENDER_SCALE = 1  # default: no supersampling

class RainbowRotate(BaseEffect):
    RENDER_SCALE = 4  # smooth gradients benefit from 4x

class Fireplace(BaseEffect):
    RENDER_SCALE = 1  # already expensive
```

Renderer passes `width * RENDER_SCALE, height * RENDER_SCALE` to the effect constructor. After render, downsamples to grid dimensions via area averaging. Only incurs cost for effects that opt in.

## Deleted Code

| File/Module | Reason |
|-------------|--------|
| `pi/app/mapping/cylinder.py` | Legacy hardcoded serpentine mapper. Replaced by pixel map + packer. |
| `pi/app/mapping/runtime_mapper.py` | Plan-based mapper. Replaced by packer.py. |
| `pi/app/mapping/runtime_plan.py` | Compiled output plan. Replaced by CompiledPixelMap. |
| `pi/app/config/installation.py` | Strip installation config. Replaced by pixel_map.yaml. |
| `pi/app/hardware_constants.py` | Hardcoded geometry constants. Grid dimensions now derived from pixel map. |
| `pi/config/hardware.yaml` | Geometry sections replaced by pixel_map.yaml. Non-geometry settings (`octo_pins`, `signal_family`, `controller_wire_order`) move to the `teensy:` section of pixel_map.yaml since they describe the Teensy's electrical configuration. |

## New Files

| File | Purpose |
|------|---------|
| `pi/app/config/pixel_map.py` | Load, validate, compile pixel_map.yaml → CompiledPixelMap |
| `pi/app/mapping/packer.py` | pack_frame(): rendered grid → output buffer with color order swizzle |
| `pi/app/api/routes/pixel_map.py` | CRUD API for pixel map: strips, scanlines, segments, single-pixel edits |

## Modified Files

| File | Change |
|------|--------|
| `pi/app/core/renderer.py` | Use CompiledPixelMap for dimensions. Remove hardcoded (10, 172) arrays. Remove downsample_width. Call pack_frame() instead of map_frame_compiled(). Handle RENDER_SCALE. |
| `pi/app/effects/base.py` | Remove `N` import. No default width/height. Add `RENDER_SCALE = 1`. |
| `pi/app/effects/generative.py` | Remove `N` import. Use self.height everywhere. |
| `pi/app/effects/audio_reactive.py` | Remove `N` import. Dynamic bin resampling. Remove NATIVE_WIDTH. |
| `pi/app/effects/imported/*.py` | Remove `N` import from all 5 files. Remove hardcoded defaults. |
| `pi/app/effects/switcher.py` | Remove hardcoded height=172. |
| `pi/app/effects/engine/buffer.py` | Remove default cols/rows. Required params. |
| `pi/app/diagnostics/patterns.py` | Remove hardcoded 5-channel/344-LED assumptions. Use pixel map. |
| `pi/app/preview/service.py` | Remove `N` import. Use pixel map dimensions. Remove downsample. |
| `pi/app/transport/usb.py` | Remove default channel/LED params. Size from pixel map config. |
| `pi/app/api/routes/preview.py` | Live preview uses pixel map dimensions for frame header. |
| `pi/app/api/routes/setup.py` | Strip CRUD replaced by pixel map routes. |
| `pi/app/api/server.py` | Register pixel_map router, remove old setup router references. |
| `pi/app/main.py` | Load pixel map at startup. Send CONFIG to Teensy. Pass dimensions to renderer. |
| `pi/app/ui/static/index.html` | New Setup screen: strip/scanline editor, grid preview, Teensy config status. |
| `pi/app/ui/static/js/app.js` | Setup UI logic: scanline editor, grid preview canvas, single-pixel edit, validation display. |
| `pi/app/ui/static/css/app.css` | Setup panel styles. |
| `pi/app/models/protocol.py` | Add CONFIG packet type (0x10) with payload schema. |
| `teensy/firmware/src/main.cpp` | Handle CONFIG packet. Dynamic OctoWS2811 buffer allocation. EEPROM persistence. |
| `teensy/firmware/include/config.h` | Remove hardcoded constants. Default config for pre-CONFIG-packet compatibility. |

## Setup UI

The Setup screen is the single place to define LED layout.

### Strip Management
- Add/remove strips
- Per-strip: ID, output pin (0-7), output offset, total LEDs
- Per-strip segment table: range + color order

### Scanline Editor
- Per-strip list of scanlines: start (x,y), end (x,y)
- Axis-aligned only (horizontal or vertical)
- LED count auto-calculated from coordinates
- Running total validated against strip's total_leds

### Single-Pixel Edit
- Click any grid cell in the preview
- Assign to strip#/LED# manually
- For edge cases and corrections

### Grid Preview
- Live canvas showing the grid
- Pixels colored by strip (each strip a different color)
- Unmapped cells shown as empty/dark
- Validation errors highlighted (overlaps, gaps)

### Teensy Config
- Auto-derived from pixel map (which strips on which outputs)
- Shows connection status, ACK/NAK feedback
- "Push Config" button (or auto-push on apply)

### Origin Selector
- Bottom-left (default) or top-left
- Dropdown in setup config section

## Integration with Vision Auto-Mapper

The vision auto-mapping system (specced separately in `2026-04-16-vision-auto-mapping-design.md`) writes into the same pixel map format. The auto-mapper discovers strips and their grid positions, then generates scanlines that are stored in `pixel_map.yaml`. Manual and automated setup produce identical data structures — the pixel map doesn't care how it was populated.

## Non-Goals

- Non-rectangular grids (future — current grid is always width × height rectangle)
- Diagonal scanlines (LEDs must follow horizontal or vertical lines)
- Per-LED brightness (per-strip brightness was removed; global brightness engine remains)
- Runtime grid resize without re-entering setup (grid changes require setup screen)
