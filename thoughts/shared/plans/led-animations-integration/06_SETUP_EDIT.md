# Phase 6 — Editable Setup Fields

## Goal

The System > Setup strip inventory becomes fully editable inline with validation, clear labels, and comprehensive instructions.

## Current state

- Read-only table showing ID, label, enabled, LEDs, color, direction, channel, slot
- Start/cancel/commit buttons
- No editing capability
- No instructions

## New state

### Editable strip table

```
┌─ Strip Inventory ──────────────────────────────────────────────┐
│                                                                 │
│ ID  Label    En  LEDs  Color   Dir    Ch  Slot  Chipset        │
│ ── ──────── ── ───── ─────── ────── ── ──── ────────          │
│ 0  [S0    ] [✓] [172] [▼BGR] [▼ ↑ ] [0] [0]  [▼WS2812B]     │
│ 1  [S1    ] [✓] [172] [▼BGR] [▼ ↓ ] [0] [1]  [▼WS2812B]     │
│ 2  [S2    ] [✓] [172] [▼BGR] [▼ ↑ ] [1] [0]  [▼WS2812B]     │
│ ...                                                             │
│ 9  [S9    ] [✓] [172] [▼BGR] [▼ ↓ ] [4] [1]  [▼WS2812B]     │
│                                                                 │
│ Validation: ✓ All strips valid                                  │
│                                                                 │
│ [Commit Changes]  [Cancel]                                      │
└─────────────────────────────────────────────────────────────────┘
```

### Field types

| Field | Type | Options/Range | Validation |
|-------|------|---------------|------------|
| Label | text input | freeform, max 10 chars | required |
| Enabled | checkbox | true/false | — |
| LEDs | number input | 0–172 | must be ≤ physical max |
| Color Order | dropdown | RGB, RBG, GRB, GBR, BRG, BGR | valid permutation |
| Direction | dropdown | ↑ bottom_to_top, ↓ top_to_bottom | valid value |
| Channel | number | 0–4 | valid range |
| Slot | number | 0–1 | valid range |
| Chipset | dropdown | WS2812B, WS2812, WS2811, SK6812, WS2813, WS2815 | valid chipset |

### Inline validation

- Red border on invalid fields
- Validation summary below table
- Commit button disabled until all valid
- Check for: duplicate logical orders, duplicate channel/slot pairs, LED count range

### Edit flow

1. User clicks "Start Setup Session"
2. Table becomes editable (inputs replace static text)
3. User modifies fields
4. Changes auto-save to staged installation via `PUT /api/setup/session/installation`
5. Validation runs client-side + server-side
6. "Commit" persists and hot-applies
7. "Cancel" restores previous state

### JS implementation

```javascript
function renderEditableStripTable(strips) {
  const tbody = document.getElementById('strip-table-body');
  tbody.innerHTML = '';
  for (const strip of strips) {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${strip.id}</td>
      <td><input type="text" value="${strip.label}" data-field="label" data-id="${strip.id}" maxlength="10"></td>
      <td><input type="checkbox" ${strip.enabled ? 'checked' : ''} data-field="enabled" data-id="${strip.id}"></td>
      <td><input type="number" value="${strip.installed_led_count}" data-field="installed_led_count" data-id="${strip.id}" min="0" max="172"></td>
      <td><select data-field="color_order" data-id="${strip.id}">
        ${['RGB','RBG','GRB','GBR','BRG','BGR'].map(o => `<option ${o===strip.color_order?'selected':''}>${o}</option>`).join('')}
      </select></td>
      <td><select data-field="direction" data-id="${strip.id}">
        <option value="bottom_to_top" ${strip.direction==='bottom_to_top'?'selected':''}>↑ Up</option>
        <option value="top_to_bottom" ${strip.direction==='top_to_bottom'?'selected':''}>↓ Down</option>
      </select></td>
      <td><input type="number" value="${strip.output_channel}" data-field="output_channel" data-id="${strip.id}" min="0" max="4"></td>
      <td><input type="number" value="${strip.output_slot}" data-field="output_slot" data-id="${strip.id}" min="0" max="1"></td>
      <td><select data-field="chipset" data-id="${strip.id}">
        ${['WS2812B','WS2812','WS2811','SK6812','WS2813','WS2815'].map(c => `<option ${c===strip.chipset?'selected':''}>${c}</option>`).join('')}
      </select></td>
    `;
    tbody.appendChild(tr);
  }
  // Attach change handlers
  tbody.querySelectorAll('input, select').forEach(el => {
    el.addEventListener('change', () => stageStripUpdate(el));
  });
}
```

### Debounced staging

When a field changes:
1. Collect the changed strip's ID and field
2. Debounce 300ms
3. `PUT /api/setup/session/installation` with batch update
4. Revalidate client-side

## CSS changes

- `.strip-edit-input` — compact inputs matching table cell widths
- `.strip-edit-select` — compact dropdowns
- `.invalid-field` — red border + tooltip
- `.validation-summary` — status message below table

## Tests

- Editable table renders all fields
- Client-side validation catches duplicate channel/slot
- Staged update API accepts field changes
- Commit with invalid data returns 422
- Cancel restores original values

## Gate

- All strip fields are editable during a setup session
- Validation prevents invalid commits
- Commit hot-applies color order and LED count changes to live output
