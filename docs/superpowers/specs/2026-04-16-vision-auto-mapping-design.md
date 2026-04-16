# Vision-Based Auto-Mapping Design

## Goal

Automatically discover LED strip wiring (channel, offset, direction, LED count) and physical positions by streaming live video from an iPhone camera while the Pi sequences through LEDs. Replaces manual strip-by-strip configuration in the Setup screen.

## Architecture

The iPhone streams live video to the Pi via SRT. The Pi controls LEDs and analyzes incoming video frames simultaneously — it knows exactly which electrical address is lit at every moment. A hierarchical 4-phase scan discovers strips quickly (~1-2 minutes per camera angle). Since the pillar is a cylinder, the user rotates and re-scans to cover all strips; the system merges results across angles.

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

For each of the 5 active OctoWS2811 channels:

1. Light ALL LEDs on the channel full white
2. Capture frame, subtract baseline
3. If bright region detected → channel has visible strips from this camera angle
4. Light first half (LEDs 0-171) → detect region
5. Light second half (LEDs 172-343) → detect region
6. Result: list of visible `(channel, half)` pairs with approximate bounding boxes

### Phase 2: Coarse-to-Fine Segmentation (~20 seconds)

For each visible channel-half:

1. **Coarse sweep** — light every 10th LED across the range (0, 10, 20, ... 170), recording which produce visible blobs and their centroid positions
2. **Cluster visible runs** — group contiguous visible LEDs by centroid continuity. A position jump (>N pixels between adjacent samples) indicates either a gap (strip wraps behind cylinder) or a daisy-chain boundary (strip 1 ends, strip 2 begins)
3. **Refine endpoints** — for each visible run, binary search within the 10-LED gaps at the start and end to find exact first/last visible LED
4. **LED count** — total visible LEDs per run. Multiple runs on the same channel-half with a large position discontinuity between them = daisy-chain boundary
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
3. If strips were already mapped from a previous camera angle, merge: more-complete data (more LEDs visible, higher confidence) wins
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
| `/start` | POST | Yes | Begin scan. Body: `{"channels": [0,1,2,3,4]}` — which OctoWS2811 outputs to scan (default: all active). Returns `{"session_id": "...", "srt_url": "srt://<ip>:9000"}`. Only one scan session may run at a time; starting a new one aborts any active session. |
| `/stop` | POST | Yes | Abort current scan session. Restores previous scene. |
| `/status` | GET | Yes | Current phase (1-4), progress %, discovered strips, stream connected (bool) |
| `/apply` | POST | Yes | Accept results. Body: `{"session_id": "..."}`. Validates discovered strips → writes installation.yaml + spatial_map.json → recompiles output plan → hot-applies. On compile/save failure, rolls back (no partial writes — uses atomic file replacement already in `save_spatial_map`). Returns the new strip list. |
| `/ws` | WebSocket | Yes | Live camera frame (downscaled) + blob overlay + JSON progress. Auth via `?token=` query param (WebSocket can't send headers). |

### WebSocket Message Types

- **Binary frame:** JPEG-encoded camera image (downscaled to ~640px wide) with blob overlay circles drawn in. Prefixed with 1-byte type marker `0x01`. Sent at ~10fps to keep bandwidth manageable.
- **JSON status:** Text message with `{"phase": 2, "progress": 0.45, "message": "Channel 2, LED 85", "strips_found": [...]}`. Sent on each scan step.

### LED Control During Scan

The scan uses a dedicated `ScanEffect` (a standard effect class with `render()`) that the auto-mapper injects via `renderer.activate_scene('_scan', params)`. The ScanEffect's params are updated in-place by the mapper each step: `{"mode": "channel_flood", "channel": 2}` or `{"mode": "single_led", "channel": 0, "index": 47}`. The renderer's existing brightness/gamma/output-plan pipeline applies normally.

**Scene ownership:** On scan start, the mapper saves `renderer.state.current_scene` and `renderer.current_effect`. On scan end (complete or abort), it restores the previous scene via `renderer.activate_scene(saved_name, saved_params)`. If no scene was active, it sets blackout.

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
| `direction` | Inferred in Phase 3 (y-trend of positions) |
| `led_count` | Discovered in Phase 2 (boundary search) |
| `color_order` | Default "BGR" (optionally detected via rgb_order.py follow-up) |
| `brightness` | Default 1.0 (user adjusts manually) |

Feeds directly into existing pipeline: `StripInstallation` → `compile_strip_plan()` → `apply_output_plan()`.

### 2. SpatialMap (→ spatial_map.json)

Must conform to the existing `SpatialMap` / `StripGeometry` schema in `pi/app/config/spatial_map.py`:

```python
SpatialMap:
  schema_version: 1
  profile_id: "auto_map"
  coordinate_space: "front_projection_uv"
  camera_resolution: [W, H]  # from stream
  visible_strips: [0, 1, 3, 5, ...]  # strips visible from this angle
  bounds: {x_min, x_max, y_min, y_max}
  strips: [StripGeometry, ...]

StripGeometry:
  id: int  # matches StripMapping.id
  anchors: [[x,y], ...]  # 5 evenly-spaced anchor points (0%, 25%, 50%, 75%, 100%)
  positions: [[x,y], ...]  # all LED positions (sampled + interpolated), normalized [0,1]
  fit_method: "auto_map_v1"
  visibility: "direct" | "partial"  # partial if some LEDs wrap behind cylinder
```

**Populating from scan data:**
- `anchors` — pick 5 evenly-spaced samples from the Phase 3 position data (LED indices at 0%, 25%, 50%, 75%, 100% of visible range)
- `positions` — all sampled positions + linearly interpolated positions for LEDs between samples, normalized to [0,1] UV space using the bounding box of all detected positions
- `visibility` — "direct" if all LEDs on the strip were visible, "partial" if gaps exist
- Multi-angle merge: when re-scanning, a strip with more visible LEDs or higher average confidence replaces the existing entry

Feeds into existing `SpatialMap` for front-projection effects.

### Merge Behavior on Re-scan

- Strips already in installation.yaml that are re-discovered → updated, not duplicated
- Strips not visible in the current scan → left untouched
- "Reset Mapping" option available to clear and start fresh

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
| `pi/app/api/server.py` | Register auto_map router |
| `pi/app/ui/static/index.html` | Auto Map panel in Setup section |
| `pi/app/ui/static/js/app.js` | Auto Map UI logic, camera canvas, progress display |
| `pi/app/ui/static/css/app.css` | Auto Map panel styles |
| `pi/pyproject.toml` | No new dependencies needed (PyAV already in `video` extra) |
| `pi/app/setup/patterns.py` | Extend with single-LED and channel-flood patterns for scan phases |

## Dependencies

- **No new Python dependencies** — PyAV (`av>=12.0`) already in pyproject.toml `video` extra; Pillow and NumPy already core dependencies
- `ffmpeg` with SRT support (system, apt) — required as PyAV's backend for SRT protocol. Should already be present if `video` extra is installed.

## Non-Goals

- Color order auto-detection during scan (existing `rgb_order.py` handles this separately)
- 3D position mapping (2D camera coordinates only, per-angle)
- Automatic camera position detection or multi-camera fusion
- Sub-LED precision (interpolation between every-5th sample is sufficient for linear strips)
