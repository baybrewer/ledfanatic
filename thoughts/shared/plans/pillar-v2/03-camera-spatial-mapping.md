# F5: Camera-Based LED Spatial Mapping

## Summary

Use the phone camera (held fixed) to automatically determine the physical 2D
position of every LED. The system lights LEDs in a scanning sequence while the
camera records their positions. The result is a spatial map that effects can
use for geometry-aware rendering — animations map perfectly to the physical
layout regardless of strip arrangement.

**Depends on**: F1 (per-strip configuration for LED counts).

---

## Why This Matters

The current mapping assumes a perfect 10-column × 172-row grid with uniform
spacing. In reality:
- Strips may not be perfectly vertical
- Spacing between strips varies (especially on a cylinder)
- Strips may have slight curves or tilts
- LED pitch varies between manufacturers

A spatial map lets effects use **actual physical coordinates** instead of
idealized grid positions. This makes radial effects, wave patterns, and
gradient directions physically accurate.

---

## Output Format: `spatial_map.json`

```json
{
  "version": 1,
  "created": "2026-04-15T12:00:00Z",
  "camera_resolution": [1280, 720],
  "strips": [
    {
      "id": 0,
      "positions": [
        [0.123, 0.005],
        [0.124, 0.011],
        [0.125, 0.017],
        ...
      ]
    },
    ...
  ],
  "bounds": {
    "x_min": 0.0, "x_max": 1.0,
    "y_min": 0.0, "y_max": 1.0
  },
  "grid": {
    "columns": 10,
    "rows": 172,
    "column_centers": [0.12, 0.22, 0.31, ...],
    "row_spacing_avg": 0.0058
  }
}
```

**Coordinate system:**
- Origin (0, 0) = bottom-left of the mapped area
- (1, 1) = top-right
- Positions are normalized to [0, 1] in both axes
- Y increases upward (matching the logical grid: row 0 = bottom)

**Per-strip positions** are ordered from LED 0 (bottom) to LED N-1 (top),
matching the logical `y` index. Each position is `[x_norm, y_norm]`.

**Grid summary** is derived from the raw positions — column centers and average
row spacing help effects that want grid-snapped coordinates.

**File location**: `pi/config/spatial_map.json`

**File size**: 10 strips × 172 LEDs × ~15 bytes per position ≈ 26 KB. Trivial.

---

## Scanning Algorithm

### Strategy: Per-Strip Sequential Scan

Light one LED at a time per strip, with **one LED per strip simultaneously**
(10 LEDs lit at once, one on each strip). Since strips are spatially separated,
there's no ambiguity about which bright dot belongs to which strip.

**Scan steps**: `MAX_LEDS_PER_STRIP` (172) frames.
**At each step** `y`: Light LED `y` on every strip.
**Capture rate**: Camera captures at ~30fps → 172 steps at ~33ms each ≈ **6 seconds**.

With a 200ms settling time per step (for camera auto-exposure and LED
persistence): 172 × 200ms = **34 seconds**. Acceptable.

**Alternative considered**: Binary Gray code encoding (11 frames for 2048 LEDs).
Faster but harder to implement and debug. Sequential scan is simpler and fast
enough at 34 seconds.

### Detection Per Frame

For each captured frame:

1. **Subtract dark baseline** (captured before scan starts)
2. **Threshold**: pixels brighter than `T` (adaptive, based on dark frame noise)
3. **Find connected components** (bright blobs)
4. **Compute centroid** of each blob
5. **Associate** each blob with the nearest strip column (using previous frame's
   column positions, or initial spatial prior from strip ordering)

### Column Association

On the first few frames (LEDs near the bottom), all 10 strips light their first
LED. The 10 blobs establish the **column identity** — leftmost blob is assigned
to the strip that's physically leftmost. The user confirms the strip-to-column
mapping in the UI before continuing.

For subsequent frames, each blob is associated with the nearest established
column (nearest-neighbor in x-coordinate). This handles strips that meander
slightly.

### Handling Missing Detections

If a blob is not detected for a particular strip at a particular step:
- **Interpolation**: Use the positions of the previous and next detected LEDs
  on that strip to interpolate the missing position.
- **Retry**: Re-light that specific LED and capture again.
- **Flag**: Mark the position as interpolated in the output.

---

## Resolution

**Question from user**: "What should the resolution of the map be?"

**Answer**: The resolution is **sub-LED** — limited only by camera pixel density
at the LED distance.

At 720p (1280×720) with the pillar filling ~60% of the frame:
- Effective area: ~768×432 pixels
- 172 LEDs vertically: ~2.5 camera pixels per LED
- 10 strips horizontally: ~77 camera pixels per strip

Centroid detection achieves **sub-pixel accuracy** (~0.3 pixel via Gaussian
fitting), so the position precision is:
- Vertical: ~0.5mm at 1.4m pillar height
- Horizontal: ~0.4mm at 0.3m pillar circumference

This is far more precision than needed. Normalized float32 coordinates capture
this fully.

**At 1080p (1920×1080)**: roughly 1.5× better precision. Recommended but not required.

---

## User Flow

1. Navigate to **System → Setup** sub-panel
2. Tap **"Map LED Positions"** button
3. Mapping wizard modal opens:
   - Instruction: "Place your phone on a stable surface pointing at the pillar.
     Keep the entire pillar visible in the frame."
   - Live camera preview
   - "Start Mapping" button
4. User positions camera and taps "Start Mapping"
5. **Stability check**: System captures 3 frames 500ms apart, computes image
   difference. If motion exceeds threshold: "Camera is moving. Please stabilize."
6. **Dark frame capture**: All LEDs off, capture baseline
7. **Column identification**: Light LED 0 on all strips, capture, find 10 blobs
   - Show overlay: numbered circles on detected positions
   - "These are your strips left-to-right. Correct?" [Confirm] [Swap]
8. **Sequential scan**: Progress bar, ~34 seconds
   - Live overlay shows detected positions as colored dots
9. **Completion**: Show full position map as dot overlay on camera frame
   - "Mapping complete. 1720/1720 LEDs detected."
   - [Save] [Retry] [Cancel]
10. "Save" writes to `spatial_map.json` and shows confirmation

---

## Backend API

### `POST /api/setup/light-led` [auth required]

Light specific LEDs for mapping.

**Request:**
```json
{
  "leds": [
    {"strip": 0, "index": 42},
    {"strip": 1, "index": 42},
    ...
  ],
  "color": [255, 255, 255]
}
```

**Behavior**: Sets the specified LEDs to the given color, all others black.
Uses identity color permutation (raw hardware output — though for spatial
mapping, color accuracy doesn't matter, only position).

### `POST /api/setup/scan-step` [auth required]

Light LED at index `y` on all strips.

**Request:**
```json
{
  "index": 42,
  "color": [255, 255, 255],
  "brightness": 128
}
```

Convenience endpoint — equivalent to calling `light-led` with one LED per strip.

### `POST /api/setup/save-spatial-map` [auth required]

Save the computed spatial map.

**Request:**
```json
{
  "strips": [
    {
      "id": 0,
      "positions": [[0.12, 0.005], [0.12, 0.011], ...]
    },
    ...
  ]
}
```

**Behavior**: Validates, computes derived grid properties, writes `spatial_map.json`.

### `GET /api/config/spatial-map`

Returns the saved spatial map, or 404 if none exists.

---

## Frontend: Image Processing

All image processing runs client-side in JavaScript using Canvas API.

### Blob Detection

```javascript
function findBlobs(frame, darkFrame, threshold = 40) {
  const diff = subtractFrames(frame, darkFrame);
  const binary = thresholdGrayscale(diff, threshold);
  const labels = connectedComponents(binary);
  return labels.map(region => ({
    centroid: computeCentroid(region),
    area: region.length,
    boundingBox: computeBBox(region),
  }));
}
```

**Connected components**: Simple flood-fill on the thresholded binary image.
Since we expect only 10 blobs per frame (one per strip), performance is fine
even with naive flood-fill on a 720p image.

**Centroid**: Intensity-weighted average of pixel positions in the blob.
This gives sub-pixel accuracy.

```javascript
function computeCentroid(pixels, frameData) {
  let sumX = 0, sumY = 0, sumW = 0;
  for (const [x, y] of pixels) {
    const idx = (y * width + x) * 4;
    const w = frameData[idx] + frameData[idx+1] + frameData[idx+2];
    sumX += x * w;
    sumY += y * w;
    sumW += w;
  }
  return { x: sumX / sumW, y: sumY / sumW };
}
```

### Normalization

After scanning all LEDs, normalize positions to [0, 1]:

```javascript
function normalizePositions(allPositions) {
  // Find bounding box of all detected positions
  let minX = Infinity, maxX = -Infinity;
  let minY = Infinity, maxY = -Infinity;

  for (const strip of allPositions) {
    for (const [x, y] of strip.positions) {
      minX = Math.min(minX, x);
      maxX = Math.max(maxX, x);
      minY = Math.min(minY, y);
      maxY = Math.max(maxY, y);
    }
  }

  const rangeX = maxX - minX || 1;
  const rangeY = maxY - minY || 1;

  return allPositions.map(strip => ({
    id: strip.id,
    positions: strip.positions.map(([x, y]) => [
      (x - minX) / rangeX,
      1.0 - (y - minY) / rangeY,  // flip Y: camera Y increases downward
    ])
  }));
}
```

---

## Spatial Map Loader: `pi/app/mapping/spatial.py`

New module that loads the spatial map and provides coordinate lookups.

```python
"""
Spatial map loader.

Loads LED position data from spatial_map.json and provides
coordinate lookups for geometry-aware effects.
"""

import json
from pathlib import Path
from typing import Optional
import numpy as np

_spatial_map: Optional[np.ndarray] = None  # shape (STRIPS, MAX_LEDS, 2)

def load_spatial_map() -> Optional[np.ndarray]:
    """Load spatial_map.json. Returns None if no map exists."""
    global _spatial_map
    for config_dir in [Path("/opt/pillar/config"), Path(__file__).parent.parent.parent / "config"]:
        path = config_dir / "spatial_map.json"
        if path.exists():
            with open(path) as f:
                data = json.load(f)
            _spatial_map = _parse_map(data)
            return _spatial_map
    return None

def _parse_map(data: dict) -> np.ndarray:
    """Parse JSON map into numpy array."""
    strips = sorted(data['strips'], key=lambda s: s['id'])
    max_leds = max(len(s['positions']) for s in strips)
    arr = np.zeros((len(strips), max_leds, 2), dtype=np.float32)
    for s in strips:
        positions = np.array(s['positions'], dtype=np.float32)
        arr[s['id'], :len(positions)] = positions
    return arr

def get_positions() -> Optional[np.ndarray]:
    """Get position array (STRIPS, MAX_LEDS, 2) or None."""
    return _spatial_map

def get_column_centers() -> Optional[np.ndarray]:
    """Get average X position per strip."""
    if _spatial_map is None:
        return None
    return _spatial_map[:, :, 0].mean(axis=1)
```

**Effects can use spatial positions** like this:

```python
class SpatialWave(Effect):
    def render(self, t, state):
        positions = spatial.get_positions()
        if positions is None:
            # Fallback: use idealized grid
            positions = self._default_grid()

        # Use actual x,y positions for wave calculation
        x = positions[:, :, 0]  # (10, 172)
        y = positions[:, :, 1]
        distance = np.sqrt(x**2 + y**2)
        wave = np.sin(distance * 10 - t * 3)
        # ... map wave values to colors
```

---

## Rendering Integration

The spatial map is **optional**. Effects that don't need it continue using the
logical grid. Effects that want physical positions call `spatial.get_positions()`
and fall back to a default grid if no map exists.

The spatial map does NOT change the mapping layer (`cylinder.py`) — that still
handles the logical→electrical conversion. The spatial map is an additional
data source for effects that want geometry-aware rendering.

---

## Acceptance Criteria

- [ ] Camera wizard opens with live preview and stability check
- [ ] Dark frame baseline is captured before scanning
- [ ] Column identification correctly maps 10 blobs to 10 strips
- [ ] Sequential scan lights one LED per strip and captures centroids
- [ ] Missing detections are interpolated
- [ ] Positions are normalized to [0, 1] with Y=0 at bottom
- [ ] `spatial_map.json` is written with correct schema
- [ ] `GET /api/config/spatial-map` returns saved map
- [ ] Spatial map loads at startup if present
- [ ] Effects can query physical positions via `spatial.get_positions()`
- [ ] Scanning completes in under 60 seconds for 172 LEDs × 10 strips
- [ ] Works on iPhone Safari over local WiFi

---

## Test Plan

### Automated (pytest)

```python
def test_spatial_map_parsing():
    """spatial_map.json loads into correct numpy shape."""

def test_spatial_map_normalization():
    """Positions are in [0,1] range after normalization."""

def test_get_positions_returns_none_without_map():
    """No spatial_map.json → get_positions() returns None."""

def test_get_column_centers():
    """Column centers match average X per strip."""

def test_save_spatial_map_validation():
    """Invalid maps (wrong strip count, out of range) are rejected."""

def test_scan_step_endpoint():
    """POST /api/setup/scan-step lights correct LEDs."""

def test_save_spatial_map_endpoint():
    """POST /api/setup/save-spatial-map writes JSON file."""
```

### Manual (iPhone Safari)

- [ ] Camera preview shows stable when phone is propped up
- [ ] Stability check rejects shaky camera
- [ ] Column identification labels match actual strip positions
- [ ] Scan progress overlay shows dots appearing in correct positions
- [ ] Saved map coordinates match visible strip layout
- [ ] Effects using spatial map render geometry-correctly

---

## Performance Budget

| Operation | Time |
|-----------|------|
| Camera startup | ~2s |
| Stability check | ~2s |
| Dark frame capture | ~1s |
| Column identification | ~2s |
| Sequential scan (172 steps × 200ms) | ~34s |
| Normalization + save | ~1s |
| **Total** | **~42s** |

With faster settling (100ms per step): ~20s total. Can be tuned.

---

## Future Enhancements (Not In Scope)

- 3D mapping (multiple camera positions for depth)
- Automatic re-mapping on strip change
- Sub-LED interpolation for effects rendered at higher resolution
- Gray code scanning for faster capture (11 frames vs 172)
