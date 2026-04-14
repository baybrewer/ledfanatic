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

**Internal canvas size:** 86px wide × 1382px tall (10 dots × 8px pitch + 6px margin, 172 dots × 8px pitch + 6px margin).
**Display size:** CSS-controlled, NOT `width: 100%`. Two modes:
- **1:1 mode (default on desktop):** Canvas displayed at native 86×1382px inside a scrollable container. Each dot is exactly 6px — small like a lowercase "o".
- **Fit mode (default on mobile ≤480px):** Canvas scaled to fit the available *height* (`max-height: calc(100dvh - 120px)` for sticky controls), with auto width. This shows the full pillar at once in a compact overview. The 86×1382 canvas scaled to ~500px tall becomes ~31px wide — small but visible as a strip overview. User can pinch-zoom for detail. Alternatively, use `transform: scale()` on a wrapper div to control both axes proportionally.

**Important:** Set `canvas.width = 86; canvas.height = 1382;` as the internal resolution. Control display size ONLY via CSS `width`/`height` on the canvas element, NOT via canvas attributes. Use `image-rendering: pixelated` for crisp scaling.

**Colors:** Actual RGB values from the effect render. Black pixels (0,0,0) are drawn as very dark gray (8,8,12) to show the grid.

### Layout from installation config

The simulator reads the installation config to determine:
- Number of strips and their order
- Direction arrows (↑ for bottom_to_top, ↓ for top_to_bottom)
- Enabled/disabled strips (disabled = dimmed)
- Strip labels at bottom

If `spatial_map.json` exists and geometry_mode is `front_projection`, use the UV coordinates to position pixels in their physical layout instead of the grid.

### Mobile layout

On phones (≤480px), the Sim tab uses a **stacked layout** (not two-column):
1. Sticky control bar: effect selector + Preview/Stop buttons
2. Scrollable canvas below
3. Advanced controls (speed, palette, FPS) collapsible below the canvas

On wider screens (>480px), two-column layout with controls on the left.

### Canvas rendering approach

Use HTML5 Canvas with `2d` context. Internal resolution is fixed; CSS controls display size:

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

### Preview/live parity fix

The current `PreviewService` instantiates effects at `width=10` while the live renderer uses `internal_width=40` and downsamples. This causes preview/live mismatch for effects that use the extra columns.

**Fix:** `PreviewService.start()` must use the same `internal_width` as the live renderer, then downsample to 10 before streaming. This matches the live render path and keeps the preview faithful.

### Strip labels and direction indicators

Below the canvas, show:
- Strip IDs (S0, S1, ..., S9)
- Direction arrows (↑/↓)
- Current effect name and palette

## HTML changes

Replace the current Sim tab with a responsive layout:
- **Mobile (≤480px):** Stacked — sticky control bar on top, scrollable canvas below
- **Desktop (>480px):** Two-column — controls left, canvas right

## CSS changes

- `.sim-layout` — flexbox column (mobile) / row (desktop via media query)
- `.sim-controls` — sticky top bar on mobile, fixed-width left panel on desktop
- `.sim-canvas-wrap` — scrollable container, `overflow-y: auto; max-height: 70vh;`
- `.sim-canvas` — `image-rendering: pixelated;` for crisp scaling
- `.sim-strip-labels` — bottom strip label row
- View toggle: "1:1" vs "Fit" button switches CSS class on canvas container
- `@media (min-width: 480px)` — switch to side-by-side layout

## Performance

- Throttle to 30 FPS max for preview
- Skip rendering if tab not visible (use `document.hidden`)
- Use `requestAnimationFrame` for smooth rendering
- Disconnect WebSocket when leaving Sim tab

## Tests

- Preview service renders correct frame dimensions
- Canvas receives and parses binary frames
- Strip labels match installation config
- Direction indicators read from each strip's `direction` field in installation config (NOT assumed from even/odd parity)

## Gate

- Sim tab shows live pixel-dot preview
- Pixel layout matches physical arrangement
- Preview does not affect live LEDs
- Scrollable on mobile
- Disconnects when leaving tab
