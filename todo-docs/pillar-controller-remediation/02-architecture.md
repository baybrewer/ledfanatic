# Architecture

## System overview (unchanged)

```
iPhone → Wi-Fi → Pi (FastAPI + render + transport) → USB → Teensy → OctoWS2811 → LEDs
```

## Module responsibility map (post-remediation)

### pi/app/main.py
- Load config from correct paths (dev vs prod)
- Initialize all components with injected config
- Register FastAPI lifecycle hooks (startup/shutdown)
- Track background tasks for clean cancellation

### pi/app/api/server.py
- FastAPI app factory
- Auth dependency (Bearer token via `Depends`)
- REST endpoints with auth enforcement
- WebSocket live updates
- Upload handling with size limits
- No business logic — delegates to core modules

### pi/app/api/auth.py (NEW)
- `get_auth_token()` — reads configured token
- `require_auth` — FastAPI Depends that validates Bearer header
- Fail closed: missing config → always reject

### pi/app/core/renderer.py
- Render loop with corrected metrics
- Consumes effective brightness from brightness engine
- Frame counting: rendered vs sent separation
- Clean shutdown via cancellation

### pi/app/core/state.py
- Persistent JSON state with debounced writes
- Config path injected, not hardcoded
- Batched save API: `mark_dirty()` + periodic flush

### pi/app/core/brightness.py (NEW)
- `BrightnessEngine` class
- Manual brightness cap
- Solar automation (astral-based sunrise/sunset)
- Five-phase transition model
- `get_effective_brightness(now) → float`
- Pure computation, no side effects, fully testable

### pi/app/transport/usb.py
- Fixed stats parser (28-byte threshold)
- Corrected metrics: send success/failure tracking

### pi/app/models/protocol.py
- Explicit blackout payloads (0x01 on, 0x00 off)
- Stats payload format constant

### pi/app/media/manager.py
- Fixed metadata key construction
- Config-driven dimensions (not hardcoded)
- Streaming upload handling

### pi/app/audio/analyzer.py
- Thread-safe state via lock + snapshot dict
- Clean stop/join on shutdown

### teensy/firmware/src/main.cpp
- Explicit blackout (payload byte, not toggle)
- COBS decoder reset includes `_pending_zero`

## Brightness five-phase model

```
Phase 0: NIGHT          → solar_factor = night_brightness (default 0.3)
Phase 1: DAWN           → linear ramp from night to 1.0 over dawn_minutes
Phase 2: DAY            → solar_factor = 1.0
Phase 3: DUSK           → linear ramp from 1.0 to night over dusk_minutes
Phase 4: NIGHT (again)  → solar_factor = night_brightness
```

Transitions:
- Dawn starts at `sunrise - dawn_offset_minutes`
- Dawn ends at `sunrise + dawn_offset_minutes`
- Dusk starts at `sunset - dusk_offset_minutes`
- Dusk ends at `sunset + dusk_offset_minutes`

Config:
```yaml
brightness:
  manual_cap: 0.8
  auto_enabled: false
  location:
    latitude: 37.7749
    longitude: -122.4194
    timezone: "America/Los_Angeles"
  solar:
    night_brightness: 0.3
    dawn_offset_minutes: 30
    dusk_offset_minutes: 30
```

Effective brightness = `manual_cap * solar_factor` (when auto enabled)
Effective brightness = `manual_cap` (when auto disabled or calc fails)

## Auth architecture

```
Request → Bearer token header → auth dependency → match config token → allow/reject
```

- Token stored in `system.yaml` under `auth.token`
- No token configured → all privileged requests rejected (fail closed)
- Read-only endpoints (GET status, GET effects list) remain open
- All POST/DELETE endpoints that mutate state require auth
- WebSocket requires token as query parameter `?token=...`

## Deployment architecture

```
/opt/pillar/
  venv/           ← Python virtual environment
  config/         ← system.yaml, hardware.yaml, effects.yaml (untracked real config)
  media/          ← uploaded media files
  cache/          ← transcoded frame cache
  logs/           ← application logs
```

Source code lives in the venv via `pip install -e /path/to/repo/pi`.
systemd runs `/opt/pillar/venv/bin/pillar`.

## State persistence model

```
StateManager:
  _state: dict         ← in-memory state
  _dirty: bool         ← needs write
  _flush_interval: 1s  ← max write frequency

  mark_dirty()         ← called after mutations
  flush()              ← periodic task writes if dirty
  force_save()         ← immediate write (shutdown)
```
