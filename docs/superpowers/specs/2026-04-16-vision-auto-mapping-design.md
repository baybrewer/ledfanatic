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

1. **Coarse sweep** — light every 10th LED across the channel-half range using **absolute channel indices**: first half probes `0, 10, 20, ... 170`; second half probes `172, 182, 192, ... 342`. All recorded indices (`start_index`, `end_index`, `offset`) are absolute channel indices throughout the entire pipeline — never half-relative.
2. **Cluster visible runs** — group contiguous visible LEDs by centroid continuity. A position jump (>N pixels between adjacent samples) marks a **visibility gap**. All gaps are treated equally at this stage — the system does NOT attempt to distinguish occlusion (strip wraps behind cylinder) from electrical boundaries (daisy-chain) from a single camera angle.
3. **Refine endpoints** — for each visible run, binary search within the 10-LED gaps at the start and end to find exact first/last visible LED
4. **LED count** — total visible LEDs per run. Runs are recorded as `(channel, start_index, end_index, centroids)`. Daisy-chain boundaries (where strip 1 ends and strip 2 begins on the same channel) are only committed when corroborated: either (a) a second scan angle shows the gap persists even when both sides are visible, or (b) the electrical index crosses the known 172-LED boundary AND positions are spatially discontinuous. Until corroborated, gaps remain "unresolved" and the system presents them to the user for confirmation.
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
| `/start` | POST | Yes | Begin a scan pass. Body: `{"channels": [0,1,2,3,4], "new_draft": false}`. If `new_draft` is true (or no draft exists), creates a fresh draft mapping session, discarding any previous draft. If false (default) and a draft exists, adds a new scan pass to it — candidates from previous passes are preserved and merged with new results. Only one draft exists at a time; only one scan pass may run at a time. Starting a new pass aborts any active pass but keeps the draft. Returns `{"draft_id": "...", "pass_id": "...", "srt_url": "srt://<ip>:9000"}`. |
| `/stop` | POST | Yes | Abort current scan pass. Body: `{"draft_id": "...", "pass_id": "..."}`. Rejects if IDs don't match. Restores previous scene. Draft and its accumulated candidates are preserved — user can start another pass or apply/discard. |
| `/status` | GET | Yes | Returns `{"draft_id": "...", "pass_id": "...", "phase": 1-4, "progress": 0.0-1.0, "strips_found": N, "stream_connected": bool, "passes_completed": N}`. Clients check `draft_id`/`pass_id` to avoid acting on stale data. |
| `/results` | GET | Yes | Returns `{"draft_id": "...", "candidates": [...]}`. Candidates are the merged result across ALL passes in this draft. Each: `{candidate_id, channel, offset, led_count, direction, confidence, status, visible_count, source_passes: [pass_ids]}` where status is `"confirmed"`, `"unresolved"`, or `"rejected"`. `confidence` is candidate-level (0.0-1.0) based on average blob brightness and detection consistency. |
| `/resolve` | POST | Yes | Resolve ambiguous candidates. Body: `{"draft_id": "...", "resolutions": [{"candidate_id": 0, "action": "accept"}, {"candidate_id": 1, "action": "reject"}, {"candidate_id": 2, "action": "merge", "merge_with": 3}]}`. Actions: `accept` (confirm as strip), `reject` (discard), `merge` (combine two candidates from different angles). Returns updated results. |
| `/apply` | POST | Yes | Merge confirmed results into existing config. Body: `{"draft_id": "..."}`. Fails if any candidates are still `"unresolved"`. **Merge semantics:** confirmed candidates are matched to existing strips in installation.yaml by `(channel, offset)`. Matched strips are updated (led_count, direction, etc.). Unmatched candidates are added as new strips with the next available ID. Existing strips NOT present in the draft are preserved unchanged (stable IDs). SpatialMap entries follow the same merge: update matched, add new, preserve untouched. Transaction: (1) load existing config, (2) merge, (3) validate + compile output plan in memory, (4) stage both files to temps, (5) swap both. If any step fails before swap, no files modified. Clears draft on success. Returns the full strip list. |
| `/discard` | POST | Yes | Discard the draft and all accumulated candidates. Body: `{"draft_id": "..."}`. Restores previous scene if a pass was active. |
| `/ws` | WebSocket | Yes | Live camera frame (downscaled) + blob overlay + JSON progress. Auth via `?token=` query param (WebSocket can't send headers). |

### WebSocket Message Types

- **Binary frame:** JPEG-encoded camera image (downscaled to ~640px wide) with blob overlay circles drawn in. Prefixed with 1-byte type marker `0x01`. Sent at ~10fps to keep bandwidth manageable.
- **JSON status:** Text message with `{"phase": 2, "progress": 0.45, "message": "Channel 2, LED 85", "strips_found": [...]}`. Sent on each scan step.

### LED Control During Scan

The scan uses a dedicated `ScanEffect` (a standard effect class with `render()`) that the auto-mapper injects via `renderer.activate_scene('_scan', params)`. The ScanEffect's params are updated in-place by the mapper each step: `{"mode": "channel_flood", "channel": 2}` or `{"mode": "single_led", "channel": 0, "index": 47}`. The renderer's existing brightness/gamma/output-plan pipeline applies normally.

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
  visible_strips: [0, 1, 3, 5, ...]  # union of all strips mapped across all accepted scans
  bounds: {x_min, x_max, y_min, y_max}
  strips: [StripGeometry, ...]

StripGeometry:
  id: int  # matches StripMapping.id
  anchors: [[x,y]|null, ...]  # 5 canonical strip points (0%, 25%, 50%, 75%, 100% of full strip), null if unobserved
  positions: [[x,y]|null, ...]  # ALWAYS full strip length (led_count entries), indexed by absolute LED index; null if unobserved
  fit_method: "auto_map_v1"
  visibility: "direct" | "partial" | "inferred"  # direct=all LEDs observed, partial=some null, inferred=legacy (from geometry.py anchor-based fitting)
```

**Populating from scan data:**
- `positions` — ALWAYS a full-length array of `led_count` entries, indexed by absolute LED index (0 through led_count-1). Observed positions are stored as `[x_uv, y_uv]` in image-space UV: `x_uv = x_px / frame_width`, `y_uv = 1.0 - (y_px / frame_height)` (bottom-left origin). Unobserved LEDs (not visible from any scan angle yet) are stored as `null`. This uses the camera resolution as the stable coordinate frame, NOT a per-scan bounding box — ensuring coordinates remain comparable across scan angles. The `bounds` field is computed as metadata (min/max of all non-null strip positions) but is not used for normalization.
- `anchors` — always 5 entries at canonical strip positions: LED indices `round(f * (led_count - 1))` for `f` in `[0.0, 0.25, 0.5, 0.75, 1.0]` (so for 172 LEDs: indices 0, 43, 86, 128, 171). If the LED at that index was observed, store its `[x_uv, y_uv]`; if unobserved, store `null`. This means partially-visible strips have sparse anchors (e.g., `[null, null, [0.3, 0.5], [0.35, 0.7], [0.4, 0.9]]` when only the bottom half was visible). Anchors are updated on re-scan as positions fill in.
- `visibility` — "direct" if all LEDs on the strip have non-null positions, "partial" if any are still null. Existing maps from the geometry wizard may contain "inferred" (strips not directly visible but fitted from anchor data) — auto-map does not emit "inferred" but must tolerate it when reading existing maps for merge.
- `visible_strips` — the union of all strip IDs that have at least one non-null position, accumulated across all accepted scans
- Multi-angle merge: merge is per-LED-index, never destructive. Newly observed positions fill in null entries. For positions already observed in a previous scan: only replace an existing non-null position if the new candidate has higher `confidence` AND the specific LED was also observed (non-null) in the new scan. An existing non-null position is NEVER overwritten with null — this ensures multi-angle scans are strictly additive.

Feeds into existing `SpatialMap` for front-projection effects.

### Merge Behavior on Re-scan

**Auto-reconciliation:** A new candidate is auto-matched to an existing candidate when:
1. Same channel AND overlapping electrical index range, AND
2. Geometric consistency (centroid positions for overlapping indices within tolerance)

When auto-matched, the existing candidate's positions are updated per the non-destructive per-LED merge rule. The merged candidate's `offset` = min of both offsets, `led_count` = max end index - min start index + 1, `direction` and `confidence` from the candidate with more visible LEDs.

**Disjoint segments:** When two candidates are on the same channel but have non-overlapping index ranges (e.g., pass 1 sees LEDs 0-80, pass 2 sees LEDs 90-171), auto-reconciliation cannot determine if they're the same strip or two different strips. These are left as separate `"unresolved"` candidates. The user resolves via `/resolve` with `"action": "merge"` — which combines them into one strip with `offset` = min start, `led_count` = combined range, positions merged per-LED-index.

**Ambiguous matches** (overlapping range but inconsistent geometry) are also flagged as `"unresolved"`.

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
| `pi/app/config/spatial_map.py` | Schema migration: `StripGeometry.positions` changes from `list[list[float]]` to `list[Optional[list[float]]]` to support null entries for unobserved LEDs. Bump `SCHEMA_VERSION` to 2. Update `_parse_spatial_map()` and `to_dict()` to tolerate nulls. Update any consumers (e.g., `geometry.py` line 198) to skip null entries. |
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
