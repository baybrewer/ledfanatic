# Phase 5 — Inline Simulator Preview

## Goal

The Sim tab shows a real-time pixel-dot visualization of the active animation on the right side of the interface. Pixels are small (like the letter "o"), and the layout matches the physical LED arrangement from the installation config.

## Design

### Layout

```
┌──────────────────────────────────────────────────┐
│ Sim                                               │
│                                                    │
│ ┌─ Controls ──────────┐  ┌─ Preview ────────────┐ │
│ │ Effect: [▼ Aurora  ] │  │                      │ │
│ │ Speed:  [═══●══] 1.5 │  │  ○○○○○○○○○○         │ │
│ │ Palette:[▼ Ocean   ] │  │  ○○○○○○○○○○         │ │
│ │                      │  │  ○○○○○○○○○○         │ │
│ │ [Preview] [Stop]     │  │  ○○○○○○○○○○         │ │
│ │                      │  │  ... (172 rows)      │ │
│ │ Status: Previewing   │  │  ○○○○○○○○○○         │ │
│ │ FPS: 30              │  │  ○○○○○○○○○○         │ │
│ │                      │  │                      │ │
│ │ Preview does not     │  │  S0 S1 S2 ... S9    │ │
│ │ change live LEDs.    │  │  ↑  ↓  ↑  ↓  ...   │ │
│ └──────────────────────┘  └──────────────────────┘ │
│                                                    │
└──────────────────────────────────────────────────┘
```

### Pixel rendering

**Size:** Each LED is a filled circle ~6px diameter (the size of a small "o" in body text)
**Spacing:** ~2px gap between pixels
**Total canvas:** ~80px wide × ~1400px tall for 10×172 (scrollable)
**Colors:** Actual RGB values from the effect render

### Layout from installation config

The simulator reads the installation config to determine:
- Number of strips and their order
- Direction arrows (↑ for bottom_to_top, ↓ for top_to_bottom)
- Enabled/disabled strips (disabled = dimmed)
- Strip labels at bottom

If `spatial_map.json` exists and geometry_mode is `front_projection`, use the UV coordinates to position pixels in their physical layout instead of the grid.

### Canvas rendering approach

Use HTML5 Canvas with `OffscreenCanvas` or direct `2d` context:

```javascript
function renderSimulator(frameData, width, height) {
  const ctx = simCanvas.getContext('2d');
  const pixelSize = 6;
  const gap = 2;
  const pitch = pixelSize + gap;

  ctx.fillStyle = '#08080c';
  ctx.fillRect(0, 0, simCanvas.width, simCanvas.height);

  for (let x = 0; x < width; x++) {
    for (let y = 0; y < height; y++) {
      const idx = (x * height + y) * 3;
      const r = frameData[idx];
      const g = frameData[idx + 1];
      const b = frameData[idx + 2];

      if (r === 0 && g === 0 && b === 0) continue; // skip black

      const cx = x * pitch + pixelSize / 2 + 4;
      const cy = y * pitch + pixelSize / 2 + 4;

      ctx.beginPath();
      ctx.arc(cx, cy, pixelSize / 2, 0, Math.PI * 2);
      ctx.fillStyle = `rgb(${r},${g},${b})`;
      ctx.fill();
    }
  }
}
```

### Scrolling

The canvas is 172 rows × 8px pitch = ~1376px tall. On mobile, the canvas container should scroll vertically. A "fit to screen" toggle scales the canvas to fit the viewport width, making pixels tiny but showing the full pillar.

### View modes

1. **Grid view** (default) — 10 columns evenly spaced, all 172 rows
2. **Physical view** (if spatial_map exists) — pixels positioned at their UV coordinates
3. **Fit view** — scaled to fit viewport (for overview)

### WebSocket frame protocol

Already implemented in preview service. Binary frames with header:
```
u8   message_type  (0x01 = frame)
u32  frame_id
u16  width
u16  height
u8   encoding      (0 = RGB)
payload = width * height * 3 bytes
```

### Strip labels and direction indicators

Below the canvas, show:
- Strip IDs (S0, S1, ..., S9)
- Direction arrows (↑/↓)
- Current effect name and palette

## HTML changes

Replace the current Sim tab content with a two-column layout:
- Left: controls (effect selector, params, start/stop)
- Right: canvas container with scroll

## CSS changes

- `.sim-layout` — flexbox two-column
- `.sim-controls` — fixed width left panel
- `.sim-canvas-wrap` — scrollable right panel
- `.sim-pixel` — not needed (canvas-rendered)
- `.sim-strip-labels` — bottom strip label row
- Media queries: stack vertically on narrow screens

## Performance

- Throttle to 30 FPS max for preview
- Skip rendering if tab not visible (use `document.hidden`)
- Use `requestAnimationFrame` for smooth rendering
- Disconnect WebSocket when leaving Sim tab

## Tests

- Preview service renders correct frame dimensions
- Canvas receives and parses binary frames
- Strip labels match installation config
- Direction indicators correct for even/odd strips

## Gate

- Sim tab shows live pixel-dot preview
- Pixel layout matches physical arrangement
- Preview does not affect live LEDs
- Scrollable on mobile
- Disconnects when leaving tab
