# Pillar Controller

LED pillar controller: Raspberry Pi + Teensy 4.1 + OctoWS2811.

## Architecture
- **Pi**: FastAPI backend, phone UI, effects/media/audio, USB frame transport
- **Teensy**: OctoWS2811 DMA output, packet handling, diagnostics
- **Protocol**: Binary COBS-framed packets over USB Serial with CRC32
- **Canvas**: Logical 10x172 RGB -> mapped to 5x344 electrical channels

## SSOT enforcement
- `pi/config/hardware.yaml` — physical layout (strips, channels, LEDs)
- `pi/app/hardware_constants.py` — Python constants loaded from hardware.yaml
- `teensy/firmware/include/config.h` — Teensy constants (validated by tests, regenerable via `pi/scripts/generate_teensy_config.py`)
- `pi/app/models/protocol.py` — protocol packet types, payload schemas, constants
- `pi/config/effects.yaml` — effect defaults and palettes (merged into renderer)

## Key modules
- `pi/app/main.py` — entry point, lifecycle, startup/shutdown
- `pi/app/api/server.py` — app factory and router composition
- `pi/app/api/routes/` — route modules (system, scenes, brightness, media, audio, diagnostics, transport, ws)
- `pi/app/api/schemas.py` — Pydantic request/response models
- `pi/app/api/auth.py` — centralized Bearer token auth (fail-closed)
- `pi/app/core/renderer.py` — render loop, scene activation, effects config merge
- `pi/app/core/brightness.py` — brightness engine + solar automation (astral)
- `pi/app/core/state.py` — debounced persistent state (mark_dirty/flush)
- `pi/app/hardware_constants.py` — SSOT constants from hardware.yaml
- `pi/app/mapping/cylinder.py` — serpentine mapping (uses hardware_constants)
- `pi/app/transport/usb.py` — USB serial transport (lock-protected I/O)
- `teensy/firmware/src/main.cpp` — Teensy firmware

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
- `pi/config/hardware.yaml` — physical layout SSOT
- `pi/config/effects.yaml` — effect defaults, merged into renderer

## Deployment
- Canonical source: `/opt/pillar/src/` (both setup.sh and deploy.sh use this)
- `pip install -e /opt/pillar/src[audio,video]` in `/opt/pillar/venv/`
- systemd runs `/opt/pillar/venv/bin/pillar`
- Hotspot provisioned by setup.sh from system.yaml network config

## Running locally (dev)
```bash
cd pi
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
PILLAR_DEV=1 python -m app.main  # starts on :8000
```

## Running tests
```bash
cd pi
source .venv/bin/activate
PYTHONPATH=. pytest tests/ -v  # ~219 tests
```

## Deploying to Pi
```bash
pi/scripts/setup.sh            # first time (creates user, venv, hotspot, sudoers)
pi/scripts/deploy.sh pillar.local  # updates (rsync + pip install + restart)
```

## Rules
- Pi owns rendering; Teensy owns LED output
- Never hardcode geometry — use hardware_constants.py / hardware.yaml
- 60 FPS default target
- Scene activation goes through renderer.activate_scene() for all types
- Serial I/O protected by asyncio.Lock; send_frame uses asyncio.to_thread
- State saves are debounced (mark_dirty + periodic flush), force_save on shutdown
- state.json and media metadata.json carry schema_version for migration safety
- `docs/current-contracts.md` is the canonical human-readable contract reference
- `docs/MASTER_SPEC.md` is a consolidated copy of planning docs — do not edit independently
