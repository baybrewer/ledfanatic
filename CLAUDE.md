# LED Fanatic

LED controller: Raspberry Pi + Teensy 4.1 + OctoWS2811.

## Architecture
- **Pi**: FastAPI backend, phone UI, effects/media/audio, USB frame transport
- **Teensy**: OctoWS2811 DMA output, packet handling, diagnostics
- **Protocol**: Binary COBS-framed packets over USB Serial with CRC32
- **Layout**: Declarative YAML config → compiled mapping table → fast packer (3-layer architecture)
- **Canvas**: Logical grid (width × height × RGB) → mapped to physical outputs via layout compiler

## Layout system (rewrote 2026-04-26)
- `pi/config/layout.yaml` — declarative LED geometry SSOT (outputs, segments, directions, offsets)
- `pi/app/layout/schema.py` — data models: LayoutConfig, OutputConfig, LinearSegment, ExplicitSegment
- `pi/app/layout/compiler.py` — validates config, compiles to CompiledLayout with forward/reverse LUTs + flat MappingEntry list + precomputed NumPy pack indices
- `pi/app/layout/packer.py` — vectorized NumPy packer using precomputed index arrays (0.24ms per frame)
- `pi/app/layout/__init__.py` — public API: load_layout, save_layout, compile_layout, validate_layout, pack_frame, output_config_list
- Per-segment color_order supported (overrides output-level default)
- Renderer flips y-axis when origin is "bottom_left" (effects use screen coords, y=0 at top)

## Key modules
- `pi/app/main.py` — entry point, lifecycle, startup/shutdown
- `pi/app/api/server.py` — app factory and router composition
- `pi/app/api/routes/` — route modules (system, scenes, brightness, media, audio, diagnostics, transport, ws, layout, setup)
- `pi/app/api/routes/layout.py` — layout CRUD, test-segment, segment-identify, strip-identify, LED probe
- `pi/app/api/schemas.py` — Pydantic request/response models
- `pi/app/api/auth.py` — centralized Bearer token auth (fail-closed)
- `pi/app/core/renderer.py` — render loop, scene activation, test patterns (segment/strip identify, probe), y-flip for bottom_left origin
- `pi/app/core/brightness.py` — brightness engine + solar automation (astral)
- `pi/app/core/state.py` — debounced persistent state (mark_dirty/flush)
- `pi/app/transport/usb.py` — USB serial transport (lock-protected I/O)
- `pi/app/effects/` — generative, audio-reactive, imported, simulation, tetris, fireworks, scrolltext
- `pi/app/effects/registry.py` — canonical ALL_EFFECTS dict (SSOT for renderer + benchmark)
- `pi/app/audio/adapter.py` — AudioCompatAdapter with resample_bands() for width-independent effects
- `teensy/firmware/src/main.cpp` — Teensy firmware

## Effects rules
- **Never hardcode grid dimensions** in effects — use self.width/self.height
- Audio bands must be resampled to grid width via `AudioCompatAdapter.resample_bands(bands, self.width)`
- Effects render in screen coordinates (y=0 = top) — renderer handles y-flip for physical origin
- Temporal smoothing (blend with previous frame) for fire/smooth effects
- All new effects must be registered in `pi/app/effects/registry.py` (SSOT) and `pi/app/effects/catalog.py` (metadata)
- Particle systems should use NumPy structured arrays, not Python object lists

## Compositor (added 2026-04-28)
- `pi/app/core/compositor.py` — Layer model, 5 blend modes (normal/add/screen/multiply/max), Compositor class
- Layer stack with per-layer opacity, blend mode, enable/disable
- Per-layer error isolation (crash in one layer doesn't affect others)
- Shared `_create_effect()` honors RENDER_SCALE, YAML param merge, animation_switcher
- Mode exclusion: activate_scene clears compositor; /layers/add bootstraps from current scene
- State persistence: current_layers + render_mode in state.json v2
- API: GET/POST /api/scenes/layers (add, remove, update, reorder)
- Brightness calibration: per-segment 3-point RGB correction curves in layout.yaml → 256-entry LUTs in pack_frame

## Performance (measured 2026-04-26)
- pack_frame: 0.24ms (vectorized NumPy fancy indexing, precomputed at compile time)
- send_frame: ~5ms (USB CDC, hardware-limited)
- Render profiling: `effect_render_ms`, `pack_ms`, `send_ms` exposed via `/api/system/status`
- Benchmark harness: `python -m tools.bench_effects` (uses ALL_EFFECTS from registry)

## Auth
- Bearer token in `Authorization` header
- Token in `system.yaml` under `auth.token`
- All POST/DELETE endpoints require auth; GET endpoints are public
- Fail closed: no configured token = all protected endpoints rejected

## Brightness
- Manual cap always active (0.0-1.0)
- Optional solar automation (astral library, 5 phases: night/dawn/day/dusk)
- Effective brightness = min(manual_cap, solar_factor)
- Config in system.yaml under `brightness`

## Protocol rules
- Blackout is explicit (payload 0x01=on, 0x00=off), never toggle
- Stats payload is exactly 28 bytes (7 x uint32)
- PING returns STATS directly (not PONG+STATS)
- Test patterns clear on valid FRAME receipt or TEST_PATTERN_NONE (0xFF)
- COBS implementation must match golden vectors in test_protocol.py

## Config precedence
code defaults < yaml config files < persisted state (state.json) < live API overrides

## Config files
- `pi/config/system.yaml.example` — template (tracked, placeholders)
- `pi/config/system.yaml` — real config (gitignored, contains secrets)
- `pi/config/layout.yaml` — LED layout geometry SSOT
- `pi/config/hardware.yaml` — legacy physical layout reference
- `pi/config/effects.yaml` — effect defaults, merged into renderer

## Deployment
- **ALWAYS deploy after changes** — no local testing; the hardware is on the Pi. Never claim done without deploying first.
- **NEVER edit Pi config files without explicit user permission** — the user's hardware layout is non-obvious
- Deploy target: `jim@ledfanatic.local` (run `bash pi/scripts/deploy.sh ledfanatic.local`)
- Config files live at `/opt/ledfanatic/config/` on Pi — deploy script only copies code, NOT config
- To push a new config: `scp` or `sudo cp /opt/ledfanatic/src/config/X /opt/ledfanatic/config/X`
- Canonical source: `/opt/ledfanatic/src/` (both setup.sh and deploy.sh use this)
- ``pip install -e /opt/ledfanatic/src[audio,video]`` in `/opt/ledfanatic/venv/`
- systemd runs `/opt/ledfanatic/venv/bin/ledfanatic` (port 80, not 8000)
- Hotspot provisioned by setup.sh from system.yaml network config

## Running locally (dev)
```bash
cd pi
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
LEDFANATIC_DEV=1 python -m app.main  # starts on :8000
```

## Running tests
```bash
cd pi
source .venv/bin/activate
PYTHONPATH=. pytest tests/ -v
```

## Deploying to Pi
```bash
pi/scripts/setup.sh            # first time (creates user, venv, hotspot, sudoers)
pi/scripts/deploy.sh ledfanatic.local  # updates (rsync + pip install + restart)
```

## UI pages
- Main controller: `http://ledfanatic.local/` — tabs: Live, Effects, Media, Audio, Sim, Game, System
- LED Probe: `http://ledfanatic.local/static/probe.html` — keyboard-driven single-LED test tool
- Tetris controller: Game tab in main UI (also standalone at `/static/tetris.html`)

## Rules
- Pi owns rendering; Teensy owns LED output
- Never hardcode geometry — use layout.yaml / CompiledLayout
- Never hardcode audio band count — use resample_bands()
- 60 FPS default target
- Scene activation goes through renderer.activate_scene() for all types
- Serial I/O protected by asyncio.Lock; send_frame uses asyncio.to_thread
- State saves are debounced (mark_dirty + periodic flush), force_save on shutdown
- state.json and media metadata.json carry schema_version for migration safety
- `_on_teensy_connect` callback references `deps.compiled_layout` (not captured local) so layout API changes propagate to reconnects
