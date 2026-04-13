# F2: UI Tooltips & Polish

## Summary

Add tooltips to every interactive element in the web UI. Fix cryptic button
labels (the "B" and "R" buttons). Make the interface self-documenting so a
new user can understand every control without reading source code.

**No backend changes.** This is purely HTML/CSS/JS.

---

## Problem

The current UI has:
- **"B" button** in the header → Blackout ON (turns all LEDs off)
- **"R" button** in the header → Blackout OFF (resumes display)
- No tooltips anywhere
- Effect buttons show only the effect name with no description
- Diagnostic buttons have terse labels
- No visual indication of what brightness auto-toggle does

A user picking up the phone for the first time has no way to know what
"B" and "R" do, what "Seam Test" means, or what "Solar" automation does.

---

## Tooltip Implementation

### Approach: CSS `title` + Custom Touch Tooltip

On desktop, native `title` attributes show tooltips on hover. On mobile
(the primary use case), there's no hover. We need a custom tooltip that
shows on **long-press** (touch and hold ~500ms).

```javascript
function initTooltips() {
  document.querySelectorAll('[data-tooltip]').forEach(el => {
    let timer;

    el.addEventListener('touchstart', (e) => {
      timer = setTimeout(() => {
        showTooltip(el, el.dataset.tooltip);
      }, 500);
    });

    el.addEventListener('touchend', () => {
      clearTimeout(timer);
      hideTooltip();
    });

    el.addEventListener('touchmove', () => {
      clearTimeout(timer);
      hideTooltip();
    });

    // Desktop: also use title for native tooltip
    el.title = el.dataset.tooltip;
  });
}
```

### Tooltip Element

```html
<div id="tooltip" class="tooltip hidden">
  <span id="tooltip-text"></span>
</div>
```

```css
.tooltip {
  position: fixed;
  z-index: 300;
  background: #1e1e2e;
  color: #e0e0f0;
  border: 1px solid #3a3a5e;
  border-radius: 8px;
  padding: 8px 12px;
  font-size: 13px;
  max-width: 250px;
  pointer-events: none;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.5);
  transition: opacity 0.15s;
}
.tooltip.hidden {
  display: none;
}
```

### Positioning

```javascript
function showTooltip(anchor, text) {
  const tooltip = document.getElementById('tooltip');
  const tooltipText = document.getElementById('tooltip-text');
  tooltipText.textContent = text;
  tooltip.classList.remove('hidden');

  const rect = anchor.getBoundingClientRect();
  const tooltipRect = tooltip.getBoundingClientRect();

  // Position above the element, centered
  let left = rect.left + rect.width / 2 - tooltipRect.width / 2;
  let top = rect.top - tooltipRect.height - 8;

  // Clamp to viewport
  left = Math.max(8, Math.min(left, window.innerWidth - tooltipRect.width - 8));
  if (top < 8) top = rect.bottom + 8;  // flip below if no room above

  tooltip.style.left = `${left}px`;
  tooltip.style.top = `${top}px`;
}
```

---

## Button Label Fixes

### Header Blackout Buttons

**Current:**
```html
<button id="blackout-on-btn" class="icon-btn">B</button>
<button id="blackout-off-btn" class="icon-btn">R</button>
```

**New:**
```html
<button id="blackout-on-btn" class="icon-btn" data-tooltip="Blackout — turn all LEDs off">⏻</button>
<button id="blackout-off-btn" class="icon-btn" data-tooltip="Resume — turn LEDs back on">▶</button>
```

Use Unicode symbols instead of letters:
- `⏻` (U+23FB, power symbol) for blackout
- `▶` (U+25B6, play triangle) for resume

**Alternative if Unicode rendering is inconsistent on iOS:**
```html
<button id="blackout-on-btn" class="icon-btn" data-tooltip="Blackout — turn all LEDs off">OFF</button>
<button id="blackout-off-btn" class="icon-btn" data-tooltip="Resume — turn LEDs back on">ON</button>
```

---

## Complete Tooltip Map

### Status Bar

| Element | Tooltip Text |
|---------|-------------|
| Connection dot | "Connection status — green = connected to pillar" |
| FPS display | "Current rendering frame rate" |
| Blackout ON button | "Blackout — turn all LEDs off" |
| Blackout OFF button | "Resume — turn LEDs back on" |

### Quick Controls

| Element | Tooltip Text |
|---------|-------------|
| Brightness slider | "Manual brightness cap (0–100%)" |
| Auto toggle | "Solar automation — adjusts brightness based on time of day" |
| Phase badge | "Current solar phase (night/dawn/day/dusk)" |
| Effective readout | "Actual brightness after solar adjustment" |

### Tab Buttons

| Tab | Tooltip |
|-----|---------|
| Live | "Current scene and saved presets" |
| Effects | "Choose a generative or audio-reactive effect" |
| Media | "Upload and play images, GIFs, and videos" |
| Audio | "Audio input settings and reactive effects" |
| Diag | "Hardware diagnostics and wiring tests" |
| System | "System settings, FPS, and power controls" |

### Effects Panel

Each effect button gets a tooltip describing what it does:

| Effect | Tooltip |
|--------|---------|
| solid_color | "Fill all LEDs with a single color" |
| vertical_gradient | "Animated color gradient scrolling vertically" |
| rainbow_rotate | "Rainbow colors rotating around the pillar" |
| plasma | "Organic flowing plasma pattern" |
| twinkle | "Random twinkling star-like sparkles" |
| spark | "Upward-moving sparks with glowing trails" |
| noise_wash | "Smooth flowing noise pattern" |
| color_wipe | "Color sweep moving up the pillar" |
| scanline | "Horizontal line scanning up the pillar" |
| fire | "Realistic fire simulation" |
| sine_bands | "Animated sine-wave color bands" |
| cylinder_rotate | "Pattern rotating around the cylinder" |
| seam_pulse | "Highlight the seam between first and last strip" |
| diagnostic_labels | "Show each strip in a distinct color for identification" |

| Audio Effect | Tooltip |
|-------------|---------|
| vu_pulse | "VU meter — fills based on audio volume" |
| band_colors | "Low/mid/high frequency bands as color zones" |
| beat_flash | "Flash on each detected beat" |
| energy_ring | "Rotating ring driven by audio energy" |
| spectral_glow | "Columns glow based on frequency spectrum" |

### Diagnostics Panel

| Button | Tooltip |
|--------|---------|
| Strip Identify | "Light each strip in a unique color to verify wiring" |
| Bottom-Top Sweep | "White sweep from bottom to top on all strips" |
| Serpentine Chase | "Chase light following the serpentine wiring path" |
| Seam Test | "Highlight the wrap boundary between strip 9 and strip 0" |
| Channel Identify | "Light one OctoWS2811 channel at a time" |
| RGB Order | "Cycle through red, green, blue to verify color order" |
| All Black | "Turn all LEDs completely off (Teensy test pattern)" |
| All White | "Turn all LEDs white at safe brightness" |
| Heartbeat | "Gentle breathing pulse (Teensy connectivity test)" |
| Return to Normal | "Clear test pattern and return to active scene" |

### Media Panel

| Element | Tooltip |
|---------|---------|
| Upload button | "Upload an image, GIF, or video to display on the pillar" |
| Media items | "Tap to play this media on the pillar" |

### Audio Panel

| Element | Tooltip |
|---------|---------|
| Device select | "Choose which microphone to use for audio input" |
| Sensitivity slider | "Audio detection sensitivity (higher = more responsive)" |
| Gain slider | "Audio signal amplification (higher = louder input)" |
| Start Audio | "Begin audio analysis for reactive effects" |
| Stop Audio | "Stop audio analysis" |
| Meter bars | "Live audio levels — bass (red), mid (green), high (purple)" |

### System Panel

| Element | Tooltip |
|---------|---------|
| FPS select | "Target rendering frame rate (higher = smoother but more CPU)" |
| Transport status | "USB connection status to Teensy controller" |
| Firmware version | "Teensy firmware version" |
| Frames sent | "Total frames sent to Teensy since startup" |
| Auth token input | "Bearer token for API authentication" |
| Update Token | "Save authentication token to this device" |
| Strip Setup | "Configure per-strip LED count, color order, and chipset" |
| Restart App | "Restart the pillar controller service" |
| Reboot Pi | "Reboot the Raspberry Pi (takes ~30 seconds)" |

---

## Effect Description Source

Tooltips for effects should come from the effect registry, not be hardcoded
in JavaScript. Add a `description` field to the effect classes:

```python
class Fire(Effect):
    """Realistic fire simulation"""  # <- This becomes the tooltip
    ...
```

Modify `GET /api/scenes/list` to include descriptions:

**Current response:**
```json
{
  "generative": ["solid_color", "fire", ...],
  "audio": ["vu_pulse", ...],
  "diagnostic": ["diag_strip_identify", ...]
}
```

**New response:**
```json
{
  "generative": [
    {"name": "solid_color", "description": "Fill all LEDs with a single color"},
    {"name": "fire", "description": "Realistic fire simulation"},
    ...
  ],
  "audio": [ ... ],
  "diagnostic": [ ... ]
}
```

This is the **only backend change** in F2 — adding descriptions to the
scene list endpoint. The descriptions come from each Effect class's docstring.

---

## Additional Polish Items

### 1. Active State Clarity

Currently, active buttons get a purple glow. Add a small text label below
the effects grid showing the active effect name:

```html
<p id="active-effect-label" class="dim">Active: fire</p>
```

### 2. Confirmation Toasts

Add a lightweight toast notification for successful actions:

```javascript
function showToast(message, duration = 2000) {
  const toast = document.getElementById('toast');
  toast.textContent = message;
  toast.classList.remove('hidden');
  setTimeout(() => toast.classList.add('hidden'), duration);
}
```

```html
<div id="toast" class="toast hidden"></div>
```

```css
.toast {
  position: fixed;
  bottom: 80px;
  left: 50%;
  transform: translateX(-50%);
  background: #2a2a3e;
  color: #e0e0f0;
  padding: 10px 20px;
  border-radius: 8px;
  font-size: 14px;
  z-index: 250;
  transition: opacity 0.3s;
}
.toast.hidden { opacity: 0; pointer-events: none; }
```

### 3. Brightness Percentage Visible on Slider

The current `#brightness-value` span shows "80%" but it's easy to miss.
Make it more prominent:

```css
#brightness-value {
  font-size: 1.2em;
  font-weight: bold;
  min-width: 3em;
  text-align: right;
}
```

### 4. Dangerous Action Styling

The Reboot button is already `.danger` (red). Add a double-confirmation:

```javascript
document.getElementById('reboot-btn').addEventListener('click', async () => {
  if (!confirm('Reboot the Raspberry Pi? This will take ~30 seconds.')) return;
  if (!confirm('Are you sure? All LEDs will go dark during reboot.')) return;
  await api('POST', '/api/system/reboot');
});
```

---

## Acceptance Criteria

- [ ] Every button and interactive element has a `data-tooltip` attribute
- [ ] Long-press (500ms) on mobile shows tooltip above the element
- [ ] Desktop hover shows native title tooltip
- [ ] Blackout buttons show clear symbols (not just "B" and "R")
- [ ] Effect buttons show effect descriptions on long-press
- [ ] `GET /api/scenes/list` includes description field per effect
- [ ] Descriptions come from Effect class docstrings (SSOT)
- [ ] Toast notifications appear for save/apply actions
- [ ] No regressions: all existing functionality works identically

---

## Test Plan

### Manual Testing Checklist (iPhone Safari)

- [ ] Long-press on "⏻" shows "Blackout — turn all LEDs off"
- [ ] Long-press on "▶" shows "Resume — turn LEDs back on"
- [ ] Long-press on brightness slider shows tooltip
- [ ] Long-press on each tab shows description
- [ ] Long-press on an effect shows effect description
- [ ] Long-press on a diagnostic button shows description
- [ ] Tooltip doesn't overflow screen on edge buttons
- [ ] Tooltip disappears on finger lift
- [ ] Tooltip doesn't trigger the button's tap action
- [ ] Toast appears after saving preset
- [ ] Toast appears after changing FPS
- [ ] Toast appears after uploading media

### Automated (pytest)

```python
def test_scenes_list_includes_descriptions():
    """GET /api/scenes/list returns description for each effect."""

def test_all_effects_have_docstrings():
    """Every registered effect class has a non-empty docstring."""
```

---

## Files Changed

| File | Changes |
|------|---------|
| `pi/app/ui/index.html` | Add `data-tooltip` attrs, fix button labels, add toast + tooltip elements |
| `pi/app/ui/static/css/app.css` | Tooltip styles, toast styles, brightness polish |
| `pi/app/ui/static/js/app.js` | `initTooltips()`, `showTooltip()`, `showToast()`, load descriptions |
| `pi/app/api/server.py` | Modify `/api/scenes/list` to include descriptions |
| `pi/app/effects/generative.py` | Ensure all effect classes have docstrings |
| `pi/app/effects/audio_reactive.py` | Ensure all effect classes have docstrings |
| `pi/app/diagnostics/tests.py` | Ensure all diagnostic effects have docstrings |
