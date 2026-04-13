# F6: Web Simulator (Live Effect Preview)

## Summary

Add a real-time LED simulator to the web portal that shows what the active
effect looks like on a visual representation of the pillar. The simulator
receives rendered frames from the Pi via WebSocket and displays them on a
`<canvas>` element. Users can preview any effect before selecting it, and
see what's currently playing in real time.

**Depends on**: F3 (animation integration — need effects to preview).

---

## Architecture: Server-Streamed Frames (SSOT)

**Decision**: The Pi renders frames and streams them to the browser. The browser
does NOT re-implement effect logic in JavaScript.

**Why**:
- **SSOT**: The Pi's Python renderer is the single source of truth for what effects
  look like. A JS re-implementation would diverge and require double maintenance.
- **Accuracy**: The browser shows exactly what the LEDs display, including
  brightness, gamma, and any active calibration.
- **Simplicity**: No need to port numpy-based effects to JavaScript.

**Trade-off**: Requires WiFi connection to the Pi. Offline simulation is not
supported. This is acceptable — the phone is already on the pillar's WiFi.

---

## Frame Streaming Protocol

### WebSocket Extension

The existing `/ws` WebSocket sends state updates every 500ms. We extend it
with an opt-in frame stream:

**Client subscribes:**
```json
{"type": "subscribe", "channel": "frames", "fps": 15}
```

**Client unsubscribes:**
```json
{"type": "unsubscribe", "channel": "frames"}
```

**Server sends frames (binary):**
```
[1 byte: message type = 0x01]
[4 bytes: frame_id, uint32 LE]
[N bytes: pixel data, RGB, row-major]
```

Pixel data is the **logical canvas** (10×172×3 = 5,160 bytes per frame).
This is the frame BEFORE mapping to electrical channels — it represents
what the user conceptually sees (strips as columns, LEDs as rows).

**Total per-frame message**: 1 + 4 + 5160 = **5,165 bytes**.

At 15fps: 5,165 × 15 = **~75 KB/s**. Trivial over WiFi.

**State messages continue as JSON text frames** (existing behavior). Frame
messages are binary. The client distinguishes by WebSocket message type
(text vs binary).

### Server Implementation

In `pi/app/api/server.py`:

```python
# Track simulator subscribers
frame_subscribers: set[WebSocket] = set()
frame_subscriber_fps: dict[WebSocket, int] = {}

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    # ... existing state broadcast logic ...

    async for message in ws:
        if isinstance(message, str):
            data = json.loads(message)
            if data.get('type') == 'subscribe' and data.get('channel') == 'frames':
                fps = min(data.get('fps', 15), 30)  # cap at 30fps
                frame_subscribers.add(ws)
                frame_subscriber_fps[ws] = fps
            elif data.get('type') == 'unsubscribe' and data.get('channel') == 'frames':
                frame_subscribers.discard(ws)
                frame_subscriber_fps.pop(ws, None)
```

In the renderer, add a frame broadcast hook:

```python
# In Renderer class:
self.frame_callback: Optional[Callable] = None

# After rendering each frame (in the render loop):
if self.frame_callback and logical_frame is not None:
    self.frame_callback(logical_frame, frame_id)
```

The server registers this callback and distributes to subscribers at each
subscriber's requested FPS (using frame skipping):

```python
def broadcast_frame(logical_frame: np.ndarray, frame_id: int):
    if not frame_subscribers:
        return
    # Build binary message
    header = struct.pack('<BI', 0x01, frame_id)
    payload = header + logical_frame.tobytes()
    for ws in list(frame_subscribers):
        target_fps = frame_subscriber_fps.get(ws, 15)
        if frame_id % max(1, 60 // target_fps) != 0:
            continue
        try:
            asyncio.create_task(ws.send_bytes(payload))
        except Exception:
            frame_subscribers.discard(ws)
```

---

## Browser Renderer

### Canvas Setup

```html
<canvas id="simulator-canvas" width="200" height="344"></canvas>
```

The canvas renders at 20× horizontal scale (200px for 10 strips) and 2×
vertical scale (344px for 172 LEDs). This gives each LED a 20×2 pixel
rectangle — large enough to see individual LEDs while fitting on a phone screen.

### Rendering

```javascript
const canvas = document.getElementById('simulator-canvas');
const ctx = canvas.getContext('2d');

function renderFrame(pixelData) {
  // pixelData: Uint8Array, 10 * 172 * 3 bytes, row-major
  // Layout: [strip0_led0_R, strip0_led0_G, strip0_led0_B, strip0_led1_R, ...]

  const strips = 10;
  const ledsPerStrip = 172;
  const pixelWidth = canvas.width / strips;    // 20px per strip
  const pixelHeight = canvas.height / ledsPerStrip;  // 2px per LED

  const imageData = ctx.createImageData(canvas.width, canvas.height);
  const data = imageData.data;

  for (let strip = 0; strip < strips; strip++) {
    for (let led = 0; led < ledsPerStrip; led++) {
      const srcIdx = (strip * ledsPerStrip + led) * 3;
      const r = pixelData[srcIdx];
      const g = pixelData[srcIdx + 1];
      const b = pixelData[srcIdx + 2];

      // Map to canvas pixels (Y=0 at top in canvas, but LED 0 = bottom)
      const canvasY = (ledsPerStrip - 1 - led);

      // Fill the rectangle for this LED
      for (let py = 0; py < pixelHeight; py++) {
        for (let px = 0; px < pixelWidth; px++) {
          const destX = strip * pixelWidth + px;
          const destY = canvasY * pixelHeight + py;
          const destIdx = (destY * canvas.width + destX) * 4;
          data[destIdx] = r;
          data[destIdx + 1] = g;
          data[destIdx + 2] = b;
          data[destIdx + 3] = 255;
        }
      }
    }
  }

  ctx.putImageData(imageData, 0, 0);
}
```

**Optimization**: Use `putImageData` (fast, no compositing). Pre-allocate the
ImageData object. For 200×344 pixels at 15fps, this is trivial.

### WebSocket Binary Handling

```javascript
function connectSimulatorWS() {
  const ws = new WebSocket(`ws://${location.host}/ws`);

  ws.onopen = () => {
    // Subscribe to frame stream
    ws.send(JSON.stringify({
      type: 'subscribe',
      channel: 'frames',
      fps: 15
    }));
  };

  ws.onmessage = (event) => {
    if (typeof event.data === 'string') {
      // Existing JSON state update
      handleStateUpdate(JSON.parse(event.data));
    } else if (event.data instanceof Blob) {
      // Binary frame data
      event.data.arrayBuffer().then(buffer => {
        const view = new Uint8Array(buffer);
        const type = view[0];
        if (type === 0x01) {
          const pixelData = view.slice(5);  // skip header
          renderFrame(pixelData);
        }
      });
    }
  };

  ws.onclose = () => {
    // Reconnect logic (same as existing)
    setTimeout(connectSimulatorWS, 2000);
  };
}
```

---

## UI Integration

### Option A: Simulator Tab (Recommended)

Add a new "Sim" tab between "Effects" and "Media":

```html
<button class="tab" data-tab="sim">Sim</button>
```

```html
<div id="panel-sim" class="panel">
  <div class="sim-container">
    <canvas id="simulator-canvas" width="200" height="344"></canvas>
    <div id="sim-info">
      <p>Scene: <strong id="sim-scene-name">—</strong></p>
      <p>FPS: <span id="sim-fps">—</span></p>
    </div>
  </div>
  <div class="sim-controls">
    <label>Preview FPS
      <select id="sim-fps-select">
        <option value="5">5</option>
        <option value="10">10</option>
        <option value="15" selected>15</option>
        <option value="30">30</option>
      </select>
    </label>
    <label>View
      <select id="sim-view-select">
        <option value="flat" selected>Flat (unwrapped)</option>
        <option value="cylinder">Cylinder (wrapped)</option>
      </select>
    </label>
  </div>
</div>
```

### Option B: Inline Preview in Effects Tab

Show a small simulator canvas at the top of the Effects panel, so users see
the preview while browsing effects:

```html
<div id="panel-effects" class="panel">
  <div class="effect-preview">
    <canvas id="effect-preview-canvas" width="100" height="172"></canvas>
  </div>
  <h2>Generative Effects</h2>
  ...
</div>
```

**Recommendation**: Start with **Option A** (separate tab) for simplicity.
The inline preview can be added later if users want it.

### CSS

```css
.sim-container {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding: 16px 0;
}

#simulator-canvas {
  border: 1px solid var(--border);
  border-radius: 4px;
  image-rendering: pixelated;  /* sharp pixels, no antialiasing */
  width: 100%;
  max-width: 300px;
  aspect-ratio: 200 / 344;
}

.sim-controls {
  display: flex;
  gap: 16px;
  justify-content: center;
  padding: 8px 0;
}
```

### Lifecycle

- **Subscribe** when the Sim tab is opened (or when simulator is visible)
- **Unsubscribe** when navigating away from the Sim tab
- This avoids wasting bandwidth when the simulator isn't visible

```javascript
function initSimulator() {
  const simTab = document.querySelector('[data-tab="sim"]');

  // Subscribe on tab open
  simTab.addEventListener('click', () => {
    if (!simSubscribed) {
      ws.send(JSON.stringify({ type: 'subscribe', channel: 'frames', fps: 15 }));
      simSubscribed = true;
    }
  });

  // Unsubscribe on tab change
  document.querySelectorAll('.tab').forEach(tab => {
    if (tab.dataset.tab !== 'sim') {
      tab.addEventListener('click', () => {
        if (simSubscribed) {
          ws.send(JSON.stringify({ type: 'unsubscribe', channel: 'frames' }));
          simSubscribed = false;
        }
      });
    }
  });
}
```

---

## Cylinder View (Optional Enhancement)

For the "Cylinder (wrapped)" view option, render the canvas as if looking
at the pillar from the side using CSS 3D transform:

```css
#simulator-canvas.cylinder-view {
  transform: perspective(400px) rotateY(15deg);
  border-radius: 8px;
}
```

This is a simple CSS perspective trick — not true 3D rendering. It gives a
subtle sense of the cylindrical shape. True 3D (WebGL) is out of scope.

---

## Effect Preview Mode

Users should be able to preview an effect **before** activating it. This
requires the renderer to support a "preview" mode where it renders a specified
effect without changing the active scene.

### API: `POST /api/scenes/preview` [auth required]

```json
{
  "name": "fire",
  "params": {"cooling": 80}
}
```

**Behavior**: Temporarily renders the specified effect for simulator output
only. The actual LED output continues showing the current scene. After 10
seconds (or explicit stop), preview mode ends.

**Implementation**: The renderer maintains a separate `preview_effect` instance.
When preview is active, the frame broadcast hook sends preview frames instead
of live frames. The LED output pipeline is unaffected.

```python
# In Renderer:
self.preview_effect: Optional[Effect] = None
self.preview_start: float = 0

def set_preview(self, name: str, params: dict = None):
    cls = self.effect_registry.get(name)
    if cls:
        self.preview_effect = cls(width=self.width, height=self.height, params=params)
        self.preview_start = time.monotonic()

def clear_preview(self):
    self.preview_effect = None
```

In the render loop:
```python
# Normal render for LED output
live_frame = self.current_effect.render(t, state)

# Preview render for simulator (if active)
if self.preview_effect:
    if t - self.preview_start > 10.0:
        self.clear_preview()
    else:
        preview_frame = self.preview_effect.render(t, state)
        if self.frame_callback:
            self.frame_callback(preview_frame, frame_id)
            return  # don't broadcast live frame
```

### `POST /api/scenes/preview/stop` [auth required]

Clears preview mode. Simulator shows live frames again.

---

## Bandwidth & Performance

| Metric | Value |
|--------|-------|
| Frame size (logical canvas) | 5,160 bytes |
| Header overhead | 5 bytes |
| Frames per second | 15 (configurable 5–30) |
| Bandwidth | ~75 KB/s at 15fps |
| Browser render cost | <1ms per frame (200×344 putImageData) |
| Server broadcast cost | ~0.1ms per frame (serialize + send) |

**Impact on render loop**: Negligible. The frame is already in memory as a
numpy array. `.tobytes()` is a zero-copy operation. The actual send is async.

**Multiple clients**: Each subscriber gets its own stream. At 5 simultaneous
clients × 15fps × 5KB = ~375 KB/s. Still trivial.

---

## Acceptance Criteria

- [ ] Sim tab shows a canvas rendering the current effect in real time
- [ ] Frame stream subscribes/unsubscribes on tab switch (no wasted bandwidth)
- [ ] Preview FPS is selectable (5, 10, 15, 30)
- [ ] Canvas renders with sharp pixels (no antialiasing)
- [ ] LED 0 (bottom) is at the bottom of the canvas
- [ ] Frame data matches what's actually displayed on the LEDs
- [ ] Preview mode shows a different effect without changing LEDs
- [ ] Preview auto-expires after 10 seconds
- [ ] WebSocket reconnect restores simulator
- [ ] Existing state broadcast (JSON) is unaffected by frame stream

---

## Test Plan

### Automated (pytest)

```python
def test_frame_broadcast_callback():
    """Renderer calls frame_callback with logical frame."""

def test_frame_broadcast_shape():
    """Broadcast frame is (STRIPS, HEIGHT, 3) uint8."""

def test_preview_effect_renders_separately():
    """Preview effect doesn't change active scene."""

def test_preview_auto_expires():
    """Preview clears after timeout."""

def test_ws_subscribe_unsubscribe():
    """Client can subscribe/unsubscribe to frame channel."""

def test_ws_binary_frame_format():
    """Binary frame message has correct header and size."""
```

### Manual (iPhone Safari)

- [ ] Sim tab shows live updating canvas
- [ ] Switching effects updates simulator in real time
- [ ] Switching tabs stops frame stream (verify no bandwidth usage)
- [ ] Preview mode shows requested effect while LEDs show current
- [ ] Canvas renders correctly in portrait and landscape
- [ ] FPS selector changes update rate visibly

---

## Files Changed

| File | Changes |
|------|---------|
| `pi/app/api/server.py` | Frame subscriber management, binary WS messages, preview endpoints |
| `pi/app/core/renderer.py` | `frame_callback`, preview effect support |
| `pi/app/ui/index.html` | Sim tab + panel markup |
| `pi/app/ui/static/js/app.js` | `connectSimulatorWS()`, `renderFrame()`, subscribe lifecycle |
| `pi/app/ui/static/css/app.css` | Simulator canvas styles |

---

## Future: Live Coding (Not In Scope)

The simulator lays the groundwork for Pixel Blaze-style live coding:

1. **Code editor** in the browser (CodeMirror or Monaco)
2. **Send Python source** to the Pi via API
3. **Pi hot-reloads** the effect from source (sandboxed eval)
4. **Simulator** shows live preview while editing
5. **Save** persists the custom effect to the effects directory

This requires sandboxed execution, syntax validation, error reporting, and
security hardening. It's a major feature beyond the simulator itself. The
simulator's architecture (frame streaming, preview mode) directly supports it.
