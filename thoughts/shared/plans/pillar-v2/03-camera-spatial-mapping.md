# F5: Camera-Based LED Spatial Mapping

## Summary

Use the phone camera (held fixed) to automatically determine the physical 2D
position of every LED. The system lights LEDs in a scanning sequence while the
camera records their positions. The result is a spatial map that effects can
use for geometry-aware rendering.

**Depends on**: F1 (per-strip configuration for LED counts).

**Shares with F4**: `CameraWizard` component, `POST /api/setup/set-leds`
endpoint, camera wizard modal markup. See `02-camera-rgb-detection.md`.

---

## Why This Matters

The current mapping assumes a perfect 10-column × 172-row grid with uniform
spacing. In reality strips may not be perfectly vertical, spacing varies, and
LED pitch differs between manufacturers. A spatial map lets effects use
**actual physical coordinates** instead of idealized grid positions.

---

## Output Format: `pi/config/spatial_map.json`

```json
{
  "version": 1,
  "created": "2026-04-15T12:00:00Z",
  "camera_resolution": [1280, 720],
  "strips": [
    {
      "id": 0,
      "positions": [[0.123, 0.005], [0.124, 0.011], ...]
    }
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
- Y increases upward (matching logical grid: row 0 = bottom)
- Positions normalized to [0, 1] in both axes

**Per-strip positions** ordered from LED 0 (bottom) to LED N-1 (top).

**File size**: 10 strips × 172 LEDs × ~15 bytes per position ≈ 26 KB. Trivial.

---

## Scanning Algorithm

### Strategy: Per-Strip Sequential Scan

Light one LED at a time per strip, with **one LED per strip simultaneously**
(10 LEDs lit at once, one on each strip). Since strips are spatially separated,
there's no ambiguity about which bright dot belongs to which strip.

**Scan steps**: `MAX_LEDS_PER_STRIP` (172) frames.
**At each step** `y`: Light LED `y` on every strip via `POST /api/setup/set-leds`.
**With 200ms settling time**: 172 × 200ms = **~34 seconds**. Acceptable.

### Detection Per Frame

1. **Subtract dark baseline** (captured before scan starts)
2. **Threshold**: pixels brighter than adaptive `T`
3. **Find connected components** (bright blobs)
4. **Compute centroid** of each blob (intensity-weighted)
5. **Associate** each blob with nearest established column

### Column Association

First few frames establish **column identity** — 10 blobs map to 10 strips.
User confirms mapping in UI. Subsequent frames use nearest-neighbor.

### Missing Detections

- **Interpolation**: from previous and next detected LEDs on that strip
- **Retry**: re-light that specific LED
- **Flag**: mark position as interpolated

---

## Resolution

At 720p with the pillar filling ~60% of the frame:
- ~2.5 camera pixels per LED vertically
- ~77 camera pixels per strip horizontally
- Centroid detection achieves sub-pixel accuracy (~0.3 pixel)
- Effective precision: ~0.5mm vertical, ~0.4mm horizontal

Normalized float32 coordinates capture this fully.

---

## User Flow

1. Navigate to **System → Setup** sub-panel
2. Tap **"Map LED Positions"** button
3. Shared camera wizard modal opens (same markup as F4):
   - Title set to "LED Position Mapping"
   - Instruction: "Place phone on stable surface pointing at pillar"
   - Live camera preview
4. Tap "Start Mapping"
5. **Stability check**: 3 frames 500ms apart, compute motion. Reject if shaky.
6. **Dark frame capture**: All LEDs off via `POST /api/display/blackout`
7. **Column identification**: Light LED 0 on all strips via `POST /api/setup/set-leds`:
   ```json
   {
     "leds": [
       {"strip": 0, "index": 0, "color": [255,255,255]},
       {"strip": 1, "index": 0, "color": [255,255,255]},
       ...
     ],
     "all_others": "black",
     "use_identity_permutation": false
   }
   ```
   Show overlay with numbered circles. User confirms strip-to-column mapping.
8. **Sequential scan**: Progress bar, ~34 seconds.
   Each step calls `POST /api/setup/set-leds` with one LED per strip.
9. **Completion**: Show full map as dot overlay.
   "1720/1720 LEDs detected." [Save] [Retry] [Cancel]
10. "Save" calls `POST /api/setup/save-spatial-map`

---

## Backend API

Uses the shared `POST /api/setup/set-leds` from F4 for LED control.

### `POST /api/setup/save-spatial-map` [auth required]

Save the computed spatial map.

**Request body:**
```json
{
  "camera_resolution": [1280, 720],
  "strips": [
    {"id": 0, "positions": [[0.12, 0.005], [0.12, 0.011], ...]},
    ...
  ]
}
```

**Behavior**: Validates, computes derived grid properties, writes
`pi/config/spatial_map.json` using atomic temp-file + rename.

### `GET /api/config/spatial-map`

Returns the saved spatial map, or 404 if none exists.

### Implementation

Both endpoints go in the existing `pi/app/api/routes/setup.py` (shared with F4):

```python
@router.post("/save-spatial-map", dependencies=[Depends(require_auth)])
async def save_spatial_map(body: SaveSpatialMapRequest):
    ...

@router.get("/spatial-map")  # in config router (pi/app/api/routes/config.py)
async def get_spatial_map():
    ...
```

### Pydantic models in `pi/app/api/schemas.py`

```python
class StripPositions(BaseModel):
    id: int
    positions: list[list[float]]

class SaveSpatialMapRequest(BaseModel):
    camera_resolution: list[int]
    strips: list[StripPositions]
```

---

## Frontend: Image Processing

All processing client-side using Canvas API and the shared `CameraWizard`.

### Blob Detection

```javascript
function findBlobs(frame, darkFrame, threshold = 40) {
  const diff = subtractFrames(frame, darkFrame);
  const binary = thresholdGrayscale(diff, threshold);
  const labels = connectedComponents(binary);
  return labels.map(region => ({
    centroid: computeCentroid(region, frame),
    area: region.length,
  }));
}
```

### Centroid (intensity-weighted)

```javascript
function computeCentroid(pixels, frameData, width) {
  let sumX = 0, sumY = 0, sumW = 0;
  for (const [x, y] of pixels) {
    const idx = (y * width + x) * 4;
    const w = frameData.data[idx] + frameData.data[idx+1] + frameData.data[idx+2];
    sumX += x * w; sumY += y * w; sumW += w;
  }
  return { x: sumX / sumW, y: sumY / sumW };
}
```

### Normalization

After scanning all LEDs, normalize to [0, 1] with Y flipped (camera Y
increases downward, but LED 0 = bottom):

```javascript
function normalizePositions(allPositions) {
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const strip of allPositions) {
    for (const [x, y] of strip.positions) {
      minX = Math.min(minX, x); maxX = Math.max(maxX, x);
      minY = Math.min(minY, y); maxY = Math.max(maxY, y);
    }
  }
  const rangeX = maxX - minX || 1;
  const rangeY = maxY - minY || 1;
  return allPositions.map(strip => ({
    id: strip.id,
    positions: strip.positions.map(([x, y]) => [
      (x - minX) / rangeX,
      1.0 - (y - minY) / rangeY,
    ])
  }));
}
```

---

## Spatial Map Loader: `pi/app/mapping/spatial.py`

```python
"""Spatial map loader — LED position data from camera mapping."""

import json
from pathlib import Path
from typing import Optional
import numpy as np

_spatial_map: Optional[np.ndarray] = None

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

### Startup Loading (in `pi/app/main.py`)

Add after hardware constants load, before renderer.run():

```python
from .mapping.spatial import load_spatial_map
load_spatial_map()  # loads if file exists, None otherwise
```

### Effect Usage (optional, fallback to grid)

```python
from ..mapping import spatial

class SpatialWave(Effect):
    def render(self, t, state):
        positions = spatial.get_positions()
        if positions is None:
            positions = self._default_grid()
        # Use actual x,y positions for wave calculation
```

---

## Acceptance Criteria

- [ ] Camera wizard opens with live preview and stability check
- [ ] Dark frame baseline captured before scanning
- [ ] Column identification correctly maps 10 blobs to 10 strips
- [ ] Sequential scan captures centroids using `POST /api/setup/set-leds`
- [ ] Missing detections are interpolated
- [ ] Positions normalized to [0, 1] with Y=0 at bottom
- [ ] `spatial_map.json` written with correct schema (atomic write)
- [ ] `GET /api/config/spatial-map` returns saved map (404 if none)
- [ ] Spatial map loads at startup in main.py
- [ ] Effects can query positions via `spatial.get_positions()`
- [ ] Scanning completes in under 60 seconds
- [ ] Works on iPhone Safari over local WiFi
- [ ] `deploy.sh` rsync includes `spatial_map.json`
- [ ] `docs/current-contracts.md` updated with new endpoints
- [ ] All ~219 existing tests pass (regression)

---

## Test Plan

### Automated (pytest)

```python
def test_spatial_map_parsing():
    """spatial_map.json loads into correct numpy shape."""

def test_spatial_map_normalization():
    """Positions are in [0,1] range."""

def test_get_positions_returns_none_without_map():
    """No file → None."""

def test_get_column_centers():
    """Column centers match average X per strip."""

def test_save_spatial_map_validation():
    """Invalid maps rejected (wrong strip count, out of range)."""

def test_save_spatial_map_endpoint():
    """POST writes JSON file."""

def test_get_spatial_map_endpoint():
    """GET returns saved map. 404 if none."""
```

### Manual (iPhone Safari)

- [ ] Stability check rejects shaky camera
- [ ] Column identification labels match actual strip positions
- [ ] Scan progress shows dots appearing correctly
- [ ] Saved map coordinates match visible layout

---

## Performance Budget

| Operation | Time |
|-----------|------|
| Camera startup | ~2s |
| Stability check | ~2s |
| Dark frame capture | ~1s |
| Column identification | ~2s |
| Sequential scan (172 × 200ms) | ~34s |
| Normalization + save | ~1s |
| **Total** | **~42s** |

---

## Future Enhancements (Not In Scope)

- 3D mapping (multiple camera positions)
- Gray code scanning (11 frames vs 172)
- Automatic re-mapping on strip change
