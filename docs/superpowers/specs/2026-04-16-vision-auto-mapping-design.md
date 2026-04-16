# Vision-Based Auto-Mapping Design

## Goal

Automatically discover LED strip wiring (channel, offset, direction, LED count) and physical positions by streaming live video from an iPhone camera while the Pi sequences through LEDs. Replaces manual strip-by-strip configuration in the Setup screen. Once positions are known, enable a raster rendering pipeline where effects render to a 2D canvas and the system samples at each LED's physical position — decoupling effects from strip geometry.

## Architecture

The iPhone streams live video to the Pi via SRT. The Pi controls LEDs and analyzes incoming video frames simultaneously — it knows exactly which electrical address is lit at every moment. A hierarchical 4-phase scan discovers strips quickly (~1-2 minutes per camera angle). Since the pillar is a cylinder, the user rotates and re-scans to cover all strips; the system merges results across angles.

**Hardware constants dependency:** All scan ranges, strip boundaries, and channel limits are derived from `pi/app/hardware_constants.py` (which loads from `hardware.yaml`). Key values: `PHYSICAL_LEDS_PER_STRIP`, `ELECTRICAL_LEDS_PER_OUTPUT`, `ACTIVE_OUTPUTS`. The spec never hardcodes LED counts — implementations must read these at runtime.

**Pre-existing bug (out of scope but noted):** `MAX_LEDS_PER_CHANNEL` in `installation.py` is hardcoded to 1100, which is less than the current `ELECTRICAL_LEDS_PER_OUTPUT` (1200). This should be derived from hardware constants. The auto-map implementation should use `ELECTRICAL_LEDS_PER_OUTPUT` directly, not `MAX_LEDS_PER_CHANNEL`.

## Stream Ingestion

- **Protocol:** SRT (Secure Reliable Transport) — peer-to-peer, no server needed
- **iPhone app:** Larix Broadcaster in SRT caller mode
- **Pi listener:** PyAV (`av` package, already in pyproject.toml as `video` extra) opens `srt://0.0.0.0:9000?mode=listener` via its ffmpeg backend
- **Frame format:** Decoded to numpy arrays (H×W×3 uint8 RGB) at source framerate (~30fps)
- **No new Python dependencies** — PyAV already available; blob detection uses Pillow + NumPy (same approach as existing `geometry.py`)
- **System dependency:** `ffmpeg` with SRT support (apt: `ffmpeg`, should already be present for the `video` extra)

### Connection Flow

1. User taps "Auto Map" in Setup UI
2. Pi starts SRT listener on port 9000
3. UI displays: "Point Larix at `srt://<pi-ip>:9000`" with connection status indicator
4. Once connected, Pi captures 5-10 baseline frames (all LEDs off) for background subtraction
5. Scan begins automatically once baseline is stable

## Scan Protocol

### Phase 1: Channel Probe (~10 seconds)

For each active OctoWS2811 channel (0 through `ACTIVE_OUTPUTS - 1`):

1. Light ALL LEDs on the channel full white
2. Capture frame, subtract baseline
3. If bright region detected → channel has visible strips from this camera angle
4. Light first half (LEDs 0 to `PHYSICAL_LEDS_PER_STRIP - 1`) → detect region
5. Light second half (LEDs `PHYSICAL_LEDS_PER_STRIP` to `ELECTRICAL_LEDS_PER_OUTPUT - 1`) → detect region
6. Result: list of visible `(channel, half)` pairs with approximate bounding boxes

### Phase 2: Coarse-to-Fine Segmentation (~20 seconds)

For each visible channel-half:

1. **Coarse sweep** — light every 10th LED across the channel-half range using **absolute channel indices**. Ranges derived from hardware constants: first half probes `0, 10, 20, ... PHYSICAL_LEDS_PER_STRIP - 1`; second half probes `PHYSICAL_LEDS_PER_STRIP, PHYSICAL_LEDS_PER_STRIP + 10, ...  ELECTRICAL_LEDS_PER_OUTPUT - 1`. All recorded indices (`start_index`, `end_index`, `offset`) are absolute channel indices throughout the entire pipeline — never half-relative.
2. **Cluster visible runs** — group contiguous visible LEDs by centroid continuity. A position jump (>N pixels between adjacent samples) marks a **visibility gap**. All gaps are treated equally at this stage — the system does NOT attempt to distinguish occlusion (strip wraps behind cylinder) from electrical boundaries (daisy-chain) from a single camera angle.
3. **Refine endpoints** — for each visible run, binary search within the 10-LED gaps at the start and end to find exact first/last visible LED
4. **LED count** — total visible LEDs per run. Runs are recorded as `(channel, start_index, end_index, centroids)`. Daisy-chain boundaries (where strip 1 ends and strip 2 begins on the same channel) are only committed when corroborated: either (a) a second scan angle shows the gap persists even when both sides are visible, or (b) the electrical index crosses the known `PHYSICAL_LEDS_PER_STRIP` boundary AND positions are spatially discontinuous. Until corroborated, gaps remain "unresolved" and the system presents them to the user for confirmation.
5. **Partial visibility** — if a strip wraps behind the cylinder, only the visible segment is mapped in this pass. Re-scan from another angle fills in the rest.

### Phase 3: Position Sampling (~30-60 seconds)

For each discovered strip:

1. Scan every 5th LED, recording `(led_index, centroid_x, centroid_y)`
2. Average centroid across 2-3 frames to reduce jitter
3. **Direction inference** — fit a principal axis through the sampled centroids (PCA or linear regression). Direction is determined by whether LED index increases along the principal axis in one direction or the other. This handles tilted phones and diagonal framing — not dependent on raw screen-space y.
4. Interpolate between samples for full LED coverage (strips are physically linear)

### Phase 4: Results & Merge

1. Build candidate `StripMapping` entries from discovered data
2. Build `SpatialMap` positions from samples + interpolation
3. Merge with existing draft candidates: electrical fields (channel, offset, led_count, direction) reconcile by electrical overlap; spatial positions merge per-LED non-destructively within the same viewpoint (see Merge Behavior section for full rules)
4. Present results to user in UI for review before applying

## Vision / Blob Detection

All detection uses frame differencing to handle ambient light.

### Detection Pipeline (per LED probe)

1. **Baseline frame** — captured at scan start (all LEDs off), refreshed periodically to handle ambient lighting drift
2. **Lit frame** — captured with target LED(s) on. Wait 2-3 frames for LED to stabilize (camera exposure latency)
3. **Difference image** — `abs(lit_frame - baseline)` isolates LED contribution from ambient
4. **Threshold + mask** — threshold the difference image (NumPy boolean mask where brightness > threshold), find the largest connected bright region
5. **Centroid** — brightness-weighted centroid of the masked region (NumPy coordinate averaging) gives sub-pixel position
6. **Confidence score** — based on blob brightness, size, roundness. No blob or multiple blobs = low confidence, flagged for review

### Robustness

- **Minimum brightness threshold** — rejects camera noise and ambient flicker
- **Maximum blob size** — rejects reflections or large bright areas
- **Temporal averaging** — for Phase 3 position sampling, average centroid across 2-3 frames
- **LED color** — scan uses full white for maximum contrast. Color order doesn't affect detection (tinted blob still detected). Color order detection deferred to existing `rgb_order.py` as optional follow-up

### Libraries used

- **PyAV** (`av`) — SRT stream ingestion, frame decode to numpy arrays (already a project dependency)
- **NumPy** — frame differencing (`np.abs`), thresholding, centroid calculation, interpolation
- **Pillow** — optional: JPEG encoding for WebSocket camera view, overlay drawing
- No OpenCV required — all vision operations use NumPy array math, consistent with existing `geometry.py`

## API Endpoints

All under `/api/setup/auto-map/`. All endpoints require Bearer auth (camera feed is sensitive).

| Endpoint | Method | Auth | Purpose |
|----------|--------|------|---------|
| `/start` | POST | Yes | Begin a scan pass. Body: `{"channels": [0,1,2,3,4], "new_draft": false}`. If `new_draft` is true (or no draft exists), creates a fresh draft mapping session, discarding any previous draft. If false (default) and a draft exists, adds a new scan pass to it — candidates from previous passes are preserved and merged with new results. Only one draft exists at a time; only one scan pass may run at a time. Starting a new pass aborts any active pass but keeps the draft. Returns `{"draft_id": "...", "pass_id": "...", "srt_url": "srt://<ip>:9000"}`. |
| `/stop` | POST | Yes | Abort current scan pass. Body: `{"draft_id": "...", "pass_id": "..."}`. Rejects if IDs don't match. Restores previous scene. Draft and its accumulated candidates are preserved — user can start another pass or apply/discard. |
| `/status` | GET | Yes | Returns `{"draft_id": "...", "pass_id": "...", "phase": 1-4, "progress": 0.0-1.0, "strips_found": N, "stream_connected": bool, "passes_completed": N}`. Clients check `draft_id`/`pass_id` to avoid acting on stale data. |
| `/results` | GET | Yes | Returns `{"draft_id": "...", "candidates": [...]}`. Candidates are the merged result across ALL passes in this draft. Each: `{candidate_id, channel, offset, led_count, direction, confidence, status, visible_count, source_passes: [pass_ids]}` where status is `"confirmed"`, `"unresolved"`, or `"rejected"`. `confidence` is candidate-level (0.0-1.0) based on average blob brightness and detection consistency. |
| `/resolve` | POST | Yes | Resolve ambiguous candidates. Body: `{"draft_id": "...", "resolutions": [{"candidate_id": 0, "action": "accept"}, {"candidate_id": 1, "action": "reject"}, {"candidate_id": 2, "action": "merge", "merge_with": 3}]}`. Actions: `accept` (confirm as strip), `reject` (discard), `merge` (combine any two user-selected candidates into one strip, regardless of angle — validates that they're on the same channel). Returns updated results. |
| `/apply` | POST | Yes | Merge confirmed results into existing config. Body: `{"draft_id": "..."}`. Fails if any candidates are still `"unresolved"`. **Merge semantics:** confirmed candidates are matched to existing strips in installation.yaml using electrical overlap (same channel AND overlapping index range) — the same reconciliation rule used within the draft. Matched strips are updated (offset, led_count, direction, etc. may change if the scan discovered a more complete range). Unmatched candidates are added as new strips with the next available ID. Existing strips NOT present in the draft are preserved unchanged (stable IDs). SpatialMap is **replaced wholesale** with the draft's primary-viewpoint data (not merged) — spatial coordinates from different viewpoints/drafts are not comparable. The old spatial_map.json is overwritten entirely. Transaction: (1) load existing config, (2) merge, (3) validate + compile output plan in memory, (4) stage both files to temps, (5) swap both. If any step fails before swap, no files modified. Clears draft on success. Returns the full strip list. |
| `/discard` | POST | Yes | Discard the draft and all accumulated candidates. Body: `{"draft_id": "..."}`. Restores previous scene if a pass was active. |
| `/ws` | WebSocket | Yes | Live camera frame (downscaled) + blob overlay + JSON progress. Auth via `?token=` query param (WebSocket can't send headers). |

### WebSocket Message Types

- **Binary frame:** JPEG-encoded camera image (downscaled to ~640px wide) with blob overlay circles drawn in. Prefixed with 1-byte type marker `0x01`. Sent at ~10fps to keep bandwidth manageable.
- **JSON status:** Text message with `{"phase": 2, "progress": 0.45, "message": "Channel 2, LED 85", "strips_found": [...]}`. Sent on each scan step.

### LED Control During Scan

The scan uses a dedicated `ScanEffect` (a standard effect class with `render()`), registered at startup via `renderer.register_effect('_scan', ScanEffect)` in `main.py`. The auto-mapper activates it via `renderer.activate_scene('_scan', params)`. The ScanEffect's params are updated in-place by the mapper each step: `{"mode": "channel_flood", "channel": 2}` or `{"mode": "single_led", "channel": 0, "index": 47}`. The renderer's existing brightness/gamma/output-plan pipeline applies normally.

**Scene ownership:** On scan start, the mapper snapshots:
- `renderer.state.current_scene` (scene name string, e.g. `"rainbow"` or `"media:clip1"`)
- The active effect's runtime params from `renderer.current_effect.params` (not from state_manager — media scenes and other effects may have runtime state that isn't persisted)
- `renderer.state.blackout` (bool)

The scan forces `renderer.state.blackout = False` so probe LEDs are actually visible. On scan end (complete or abort), it restores all three: `renderer.activate_scene(saved_name, saved_params, media_manager=deps.media_manager)` then `renderer.state.blackout = saved_blackout`. This handles both generative and media scenes (the `media_manager` kwarg is required for `media:` prefixed scenes per `renderer.py:178-198`). If no scene was active (`current_scene` is None), it explicitly clears the scan effect: `renderer.current_effect = None`, `renderer.state.current_scene = None`, then restores `renderer.state.blackout = saved_blackout`. This ensures the `_scan` effect is fully removed and the renderer returns to idle (black output unless blackout was off and another scene is later activated).

**Concurrency guard:** Only one scan session at a time. The `/start` endpoint checks for an active session and aborts it before starting a new one. The scan task runs as a background `asyncio.Task` — cancellation via `/stop` triggers cleanup.

### Reconnect & Abort

- **Stream disconnect during scan:** If the SRT stream drops, the scan pauses and waits up to 30 seconds for reconnection. If the stream doesn't return, the scan aborts and partial results are preserved (user can apply what was found or re-scan).
- **Abort:** `/stop` cancels the scan task, restores the previous scene, and returns partial results.

## UI (Setup Screen Additions)

- **"Auto Map" button** — opens the auto-map panel within the existing Setup screen
- **Stream connection indicator** — shows SRT URL for Larix, green when connected
- **Live camera view** — canvas showing incoming stream with detected blobs highlighted as colored circles
- **Phase progress** — current phase (1-4), progress bar, descriptive text ("Scanning channel 2, LED 85...")
- **Strip discovery table** — fills in as strips are found: strip ID, channel, offset, LED count, direction, confidence
- **Coverage indicator** — which strips are mapped vs unmapped. Encourages user to rotate for remaining strips
- **"Apply" button** — appears after scan completes. Shows preview of discovered mapping before committing
- **"Re-scan" button** — run again from a different angle to fill in missing strips

## Output Format

### 1. StripMapping (→ installation.yaml)

Each discovered strip produces a `StripMapping` entry:

| Field | Source |
|-------|--------|
| `id` | Auto-assigned (0, 1, 2...) |
| `channel` | Discovered in Phase 1 (which OctoWS2811 output) |
| `offset` | Discovered in Phase 2 (LED offset on channel) |
| `direction` | Inferred in Phase 3 (principal-axis projection of centroid trend) |
| `led_count` | Discovered in Phase 2 (boundary search) |
| `color_order` | Default "BGR" (optionally detected via rgb_order.py follow-up) |
| `brightness` | Default 1.0 (user adjusts manually) |

Feeds directly into existing pipeline: `StripInstallation` → `compile_strip_plan()` → `apply_output_plan()`.

### 2. SpatialMap (→ spatial_map.json)

Must conform to the existing `SpatialMap` / `StripGeometry` schema in `pi/app/config/spatial_map.py`:

```python
SpatialMap:
  schema_version: 2  # bumped from 1 — v2 allows null entries in positions
  profile_id: "auto_map"
  coordinate_space: "front_projection_uv"
  camera_resolution: [W, H]  # from stream
  visible_strips: [0, 1, 3, 5, ...]  # strip IDs with at least one non-null position (primary viewpoint only)
  bounds: {x_min, x_max, y_min, y_max}
  strips: [StripGeometry, ...]

StripGeometry:
  id: int  # matches StripMapping.id
  anchors: [[x,y]|null, ...]  # 5 canonical strip points (0%, 25%, 50%, 75%, 100% of full strip), null if unobserved
  positions: [[x,y]|null, ...]  # ALWAYS full strip length (led_count entries), indexed by strip-local index (0..led_count-1); null if unobserved
  fit_method: "auto_map_v1"
  visibility: "direct" | "partial" | "inferred"  # direct=all LEDs observed, partial=some null, inferred=legacy (from geometry.py anchor-based fitting)
```

**Populating from scan data:**
- `positions` — ALWAYS a full-length array of `led_count` entries, indexed by **strip-local** index (0 through led_count-1). Discovery uses channel-absolute indices internally; conversion to strip-local: `strip_local = channel_index - offset`. Observed positions are stored as `[x_uv, y_uv]` in image-space UV: `x_uv = x_px / frame_width`, `y_uv = 1.0 - (y_px / frame_height)` (bottom-left origin). Unobserved LEDs are stored as `null`. The `bounds` field is computed as metadata (min/max of all non-null strip positions) but is not used for normalization.
- `anchors` — always 5 entries at canonical strip positions: LED indices `round(f * (led_count - 1))` for `f` in `[0.0, 0.25, 0.5, 0.75, 1.0]` (e.g., for a 600-LED strip: indices 0, 150, 300, 449, 599). If the LED at that index was observed, store its `[x_uv, y_uv]`; if unobserved, store `null`. Partially-visible strips have sparse anchors.
- `visibility` — "direct" if all LEDs on the strip have non-null positions, "partial" if any are still null. Existing maps from the geometry wizard may contain "inferred" (strips not directly visible but fitted from anchor data) — auto-map does not emit "inferred" but must tolerate it when reading existing maps for merge.
- `visible_strips` — strip IDs that have at least one non-null position in the spatial map (i.e., strips seen from the primary viewpoint). Strips discovered electrically from secondary angles but without spatial positions are NOT included — they exist in `installation.yaml` but not in `visible_strips`.

**Critical: Spatial positions are single-viewpoint only.** Coordinates from different camera angles are NOT comparable (different projection). Therefore:
- **Electrical mapping** (StripMapping: channel, offset, direction, led_count) merges freely across all scan passes regardless of camera angle. This is the primary purpose of multi-angle rescans.
- **Spatial positions** (SpatialMap: positions, anchors) are ONLY written from the **first pass** in a draft (which establishes the viewpoint). Subsequent passes from different camera angles contribute electrical mapping only — their position data is discarded, not written to the spatial map. This keeps `spatial_map.json` in a single consistent `front_projection_uv` coordinate space.
- Strips not visible from the primary angle will have all-null positions and anchors in the spatial map. This is expected — front-projection effects only use strips with valid positions.
- To get spatial coverage of all strips, the user should position the camera to see as many strips as possible in the first pass.

Feeds into existing `SpatialMap` for front-projection effects.

### Merge Behavior on Re-scan

**Auto-reconciliation** uses electrical overlap only (not geometry — coordinates from different camera angles are not comparable):

1. Same channel AND overlapping electrical index range → auto-match
2. Geometric consistency is checked only when both candidates come from the same viewpoint (same pass or same camera angle). Cross-angle passes skip the geometry check entirely.

When auto-matched, electrical fields are merged: `offset` = min of both offsets, `led_count` = max end index - min start index + 1, `direction` and `confidence` from the candidate with more visible LEDs. Spatial positions are only merged from the primary viewpoint (first pass): same-viewpoint positions merge per-LED non-destructively; cross-viewpoint position data is discarded entirely.

**Disjoint segments:** Two candidates on the same channel but with non-overlapping index ranges (e.g., pass 1 sees LEDs 0-80, pass 2 sees LEDs 90-171) cannot be auto-reconciled. These are left as `"unresolved"`. The user resolves via `/resolve` with `"action": "merge"` — which combines their electrical fields into one strip with `offset` = min start, `led_count` = combined range. Spatial positions come only from the primary-viewpoint candidate; if both are from the primary viewpoint, they merge per-LED-index. Cross-viewpoint spatial data is discarded.

**Other unresolved cases:** Candidates that can't be auto-matched (e.g., same channel, overlapping range, but wildly different LED counts suggesting a wiring change) are flagged as `"unresolved"`.

- Auto-matched strips → updated with per-LED merge, not duplicated
- Strips not visible in the current pass → left untouched
- "Reset Mapping" option available via `/start` with `new_draft: true`

## New Files

| File | Purpose |
|------|---------|
| `pi/app/setup/stream.py` | SRT stream receiver — opens stream, yields numpy frames, handles connect/disconnect |
| `pi/app/setup/auto_mapper.py` | Scan controller — orchestrates 4 phases, coordinates LED control + frame capture |
| `pi/app/setup/vision.py` | Blob detection — background subtraction, threshold, centroid extraction, confidence |
| `pi/app/api/routes/auto_map.py` | API routes for auto-map start/stop/status/apply/ws |

## Modified Files

| File | Change |
|------|--------|
| `pi/app/config/spatial_map.py` | Schema migration: both `StripGeometry.positions` and `StripGeometry.anchors` change from `list[list[float]]` to `list[Optional[list[float]]]` to support null entries for unobserved LEDs. Bump `SCHEMA_VERSION` to 2. Update `_parse_spatial_map()` and `to_dict()` to tolerate nulls in both fields. Update consumers (e.g., `geometry.py` line 198) to skip null entries. |
| `pi/app/main.py` | Register `_scan` effect at startup: `renderer.register_effect('_scan', ScanEffect)` |
| `pi/app/api/server.py` | Register auto_map router |
| `pi/app/ui/static/index.html` | Auto Map panel in Setup section |
| `pi/app/ui/static/js/app.js` | Auto Map UI logic, camera canvas, progress display |
| `pi/app/ui/static/css/app.css` | Auto Map panel styles |
| `pi/pyproject.toml` | No new dependencies needed (PyAV already in `video` extra) |
| `pi/app/setup/patterns.py` | Extend with single-LED and channel-flood patterns for scan phases |

## Dependencies

- **No new Python dependencies** — PyAV (`av>=12.0`) already in pyproject.toml `video` extra; Pillow and NumPy already core dependencies
- `ffmpeg` with SRT support (system, apt) — required as PyAV's backend for SRT protocol. Should already be present if `video` extra is installed.

## Raster Rendering Pipeline

Once the spatial map is populated, the rendering pipeline changes from strip-indexed to raster-sampled. This replaces the current model where effects render directly to a `(strips × leds_per_strip × 3)` logical frame.

### Current Pipeline (strip-indexed)
```
Effect.render() → logical frame (10 × N × 3)
  → brightness/gamma
  → map_frame_compiled() → channel data (5 × 344 × 3)
  → serialize → USB → Teensy
```

Effects must know they're rendering to strip columns. Geometry is baked into the effect.

### New Pipeline (raster-sampled)
```
Effect.render() → raster canvas (W × H × 3)
  → sample at each LED's UV position from spatial map
  → logical frame (strips × leds_per_strip × 3)  [same format as strip-indexed]
  → brightness/gamma
  → map_frame_compiled() → channel data  [same pipeline from here on]
  → serialize → USB → Teensy
```

Effects render to a generic 2D canvas with no knowledge of strip geometry. The raster sampler converts it to a standard logical frame, which then flows through the existing pipeline unchanged.

### Design

**Raster canvas:** Effects render to a rectangular numpy array of configurable resolution (e.g., 320×240, 640×480). Higher resolution = smoother gradients between LEDs but more CPU cost. Default: 320×240 (sufficient for ~1720 LEDs).

**UV sampling:** For each LED with a non-null position in the spatial map:
1. Look up the LED's `[x_uv, y_uv]` (normalized 0-1)
2. Map to raster pixel: `px_x = x_uv * (W - 1)`, `px_y = y_uv * (H - 1)`
3. Sample the raster at that pixel (bilinear interpolation for sub-pixel positions)
4. Result: RGB color for that LED

**LEDs without spatial positions** (null in the spatial map) in a raster effect: rendered as black (off). The raster canvas has no strip-indexed data to fall back to — only LEDs with known UV positions can be sampled. This is expected: strips not visible during mapping have no spatial coordinates and don't participate in raster effects.

**Precomputed lookup table:** The UV → pixel mapping is static once the spatial map is loaded. On spatial map load/change, precompute a lookup table: `led_uv_lut[strip_id][led_index] = (px_x, px_y)` or `None`. This avoids per-frame UV lookups.

**Effect render mode:** Effects declare their output type via a class attribute `RENDER_MODE`:
- `RENDER_MODE = "strip"` (default) — legacy strip-indexed output `(width, height, 3)`. Renderer uses `map_frame_compiled` pipeline.
- `RENDER_MODE = "raster"` — 2D canvas output `(canvas_h, canvas_w, 3)`. Renderer samples at LED UV positions via the lookup table.

The renderer checks `effect.RENDER_MODE` to route through the correct path — no shape-based guessing. Existing effects default to `"strip"` with no code changes required.

### Modified Files (additional)

| File | Change |
|------|--------|
| `pi/app/core/renderer.py` | After effect renders raster, sample at UV positions instead of calling `map_frame_compiled` directly. Fallback to strip-indexed path when no spatial map. |
| `pi/app/core/raster_sampler.py` (new) | Precomputes UV lookup table from spatial map. Provides `sample_raster(canvas, lut) → logical_frame (strips × leds × 3)`. Bilinear interpolation. The output is a standard logical frame that then flows through the existing `map_frame_compiled()` pipeline (which applies strip direction, per-strip brightness, swizzle, channel packing). |
| `pi/app/effects/base.py` (if exists) | Update base effect class with canvas dimensions. |

### Backward Compatibility

- Effects default to `RENDER_MODE = "strip"` — no code changes needed for existing effects.
- The spatial map is optional. Without it, only `"strip"` mode works. `"raster"` mode effects require a populated spatial map; if none is loaded, the renderer outputs black for that effect and logs a warning. Raster canvases cannot be meaningfully interpreted as strip-indexed frames.
- This is a gradual migration: effects can be updated one at a time to set `RENDER_MODE = "raster"` and render to the canvas.

## Non-Goals

- Color order auto-detection during scan (existing `rgb_order.py` handles this separately)
- 3D position mapping (2D camera coordinates only, per-angle)
- Automatic camera position detection or multi-camera fusion
- Sub-LED precision (interpolation between every-5th sample is sufficient for linear strips)
