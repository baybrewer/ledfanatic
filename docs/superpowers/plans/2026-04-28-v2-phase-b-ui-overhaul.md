# LED Fanatic v2.0 — Phase B: UI Overhaul

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform the control panel from a narrow 480px mobile-only layout into a beautiful, futuristic, full-screen responsive UI that works on iPad, desktop, and phone.

**Architecture:** CSS-only visual upgrade with responsive breakpoints. No framework — stay vanilla JS. The existing `app.js` (2047 lines) and `index.html` stay as the foundation. CSS gets a complete rewrite. HTML gets structural tweaks for the new layout.

**Tech Stack:** Vanilla CSS (custom properties, grid, flexbox, backdrop-filter), vanilla JS, no build step

---

## Current Problems

1. `max-width: 480px` on `#app` — wastes screen on iPad/desktop
2. Effect buttons are plain text blocks — no visual identity
3. No visual hierarchy — everything looks the same
4. No responsive breakpoints for tablet/desktop
5. Tab bar is cramped and plain
6. Status bar is minimal
7. Color scheme is flat — no depth, glow, or texture

## Design Direction

**Aesthetic:** Dark glassmorphism with accent glow. Think sci-fi HUD meets music production software. Subtle gradients, soft glows, glass panels with blur, animated borders.

**Layout tiers:**
- **Phone (≤640px):** Single column, stacked
- **Tablet/iPad (641-1024px):** Two columns — effects grid left, controls/preview right
- **Desktop (>1024px):** Same two columns but wider, with more grid columns for effect cards

Note: A third column (category rail) is deferred to Phase C (effect organization).

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Rewrite | `pi/app/ui/static/css/app.css` | Complete visual overhaul |
| Modify | `pi/app/ui/static/index.html` | Structural changes for responsive layout |
| Modify | `pi/app/ui/static/js/app.js` | Effect card rendering, category logic |

---

### Task 1: Remove 480px Cap + Responsive Shell

**Files:**
- Modify: `pi/app/ui/static/css/app.css`

- [ ] **Step 1: Remove max-width constraint and add responsive container**

Replace the `#app` rule (line 37-44):

```css
#app {
  display: flex;
  flex-direction: column;
  min-height: 100dvh;
  width: 100%;
  max-width: 1400px;
  margin: 0 auto;
  padding: var(--safe-top) 16px var(--safe-bottom);
}

/* Tablet: wider padding */
@media (min-width: 641px) {
  #app { padding-left: 24px; padding-right: 24px; }
}

/* Desktop: even wider */
@media (min-width: 1025px) {
  #app { padding-left: 32px; padding-right: 32px; }
}
```

- [ ] **Step 2: Deploy and verify full-width on iPad**

```bash
bash pi/scripts/deploy.sh ledfanatic.local
```

Open on iPad — should now fill the screen instead of narrow center strip.

- [ ] **Step 3: Commit**

```bash
git add pi/app/ui/static/css/app.css
git commit -m "feat: remove 480px cap, add responsive container up to 1400px"
```

---

### Task 2: Futuristic Color Scheme + Glass Effect Variables

**Files:**
- Modify: `pi/app/ui/static/css/app.css`

- [ ] **Step 1: Upgrade CSS custom properties**

Replace the `:root` block:

```css
:root {
  /* Core palette */
  --bg: #060610;
  --bg-gradient: linear-gradient(135deg, #060610 0%, #0d0d1a 50%, #0a0a18 100%);
  --surface: rgba(20, 20, 35, 0.8);
  --surface2: rgba(30, 30, 50, 0.6);
  --surface-solid: #14141f;
  --border: rgba(100, 100, 180, 0.15);
  --border-glow: rgba(108, 92, 231, 0.3);

  /* Text */
  --text: #e8e8f8;
  --text-dim: #7878a0;
  --text-muted: #4a4a6a;

  /* Accent */
  --accent: #6c5ce7;
  --accent-light: #a29bfe;
  --accent-glow: rgba(108, 92, 231, 0.4);
  --accent-gradient: linear-gradient(135deg, #6c5ce7, #a29bfe);

  /* Status */
  --success: #00cec9;
  --warning: #ffeaa7;
  --danger: #ff6b6b;

  /* Glass */
  --glass-bg: rgba(15, 15, 30, 0.7);
  --glass-border: rgba(100, 100, 180, 0.2);
  --glass-blur: 12px;

  /* Layout */
  --radius: 12px;
  --radius-lg: 16px;
  --safe-top: env(safe-area-inset-top, 0px);
  --safe-bottom: env(safe-area-inset-bottom, 0px);

  /* Transitions */
  --transition-fast: 150ms ease;
  --transition-normal: 250ms ease;
}
```

- [ ] **Step 2: Update body background**

```css
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
  background: var(--bg);
  background-image: var(--bg-gradient);
  /* M3 fix: no background-attachment:fixed — causes scroll jank on iPad */
  color: var(--text);
  overflow-x: hidden;
  min-height: 100dvh;
  user-select: none;
  -webkit-user-select: none;
  line-height: 1.5;
}
```

- [ ] **Step 3: Add glass panel utility class**

```css
.glass {
  background: var(--glass-bg);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius);
}

/* M3 fix: backdrop-filter behind @supports with solid fallback */
@supports (backdrop-filter: blur(1px)) or (-webkit-backdrop-filter: blur(1px)) {
  .glass {
    backdrop-filter: blur(var(--glass-blur));
    -webkit-backdrop-filter: blur(var(--glass-blur));
  }
}

/* M4 fix: respect reduced motion preference */
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    transition-duration: 0.01ms !important;
  }
}
```

- [ ] **Step 4: Add focus-visible styles for keyboard accessibility**

```css
/* M4 fix: keyboard focus visibility */
:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

/* Remove blanket user-select: none — only on controls */
```

Update the body rule to remove `user-select: none`:

```css
body {
  /* ... other properties ... */
  /* M4 fix: removed user-select:none from body — only apply to buttons/controls */
}

button, .tab, .effect-card, .category-btn {
  user-select: none;
  -webkit-user-select: none;
}
```

- [ ] **Step 4: Commit**

```bash
git commit -am "feat: futuristic color scheme with glass effects"
```

---

### Task 3: Status Bar Redesign

**Files:**
- Modify: `pi/app/ui/static/css/app.css`

- [ ] **Step 1: Redesign status bar with glass effect and glow**

```css
header#status-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 16px;
  background: var(--surface-solid);
  border-bottom: 1px solid var(--glass-border);
  position: sticky;
  top: 0;
  z-index: 100;
}

@supports (backdrop-filter: blur(1px)) or (-webkit-backdrop-filter: blur(1px)) {
  header#status-bar {
    background: var(--glass-bg);
    backdrop-filter: blur(16px);
    -webkit-backdrop-filter: blur(16px);
  }
}

header#status-bar h1 {
  font-size: 18px;
  font-weight: 700;
  background: var(--accent-gradient);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  letter-spacing: 0.5px;
}
```

- [ ] **Step 2: Style connection dot with pulse animation**

```css
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
  margin-right: 6px;
}

.dot.connected {
  background: var(--success);
  box-shadow: 0 0 8px var(--success);
  animation: pulse-glow 2s ease-in-out infinite;
}

.dot.disconnected {
  background: var(--danger);
  box-shadow: 0 0 8px var(--danger);
}

@keyframes pulse-glow {
  0%, 100% { box-shadow: 0 0 4px var(--success); }
  50% { box-shadow: 0 0 12px var(--success), 0 0 20px rgba(0, 206, 201, 0.3); }
}
```

- [ ] **Step 3: Commit**

```bash
git commit -am "feat: glass status bar with gradient title and pulse dot"
```

---

### Task 4: Tab Navigation Redesign

**Files:**
- Modify: `pi/app/ui/static/css/app.css`

- [ ] **Step 1: Redesign tabs as pill-style navigation**

```css
#tabs {
  display: flex;
  gap: 4px;
  padding: 8px 16px;
  background: var(--surface-solid);
  border-bottom: 1px solid var(--border);
  overflow-x: auto;
  scrollbar-width: none;
  -ms-overflow-style: none;
}
#tabs::-webkit-scrollbar { display: none; }

.tab {
  padding: 8px 20px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--text-dim);
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  white-space: nowrap;
  transition: all var(--transition-fast);
  position: relative;
}

.tab:hover {
  color: var(--text);
  background: var(--surface2);
}

.tab.active {
  color: var(--text);
  background: var(--accent);
  box-shadow: 0 2px 8px var(--accent-glow);
}

/* Tablet+: larger tabs */
@media (min-width: 641px) {
  .tab {
    padding: 10px 28px;
    font-size: 15px;
  }
}
```

- [ ] **Step 2: Commit**

```bash
git commit -am "feat: pill-style tab navigation with glow"
```

---

### Task 5: Effect Grid — Cards Instead of Plain Buttons

**Files:**
- Modify: `pi/app/ui/static/css/app.css`
- Modify: `pi/app/ui/static/js/app.js`

This is the biggest visual change — effects become cards with category color accents.

**H1 fix:** Active-state matching must use `data-effect` attribute, not `textContent`.
**H2 fix:** All CSS selectors target existing DOM IDs/classes OR this task explicitly
updates them. The plan adds new classes alongside existing IDs where needed.

- [ ] **Step 1: Update HTML classes to match new CSS selectors**

In `index.html`, update the effects panel structure:
- Change `<div id="effects-grid" class="button-grid">` to `<div id="effects-grid" class="effects-grid">`
- Change `<div id="effects-filter-bar">` to `<div id="effects-filter-bar" class="category-filters">`
- Change `<div class="effects-preview">` to `<aside class="effects-sidebar">`
- Change `<div id="active-effect-controls">` to `<div id="active-effect-controls" class="effect-controls">`

Also update `app.js` filter button rendering:
- Change `.filter-btn` class usage to `.category-btn`
- Update `applyEffectsFilter()` to query `.category-btn` instead of `.filter-btn`

- [ ] **Step 2: Define category color map in JS**

In `app.js`, add near the top (after the existing `effectCategory` function):

```javascript
// L2 fix: keyed by DISPLAY category (output of effectCategory()), not raw group
const CATEGORY_COLORS = {
  'Ambient': '#00cec9',
  'Sound Reactive': '#fd79a8',
  'Simulation': '#6c5ce7',
  'Built-in': '#00b894',
  'Game': '#fdcb6e',
  'Classic': '#e17055',
  'Special': '#a29bfe',
  'Other': '#636e72',
};

function getCategoryColor(group) {
  const displayCat = effectCategory(group);
  return CATEGORY_COLORS[displayCat] || '#6c5ce7';
}

// Also update CATEGORY_MAP to cover simulation and game groups:
// Add to existing CATEGORY_MAP: simulation: 'Simulation', game: 'Game'
```

- [ ] **Step 2: Update effect button rendering to cards**

Find the effect button rendering in `loadEffects()` (around line 284) and update the button creation to include a category accent stripe:

```javascript
// Replace the simple button creation with card-style:
const btn = document.createElement('button');
btn.className = 'effect-card';
btn.dataset.effect = name;  // H1 fix: always use data-effect for matching
btn.dataset.group = meta.group || '';
const catColor = getCategoryColor(meta.group);
btn.innerHTML = `
  <div class="effect-card-accent" style="background:${catColor}"></div>
  <div class="effect-card-body">
    <span class="effect-card-name">${meta.label || name.replace(/_/g, ' ')}</span>
    <span class="effect-card-group">${effectCategory(meta.group)}</span>
  </div>
`;
```

Also update the active-effect highlighting code (currently in `activateEffect()` around
line 551 of app.js). Change from `b.textContent === label` to `b.dataset.effect === name`:

```javascript
// H1 fix: match on data-effect attribute, not textContent
document.querySelectorAll('.effect-card').forEach(b => {
  b.classList.toggle('active-scene', b.dataset.effect === name);
});
```

- [ ] **Step 3: Add effect card CSS**

```css
/* Effect cards grid */
.effects-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 8px;
  padding: 8px 0;
}

@media (min-width: 641px) {
  .effects-grid {
    grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
    gap: 10px;
  }
}

@media (min-width: 1025px) {
  .effects-grid {
    grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
    gap: 12px;
  }
}

.effect-card {
  display: flex;
  flex-direction: column;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
  cursor: pointer;
  transition: all var(--transition-fast);
  position: relative;
}

.effect-card:hover {
  border-color: var(--border-glow);
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
}

.effect-card.active-scene {
  border-color: var(--accent);
  box-shadow: 0 0 12px var(--accent-glow), inset 0 0 20px rgba(108, 92, 231, 0.1);
}

.effect-card-accent {
  height: 3px;
  width: 100%;
}

.effect-card-body {
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.effect-card-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
  line-height: 1.3;
}

.effect-card-group {
  font-size: 11px;
  color: var(--text-dim);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
```

- [ ] **Step 4: Update category filter buttons**

```css
.category-filters {
  display: flex;
  gap: 6px;
  padding: 8px 0;
  overflow-x: auto;
  scrollbar-width: none;
  flex-wrap: wrap;
}

.category-btn {
  padding: 6px 14px;
  border: 1px solid var(--border);
  border-radius: 20px;
  background: transparent;
  color: var(--text-dim);
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  white-space: nowrap;
  transition: all var(--transition-fast);
}

.category-btn:hover {
  border-color: var(--accent);
  color: var(--text);
}

.category-btn.active {
  background: var(--accent);
  border-color: var(--accent);
  color: white;
  box-shadow: 0 2px 8px var(--accent-glow);
}
```

- [ ] **Step 5: Commit**

```bash
git commit -am "feat: effect cards with category colors and responsive grid"
```

---

### Task 6: Controls Panel Glass Styling

**Files:**
- Modify: `pi/app/ui/static/css/app.css`

- [ ] **Step 1: Glass-style panels for controls and sections**

```css
.panel {
  padding: 16px 0;
  flex: 1;
}

/* Section headings */
.panel h3 {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-dim);
  text-transform: uppercase;
  letter-spacing: 1px;
  margin-bottom: 12px;
  padding-bottom: 6px;
  border-bottom: 1px solid var(--border);
}

/* Action buttons */
.action-btn {
  padding: 8px 18px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  color: var(--text);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all var(--transition-fast);
}

.action-btn:hover {
  border-color: var(--accent);
  background: var(--surface2);
}

.action-btn.primary {
  background: var(--accent);
  border-color: var(--accent);
  color: white;
}

.action-btn.primary:hover {
  background: var(--accent-light);
  box-shadow: 0 2px 12px var(--accent-glow);
}

/* Input fields */
input[type="text"],
input[type="number"],
select {
  padding: 8px 12px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  color: var(--text);
  font-size: 14px;
  transition: border-color var(--transition-fast);
}

input[type="text"]:focus,
input[type="number"]:focus,
select:focus {
  outline: none;
  border-color: var(--accent);
  box-shadow: 0 0 8px var(--accent-glow);
}

/* Sliders */
input[type="range"] {
  -webkit-appearance: none;
  appearance: none;
  height: 4px;
  background: var(--surface2);
  border-radius: 2px;
  outline: none;
}

input[type="range"]::-webkit-slider-thumb {
  -webkit-appearance: none;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: var(--accent);
  border: 2px solid var(--accent-light);
  cursor: pointer;
  box-shadow: 0 0 6px var(--accent-glow);
}
```

- [ ] **Step 2: Quick controls glass treatment**

```css
#quick-controls {
  padding: 12px 16px;
  background: var(--surface-solid);
  border-bottom: 1px solid var(--glass-border);
}

@supports (backdrop-filter: blur(1px)) or (-webkit-backdrop-filter: blur(1px)) {
  #quick-controls {
    background: var(--glass-bg);
    backdrop-filter: blur(8px);
    -webkit-backdrop-filter: blur(8px);
  }
}

.brightness-control {
  display: flex;
  align-items: center;
  gap: 12px;
}

.brightness-control label {
  font-size: 13px;
  color: var(--text-dim);
  min-width: 70px;
}

.brightness-control input[type="range"] {
  flex: 1;
}
```

- [ ] **Step 3: Commit**

```bash
git commit -am "feat: glass panels, styled inputs/sliders/buttons"
```

---

### Task 7: Responsive Effects Layout (Tablet + Desktop)

**Files:**
- Modify: `pi/app/ui/static/index.html`
- Modify: `pi/app/ui/static/css/app.css`

- [ ] **Step 1: Reuse existing layout structure — move controls into sidebar**

The effects panel already has `.effects-layout` / `.effects-main` / `.effects-preview`
in `index.html` (around line 80). Do NOT add a new wrapper. Instead:

1. Rename `.effects-preview` to `.effects-sidebar` (it already contains the preview canvas)
2. Move `#active-effect-controls` INTO `.effects-sidebar`, below the preview canvas
3. This puts the effect grid on the left and controls+preview on the right

No new wrapper divs — just rearrange existing elements.

- [ ] **Step 2: Add responsive layout CSS**

```css
.effects-layout {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.effects-main {
  flex: 1;
  min-width: 0;
}

.effects-sidebar {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

/* Tablet: side-by-side */
@media (min-width: 641px) {
  .effects-layout {
    flex-direction: row;
  }
  .effects-sidebar {
    width: 300px;
    flex-shrink: 0;
    position: sticky;
    top: 60px;
    max-height: calc(100dvh - 120px);
    overflow-y: auto;
  }
}

/* Desktop: wider sidebar */
@media (min-width: 1025px) {
  .effects-sidebar {
    width: 360px;
  }
}
```

- [ ] **Step 3: Style the effect controls sidebar as a glass card**

```css
.effect-controls {
  background: var(--surface-solid);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-lg);
  padding: 16px;
}

@supports (backdrop-filter: blur(1px)) or (-webkit-backdrop-filter: blur(1px)) {
  .effect-controls {
    background: var(--glass-bg);
    backdrop-filter: blur(8px);
    -webkit-backdrop-filter: blur(8px);
  }
}

.effect-controls h4 {
  font-size: 16px;
  font-weight: 600;
  margin-bottom: 12px;
  color: var(--text);
}

.param-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}

.param-row label {
  font-size: 12px;
  color: var(--text-dim);
  min-width: 80px;
}

.param-row input[type="range"] {
  flex: 1;
}

.param-row .param-value {
  font-size: 12px;
  color: var(--accent-light);
  min-width: 40px;
  text-align: right;
}
```

- [ ] **Step 4: Commit**

```bash
git commit -am "feat: responsive effects layout — sidebar on tablet/desktop"
```

---

### Task 8: Deploy + Visual Polish Pass

- [ ] **Step 1: Deploy full UI overhaul**

```bash
bash pi/scripts/deploy.sh ledfanatic.local
```

- [ ] **Step 2: Test on iPad, iPhone, desktop browser**

Verify:
- Full-width layout on all devices
- Glass effects render properly
- Effect cards display with category colors
- Category filter pills work
- Sidebar sticky on tablet/desktop
- Sliders have custom styling
- Status bar is glassmorphic with gradient title
- Tab pills glow when active

- [ ] **Step 3: Final commit and tag**

```bash
git commit -am "Phase B complete: futuristic responsive UI overhaul"
git tag v2.0.0-ui
```

---

## Acceptance Criteria

| Criterion | Test |
|-----------|------|
| Full-width on iPad/desktop | Visual: no narrow center strip |
| Responsive at 3 breakpoints | Resize browser window |
| Glass effect on status bar | Visual: backdrop blur visible |
| Effect cards with category colors | Visual: colored accent stripes |
| Category filter pills | Click: filters effects |
| Sidebar on tablet | Rotate iPad: controls on right |
| Custom slider styling | Visual: purple thumb with glow |
| Tab pills with glow | Click: active tab glows |
| Works on phone | Narrow viewport: single column |
