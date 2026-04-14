# F6: Web Simulator (Live Effect Preview)

## Summary

Add a real-time LED simulator to the web portal that shows what the active
effect looks like. The simulator receives rendered frames from the Pi via
WebSocket and displays them on a `<canvas>` element.

**Depends on**: F3 (animation integration — need effects to preview).

---

## Architecture: Server-Streamed Frames (SSOT)

The Pi renders frames and streams them to the browser. The browser does NOT
re-implement effect logic in JavaScript.

**Why SSOT**: The Pi's Python renderer is the single source of truth. A JS
re-implementation would diverge and require double maintenance.

**Trade-off**: Requires WiFi connection. Acceptable — the phone is already
on the pillar's WiFi.

---

## Frame Streaming Protocol

### WebSocket Extension (in `pi/app/api/routes/ws.py`)

The existing `/ws` sends JSON state updates every 500ms. We extend with
opt-in binary frame streaming.

**Client subscribes (text message):**
```json
{"action": "subscribe_frames", "fps": 15}
```

**Client unsubscribes:**
```json
{"action": "unsubscribe_frames"}
```

**Server sends frames (binary WebSocket message):**
```
[1 byte: message type = 0x01]
[4 bytes: frame_id, uint32 LE]
[N bytes: pixel data, RGB, strip-major]
```

Pixel data is the **logical canvas** (10×172×3 = 5,160 bytes).
Total per frame: 5,165 bytes. At 15fps: ~75 KB/s.

**Text messages continue as JSON** (existing behavior). Binary messages are
frames. Client distinguishes by WebSocket message type.

**Backward compatibility**: Existing clients that don't subscribe to frames
are unaffected. The `action` field follows the existing WS message pattern
(`"ping"`, `"get_state"`).

### Server Implementation

In `pi/app/api/routes/ws.py`, extend `_handle_ws_message`:

```python
frame_subscribers: dict[WebSocket, int] = {}  # ws → target_fps

async def _handle_ws_message(msg, ws):
    action = msg.get('action')
    if action == 'ping':
        await ws.send_json({'action': 'pong'})
    elif action == 'get_state':
        ...
    elif action == 'subscribe_frames':
        fps = min(msg.get('fps', 15), 30)
        frame_subscribers[ws] = fps
    elif action == 'unsubscribe_frames':
        frame_subscribers.pop(ws, None)
```

### Renderer Frame Callback

In `pi/app/core/renderer.py`, add a callback hook:

```python
class Renderer:
    def __init__(self, ...):
        ...
        self.frame_callback: Optional[Callable] = None
```

After rendering each frame in `_render_frame()`:

```python
# After computing logical_frame, before mapping:
if self.frame_callback and logical_frame is not None:
    self.frame_callback(logical_frame, self.state.frames_rendered)
```

The server registers this callback to distribute to frame subscribers:

```python
import struct

def on_frame(logical_frame: np.ndarray, frame_id: int):
    if not frame_subscribers:
        return
    header = struct.pack('<BI', 0x01, frame_id)
    payload = header + logical_frame.tobytes()
    for ws, target_fps in list(frame_subscribers.items()):
        if frame_id % max(1, 60 // target_fps) != 0:
            continue
        try:
            asyncio.create_task(ws.send_bytes(payload))
        except Exception:
            frame_subscribers.pop(ws, None)

deps.renderer.frame_callback = on_frame
```

---

## Renderer Override Mode (Unified with F4)

F4/F5 need calibration override. F6 needs preview mode. Instead of separate
mutable fields, use the unified `RenderOverride` from `00-overview.md`:

```python
@dataclass
class RenderOverride:
    mode: str = "normal"  # "normal" | "calibration" | "preview"
    # Calibration fields (F4/F5):
    led_spec: Optional[SetLedsRequest] = None
    # Preview fields (F6):
    preview_effect: Optional[Effect] = None
    preview_start: float = 0.0
    preview_timeout: float = 10.0
```

In the render loop:

```python
if self.render_override.mode == "calibration":
    logical_frame = self._render_calibration()
elif self.render_override.mode == "preview":
    if time.monotonic() - self.render_override.preview_start > self.render_override.preview_timeout:
        self.render_override = RenderOverride()  # auto-expire
    else:
        preview_frame = self.render_override.preview_effect.render(t, self.state)
        # Send preview to simulator subscribers, live frame to LEDs
        if self.frame_callback:
            self.frame_callback(preview_frame, frame_id)
        # Continue normal render for actual LED output
else:
    # Normal render — send to both LEDs and simulator
```

**Key behavior**: In preview mode, the **simulator** shows the preview effect
while **LEDs** continue showing the current scene. This lets users browse
effects without disrupting the live display.

---

## Preview API

### `POST /api/simulator/preview` [auth required]

Start previewing an effect in the simulator.

**Request:**
```json
{"effect": "fire", "params": {"cooling": 80}}
```

**Behavior**: Renderer creates a temporary Effect instance for preview.
Simulator subscribers receive preview frames. LEDs continue showing current
scene. Preview auto-expires after 10 seconds.

### `POST /api/simulator/preview/stop` [auth required]

Clear preview mode. Simulator shows live frames again.

### Implementation: `pi/app/api/routes/simulator.py`

```python
from fastapi import APIRouter, Depends
from ..schemas import PreviewRequest

def create_router(deps, require_auth) -> APIRouter:
    router = APIRouter(prefix="/api/simulator", tags=["simulator"])

    @router.post("/preview", dependencies=[Depends(require_auth)])
    async def start_preview(req: PreviewRequest):
        deps.renderer.set_preview(req.effect, req.params)
        return {"status": "ok"}

    @router.post("/preview/stop", dependencies=[Depends(require_auth)])
    async def stop_preview():
        deps.renderer.clear_override()
        return {"status": "ok"}

    return router
```

### Pydantic model in `pi/app/api/schemas.py`

```python
class PreviewRequest(BaseModel):
    effect: str
    params: dict = {}
```

### Wiring in `server.py`

```python
from .routes import simulator as simulator_routes
app.include_router(simulator_routes.create_router(deps, require_auth))
```

---

## Browser Renderer

### Canvas Setup

```html
<canvas id="simulator-canvas" width="200" height="344"></canvas>
```

20× horizontal scale, 2× vertical scale. Each LED = 20×2 pixel rectangle.

### Rendering

```javascript
const canvas = document.getElementById('simulator-canvas');
const ctx = canvas.getContext('2d');
const imageData = ctx.createImageData(canvas.width, canvas.height);

function renderFrame(pixelData) {
  const strips = 10, ledsPerStrip = 172;
  const pxW = canvas.width / strips;
  const pxH = canvas.height / ledsPerStrip;
  const data = imageData.data;

  for (let strip = 0; strip < strips; strip++) {
    for (let led = 0; led < ledsPerStrip; led++) {
      const srcIdx = (strip * ledsPerStrip + led) * 3;
      const r = pixelData[srcIdx], g = pixelData[srcIdx+1], b = pixelData[srcIdx+2];
      const canvasY = (ledsPerStrip - 1 - led);
      for (let py = 0; py < pxH; py++) {
        for (let px = 0; px < pxW; px++) {
          const destIdx = ((canvasY * pxH + py) * canvas.width + strip * pxW + px) * 4;
          data[destIdx] = r; data[destIdx+1] = g;
          data[destIdx+2] = b; data[destIdx+3] = 255;
        }
      }
    }
  }
  ctx.putImageData(imageData, 0, 0);
}
```

### WebSocket Binary Handling

```javascript
ws.onmessage = (event) => {
  if (typeof event.data === 'string') {
    handleStateUpdate(JSON.parse(event.data));
  } else if (event.data instanceof Blob) {
    event.data.arrayBuffer().then(buffer => {
      const view = new Uint8Array(buffer);
      if (view[0] === 0x01) {
        renderFrame(view.slice(5));
      }
    });
  }
};
```

---

## UI Integration

### New "Sim" Tab

```html
<button class="tab" data-tab="sim" data-tooltip="Live effect preview">Sim</button>
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
  </div>
</div>
```

### CSS

```css
.sim-container { display: flex; flex-direction: column; align-items: center; gap: 12px; padding: 16px 0; }
#simulator-canvas {
  border: 1px solid var(--border); border-radius: 4px;
  image-rendering: pixelated; width: 100%; max-width: 300px;
  aspect-ratio: 200 / 344;
}
```

### Lifecycle

Subscribe when Sim tab opens. Unsubscribe when navigating away.

```javascript
function initSimulator() {
  let subscribed = false;

  document.querySelector('[data-tab="sim"]').addEventListener('click', () => {
    if (!subscribed && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ action: 'subscribe_frames', fps: 15 }));
      subscribed = true;
    }
  });

  document.querySelectorAll('.tab').forEach(tab => {
    if (tab.dataset.tab !== 'sim') {
      tab.addEventListener('click', () => {
        if (subscribed && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ action: 'unsubscribe_frames' }));
          subscribed = false;
        }
      });
    }
  });
}
```

---

## Bandwidth & Performance

| Metric | Value |
|--------|-------|
| Frame size | 5,160 bytes |
| Header | 5 bytes |
| FPS | 15 (configurable 5–30) |
| Bandwidth | ~75 KB/s at 15fps |
| Browser render cost | <1ms (200×344 putImageData) |
| Server broadcast cost | ~0.1ms (tobytes + send) |

---

## Acceptance Criteria

- [ ] Sim tab shows canvas rendering current effect in real time
- [ ] Frame stream subscribes/unsubscribes on tab switch
- [ ] Preview FPS selectable (5, 10, 15, 30)
- [ ] Canvas renders with sharp pixels (pixelated)
- [ ] LED 0 at bottom of canvas
- [ ] Frame data matches LED output
- [ ] Preview mode shows different effect without changing LEDs
- [ ] Preview auto-expires after 10 seconds
- [ ] WebSocket reconnect restores simulator
- [ ] Existing JSON state broadcast unaffected
- [ ] `docs/current-contracts.md` §2 updated with frame stream protocol
- [ ] New routes added to `docs/current-contracts.md` §1
- [ ] All ~219 existing tests pass (regression)

---

## Test Plan

### Automated (pytest)

```python
def test_frame_callback_invoked():
    """Renderer calls frame_callback with logical frame."""

def test_frame_callback_shape():
    """Broadcast frame is (STRIPS, HEIGHT, 3) uint8."""

def test_preview_doesnt_change_active_scene():
    """Preview renders separately from live scene."""

def test_preview_auto_expires():
    """Preview clears after timeout."""

def test_ws_subscribe_unsubscribe():
    """Client can subscribe/unsubscribe to frames channel."""

def test_ws_binary_frame_format():
    """Binary message has correct header byte and payload size."""
```

### Manual (iPhone Safari)

- [ ] Sim tab shows live updating canvas
- [ ] Switching effects updates simulator
- [ ] Switching tabs stops frame stream
- [ ] Preview mode works
- [ ] FPS selector visibly changes update rate

---

## Files Changed

| File | Changes |
|------|---------|
| `pi/app/api/routes/ws.py` | Frame subscriber management, binary WS messages |
| `pi/app/api/routes/simulator.py` | Preview endpoints (new, create_router pattern) |
| `pi/app/api/schemas.py` | PreviewRequest model |
| `pi/app/api/server.py` | Mount simulator router |
| `pi/app/core/renderer.py` | frame_callback, RenderOverride (shared with F4) |
| `pi/app/ui/index.html` | Sim tab + panel |
| `pi/app/ui/static/js/app.js` | simulator canvas, WS binary, subscribe lifecycle |
| `pi/app/ui/static/css/app.css` | Simulator styles |

---

## Future: Live Coding (Not In Scope)

The simulator lays groundwork for Pixel Blaze-style live coding:
code editor → send Python to Pi → hot-reload effect → simulator shows preview.
Requires sandboxed execution, error reporting, security hardening. The
simulator's frame streaming and preview mode directly support this future work.
