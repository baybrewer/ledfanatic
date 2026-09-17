# Potentiometer Control — Design

**Date:** 2026-09-17
**Status:** Approved pending review

## Goal

Control brightness and effect selection with three physical potentiometers, with no app required — while keeping the app fully usable (last-writer-wins between knobs and app).

## Hardware

- 3 linear-taper pots (two B10K on hand + one B5K or B20K — value is irrelevant since the divider is ratiometric; linear taper required, no resistors needed).
- Wiring per pot: 3.3V — wiper — GND. **Never 5V** (Teensy 4.1 pins are not 5V-tolerant).
- Teensy 4.1 pins **A14 (38), A15 (39), A16 (40)** — physically adjacent at the board end, unused by OctoWS2811.
- Assignment: A14 = brightness, A15 = menu (group), A16 = pattern.

## Firmware (Teensy)

- Read the 3 ADC pins each loop; exponential smoothing (`filtered += (raw - filtered) >> 3`) for noise.
- Every 50 ms, send new upstream packet **`PKT_POTS = 0x31`**, payload: 3 × uint16 little-endian filtered values (6 bytes), COBS-framed with CRC32 like all packets.
- Sent unconditionally at 20 Hz (no change-detection in firmware — the Pi decides what "moved" means).
- No changes to STATS (stays 28 bytes), PING, FRAME, or any existing handler. Firmware version bump.

## Protocol

- `PKT_POTS = 0x31` added to `teensy/firmware/include/config.h` and `pi/app/models/protocol.py` (both sides — SSOT is the shared ID table in each).
- Payload: exactly 6 bytes (3 × uint16 LE, 10-bit range 0–1023 — Teensy default `analogRead` resolution; Pi normalizes to 0.0–1.0).
- Golden-vector test added to `test_protocol.py` for framing.

## Pi: `pi/app/core/pots.py` — `PotController`

Receives normalized pot values from the transport read loop (same dispatch path as STATS).

- **Activation (last-writer-wins):** each pot is inert until it moves more than a **2% deadband** from its last-settled position. App changes via API always apply immediately; a pot re-asserts only when physically turned.
- **Brightness pot** → 0.0–1.0 → `brightness.set_manual_cap()` (existing engine; same code path as the API). Continuous while moving, small step threshold to avoid state-file churn (state saves already debounced).
- **Menu pot** → index into `["favorites"] + sorted(effect groups)`. **Pattern pot** → index into the selected group's effect list (alphabetical, matching UI sort).
- **Hysteresis:** selector pots quantize with dead gaps between zones (switch at zone-center crossings, not edges) so a knob resting on a boundary never flickers between effects.
- **Debounce:** effect activation fires ~300 ms after the knob stops moving, so sweeping doesn't activate every effect passed over.
- Effect changes go through `renderer.activate_scene()` — the mandatory path for all scene types.
- Empty favorites list → the favorites slot is skipped in the menu mapping.
- Mapping/hysteresis logic implemented as pure functions for unit testing; the controller is a thin stateful wrapper.

## Category overlay (LED feedback while turning the menu pot)

- While the **menu pot** is moving (and for ~1.5 s after it settles), the current category name is drawn on the **left panel** as an overlay on top of the running effect, then fades out (~300 ms).
- **Book-spine orientation:** glyphs rotated 90° clockwise (letter tops face right), reading top-to-bottom. Names taller than the panel scroll vertically.
- Rendered by the renderer after the effect frame (before pack/y-flip handling), reusing scrolltext's PIL text rasterization — extracted into a small shared helper so scrolltext and the overlay share one font path (DRY).
- Overlay region is configurable in `system.yaml` (`pots.overlay: {x0, x1}`), defaulting to the left half of the grid (columns 0 to width/2 − 1). No hardcoded geometry.
- Pattern pot gets **no** text overlay (deliberate) — you judge effect selection by watching the effect change.
- Text is white at full effective brightness with a dark backing box over the panel region for legibility.

## Per-knob software disable

- Each pot (brightness / menu / pattern) can be individually disabled so e.g. random guests can't change the pattern.
- State: `pots_enabled: {brightness, menu, pattern}` booleans in `state.json` (live-override tier, defaults all true).
- API: `GET /api/pots` (public — current values + enabled flags), `POST /api/pots/config` (auth — set enabled flags).
- A disabled pot's input is fully ignored: no activation, no overlay, no brightness change. Re-enabling does **not** immediately apply the pot's resting position — it must move past the deadband again (last-writer-wins preserved).
- UI: three toggles in the System tab.

## Favorites (server-side — needed independently)

- `favorites: list[str]` (effect names) in `state.json` (schema-versioned migration).
- `GET /api/effects/favorites` (public), `POST /api/effects/favorites` (auth, replaces list). Unknown effect names rejected.
- UI: ★ toggle on effect cards; a "Favorites" filter pill. Replaces nothing — archive/hide stays in localStorage.

## Testing

- `test_protocol.py`: PKT_POTS golden vector (framing + CRC).
- Unit tests for pure mapping functions: deadband, hysteresis zones, group/effect index mapping, favorites-empty case, disabled-pot ignore + re-enable semantics.
- Unit test for spine-text rasterization (orientation: letter tops face right; scroll for long names).
- Live: deploy to Pi, flash Teensy, turn knobs, confirm brightness + selection + overlay + app interplay + disable toggles.

## Out of scope

- No pots for effect parameters (speed/palette) — future.
- No extra display/feedback hardware — feedback is the LED category overlay plus the app UI.
- No effect-name overlay for the pattern pot (decided against).
- No selector switches or encoders.

## Config precedence note

Pot input is a **live override** (same tier as API calls): it updates the same runtime state and persists via the existing debounced state mechanism — it does not add a new precedence layer.
