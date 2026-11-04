# Overhead Mode + Home Assistant / Siri — Design

**Date:** 2026-11-04 · **Status:** approved by Jim (scope + effect list); Codex round 1 (9 findings) folded in

## Goal

LED Fanatic is mounted above Jim's desk for the off-season. Add a calm **Overhead** mode
(10 effects, own UI tab) and expose it to Home Assistant as a single light entity so
Siri (via HA's existing HomeKit Bridge) can say "turn on / set the overhead light to 40% /
make the overhead light blue / warmer".

Decisions already made with Jim:
- Siri/HA controls **Overhead mode only** (festival effects stay phone-only).
- Auth stays open on the LAN (matches current fail-open since 4fba641).
- Integration = **MQTT discovery** via HA's existing Mosquitto broker.
- Color temperature applies to **White only**. Everything but White/Color gets Speed + Saturation.

## Non-goals (YAGNI)

- Effect selection by Siri voice (HomeKit has no effect concept; HA effect dropdown is enough).
- Re-enabling auth, MQTT TLS, multiple HA entities, transitions/fades on HA commands.

## 1. Effects — `pi/app/effects/overhead.py`

All classes `CATEGORY = "overhead"`, names prefixed `oh_`, registered in
`OVERHEAD_EFFECTS` → `registry.py` (SSOT). Render in screen coords, use
`self.width/self.height` only, vectorized NumPy, < 3 ms/frame at 20×83 on a Pi 4.
Frame-time-based animation (`self.elapsed(t)`), so FPS changes don't change speed.

| name | label | params |
|---|---|---|
| `oh_white` | White | `kelvin` 2200–6500 (default 3000) |
| `oh_color` | Color | `color` hex (type `color`, default `#FF8C3C`) |
| `oh_liquid_rainbow` | Liquid Rainbow | speed, saturation |
| `oh_rainbow_ink` | Rainbow Ink | speed, saturation |
| `oh_oil_slick` | Oil Slick | speed, saturation |
| `oh_prism_waves` | Prism Waves | speed, saturation |
| `oh_rainbow_lava` | Rainbow Lava | speed, saturation |
| `oh_color_drift` | Color Drift | speed, saturation |
| `oh_caustics` | Caustics | speed, saturation |
| `oh_night_light` | Night Light | speed, saturation |

Speed range 0.1–3.0 (default 1.0, tuned slow — "1.0" is already chill);
saturation 0–1 (default 0.85; 0 = white-ish pastel wash).

Techniques:
- **Liquid Rainbow** — domain-warped fBm (existing `engine/noise.fbm_grid`) → hue.
- **Rainbow Ink** — rainbow dye field advected semi-Lagrangian through a slowly evolving
  curl-noise velocity field (divergence-free, unconditionally stable, no solver needed);
  dye slowly re-seeded so it never homogenizes.
- **Oil Slick** — thin-film interference: thickness = slow fBm, RGB = cos palette of thickness.
- **Prism Waves** — few low-frequency sines summed along a slowly rotating axis → hue, soft blur.
- **Rainbow Lava** — 5–7 metaballs on slow Lissajous paths; hue by field-weighted blob hue.
- **Color Drift** — whole panel one hue, ±small spatial gradient, full cycle ≈ 4 min at speed 1.
- **Caustics** — classic sum-of-abs-sines caustic pattern, teal→white tint scaled by saturation.
- **Night Light** — dim amber (≤ 25 % value) radial glow, very slow breathing; saturation
  scales toward neutral.

Shared helpers (DRY), in `pi/app/effects/engine/color.py`:
- `kelvin_to_rgb(k) -> (r,g,b)` (Tanner Helland approximation, clamped 1000–40000 K).
- `hsv_to_rgb_grid(h, s, v) -> uint8 (W,H,3)` vectorized (check for an existing one first; reuse if present).

Param descriptors carry an optional `kind` (`slider` default, `color`) so catalog metadata
can emit `type: 'color'` for `oh_color`.

## 2. Catalog / registration (targeted DRY cleanup)

`main.py` repeats the same "class PARAMS → EffectMeta" loop for imported, games and
three-body effects. Add `EffectMeta.from_effect_class(name, cls, group=None, **overrides)`
in `catalog.py` (honours `p.kind` → `type`), use it for overhead **and** replace the games
and three-body loops. Leave the imported loop's palette special-case intact but have it
call the helper too.

- `DISPLAY_CATEGORY_MAP['overhead'] = 'Overhead'`; append `'Overhead'` to
  `DISPLAY_CATEGORY_ORDER` (so physical pots can reach overhead effects).
- `app.js` `CATEGORY_MAP` mirrors it; Effects tab **excludes** group `overhead` (they live
  on the Overhead tab only).

## 3. Display control helpers — `pi/app/core/display_control.py`

Brightness/blackout mutation is currently duplicated in brightness routes and the pot
dispatcher. Extract:

- `async set_manual_brightness(deps, value: float)` — clamp 0–1, set engine cap + state.
  **Does not** send to Teensy.

**Bug fix (pre-existing, Codex):** brightness is applied twice today — the renderer
scales pixels by effective brightness (`renderer.py:439`) *and* routes/pots send the same
value to the Teensy master brightness (`main.cpp:518`), so 40 % renders ≈ 16 %. Fix: the
renderer is the only brightness stage; Teensy master brightness is pinned to 255 (send
`1.0` in `_on_teensy_connect`, remove the other `send_brightness` calls). Panels will look
brighter at the same slider value — expected.
Diagnostic outputs (segment test, identify, calibration, LED probe) currently get their
only dimming from the Teensy, so the renderer must apply effective brightness to them too:
move the brightness multiply after the test-pattern overrides, and scale the packed probe
color. Regression test: diagnostic frames respect a reduced manual cap.
- `async set_blackout(deps, enabled: bool)` — render_state + persisted state + Teensy.

Routes, pots dispatcher, and OverheadService all use these. `blackout` is persisted (key
already exists in `state.json` defaults) and restored on startup, so a Pi reboot while the
light is "off" stays off.

## 4. OverheadService — `pi/app/core/overhead.py` (SSOT for "the overhead light")

Pure-ish service over renderer, state_manager, brightness engine, display_control,
effect catalog. No MQTT/HTTP knowledge.

```python
@dataclass(frozen=True)
class OverheadState:
  on: bool                 # not blackout AND current scene is an overhead effect (no compositor)
  brightness: float        # manual_cap 0–1
  effect: str              # current overhead effect, else last-used overhead effect
  kelvin: int              # oh_white's stored kelvin
  rgb: tuple[int,int,int]  # oh_color's stored color
  color_mode: str          # 'color_temp' if effect == oh_white else 'rgb'
  params: dict             # resolved params of `effect`

class OverheadService:
  def effects(self) -> list[tuple[str, str]]          # (name, label), catalog order
  def snapshot(self) -> OverheadState
  async def apply(self, cmd: OverheadCommand) -> OverheadState
```

`OverheadCommand(on: bool|None, brightness, effect, kelvin, rgb)` semantics:
`OverheadCommand` also carries `params: dict | None` (speed/saturation etc. from the UI),
validated and clamped against the target effect's catalog param metadata; unknown keys
dropped.

- `on=False` → blackout **only if an overhead effect is showing**; while a festival scene
  or compositor is active it is a no-op (HA controls Overhead only). Brightness in an off
  command is ignored.
- Target effect resolution (HA can send effect + color together):
  - `effect` given → that overhead effect (unknown → ValueError). kelvin/rgb in the same
    command are written into `oh_white`/`oh_color` stored params; they also apply live
    only when the target *is* `oh_white` (kelvin) or `oh_color` (rgb).
  - no `effect`: kelvin → `oh_white`; rgb → `oh_color` (kelvin wins if both).
- `on=True` with no target: if an overhead effect is already showing, keep it; else
  activate `last_overhead_effect` (persisted; default `oh_white`).
- brightness (with an on/target) → `set_manual_brightness`.
- Any "on" path clears blackout. Activation goes through `renderer.activate_scene` and
  updates `current_scene/current_params/effect_params` exactly as the scenes route does —
  extract that route body into `activate_and_persist(deps, effect, params)` in
  display_control.py so scenes route, pots, and OverheadService share it.
- After apply: `await broadcast_state()` (UI websocket stays in sync).

**WebSocket payload (SSOT):** `ws.py` currently builds state in three places, without
effect params. Add one `build_state_payload(deps)` (render_state + brightness +
`current_params` + `overhead` snapshot) used for initial, requested, and broadcast sends.

## 5. REST — `pi/app/api/routes/overhead.py`

- `GET /api/overhead` → snapshot + effect list.
- `POST /api/overhead` (auth dep, currently open) body = OverheadCommand → snapshot.
Used by the UI tab; handy for curl debugging.

## 6. MQTT bridge — `pi/app/integrations/ha_mqtt.py`

Dependency: `paho-mqtt>=2.0` as optional extra `homeassistant` in `pyproject.toml`;
deploy.sh installs `[audio,video,homeassistant]`. Import is guarded: missing package or
`mqtt.enabled: false` → bridge logs once and stays off. **(New dependency — approved by
Jim via choosing option A.)**

Config (`system.yaml`, gitignored; placeholders in `.example`):
```yaml
mqtt:
  enabled: false
  host: "homeassistant.local"
  port: 1883
  username: "ledfanatic"
  password: "CHANGE_ME"
  discovery_prefix: "homeassistant"
  node_id: "ledfanatic"
  name: "Overhead Light"
```

Split for testability:
- **Pure functions** (`ha_payloads.py`): `discovery_payload(cfg, effects)`,
  `state_payload(state, effects)`, `parse_command(payload_json, effects) -> OverheadCommand`.
  Brightness 0–255 ↔ 0–1; effect label ↔ name; `color_temp_kelvin` mode.
- **Bridge** (`ha_mqtt.py`): paho client with `loop_start()` thread, **retained** LWT
  `ledfanatic/status` = `offline`, publishes retained `online` on connect. Clean shutdown
  publishes retained `offline`, waits for delivery (`wait_for_publish` with timeout), then
  disconnects + `loop_stop()`. On connect and on HA birth
  (`homeassistant/status` == `online`) → (re)publish retained discovery config. `on_message`
  hands parsed commands to the asyncio loop via `run_coroutine_threadsafe(service.apply)`;
  publishes resulting state immediately. Every successful (re)connect force-publishes the
retained state regardless of the change cache. An asyncio task polls `service.snapshot()` every
  1 s and publishes (retained) on change — covers phone/pot changes without hooking every
  mutation path. Bad payloads are logged and ignored. Started/stopped in main lifecycle,
  wrapped in try/except with logging (startup-scope lesson).

Discovery: topic `homeassistant/light/ledfanatic/overhead/config`, JSON schema light,
`unique_id: ledfanatic_overhead`, `command_topic ledfanatic/overhead/set`,
`state_topic ledfanatic/overhead/state`, `availability_topic ledfanatic/status`,
`brightness: true`, `supported_color_modes: ["color_temp","rgb"]`,
`color_temp_kelvin: true`, `min_kelvin 2200`, `max_kelvin 6500`, `effect: true`,
`effect_list` = overhead labels, `transition: false`, `flash: false`,
device block `{identifiers: ["ledfanatic_<node_id>"], name: "LED Fanatic", manufacturer,
model, sw_version}`. Discovery test asserts every required key.

Siri path (no code): HA auto-discovers `light.overhead_light` → HomeKit Bridge exposes it
(add to the bridge's entity filter if one is set) → "Hey Siri, set the overhead light to
40 %" / "make it warmer" / "turn it blue" work natively.

## 7. UI — Overhead tab

- New first tab **Overhead** (`index.html` + `app.js`, existing glassmorphism styles).
- Contents: big Power toggle; effect cards (overhead group only, reuse `effect-card`
  markup); active effect controls. Brightness = existing global header slider.
- White's kelvin slider gets a warm→cool gradient track.
- Extract the param-row builder out of `showEffectControls` into
  `buildParamRow(param, value, onChange)` and use it from both tabs (DRY); the
  auto-injected Speed slider is skipped for overhead effects.
- Tab talks to `/api/overhead` (incl. `params` for sliders); refreshes from the
  websocket `overhead` snapshot so Siri changes show up live.
- Run `frontend-design-audit` on the result and fix sev 3–4.

## 8. Testing

- `tests/test_overhead_effects.py`: every `oh_*` at 10×83 and 20×83 → correct shape,
  uint8, no exceptions over 300 frames, speed=0.1 and 3.0, saturation 0 and 1; avg
  render < 3 ms on dev machine; `kelvin_to_rgb` reference points (2700 K warm, 6500 K ≈
  neutral).
- `tests/test_overhead_service.py`: command semantics (off/on/resume/kelvin/rgb/effect/
  unknown effect/brightness while off) against real renderer + StateManager in tmp dir and
  a recording fake transport.
- `tests/test_ha_payloads.py`: discovery/state/command round-trips, brightness scaling,
  label↔name, malformed JSON, HA-shaped combined payloads (effect + color), required
  discovery keys (device.identifiers, transition/flash false).
- Service tests also cover: OFF during festival scene = no-op; params clamping.
- Existing suite stays green.
- Manual on hardware after deploy: tab works; HA entity appears; Siri commands.

## Rollout

1. Implement + tests + Codex diff review → commit/push → deploy.
2. With Jim's permission: add `mqtt:` block to `/opt/ledfanatic/config/system.yaml`
   (Jim types the MQTT password over ssh; never in chat). Jim creates an HA user for
   Mosquitto if needed.
3. Verify entity in HA, add to HomeKit Bridge filter, test Siri.
