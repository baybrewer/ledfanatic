# Setup UI Revision — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Update the Setup tab UI to work with the new `/api/layout` endpoints (the old `/api/pixel-map` was removed).

**Architecture:** Single-file change to `pi/app/ui/static/js/app.js`. Replace the "Pixel Map Setup" section (lines ~885–1446) with equivalent functionality using the new API shape. Segments are now nested under outputs and use `start + direction + length` instead of `start + end`. Test-segment uses segment `id` strings instead of numeric indices.

**Tech Stack:** Vanilla JS, HTML (inline in app.js template strings), deployed to Pi for testing

---

## File Structure

| File | Responsibility | Action |
|------|---------------|--------|
| `pi/app/ui/static/js/app.js` | Frontend application | Modify (lines 885–1446) |

---

## Task 1: Replace API Calls and Data Model

**Files:**
- Modify: `pi/app/ui/static/js/app.js:885-1446`

This task replaces the entire "Pixel Map Setup" section with the new layout-based version. Since it's all tightly coupled, we do it as one coordinated replacement.

- [ ] **Step 1: Replace the section from `// --- Pixel Map Setup ---` through `initSetup()` function end**

Find the block starting at `// --- Pixel Map Setup ---` (around line 885) and ending just before `// --- Brightness ---` (around line 1448). Replace the ENTIRE block with:

```javascript
// --- Layout Setup ---

const COLOR_ORDERS = ['BGR','RGB','GRB','GBR','BRG','RBG'];
const DIRECTIONS = ['+y', '-y', '+x', '-x'];

let _layoutData = null;
let _layoutApplyTimer = null;

function scheduleApply() {
  if (_layoutApplyTimer) clearTimeout(_layoutApplyTimer);
  _layoutApplyTimer = setTimeout(() => applyLayout(), 500);
}

function segmentColor(index) {
  const hue = (index * 137.508) % 360;
  return `hsl(${hue}, 75%, 55%)`;
}

function showPmStatus(msg, isError = false) {
  const el = document.getElementById('pm-status');
  if (!el) return;
  el.textContent = msg;
  el.className = isError ? 'status-msg error' : 'status-msg';
  setTimeout(() => { el.textContent = ''; }, 4000);
}

// --- Flatten layout data for rendering (outputs → flat segment list) ---
function flattenSegments(data) {
  const segments = [];
  if (!data || !data.outputs) return segments;
  for (const output of data.outputs) {
    for (const seg of output.segments) {
      segments.push({
        ...seg,
        channel: output.channel,
        output_id: output.id,
        color_order: output.color_order,
      });
    }
  }
  return segments;
}

// --- Compute segment end point from start + direction + length ---
function segmentEnd(seg) {
  const [sx, sy] = [seg.start.x, seg.start.y];
  const len = seg.length - 1;
  switch (seg.direction) {
    case '+x': return { x: sx + len, y: sy };
    case '-x': return { x: sx - len, y: sy };
    case '+y': return { x: sx, y: sy + len };
    case '-y': return { x: sx, y: sy - len };
    default: return { x: sx, y: sy };
  }
}

async function loadPixelMap() {
  const data = await api('GET', '/api/layout');
  if (!data || data.error) return;
  _layoutData = data;

  const originSelect = document.getElementById('pm-origin-select');
  if (originSelect && data.matrix) {
    originSelect.value = data.matrix.origin || 'bottom_left';
  }

  const gwInput = document.getElementById('pm-grid-w');
  const ghInput = document.getElementById('pm-grid-h');
  if (gwInput) gwInput.value = data.matrix ? data.matrix.width : '';
  if (ghInput) ghInput.value = data.matrix ? data.matrix.height : '';

  renderGridSVG(data);
  renderSegmentTable(data);
  renderSegmentCards(data);
  updateSummary(data);
}

function renderGridSVG(data) {
  const svg = document.getElementById('pm-grid-svg');
  if (!svg) return;

  const segments = flattenSegments(data);
  const gridW = data.compiled ? data.compiled.width : (data.matrix ? data.matrix.width : 0);
  const gridH = data.compiled ? data.compiled.height : (data.matrix ? data.matrix.height : 0);

  if (gridW === 0 || gridH === 0 || segments.length === 0) {
    svg.innerHTML = '<text x="20" y="30" fill="#666" font-size="14">No grid data</text>';
    svg.setAttribute('viewBox', '0 0 200 50');
    return;
  }

  const isBottomLeft = (data.matrix.origin || 'bottom_left') === 'bottom_left';

  const pad = { left: 30, right: 10, top: 10, bottom: 25 };
  const maxSvgW = 400;
  const maxSvgH = 180;
  const availW = maxSvgW - pad.left - pad.right;
  const availH = maxSvgH - pad.top - pad.bottom;
  const cellW = Math.max(4, Math.min(20, availW / gridW));
  const cellH = Math.max(0.5, Math.min(3, availH / gridH));
  const svgW = pad.left + gridW * cellW + pad.right;
  const svgH = pad.top + gridH * cellH + pad.bottom;

  svg.setAttribute('viewBox', `0 0 ${svgW} ${svgH}`);

  const parts = [];

  // Background grid dots
  for (let gx = 0; gx < gridW; gx++) {
    for (let gy = 0; gy < gridH; gy += Math.max(1, Math.floor(gridH / 40))) {
      const px = pad.left + gx * cellW + cellW / 2;
      const drawY = isBottomLeft ? (gridH - 1 - gy) : gy;
      const py = pad.top + drawY * cellH + cellH / 2;
      parts.push(`<circle cx="${px}" cy="${py}" r="0.8" fill="#333" />`);
    }
  }

  // X axis labels
  for (let gx = 0; gx < gridW; gx++) {
    const px = pad.left + gx * cellW + cellW / 2;
    parts.push(`<text x="${px}" y="${svgH - 5}" fill="#666" font-size="8" text-anchor="middle">${gx}</text>`);
  }

  // Y axis labels
  const yLabelStep = Math.max(1, Math.floor(gridH / 8));
  for (let gy = 0; gy <= gridH; gy += yLabelStep) {
    const drawY = isBottomLeft ? (gridH - 1 - gy) : gy;
    const py = pad.top + drawY * cellH + cellH / 2;
    parts.push(`<text x="${pad.left - 4}" y="${py + 3}" fill="#666" font-size="7" text-anchor="end">${gy}</text>`);
  }

  // Daisy-chain lines between consecutive segments on same output
  const byChannel = {};
  segments.forEach((seg, idx) => {
    const ch = seg.channel;
    if (!byChannel[ch]) byChannel[ch] = [];
    byChannel[ch].push({ seg, idx });
  });

  for (const entries of Object.values(byChannel)) {
    if (entries.length < 2) continue;
    for (let i = 0; i < entries.length - 1; i++) {
      const prevEnd = segmentEnd(entries[i].seg);
      const nextStart = entries[i + 1].seg.start;
      const x1 = pad.left + prevEnd.x * cellW + cellW / 2;
      const y1d = isBottomLeft ? (gridH - 1 - prevEnd.y) : prevEnd.y;
      const y1 = pad.top + y1d * cellH + cellH / 2;
      const x2 = pad.left + nextStart.x * cellW + cellW / 2;
      const y2d = isBottomLeft ? (gridH - 1 - nextStart.y) : nextStart.y;
      const y2 = pad.top + y2d * cellH + cellH / 2;
      parts.push(`<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="#555" stroke-width="1" stroke-dasharray="3,3" />`);
    }
  }

  // Draw segments
  for (let i = 0; i < segments.length; i++) {
    const seg = segments[i];
    const end = segmentEnd(seg);
    const color = segmentColor(i);
    const sx = pad.left + seg.start.x * cellW + cellW / 2;
    const sy_raw = isBottomLeft ? (gridH - 1 - seg.start.y) : seg.start.y;
    const sy = pad.top + sy_raw * cellH + cellH / 2;
    const ex = pad.left + end.x * cellW + cellW / 2;
    const ey_raw = isBottomLeft ? (gridH - 1 - end.y) : end.y;
    const ey = pad.top + ey_raw * cellH + cellH / 2;

    parts.push(`<line x1="${sx}" y1="${sy}" x2="${ex}" y2="${ey}" stroke="${color}" stroke-width="3" stroke-linecap="round" />`);
    parts.push(`<circle cx="${sx}" cy="${sy}" r="4" fill="${color}" stroke="#000" stroke-width="1" />`);

    const dx = ex - sx;
    const dy = ey - sy;
    const len = Math.sqrt(dx * dx + dy * dy);
    if (len > 0) {
      const ux = dx / len;
      const uy = dy / len;
      const arrowSize = 5;
      const p1x = ex - ux * arrowSize + uy * arrowSize * 0.6;
      const p1y = ey - uy * arrowSize - ux * arrowSize * 0.6;
      const p2x = ex - ux * arrowSize - uy * arrowSize * 0.6;
      const p2y = ey - uy * arrowSize + ux * arrowSize * 0.6;
      parts.push(`<polygon points="${ex},${ey} ${p1x},${p1y} ${p2x},${p2y}" fill="${color}" />`);
    }
  }

  svg.innerHTML = parts.join('\n');

  const gridInfo = document.getElementById('pm-grid-info');
  if (gridInfo && data.compiled) {
    gridInfo.textContent = `${data.compiled.width} x ${data.compiled.height} — ${data.compiled.total_mapped} mapped LEDs`;
  }
}

function renderSegmentTable(data) {
  const tbody = document.getElementById('pm-segment-tbody');
  if (!tbody) return;
  tbody.innerHTML = '';

  const segments = flattenSegments(data);

  for (let i = 0; i < segments.length; i++) {
    const seg = segments[i];
    const color = segmentColor(i);
    const colorOpts = COLOR_ORDERS.map(o =>
      `<option value="${o}" ${o === (seg.color_order || 'BGR') ? 'selected' : ''}>${o}</option>`
    ).join('');
    const outputOpts = Array.from({ length: 8 }, (_, n) =>
      `<option value="${n}" ${n === seg.channel ? 'selected' : ''}>${n}</option>`
    ).join('');
    const dirOpts = DIRECTIONS.map(d =>
      `<option value="${d}" ${d === seg.direction ? 'selected' : ''}>${d}</option>`
    ).join('');

    const row = document.createElement('tr');
    row.dataset.segIndex = i;
    row.dataset.segId = seg.id;
    row.innerHTML = `
      <td><span class="pm-seg-swatch" style="background:${color}"></span></td>
      <td><input type="text" data-field="id" value="${seg.id}" size="8" title="Segment ID"></td>
      <td><input type="number" data-field="sx" value="${seg.start.x}" min="0" max="999" size="3"></td>
      <td><input type="number" data-field="sy" value="${seg.start.y}" min="0" max="9999" size="4"></td>
      <td><select data-field="direction">${dirOpts}</select></td>
      <td><input type="number" data-field="length" value="${seg.length}" min="1" max="9999" size="4"></td>
      <td><input type="number" data-field="physical_offset" value="${seg.physical_offset}" min="0" max="9999" size="4" placeholder="0"></td>
      <td><select data-field="channel">${outputOpts}</select></td>
      <td><select data-field="color_order">${colorOpts}</select></td>
      <td><button class="pm-seg-test" title="Test this segment">T</button></td>
      <td><button class="pm-seg-delete" title="Delete segment">&times;</button></td>
    `;
    tbody.appendChild(row);

    row.querySelectorAll('input, select').forEach(input => {
      input.addEventListener('input', () => { syncTableToCards(); scheduleApply(); });
      input.addEventListener('change', () => { syncTableToCards(); scheduleApply(); });
    });

    row.querySelector('.pm-seg-test').addEventListener('click', () => {
      testSegment(row.dataset.segId);
    });

    row.querySelector('.pm-seg-delete').addEventListener('click', () => {
      row.remove();
      reindexSegmentTable();
      syncTableToCards();
      scheduleApply();
    });
  }
}

function renderSegmentCards(data) {
  const container = document.getElementById('pm-segment-cards');
  if (!container) return;
  container.innerHTML = '';

  const segments = flattenSegments(data);
  for (let i = 0; i < segments.length; i++) {
    const seg = segments[i];
    const end = segmentEnd(seg);
    const color = segmentColor(i);
    const card = document.createElement('div');
    card.className = 'pm-seg-card';
    card.dataset.segIndex = i;
    card.innerHTML = `
      <span class="pm-seg-swatch" style="background:${color}"></span>
      <span class="pm-seg-card-coords">${seg.id}: (${seg.start.x},${seg.start.y}) ${seg.direction} ×${seg.length}</span>
      <span class="pm-seg-card-leds">${seg.length} LEDs</span>
      <span class="pm-seg-card-detail">Ch: ${seg.channel} &nbsp; Color: ${seg.color_order || 'BGR'}</span>
      <button class="pm-seg-delete" title="Delete segment">&times;</button>
    `;
    container.appendChild(card);

    card.querySelector('.pm-seg-delete').addEventListener('click', () => {
      const tableRow = document.querySelector(`#pm-segment-tbody tr[data-seg-index="${i}"]`);
      if (tableRow) tableRow.remove();
      card.remove();
      reindexSegmentTable();
      scheduleApply();
    });
  }
}

function syncTableToCards() {
  const segments = collectSegmentsFlat();
  const container = document.getElementById('pm-segment-cards');
  if (!container) return;
  container.innerHTML = '';

  for (let i = 0; i < segments.length; i++) {
    const seg = segments[i];
    const color = segmentColor(i);
    const card = document.createElement('div');
    card.className = 'pm-seg-card';
    card.innerHTML = `
      <span class="pm-seg-swatch" style="background:${color}"></span>
      <span class="pm-seg-card-coords">${seg.id}: (${seg.start.x},${seg.start.y}) ${seg.direction} ×${seg.length}</span>
      <span class="pm-seg-card-leds">${seg.length} LEDs</span>
      <span class="pm-seg-card-detail">Ch: ${seg.channel} &nbsp; Color: ${seg.color_order}</span>
    `;
    container.appendChild(card);
  }
}

function reindexSegmentTable() {
  const rows = document.querySelectorAll('#pm-segment-tbody tr');
  rows.forEach((row, idx) => {
    row.dataset.segIndex = idx;
    const swatch = row.querySelector('.pm-seg-swatch');
    if (swatch) swatch.style.background = segmentColor(idx);
  });
  const segments = collectSegmentsFlat();
  const channels = new Set(segments.map(s => s.channel));
  const totalLeds = segments.reduce((sum, s) => sum + s.length, 0);
  const sumEl = document.getElementById('pm-summary-text');
  if (sumEl) {
    sumEl.textContent = `${segments.length} segments · ${channels.size} outputs · ${totalLeds} LEDs`;
  }
}

function updateSummary(data) {
  const el = document.getElementById('pm-summary-text');
  if (!el) return;
  if (data.compiled) {
    const segments = flattenSegments(data);
    const channels = new Set(segments.map(s => s.channel));
    el.textContent = `${segments.length} segments · ${channels.size} outputs · ${data.compiled.total_mapped} LEDs · Grid ${data.compiled.width}x${data.compiled.height}`;
  }
}

// --- Collect from table into flat segment list ---
function collectSegmentsFlat() {
  const rows = document.querySelectorAll('#pm-segment-tbody tr');
  const segments = [];
  rows.forEach(row => {
    const id = row.querySelector('[data-field="id"]').value || `seg_${row.dataset.segIndex}`;
    const sx = parseInt(row.querySelector('[data-field="sx"]').value) || 0;
    const sy = parseInt(row.querySelector('[data-field="sy"]').value) || 0;
    const direction = row.querySelector('[data-field="direction"]').value || '+y';
    const length = parseInt(row.querySelector('[data-field="length"]').value) || 1;
    const physical_offset = parseInt(row.querySelector('[data-field="physical_offset"]').value) || 0;
    const channel = parseInt(row.querySelector('[data-field="channel"]').value) || 0;
    const color_order = row.querySelector('[data-field="color_order"]').value || 'BGR';
    segments.push({ id, start: { x: sx, y: sy }, direction, length, physical_offset, channel, color_order });
  });
  return segments;
}

// --- Build layout config request from table state ---
function collectLayoutConfig() {
  const origin = document.getElementById('pm-origin-select').value || 'bottom_left';
  let gridW = parseInt(document.getElementById('pm-grid-w').value) || 0;
  let gridH = parseInt(document.getElementById('pm-grid-h').value) || 0;
  const segments = collectSegmentsFlat();

  // Auto-derive grid dimensions from segments if not explicitly set
  if (gridW === 0 || gridH === 0) {
    let maxX = 0, maxY = 0;
    for (const seg of segments) {
      const end = segmentEnd(seg);
      maxX = Math.max(maxX, seg.start.x, end.x);
      maxY = Math.max(maxY, seg.start.y, end.y);
    }
    if (gridW === 0) gridW = maxX + 1;
    if (gridH === 0) gridH = maxY + 1;
  }

  // Group segments by channel
  const outputMap = {};
  for (const seg of segments) {
    const ch = seg.channel;
    if (!outputMap[ch]) {
      outputMap[ch] = { id: `octo_ch${ch}`, channel: ch, color_order: seg.color_order, chipset: 'WS2812', segments: [] };
    }
    outputMap[ch].segments.push({
      id: seg.id,
      start: seg.start,
      direction: seg.direction,
      length: seg.length,
      physical_offset: seg.physical_offset,
    });
    // Use first segment's color order for the output
    if (outputMap[ch].segments.length === 1) {
      outputMap[ch].color_order = seg.color_order;
    }
  }

  return {
    version: 1,
    matrix: { width: gridW, height: gridH, origin },
    outputs: Object.values(outputMap),
  };
}

async function applyLayout() {
  const config = collectLayoutConfig();
  if (config.outputs.length === 0) {
    showPmStatus('No segments to apply', true);
    return;
  }
  const result = await api('POST', '/api/layout/apply', config);
  if (result && result.status === 'ok') {
    showPmStatus('Saved');
    // Reload to get compiled stats
    const fresh = await api('GET', '/api/layout');
    if (fresh && !fresh.error) {
      _layoutData = fresh;
      renderGridSVG(fresh);
      updateSummary(fresh);
    }
  } else {
    const detail = Array.isArray(result?.detail) ? result.detail.join('; ') : (result?.detail || result?.error || 'Failed');
    showPmStatus(detail, true);
  }
}

async function testSegment(segId) {
  const result = await api('POST', `/api/layout/test-segment/${segId}`);
  if (result && result.status === 'ok') {
    showPmStatus(`Testing segment ${segId}...`);
  } else {
    showPmStatus(result?.detail || 'Test failed', true);
  }
}

async function validateLayout() {
  const config = collectLayoutConfig();
  if (config.outputs.length === 0) {
    showPmStatus('No segments to validate', true);
    return;
  }
  const result = await api('POST', '/api/layout/validate', config);
  if (!result) {
    showPmStatus('Validation request failed', true);
    return;
  }
  if (result.valid) {
    showPmStatus('Configuration is valid');
  } else {
    const errors = (result.errors || []).join('; ');
    showPmStatus(`Validation errors: ${errors}`, true);
  }
}

function addSegmentRow(defaults) {
  const tbody = document.getElementById('pm-segment-tbody');
  if (!tbody) return;
  const idx = tbody.querySelectorAll('tr').length;
  const seg = defaults || {};
  const id = seg.id || `col_${idx}`;
  const sx = seg.sx ?? idx;
  const sy = seg.sy ?? 0;
  const direction = seg.direction ?? '+y';
  const length = seg.length ?? 83;
  const physical_offset = seg.physical_offset ?? 0;
  const channel = seg.channel ?? Math.floor(idx / 2);
  const colorOrder = seg.color_order ?? 'BGR';
  const color = segmentColor(idx);

  const colorOpts = COLOR_ORDERS.map(o =>
    `<option value="${o}" ${o === colorOrder ? 'selected' : ''}>${o}</option>`
  ).join('');
  const outputOpts = Array.from({ length: 8 }, (_, n) =>
    `<option value="${n}" ${n === channel ? 'selected' : ''}>${n}</option>`
  ).join('');
  const dirOpts = DIRECTIONS.map(d =>
    `<option value="${d}" ${d === direction ? 'selected' : ''}>${d}</option>`
  ).join('');

  const row = document.createElement('tr');
  row.dataset.segIndex = idx;
  row.dataset.segId = id;
  row.innerHTML = `
    <td><span class="pm-seg-swatch" style="background:${color}"></span></td>
    <td><input type="text" data-field="id" value="${id}" size="8"></td>
    <td><input type="number" data-field="sx" value="${sx}" min="0" max="999" size="3"></td>
    <td><input type="number" data-field="sy" value="${sy}" min="0" max="9999" size="4"></td>
    <td><select data-field="direction">${dirOpts}</select></td>
    <td><input type="number" data-field="length" value="${length}" min="1" max="9999" size="4"></td>
    <td><input type="number" data-field="physical_offset" value="${physical_offset}" min="0" max="9999" size="4" placeholder="0"></td>
    <td><select data-field="channel">${outputOpts}</select></td>
    <td><select data-field="color_order">${colorOpts}</select></td>
    <td><button class="pm-seg-test" title="Test this segment">T</button></td>
    <td><button class="pm-seg-delete" title="Delete segment">&times;</button></td>
  `;
  tbody.appendChild(row);

  row.querySelectorAll('input, select').forEach(input => {
    input.addEventListener('input', () => { syncTableToCards(); scheduleApply(); });
    input.addEventListener('change', () => { syncTableToCards(); scheduleApply(); });
  });

  row.querySelector('.pm-seg-test').addEventListener('click', () => {
    testSegment(row.querySelector('[data-field="id"]').value);
  });

  row.querySelector('.pm-seg-delete').addEventListener('click', () => {
    row.remove();
    reindexSegmentTable();
    syncTableToCards();
    scheduleApply();
  });

  syncTableToCards();
  reindexSegmentTable();
}

function initSetup() {
  document.getElementById('pm-add-segment-btn').addEventListener('click', () => {
    const segments = collectSegmentsFlat();
    const nextIdx = segments.length;
    const nextX = nextIdx;
    const dir = (nextIdx % 2 === 0) ? '+y' : '-y';
    const sy = (dir === '+y') ? 0 : 82;
    const channel = Math.floor(nextIdx / 2);
    const physOffset = (nextIdx % 2 === 0) ? 0 : 83;
    addSegmentRow({ id: `col_${nextIdx}`, sx: nextX, sy, direction: dir, length: 83, physical_offset: physOffset, channel, color_order: 'BGR' });
    scheduleApply();
  });

  document.getElementById('pm-validate-btn').addEventListener('click', () => validateLayout());
  document.getElementById('pm-apply-btn').addEventListener('click', () => applyLayout());
  document.getElementById('pm-origin-select').addEventListener('change', () => scheduleApply());
  document.getElementById('pm-grid-w').addEventListener('input', () => scheduleApply());
  document.getElementById('pm-grid-h').addEventListener('input', () => scheduleApply());
}
```

- [ ] **Step 2: Update the HTML table header**

In the same file, find the HTML template for the segment table header (search for `<th>` elements related to "Start X", "Start Y", "End X", "End Y"). Update the column headers to match the new fields:

Old headers: `#`, `Start X`, `Start Y`, `End X`, `End Y`, `LEDs`, `Range`, `Output`, `Color`, `Test`, `Del`

New headers: `#`, `ID`, `Start X`, `Start Y`, `Dir`, `Length`, `Offset`, `Ch`, `Color`, `Test`, `Del`

Search for the table header HTML and update it.

- [ ] **Step 3: Remove the `loadTeensyStatus` function**

The old `loadTeensyStatus()` called `/api/pixel-map/teensy-status` which no longer exists. Remove the function and any calls to it (search for `loadTeensyStatus` in the file). The transport status is available through `/api/system/status` which the dashboard already uses.

- [ ] **Step 4: Deploy and test**

```bash
bash pi/scripts/deploy.sh ledfanatic.local
```

Open the Setup tab in a browser and verify:
- Layout loads and displays the 10 segments correctly
- Grid SVG shows the serpentine pattern
- Editing a field and waiting 500ms triggers auto-apply
- Apply succeeds (no 404 errors in logs)
- Test button lights the correct segment
- Validate button works

- [ ] **Step 5: Commit**

```bash
git add pi/app/ui/static/js/app.js
git commit -m "feat(ui): update Setup tab for new /api/layout endpoints

Replaces old /api/pixel-map calls with /api/layout.
Segments now use start+direction+length instead of start+end.
Test-segment uses segment ID strings.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```
