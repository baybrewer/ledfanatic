# Pillar Controller Remediation — Overview

## Purpose

Full remediation pass on the pillar-controller repository to fix protocol
mismatches, security gaps, broken media paths, inconsistent deployment,
missing brightness automation, and general code quality issues.

## Scope

15 work items across these categories:

| # | Area | Severity |
|---|------|----------|
| 1 | Deployment/packaging consistency | HIGH |
| 2 | Blackout semantics (toggle → explicit) | MEDIUM |
| 3 | Media import/upload/playback fixes | HIGH |
| 4 | Stats payload Pi↔Teensy mismatch | HIGH |
| 5 | Auth for privileged endpoints | CRITICAL |
| 6 | Brightness control + sunrise/sunset automation | FEATURE |
| 7 | Upload memory/size limits | MEDIUM |
| 8 | Render/transport metrics correctness | MEDIUM |
| 9 | Shutdown/lifecycle handling | HIGH |
| 10 | Production config usage | MEDIUM |
| 11 | Remove committed secrets | CRITICAL |
| 12 | Persistence consistency | MEDIUM |
| 13 | Thread-safety audio/render | MEDIUM |
| 14 | Clean dependency/install flow | LOW |
| 15 | Repo hygiene | LOW |

## Out of scope

- New effects or media formats
- Hardware wiring changes
- Teensy firmware feature additions beyond protocol fixes
- Multi-user roles or cloud sync
- Captive portal

## Key decisions

1. **Auth model**: Shared bearer token, configured in `system.yaml`, enforced
   via FastAPI dependency injection. Fail closed.
2. **Blackout protocol**: Change from toggle to explicit on/off payload byte.
   Protocol version remains 1 (additive change — old BLACKOUT with empty
   payload treated as "on" for backward safety).
3. **Brightness model**: Manual cap + optional sunrise/sunset automation using
   `astral` library for deterministic solar calculations. Five-phase model.
4. **Deployment model**: `pip install -e .` in a venv at `/opt/pillar/venv`,
   systemd runs the installed `pillar` console script.
5. **Secrets**: WiFi password and auth token move to `system.yaml.example`;
   real values in untracked `system.yaml` on device.
6. **Stats payload**: Fix Pi parser threshold from 32→28 bytes. Add explicit
   struct format constant shared between docs and code.
7. **Media metadata**: Fix `type`→`media_type` key mismatch in dict
   construction and scan_library loader.
8. **Thread safety**: Use `threading.Lock` around audio state snapshot copy.

## Files primarily affected

### Pi backend
- `pi/app/main.py` — lifecycle, config, startup/shutdown
- `pi/app/api/server.py` — auth, endpoints, upload limits
- `pi/app/core/renderer.py` — metrics, brightness integration
- `pi/app/core/state.py` — batched persistence, config paths
- `pi/app/core/brightness.py` — NEW: brightness engine + solar
- `pi/app/transport/usb.py` — stats parsing, metrics
- `pi/app/models/protocol.py` — blackout semantics
- `pi/app/media/manager.py` — metadata fix, config integration
- `pi/app/audio/analyzer.py` — thread-safe state handoff

### Teensy firmware
- `teensy/firmware/src/main.cpp` — blackout explicit, stats payload
- `teensy/firmware/include/protocol.h` — COBS decoder reset fix

### Config
- `pi/config/system.yaml` → `pi/config/system.yaml.example`
- `pi/config/hardware.yaml` — add location config for solar

### Frontend
- `pi/app/ui/static/index.html` — brightness controls, auth
- `pi/app/ui/static/js/app.js` — auth header, brightness UI
- `pi/app/ui/static/css/app.css` — brightness section styling

### Infrastructure
- `pi/pyproject.toml` — add astral dependency
- `pi/systemd/pillar.service` — fix paths
- `pi/scripts/setup.sh` — fix install flow
- `pi/scripts/deploy.sh` — fix deploy flow
- `.gitignore` — tighten rules

### Tests
- `pi/tests/test_protocol.py` — blackout, stats fixtures
- `pi/tests/test_mapping.py` — unchanged
- `pi/tests/test_media.py` — NEW
- `pi/tests/test_brightness.py` — NEW
- `pi/tests/test_auth.py` — NEW
- `pi/tests/test_upload.py` — NEW
