# Current Contracts — Pillar Controller v1

This document describes the **shipped** behavior of the pillar controller.
It is the human-readable contract reference for future work.

For design intent and rationale, see the numbered planning docs (01–10).

## 1. Route Table

| Method | Path | Purpose | Auth |
|---|---|---|---|
| GET | `/api/system/status` | overall status | public |
| POST | `/api/system/reboot` | reboot host | protected |
| POST | `/api/system/restart-app` | restart systemd service | protected |
| GET | `/api/scenes/list` | list effects by category | public |
| POST | `/api/scenes/activate` | activate non-media scene | protected |
| GET | `/api/scenes/presets` | list saved presets | public |
| POST | `/api/scenes/presets/save` | save preset | protected |
| POST | `/api/scenes/presets/load/{name}` | load preset | protected |
| DELETE | `/api/scenes/presets/{name}` | delete preset | protected |
| GET | `/api/brightness/status` | brightness state | public |
| POST | `/api/brightness/config` | set brightness/solar config | protected |
| POST | `/api/display/brightness` | legacy brightness endpoint | protected |
| POST | `/api/display/fps` | set FPS | protected |
| POST | `/api/display/blackout` | set blackout | protected |
| GET | `/api/media/list` | list media | public |
| POST | `/api/media/upload` | upload/import media | protected |
| POST | `/api/media/play/{item_id}` | play media item | protected |
| DELETE | `/api/media/{item_id}` | delete media item | protected |
| GET | `/api/audio/devices` | list audio devices | public |
| POST | `/api/audio/config` | configure audio analyzer | protected |
| POST | `/api/audio/start` | start audio analyzer | protected |
| POST | `/api/audio/stop` | stop audio analyzer | protected |
| POST | `/api/diagnostics/test-pattern` | run diagnostic pattern | protected |
| POST | `/api/diagnostics/clear` | clear diagnostic mode | protected |
| GET | `/api/diagnostics/stats` | combined diagnostics/stats | public |
| GET | `/api/transport/status` | transport status | public |
| WS | `/ws` | websocket state channel | public |
| GET | `/` | static UI root | public |

**Auth model:** Bearer token in `Authorization` header. Token configured in `system.yaml` under `auth.token`. All POST/DELETE endpoints require auth; GET endpoints are public. Fail-closed: no configured token = all protected endpoints rejected.

## 2. Protocol Contract

### Packet framing
- Binary packets over USB Serial.
- Packet bytes are COBS-encoded.
- `0x00` is the packet delimiter.
- Raw packet layout: `header(24 bytes) | payload(N bytes) | crc32(4 bytes)`.

### Header layout (24 bytes)
```text
magic         4 bytes   "PILL"
version       1 byte
type          1 byte
flags         2 bytes   little-endian uint16
frame_id      4 bytes   little-endian uint32
timestamp_us  8 bytes   little-endian uint64
payload_len   4 bytes   little-endian uint32
```

### FRAME payload layout
```text
channels          1 byte
leds_per_channel  2 bytes   little-endian uint16
pixel_data        N bytes   channel-major RGB triplets
```

### Packet types

| Type | Value | Direction | Notes |
|---|---|---|---|
| HELLO | 0x01 | Pi → Teensy | 48-byte payload (app name + version) |
| CAPS | 0x02 | Teensy → Pi | 56-byte payload (firmware info + capabilities) |
| CONFIG | 0x03 | Pi → Teensy | Defined but currently a no-op in firmware |
| FRAME | 0x10 | Pi → Teensy | Pixel data with 3-byte meta prefix |
| PING | 0x20 | Pi → Teensy | Triggers STATS response (not PONG) |
| PONG | 0x21 | — | Reserved/unused |
| STATS | 0x30 | Teensy → Pi | 28 bytes (7 × uint32) |
| TEST_PATTERN | 0x40 | Pi → Teensy | 1-byte pattern ID; 0xFF = clear |
| BLACKOUT | 0x41 | Pi → Teensy | Explicit: 0x01 = on, 0x00 = off |
| BRIGHTNESS | 0x42 | Pi → Teensy | 1-byte brightness (0–255) |
| REBOOT_TO_BOOTLOADER | 0xFF | Pi → Teensy | Reboot Teensy to bootloader |

### STATS payload fields (28 bytes, 7 × uint32 LE)
1. `uptime_ms`
2. `frames_received`
3. `frames_applied`
4. `bad_crc`
5. `bad_frame`
6. `dropped_pending`
7. `output_fps`

### Handshake sequence
- Pi sends `HELLO` → Teensy replies `CAPS`
- `CONFIG` exists but firmware currently treats it as a no-op
- Color order is compile-time GRB

## 3. Config Precedence

```
code defaults < yaml config files < persisted state (state.json) < live API overrides
```

### Config files
| File | Purpose | Tracked |
|---|---|---|
| `pi/config/system.yaml.example` | Template with placeholders | Yes |
| `pi/config/system.yaml` | Real config (contains secrets) | No (gitignored) |
| `pi/config/hardware.yaml` | Physical layout SSOT | Yes |
| `pi/config/effects.yaml` | Effect defaults and palettes | Yes |

### Persistence
- `state.json` — current scene, presets, brightness cap, FPS (versioned with `schema_version`)
- Media `metadata.json` — per-item cache metadata (versioned with `schema_version`)
- Both use atomic writes for crash safety

## 4. Deployment Paths

### Production layout
```text
/opt/pillar/
  src/       # pi/ source tree, installed editable via pip
  venv/      # Python virtual environment
  config/    # system.yaml, hardware.yaml, effects.yaml
  media/     # uploaded media originals
  cache/     # transcoded frame cache
  logs/      # application logs
```

### Service
- systemd unit: `pillar.service`
- Command: `/opt/pillar/venv/bin/pillar`
- User: `pillar` (groups: dialout, audio)
- Auto-restart on failure

### First-run setup
```bash
# 1. Run setup script (creates user, venv, hotspot, systemd)
pi/scripts/setup.sh

# 2. Edit config
sudo -u pillar vi /opt/pillar/config/system.yaml
# Set auth.token and network.password

# 3. Start
sudo systemctl start pillar
sudo journalctl -u pillar -f
```

### Deploying updates
```bash
pi/scripts/deploy.sh pillar.local
# Syncs pi/ → /opt/pillar/src, reinstalls package, restarts service
```

### Networking
- Hotspot/AP is provisioned by `setup.sh` via NetworkManager (`nmcli`), not by FastAPI at runtime
- SSID, password, and IP come from `system.yaml` network section
- mDNS via avahi-daemon (hostname from config)

## 5. Hardware Geometry

**Source of truth:** `pi/config/hardware.yaml`

| Parameter | Value |
|---|---|
| Physical strips | 10 (S0–S9) |
| LEDs per strip | 172 |
| Total LEDs | 1720 |
| Electrical channels | 5 |
| LEDs per channel | 344 (2 × 172 serpentine) |
| Color order | GRB (compile-time) |

### Mapping
- Logical canvas: 10 columns × 172 rows (row 0 = bottom)
- Electrical: 5 channels × 344 LEDs
- Even strips (S0, S2, S4, S6, S8): bottom → top
- Odd strips (S1, S3, S5, S7, S9): top → bottom
- Seam between S9 and S0

**Current limitation:** Python mapping in `pi/app/mapping/cylinder.py` hardcodes adjacent strip pairing and even/odd serpentine directions. The `hardware.yaml` fields `channels.pairs`, `wiring.even_strip_direction`, `wiring.odd_strip_direction`, and `wiring.seam_position` exist but are not consumed by the mapping code. The mapping works correctly for the current hardware but is not reconfigurable at runtime.

### Teensy config sync
- Teensy uses compile-time macros in `teensy/firmware/include/config.h`
- Python reads from `hardware.yaml` at import time
- A cross-language test (`test_protocol.py::TestHardwareConstants`) validates both sides match

## 6. What Is Implemented vs. Backlog

### Implemented (v1)
- Full REST API (28 routes, see route table above)
- Binary protocol over USB Serial with COBS framing
- Generative effects engine with YAML config merge
- Audio-reactive effects with FFT/beat detection
- Media import (image, GIF, video) with frame cache
- Solar-aware brightness automation
- Persistent state with schema versioning
- Diagnostic test patterns (Teensy-side and Pi-side)
- WebSocket live state updates
- Auth with fail-closed behavior
- Hotspot provisioning via setup script
- systemd service with auto-restart

### Backlog (not implemented)
- `/api/effects/*` — per-effect parameter API
- `/api/mapping/*` — mapping configuration API
- Playlist API/UI
- Autoplay / scheduling
- Wi-Fi settings API/UI (runtime)
- Client-mode Wi-Fi UX
- Runtime color-order configuration via CONFIG packet
- Config-driven mapping (consuming hardware.yaml pairs/directions)
- Dedicated Teensy firmware test suite
- Captive portal
- PWA install prompt
