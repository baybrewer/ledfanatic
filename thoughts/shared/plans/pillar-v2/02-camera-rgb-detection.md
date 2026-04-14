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
3. A full-screen camera wizard modal opens (shared `CameraWizard` component):
   - Instruction: "Point your camera at the LED pillar and hold steady"
   - Live camera preview (`<video>` element)
   - "Start Detection" button
4. User taps "Start Detection". For each strip (0–9):
   a. System lights strip N in **pure red** via `POST /api/setup/set-leds`
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

## Camera Access (Shared with F5)

### Shared `CameraWizard` JS Component

Both F4 (RGB detection) and F5 (spatial mapping) need phone camera access.
Instead of duplicating, build a reusable component:

```javascript
class CameraWizard {
  constructor(videoElement, options = {}) {
    this.video = videoElement;
    this.stream = null;
    this.facingMode = options.facingMode || 'environment';
    this.resolution = options.resolution || { width: 1280, height: 720 };
  }

  async start() {
    this.stream = await navigator.mediaDevices.getUserMedia({
      video: {
        facingMode: { ideal: this.facingMode },
        width: { ideal: this.resolution.width },
        height: { ideal: this.resolution.height },
      }
    });
    this.video.srcObject = this.stream;
    await this.video.play();
  }

  captureFrame() {
    const canvas = document.createElement('canvas');
    canvas.width = this.video.videoWidth;
    canvas.height = this.video.videoHeight;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(this.video, 0, 0);
    return ctx.getImageData(0, 0, canvas.width, canvas.height);
  }

  stop() {
    if (this.stream) {
      this.stream.getTracks().forEach(t => t.stop());
      this.stream = null;
    }
  }
}
```

F4 and F5 each provide their own detection/mapping callbacks.

### HTTPS Requirement

`getUserMedia` requires secure context. On iOS Safari, it works on
non-HTTPS local network origins as long as the user grants camera permission.
Test this first. If it doesn't work, the Pi needs a self-signed TLS cert
added to the FastAPI server (documented as a setup step, not in this plan).

---

## Backend: Unified LED Control Endpoint

### `POST /api/setup/set-leds` [auth required]

Unified endpoint for both F4 (strip lighting) and F5 (individual LED lighting).
Replaces separate `light-strip` and `light-led` endpoints (DRY).

**Request:**
```json
{
  "leds": [
    {"strip": 0, "index": null, "color": [255, 0, 0]},
    {"strip": 1, "index": 42, "color": [255, 255, 255]}
  ],
  "all_others": "black",
  "use_identity_permutation": true
}
```

- `index: null` → light ALL LEDs on that strip with the given color
- `index: N` → light only LED N on that strip
- `all_others: "black"` → all unspecified LEDs are black
- `use_identity_permutation: true` → bypass color_order compensation (for
  calibration — we're observing raw hardware behavior)

**Response 200:**
```json
{"status": "ok"}
```

### `POST /api/setup/clear` [auth required]

Return to normal rendering (clear calibration override).

```json
{"status": "ok"}
```

### Implementation: `pi/app/api/routes/setup.py`

```python
from fastapi import APIRouter, Depends
from ..schemas import SetLedsRequest

def create_router(deps, require_auth) -> APIRouter:
    router = APIRouter(prefix="/api/setup", tags=["setup"])

    @router.post("/set-leds", dependencies=[Depends(require_auth)])
    async def set_leds(body: SetLedsRequest):
        deps.renderer.set_calibration_override(body)
        return {"status": "ok"}

    @router.post("/clear", dependencies=[Depends(require_auth)])
    async def clear_override():
        deps.renderer.clear_override()
        return {"status": "ok"}

    return router
```

### Pydantic models in `pi/app/api/schemas.py`

```python
class LedSpec(BaseModel):
    strip: int
    index: Optional[int] = None  # null = all LEDs on strip
    color: list[int]  # [R, G, B]

class SetLedsRequest(BaseModel):
    leds: list[LedSpec]
    all_others: str = "black"
    use_identity_permutation: bool = True
```

### Renderer Integration (unified RenderOverride)

```python
# In Renderer — uses unified override from 00-overview:
def set_calibration_override(self, led_spec: SetLedsRequest):
    self.render_override = RenderOverride(
        mode="calibration",
        led_spec=led_spec,
    )

def clear_override(self):
    self.render_override = RenderOverride(mode="normal")
```

When `render_override.mode == "calibration"`, the render loop produces a frame
with only the specified LEDs lit, using identity permutation if requested.

---

## Frontend: Detection Algorithm

### Phase 1: Capture Reference (dark frame)

```javascript
await api('POST', '/api/display/blackout', { enabled: true });
await sleep(600);
const darkFrame = camera.captureFrame();
await api('POST', '/api/display/blackout', { enabled: false });
```

### Phase 2: Per-Strip Color Detection

```javascript
async function detectStripOrder(stripId, camera) {
  const testColors = [
    { name: 'red',   rgb: [255, 0, 0] },
    { name: 'green', rgb: [0, 255, 0] },
    { name: 'blue',  rgb: [0, 0, 255] },
  ];

  const observations = {};

  for (const test of testColors) {
    await api('POST', '/api/setup/set-leds', {
      leds: [{ strip: stripId, index: null, color: test.rgb }],
      all_others: 'black',
      use_identity_permutation: true,
    });

    await sleep(600);
    const frame = camera.captureFrame();
    observations[test.name] = analyzeDominantColor(frame, darkFrame);
  }

  return inferColorOrder(observations);
}
```

### Phase 3: Analyze Dominant Color

```javascript
function analyzeDominantColor(frame, darkFrame) {
  const { data, width, height } = frame;
  let brightPixels = [];

  for (let i = 0; i < data.length; i += 4) {
    const r = Math.max(0, data[i] - darkFrame.data[i]);
    const g = Math.max(0, data[i+1] - darkFrame.data[i+1]);
    const b = Math.max(0, data[i+2] - darkFrame.data[i+2]);
    const brightness = r + g + b;
    if (brightness > 100) {
      brightPixels.push({ r, g, b });
    }
  }

  if (brightPixels.length === 0) return null;

  return {
    r: brightPixels.reduce((s, p) => s + p.r, 0) / brightPixels.length,
    g: brightPixels.reduce((s, p) => s + p.g, 0) / brightPixels.length,
    b: brightPixels.reduce((s, p) => s + p.b, 0) / brightPixels.length,
  };
}
```

### Phase 4: Infer Color Order

```javascript
function inferColorOrder(observations) {
  function dominant(obs) {
    if (!obs) return null;
    const { r, g, b } = obs;
    if (r > g && r > b) return 'R';
    if (g > r && g > b) return 'G';
    if (b > r && b > g) return 'B';
    return null;
  }

  const whenSentRed   = dominant(observations.red);
  const whenSentGreen = dominant(observations.green);
  const whenSentBlue  = dominant(observations.blue);

  // Lookup: what appears when we send R,G,B through identity permutation
  // + OctoWS2811 GRB wire output + strip's native order
  // This table must be validated empirically with real hardware.
  const ORDER_MAP = {
    'G,R,B': 'BGR',   // current default — GRB wire, BGR strip
    'R,G,B': 'GRB',   // strip matches OctoWS2811 config
    'G,B,R': 'RGB',
    'B,R,G': 'BRG',
    'R,B,G': 'RBG',
    'B,G,R': 'GBR',
  };

  const key = `${whenSentRed},${whenSentGreen},${whenSentBlue}`;
  return ORDER_MAP[key] || null;
}
```

**NOTE**: The ORDER_MAP must be validated empirically with real hardware.
Include a manual confirmation step where the user verifies what color they
see before trusting camera-only detection.

---

## Error Handling

| Condition | Response |
|-----------|----------|
| Camera permission denied | "Camera access required. Check Settings > Safari > Camera." |
| No bright pixels found | "Strip N not visible. Adjust camera position." + Retry |
| Ambiguous color detection | "Couldn't determine color for strip N." + manual dropdown |
| getUserMedia not available | "Camera not supported. Set color order manually below." |

---

## UI: Camera Wizard Modal

```html
<div id="camera-wizard-modal" class="modal hidden">
  <div class="modal-content">
    <h2 id="wizard-title">RGB Order Detection</h2>
    <video id="camera-preview" autoplay playsinline></video>
    <div id="wizard-progress" class="hidden">
      <div class="progress-bar"><div id="wizard-progress-fill"></div></div>
      <p id="wizard-status">Detecting strip 1/10...</p>
    </div>
    <div id="wizard-results" class="hidden">
      <!-- Populated by JS: results table or position map -->
    </div>
    <div class="modal-actions">
      <button id="wizard-start-btn">Start</button>
      <button id="wizard-apply-btn" class="hidden">Apply</button>
      <button id="wizard-cancel-btn" class="secondary">Cancel</button>
    </div>
  </div>
</div>
```

The modal is shared between F4 and F5. The JS configures title, start action,
and results display based on which wizard is active.

---

## Acceptance Criteria

- [ ] Camera wizard opens and shows live rear camera preview on iPhone Safari
- [ ] "Start Detection" lights strips one at a time (all others off)
- [ ] Each strip tested with R, G, B in sequence using `POST /api/setup/set-leds`
- [ ] Camera analysis correctly identifies dominant color channel
- [ ] Detected color order matches manual observation for BGR and GRB strips
- [ ] Results table shows per-strip detected order with override dropdowns
- [ ] "Apply" saves via `POST /api/config/strips`
- [ ] "Cancel" calls `POST /api/setup/clear` and restores normal rendering
- [ ] Error states show helpful messages
- [ ] Detection completes in under 30 seconds for 10 strips
- [ ] `docs/current-contracts.md` updated with setup endpoints
- [ ] All ~219 existing tests pass (regression)

---

## Test Plan

### Automated (pytest)

```python
def test_set_leds_endpoint():
    """POST /api/setup/set-leds sets calibration override."""

def test_set_leds_identity_permutation():
    """Override frame does NOT apply color_order permutation when requested."""

def test_set_leds_strip_all():
    """index=null lights all LEDs on specified strip."""

def test_clear_restores_normal():
    """POST /api/setup/clear returns to normal rendering."""

def test_set_leds_requires_auth():
    """No token → 401."""
```

### Manual (iPhone Safari)

- [ ] Camera preview displays on local WiFi
- [ ] Detection runs through all 10 strips without freezing
- [ ] Ambient light subtraction handles a lit room
- [ ] Results match manually observed colors
- [ ] Override dropdowns work and save correctly

---

## Security Notes

- Camera access is client-side only — no video data sent to server
- All frame analysis happens in browser via Canvas API
- Server only receives final color_order strings via strip config endpoint
- Camera stream stopped immediately when wizard closes
