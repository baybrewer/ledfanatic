# Vision-Based Auto-Mapping Design

## Goal

Automatically discover LED strip wiring (channel, offset, direction, LED count) and physical positions by streaming live video from an iPhone camera while the Pi sequences through LEDs. Replaces manual strip-by-strip configuration in the Setup screen.

## Architecture

The iPhone streams live video to the Pi via SRT. The Pi controls LEDs and analyzes incoming video frames simultaneously — it knows exactly which electrical address is lit at every moment. A hierarchical 4-phase scan discovers strips quickly (~1-2 minutes per camera angle). Since the pillar is a cylinder, the user rotates and re-scans to cover all strips; the system merges results across angles.

## Stream Ingestion

- **Protocol:** SRT (Secure Reliable Transport) — peer-to-peer, no server needed
- **iPhone app:** Larix Broadcaster in SRT caller mode
- **Pi listener:** OpenCV `VideoCapture` reads `srt://0.0.0.0:9000?mode=listener`
- **Frame format:** Decoded to numpy arrays (H×W×3 uint8 BGR) at source framerate (~30fps)
- **New dependency:** `opencv-python-headless` (pip, ~30MB) — wraps ffmpeg for stream decode + provides blob detection primitives
- **System dependency:** `ffmpeg` (apt) — SRT protocol backend for OpenCV

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

### Phase 2: Boundary Search (~20 seconds)

For each visible channel-half:

1. **Binary search for first LED** — find lowest index that produces a visible blob
2. **Binary search for last LED** — find highest index that produces a visible blob
3. **LED count** = last - first + 1
4. **Daisy-chain detection** — if LEDs 171→172 show a position discontinuity (jump), that's the boundary between strip 1 and strip 2 on the same channel
5. **Gap handling** — some LEDs may wrap behind the cylinder. Record gaps rather than assuming contiguous visibility.

### Phase 3: Position Sampling (~30-60 seconds)

For each discovered strip:

1. Scan every 5th LED, recording `(led_index, centroid_x, centroid_y)`
2. Average centroid across 2-3 frames to reduce jitter
3. **Direction inference** — if y-coordinate increases with LED index → `bottom_to_top`, decreases → `top_to_bottom`
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
4. **Threshold + contour** — threshold the difference image (OpenCV `threshold` + `findContours`), find largest bright contour
5. **Centroid** — weighted centroid via OpenCV `moments` gives sub-pixel position
6. **Confidence score** — based on blob brightness, size, roundness. No blob or multiple blobs = low confidence, flagged for review

### Robustness

- **Minimum brightness threshold** — rejects camera noise and ambient flicker
- **Maximum blob size** — rejects reflections or large bright areas
- **Temporal averaging** — for Phase 3 position sampling, average centroid across 2-3 frames
- **LED color** — scan uses full white for maximum contrast. Color order doesn't affect detection (tinted blob still detected). Color order detection deferred to existing `rgb_order.py` as optional follow-up

### OpenCV primitives used

- `cv2.absdiff()` — frame differencing
- `cv2.threshold()` — binary threshold
- `cv2.findContours()` — blob extraction
- `cv2.moments()` — centroid calculation
- `cv2.VideoCapture()` — SRT stream ingestion

## API Endpoints

All under `/api/setup/auto-map/`. All POST endpoints require Bearer auth.

| Endpoint | Method | Auth | Purpose |
|----------|--------|------|---------|
| `/start` | POST | Yes | Begin scan. Body: `{"channels": [0,1,2,3,4]}` — which OctoWS2811 outputs to scan (default: all active). System discovers strips on those channels. |
| `/stop` | POST | Yes | Abort scan |
| `/status` | GET | No | Current phase (1-4), progress %, discovered strips |
| `/apply` | POST | Yes | Accept results → write installation.yaml + spatial_map.json, recompile output plan |
| `/ws` | WebSocket | No | Live camera frame (downscaled) + blob overlay + JSON progress |

### WebSocket Message Types

- **Binary frame:** JPEG-encoded camera image (downscaled to ~640px wide) with blob overlay circles drawn in. Prefixed with 1-byte type marker `0x01`. Sent at ~10fps to keep bandwidth manageable.
- **JSON status:** Text message with `{"phase": 2, "progress": 0.45, "message": "Channel 2, LED 85", "strips_found": [...]}`. Sent on each scan step.

### LED Control During Scan

The scan uses a dedicated `ScanEffect` that the auto-mapper injects into the renderer via `renderer.current_effect`. The ScanEffect accepts commands like "light channel 2, LEDs 0-171 full white" or "light channel 0, LED 47 only." This keeps the transport pipeline intact (brightness, gamma, output plan mapping all still apply). When the scan ends or is aborted, the previous scene is restored.

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

For each mapped LED:
- Sampled positions (every 5th LED) as `(x, y)` in normalized camera coordinates [0, 1]
- Interpolated positions for LEDs between samples
- Multi-angle merge: positions from the camera angle with higher confidence win

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
| `pi/setup.py` or `pyproject.toml` | Add `opencv-python-headless` to dependencies |
| `pi/app/setup/patterns.py` | Extend with single-LED and channel-flood patterns for scan phases |

## Dependencies

- `opencv-python-headless` (pip) — video capture + blob detection
- `ffmpeg` (system, apt) — SRT protocol backend for OpenCV

## Non-Goals

- Color order auto-detection during scan (existing `rgb_order.py` handles this separately)
- 3D position mapping (2D camera coordinates only, per-angle)
- Automatic camera position detection or multi-camera fusion
- Sub-LED precision (interpolation between every-5th sample is sufficient for linear strips)
