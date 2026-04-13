# F4: Camera-Based RGB Order Auto-Detection

## Summary

Use the phone's rear camera via the web portal to automatically determine the
RGB color order of each LED strip. The system lights up one strip at a time in
pure red, green, and blue. The camera captures each color, analyzes the dominant
channel, and infers the strip's color order. Results are written to hardware.yaml
via the strip config API (F1).

**Depends on**: F1 (per-strip configuration must exist).

---

## User Flow

1. Navigate to **System → Setup** sub-panel
2. Tap **"Auto-detect RGB Order"** button
3. A full-screen camera wizard modal opens:
   - Instruction: "Point your camera at the LED pillar and hold steady"
   - Live camera preview (`<video>` element)
   - "Start Detection" button
4. User taps "Start Detection". For each strip (0–9):
   a. System lights strip N in **pure red** (all other strips off)
   b. Wait 600ms for camera auto-exposure to settle
   c. Capture frame, analyze dominant color in bright region
   d. Repeat for **pure green** and **pure blue**
   e. Progress indicator updates: "Strip 3/10 — detecting..."
5. After all strips: show results table with detected orders
6. User can **override** any strip's detected order with a dropdown
7. Tap **"Apply"** to save to hardware.yaml via `POST /api/config/strips`
8. Tap **"Cancel"** to discard

**Total detection time**: 10 strips × 3 colors × ~800ms = ~24 seconds

---

## Camera Access

### API: `navigator.mediaDevices.getUserMedia()`

```javascript
const stream = await navigator.mediaDevices.getUserMedia({
  video: {
    facingMode: { ideal: 'environment' },  // rear camera
    width: { ideal: 1280 },
    height: { ideal: 720 },
  }
});
const video = document.getElementById('camera-preview');
video.srcObject = stream;
```

**Requirements:**
- HTTPS or localhost (getUserMedia requires secure context)
- The local WiFi portal at `http://pillar.local` is NOT secure context
- **Solution**: The Pi must serve the portal over HTTPS with a self-signed cert,
  OR the user accesses via `http://localhost` (not applicable for remote device).

**Practical workaround**: On iOS Safari, `getUserMedia` works on non-HTTPS
origins if the page is loaded from the local network AND the user grants
camera permission. As of iOS 16+, this works on `http://` origins for
`getUserMedia` as long as the user explicitly grants access. We should test
this and document the requirement. If it doesn't work, we'll need to add a
self-signed TLS cert to the FastAPI server.

### Frame Capture

```javascript
function captureFrame(video) {
  const canvas = document.createElement('canvas');
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  const ctx = canvas.getContext('2d');
  ctx.drawImage(video, 0, 0);
  return ctx.getImageData(0, 0, canvas.width, canvas.height);
}
```

---

## Backend: Strip Lighting Control

### `POST /api/setup/light-strip` [auth required]

Light a single strip with a specific color for calibration.

**Request:**
```json
{
  "strip_id": 3,
  "color": [255, 0, 0]
}
```

**Behavior:**
- Temporarily overrides the renderer
- Sets ALL LEDs on strip `strip_id` to the given RGB color
- ALL other strips are set to black
- Sends the resulting frame to Teensy (goes through normal mapping pipeline
  but with identity color permutation — we're testing what the strip SHOWS,
  not what it should show after correction)

**IMPORTANT**: During detection, the mapping layer must send the color **without**
applying any existing color_order permutation. We're observing the raw hardware
behavior to determine what the permutation should be. Use identity permutation
`(0, 1, 2)` for all strips during the detection routine.

**Response 200:**
```json
{"status": "ok", "strip_id": 3, "color": [255, 0, 0]}
```

### `POST /api/setup/light-strip-off` [auth required]

Return to normal rendering (clear calibration override).

**Response 200:**
```json
{"status": "ok"}
```

### Implementation: `pi/app/api/setup_routes.py`

```python
router = APIRouter(prefix="/api/setup", tags=["setup"])

@router.post("/light-strip", dependencies=[Depends(require_auth)])
async def light_strip(body: LightStripRequest):
    """Light a single strip for calibration. Bypasses color permutation."""
    renderer.set_calibration_override(body.strip_id, body.color)
    return {"status": "ok", "strip_id": body.strip_id, "color": body.color}

@router.post("/light-strip-off", dependencies=[Depends(require_auth)])
async def light_strip_off():
    """Clear calibration override, return to normal rendering."""
    renderer.clear_calibration_override()
    return {"status": "ok"}
```

The renderer needs a `calibration_override` field:
```python
# In Renderer class:
self.calibration_override: Optional[tuple[int, list[int]]] = None

def set_calibration_override(self, strip_id: int, color: list[int]):
    self.calibration_override = (strip_id, color)

def clear_calibration_override(self):
    self.calibration_override = None
```

When `calibration_override` is set, the render loop produces a frame with
only that strip lit in that color (identity permutation), skipping the normal
effect rendering.

---

## Frontend: Detection Algorithm

### Phase 1: Capture Reference (dark frame)

Before starting, capture a frame with all LEDs off. This is the "ambient
baseline" — needed to subtract ambient light contamination.

```javascript
await api('POST', '/api/setup/light-strip', { strip_id: -1, color: [0,0,0] });
// Actually, just use light-strip-off to ensure normal blackout state
await api('POST', '/api/display/blackout', { enabled: true });
await sleep(600);
const darkFrame = captureFrame(video);
```

### Phase 2: Per-Strip Color Detection

For each strip 0–9, for each test color [R, G, B]:

```javascript
async function detectStripOrder(stripId, video) {
  const testColors = [
    { name: 'red',   rgb: [255, 0, 0] },
    { name: 'green', rgb: [0, 255, 0] },
    { name: 'blue',  rgb: [0, 0, 255] },
  ];

  const observations = {};

  for (const test of testColors) {
    // Light strip with test color (identity permutation)
    await api('POST', '/api/setup/light-strip', {
      strip_id: stripId,
      color: test.rgb
    });

    // Wait for LED + camera auto-exposure to settle
    await sleep(600);

    // Capture frame
    const frame = captureFrame(video);

    // Find the bright region (diff from dark frame)
    const dominant = analyzeDominantColor(frame, darkFrame);
    observations[test.name] = dominant;  // e.g., { r: 12, g: 240, b: 8 }
  }

  return inferColorOrder(observations);
}
```

### Phase 3: Analyze Dominant Color

```javascript
function analyzeDominantColor(frame, darkFrame) {
  const { data, width, height } = frame;
  let maxBrightness = 0;
  let brightPixels = [];

  // Find pixels significantly brighter than dark frame
  for (let i = 0; i < data.length; i += 4) {
    const r = data[i] - darkFrame.data[i];
    const g = data[i+1] - darkFrame.data[i+1];
    const b = data[i+2] - darkFrame.data[i+2];
    const brightness = r + g + b;

    if (brightness > 100) {  // threshold: significantly lit
      brightPixels.push({ r, g, b });
    }
  }

  if (brightPixels.length === 0) {
    return null;  // strip not visible — ask user to adjust camera
  }

  // Average the bright pixels
  const avg = {
    r: brightPixels.reduce((s, p) => s + p.r, 0) / brightPixels.length,
    g: brightPixels.reduce((s, p) => s + p.g, 0) / brightPixels.length,
    b: brightPixels.reduce((s, p) => s + p.b, 0) / brightPixels.length,
  };

  return avg;
}
```

### Phase 4: Infer Color Order

We sent R=255,G=0,B=0 through the LED pipeline with identity permutation.
The OctoWS2811 (configured as GRB) will output `[G=0, R=255, B=0]` on the wire.

If the camera sees **RED**: the strip reads wire bytes as GRB → R is in position 1
→ strip order is GRB (matches system config).

If the camera sees **GREEN**: the strip reads wire byte 0 as Green → wire byte 0
is actually the R value from setPixel → the strip's first channel is its Green
channel, meaning the strip expects RGB order (R first on wire = Green channel).

Wait — this gets confusing. Instead of trying to derive analytically in JS,
use an empirical approach:

```javascript
function inferColorOrder(observations) {
  // observations.red   = avg RGB seen by camera when we sent [255, 0, 0]
  // observations.green = avg RGB seen by camera when we sent [0, 255, 0]
  // observations.blue  = avg RGB seen by camera when we sent [0, 0, 255]

  // For each observation, determine which camera channel is dominant
  function dominant(obs) {
    if (!obs) return null;
    const { r, g, b } = obs;
    if (r > g && r > b) return 'R';
    if (g > r && g > b) return 'G';
    if (b > r && b > g) return 'B';
    return null;  // ambiguous
  }

  const whenSentRed   = dominant(observations.red);    // what color appeared
  const whenSentGreen = dominant(observations.green);
  const whenSentBlue  = dominant(observations.blue);

  // Build the mapping: what we sent → what appeared
  // This tells us the permutation the hardware applies
  // From this we can determine what color order the strip expects

  // If GRB strip (correct config): sent R→see R, sent G→see G, sent B→see B
  // If RGB strip (wrong config):   sent R→see G, sent G→see R, sent B→see B
  //   (because OctoWS2811 sends [0,255,0] wire for R input; RGB strip reads as G)

  // Lookup table of known mappings
  const ORDER_MAP = {
    'R,G,B': 'GRB',   // everything correct
    'G,R,B': 'RGB',   // R↔G swapped
    'B,G,R': 'BRG',   // derived from wire analysis
    'R,B,G': 'RBG',
    'G,B,R': 'GBR',
    'B,R,G': 'BGR',
  };

  const key = `${whenSentRed},${whenSentGreen},${whenSentBlue}`;
  return ORDER_MAP[key] || null;  // null = ambiguous, ask user
}
```

**NOTE**: The ORDER_MAP values need to be validated empirically with real
hardware. The implementation should include a test mode where the user manually
confirms what color they see for each test, and we build the lookup table from
actual observations. The camera detection then uses this validated table.

---

## Error Handling

| Condition | Response |
|-----------|----------|
| Camera permission denied | Show message: "Camera access required. Check Settings > Safari > Camera." |
| No bright pixels found | "Strip N not visible. Adjust camera position." + Retry button |
| Ambiguous color detection | "Couldn't determine color for strip N." + manual dropdown |
| getUserMedia not available | "Camera not supported. Set color order manually below." |
| HTTPS required | "Camera requires HTTPS. See setup guide for enabling TLS." |

---

## UI: Camera Wizard Modal

```html
<div id="rgb-detect-modal" class="modal hidden">
  <div class="modal-content">
    <h2>RGB Order Detection</h2>
    <video id="camera-preview" autoplay playsinline></video>
    <div id="detect-progress" class="hidden">
      <div class="progress-bar">
        <div id="detect-progress-fill"></div>
      </div>
      <p id="detect-status">Detecting strip 1/10...</p>
    </div>
    <div id="detect-results" class="hidden">
      <table id="detect-results-table">
        <thead>
          <tr><th>Strip</th><th>Detected</th><th>Override</th></tr>
        </thead>
        <tbody><!-- Populated by JS --></tbody>
      </table>
    </div>
    <div class="modal-actions">
      <button id="detect-start-btn">Start Detection</button>
      <button id="detect-apply-btn" class="hidden">Apply</button>
      <button id="detect-cancel-btn" class="secondary">Cancel</button>
    </div>
  </div>
</div>
```

### CSS (app.css additions)

```css
.modal {
  position: fixed;
  inset: 0;
  z-index: 200;
  background: rgba(0, 0, 0, 0.9);
  display: flex;
  align-items: center;
  justify-content: center;
}
.modal.hidden { display: none; }
.modal-content {
  width: 95%;
  max-width: 500px;
  background: var(--surface);
  border-radius: 16px;
  padding: 20px;
}
#camera-preview {
  width: 100%;
  border-radius: 8px;
  aspect-ratio: 16/9;
  object-fit: cover;
}
```

---

## Acceptance Criteria

- [ ] Camera wizard opens and shows live rear camera preview on iPhone Safari
- [ ] "Start Detection" lights strips one at a time (all others off)
- [ ] Each strip is tested with R, G, B in sequence
- [ ] Camera frame analysis correctly identifies dominant color channel
- [ ] Detected color order matches manual observation for at least GRB and RGB strips
- [ ] Results table shows per-strip detected order with override dropdowns
- [ ] "Apply" saves detected orders via `POST /api/config/strips`
- [ ] "Cancel" restores normal rendering without saving
- [ ] Error states (no camera, no bright pixels) show helpful messages
- [ ] Detection completes in under 30 seconds for 10 strips

---

## Test Plan

### Automated (pytest)

```python
def test_light_strip_endpoint():
    """POST /api/setup/light-strip sets calibration override."""

def test_light_strip_off_clears():
    """POST /api/setup/light-strip-off clears override."""

def test_light_strip_requires_auth():
    """No token → 401."""

def test_calibration_override_produces_correct_frame():
    """When override is set, renderer produces frame with only target strip lit."""

def test_calibration_override_uses_identity_permutation():
    """Override frame does NOT apply strip color_order permutation."""
```

### Manual (iPhone Safari)

- [ ] Camera preview displays (test on http:// and https://)
- [ ] Detection runs through all 10 strips without freezing
- [ ] Ambient light subtraction handles a lit room
- [ ] Results match manually observed colors
- [ ] Override dropdowns work and save correctly

---

## Security Notes

- Camera access is client-side only — no video data is sent to the server
- All frame analysis happens in the browser via Canvas API
- The server only receives the final color_order strings via the existing
  strip config endpoint
- Camera stream is stopped immediately when the wizard closes
