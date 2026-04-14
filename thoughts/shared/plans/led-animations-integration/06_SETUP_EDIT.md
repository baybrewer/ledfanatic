# Phase 6 — Editable Setup Fields

## Goal

The System > Setup strip inventory becomes fully editable inline with validation, clear labels, and comprehensive instructions.

## Current state

- Read-only table showing ID, label, enabled, LEDs, color, direction, channel, slot
- Start/cancel/commit buttons
- No editing capability
- No instructions

## New state

### Mobile-first strip editor — accordion cards

A 10×9 editable table is impractical on a 375px phone. Use expandable strip cards instead:

```
┌─ Strip Inventory ──────────────────────┐
│                                         │
│ ┌ S0 — Ch0:0 ↑ 172 LEDs BGR ──── [▼] ┐│
│ │  Label:    [S0          ]            ││
│ │  Enabled:  [✓]                       ││
│ │  LEDs:     [172    ] / 172 max       ││
│ │  Color:    [▼ BGR        ]           ││
│ │  Direction:[▼ ↑ Bottom→Top]          ││
│ │  Channel:  [0]  Slot: [0]           ││
│ │  Chipset:  [▼ WS2812B    ]          ││
│ └──────────────────────────────────────┘│
│                                         │
│ ┌ S1 — Ch0:1 ↓ 172 LEDs BGR ── [▶] ──┐│
│ └──────────────────────────────────────┘│
│ ┌ S2 — Ch1:0 ↑ 172 LEDs BGR ── [▶] ──┐│
│ └──────────────────────────────────────┘│
│ ...                                     │
│                                         │
│ Validation: ✓ All strips valid          │
│ [Commit Changes]  [Cancel]              │
└─────────────────────────────────────────┘
```

**Collapsed state** shows a summary line: label, channel:slot, direction arrow, LED count, color order.
**Expanded state** shows all editable fields in a single-column form layout.
Only one strip expanded at a time (accordion behavior) to keep the page manageable.

On wider screens (>600px), an editable table is acceptable as an alternative view.

### Field types

| Field | Type | Options/Range | Validation |
|-------|------|---------------|------------|
| Label | text input | freeform, max 10 chars | required |
| Enabled | checkbox | true/false | — |
| Logical Order | number input | 0–9 | unique among enabled strips |
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

### JS implementation (accordion card mode — mobile default)

```javascript
function renderStripCards(strips) {
  const container = document.getElementById('strip-cards');
  container.innerHTML = '';
  for (const strip of strips) {
    // Direction comes from the config, NOT assumed from even/odd
    const dirArrow = strip.direction === 'bottom_to_top' ? '↑' : '↓';
    const card = document.createElement('div');
    card.className = 'strip-card';
    card.innerHTML = `
      <div class="strip-card-header" data-id="${strip.id}">
        <span>${strip.label} — Ch${strip.output_channel}:${strip.output_slot} ${dirArrow} ${strip.installed_led_count} LEDs ${strip.color_order}</span>
        <span class="expand-icon">▶</span>
      </div>
      <div class="strip-card-body" hidden>
        <label>Label <input type="text" value="${strip.label}" data-field="label" data-id="${strip.id}" maxlength="10"></label>
        <label>Enabled <input type="checkbox" ${strip.enabled ? 'checked' : ''} data-field="enabled" data-id="${strip.id}"></label>
        <label>Logical Order <input type="number" value="${strip.logical_order}" data-field="logical_order" data-id="${strip.id}" min="0" max="9"></label>
        <label>LEDs <input type="number" value="${strip.installed_led_count}" data-field="installed_led_count" data-id="${strip.id}" min="0" max="172"></label>
        <label>Color Order <select data-field="color_order" data-id="${strip.id}">
          ${['RGB','RBG','GRB','GBR','BRG','BGR'].map(o => `<option ${o===strip.color_order?'selected':''}>${o}</option>`).join('')}
        </select></label>
        <label>Direction <select data-field="direction" data-id="${strip.id}">
          <option value="bottom_to_top" ${strip.direction==='bottom_to_top'?'selected':''}>↑ Bottom→Top</option>
          <option value="top_to_bottom" ${strip.direction==='top_to_bottom'?'selected':''}>↓ Top→Bottom</option>
        </select></label>
        <label>Channel <input type="number" value="${strip.output_channel}" data-field="output_channel" data-id="${strip.id}" min="0" max="4"></label>
        <label>Slot <input type="number" value="${strip.output_slot}" data-field="output_slot" data-id="${strip.id}" min="0" max="1"></label>
        <label>Chipset <select data-field="chipset" data-id="${strip.id}">
          ${['WS2812B','WS2812','WS2811','SK6812','WS2813','WS2815'].map(c => `<option ${c===strip.chipset?'selected':''}>${c}</option>`).join('')}
        </select></label>
      </div>
    `;
    container.appendChild(card);
  }
  // Accordion: expand one, collapse others
  container.querySelectorAll('.strip-card-header').forEach(h => {
    h.addEventListener('click', () => {
      const body = h.nextElementSibling;
      const wasHidden = body.hidden;
      container.querySelectorAll('.strip-card-body').forEach(b => b.hidden = true);
      container.querySelectorAll('.expand-icon').forEach(i => i.textContent = '▶');
      if (wasHidden) {
        body.hidden = false;
        h.querySelector('.expand-icon').textContent = '▼';
      }
    });
  });
  // Attach change handlers
  container.querySelectorAll('input, select').forEach(el => {
    el.addEventListener('change', () => stageStripUpdate(el));
  });
}
```

**Note:** Direction comes from the strip's `direction` field in the installation config, NOT assumed from even/odd strip ID. The `logical_order` field is included and editable.

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
