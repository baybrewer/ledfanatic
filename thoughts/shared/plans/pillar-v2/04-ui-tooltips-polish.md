# F2: UI Tooltips & Polish

## Summary

Add tooltips to every interactive element in the web UI. Fix cryptic button
labels (the "B" and "R" buttons). Make the interface self-documenting so a
new user can understand every control without reading source code.

**Backend change**: Add `description` field to the scene list endpoint.
Everything else is HTML/CSS/JS.

---

## Problem

- **"B" button** = Blackout ON. **"R" button** = Blackout OFF. Not obvious.
- No tooltips anywhere.
- Effect buttons show only names, no descriptions.
- Diagnostic buttons have terse labels.

---

## Tooltip Implementation

### Approach: `data-tooltip` + Custom Touch Tooltip

Desktop: native `title` attribute. Mobile (primary use case): long-press
(500ms touch-hold) shows custom tooltip.

```javascript
function initTooltips() {
  document.querySelectorAll('[data-tooltip]').forEach(el => {
    let timer;
    el.addEventListener('touchstart', (e) => {
      timer = setTimeout(() => showTooltip(el, el.dataset.tooltip), 500);
    });
    el.addEventListener('touchend', () => { clearTimeout(timer); hideTooltip(); });
    el.addEventListener('touchmove', () => { clearTimeout(timer); hideTooltip(); });
    el.title = el.dataset.tooltip;
  });
}
```

### Tooltip Element

```html
<div id="tooltip" class="tooltip hidden"><span id="tooltip-text"></span></div>
```

```css
.tooltip {
  position: fixed; z-index: 300;
  background: #1e1e2e; color: #e0e0f0;
  border: 1px solid #3a3a5e; border-radius: 8px;
  padding: 8px 12px; font-size: 13px; max-width: 250px;
  pointer-events: none; box-shadow: 0 4px 12px rgba(0,0,0,0.5);
}
```

### Positioning

Centered above element, clamped to viewport. Flip below if no room above.

---

## Button Label Fixes

### Header Blackout Buttons

**Current:** `B` and `R`

**New:**
```html
<button id="blackout-on-btn" class="icon-btn" data-tooltip="Blackout — turn all LEDs off">OFF</button>
<button id="blackout-off-btn" class="icon-btn" data-tooltip="Resume — turn LEDs back on">ON</button>
```

---

## Complete Tooltip Map

### Status Bar
| Element | Tooltip |
|---------|---------|
| Connection dot | "Connection status — green = connected to pillar" |
| FPS display | "Current rendering frame rate" |
| Blackout ON | "Blackout — turn all LEDs off" |
| Blackout OFF | "Resume — turn LEDs back on" |

### Quick Controls
| Element | Tooltip |
|---------|---------|
| Brightness slider | "Manual brightness cap (0–100%)" |
| Auto toggle | "Solar automation — adjusts brightness by time of day" |
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

### Effects Panel (from Effect docstrings)
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
| diagnostic_labels | "Show each strip in a distinct color" |

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

### Media, Audio, System Panels
(Full tooltip table same as original doc — abbreviated here for space.
Every interactive element gets a `data-tooltip` attribute.)

---

## Effect Description Source (SSOT)

Tooltips for effects come from Effect class docstrings — not hardcoded in JS.

### Backend change: `pi/app/api/routes/scenes.py`

The current scene list response format is:
```json
{
  "effects": {
    "fire": {"type": "generative"},
    "vu_pulse": {"type": "audio"},
    ...
  },
  "current": "fire"
}
```

Add a `description` field from the Effect class docstring:
```json
{
  "effects": {
    "fire": {"type": "generative", "description": "Realistic fire simulation"},
    ...
  },
  "current": "fire"
}
```

**Implementation** (in `scenes.py:list_effects()`):

```python
@router.get("/list")
async def list_effects():
    all_effects = {}
    for name, cls in deps.renderer.effect_registry.items():
        effect_type = 'generative'
        if name in AUDIO_EFFECTS:
            effect_type = 'audio'
        elif name in DIAGNOSTIC_EFFECTS:
            effect_type = 'diagnostic'
        all_effects[name] = {
            'type': effect_type,
            'description': (cls.__doc__ or '').strip(),
        }
    return {'effects': all_effects, 'current': deps.render_state.current_scene}
```

**Prerequisite**: Ensure all Effect classes have non-empty docstrings. Add
missing docstrings to `pi/app/effects/generative.py`,
`pi/app/effects/audio_reactive.py`, and `pi/app/diagnostics/patterns.py`
before this change.

### Frontend change

```javascript
// In loadEffects(), when building effect buttons:
function buildEffectButton(name, info) {
  const btn = document.createElement('button');
  btn.textContent = name.replace(/_/g, ' ');
  btn.dataset.tooltip = info.description || name;
  btn.addEventListener('click', () => activateEffect(name));
  return btn;
}
```

---

## Additional Polish

### Toast Notifications

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

### Dangerous Action Styling

Double-confirm for reboot:
```javascript
document.getElementById('reboot-btn').addEventListener('click', async () => {
  if (!confirm('Reboot the Pi? Takes ~30 seconds.')) return;
  if (!confirm('Are you sure? All LEDs will go dark.')) return;
  await api('POST', '/api/system/reboot');
});
```

---

## Acceptance Criteria

- [ ] Every button and interactive element has `data-tooltip`
- [ ] Long-press (500ms) on mobile shows tooltip
- [ ] Desktop hover shows native title tooltip
- [ ] Blackout buttons show "OFF"/"ON" (not "B"/"R")
- [ ] Effect buttons show descriptions from docstrings on long-press
- [ ] Scene list endpoint includes `description` field per effect
- [ ] Descriptions come from Effect `__doc__` (SSOT)
- [ ] Toast notifications for save/apply actions
- [ ] All existing ~219 tests pass (regression)

---

## Test Plan

### Automated (pytest)

```python
def test_scenes_list_includes_descriptions():
    """GET /api/scenes/list returns description for each effect."""

def test_all_effects_have_docstrings():
    """Every registered effect class has a non-empty docstring."""
```

### Manual (iPhone Safari)

- [ ] Long-press shows tooltips for all buttons
- [ ] Tooltip doesn't overflow screen edges
- [ ] Tooltip doesn't trigger tap action
- [ ] Toast appears after saving preset, changing FPS, uploading media

---

## Files Changed

| File | Changes |
|------|---------|
| `pi/app/ui/index.html` | `data-tooltip` attrs, fix button labels, toast + tooltip elements |
| `pi/app/ui/static/css/app.css` | Tooltip styles, toast styles |
| `pi/app/ui/static/js/app.js` | `initTooltips()`, `showTooltip()`, `showToast()`, load descriptions |
| `pi/app/api/routes/scenes.py` | Add `description` from `cls.__doc__` to scene list |
| `pi/app/effects/generative.py` | Ensure all classes have docstrings |
| `pi/app/effects/audio_reactive.py` | Ensure all classes have docstrings |
| `pi/app/diagnostics/patterns.py` | Ensure all classes have docstrings |
