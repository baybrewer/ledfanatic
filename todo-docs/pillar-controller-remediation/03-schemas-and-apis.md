# Schemas and APIs — Canonical SSOT

## 1. Protocol packet format

All packets: little-endian, COBS-framed, 0x00 delimited.

### Header (24 bytes)
```
Offset  Size  Field
0       4     magic           "PILL"
4       1     version         1
5       1     type            PacketType enum
6       2     flags           reserved
8       4     frame_id        monotonic counter
12      8     timestamp_us    microsecond timestamp
20      4     payload_len     byte count of payload
```

### Packet types
```
0x01  HELLO
0x02  CAPS
0x03  CONFIG
0x10  FRAME
0x20  PING
0x21  PONG
0x30  STATS
0x40  TEST_PATTERN
0x41  BLACKOUT
0x42  BRIGHTNESS
0xFF  REBOOT_TO_BOOTLOADER
```

### FRAME payload
```
Offset  Size  Field
0       1     channels        5
1       2     leds_per_ch     344 (little-endian)
3       N     pixel_data      channels × leds_per_ch × 3 bytes (RGB)
```
N = 5 × 344 × 3 = 5160 bytes. Total payload = 5163 bytes.

### HELLO payload (48 bytes)
```
Offset  Size  Field
0       32    app_name        null-padded UTF-8
32      16    app_version     null-padded UTF-8
```

### CAPS payload (56 bytes) — Teensy → Pi
```
Offset  Size  Field
0       16    firmware_version  null-padded UTF-8
16      1     protocol_version  1
17      1     outputs           5
18      2     leds_per_strip    344 (little-endian)
20      4     color_order       null-padded UTF-8 ("GRB")
24      32    reserved
```

### STATS payload (28 bytes) — Teensy → Pi
```
Offset  Size  Field
0       4     uptime_ms         uint32
4       4     frames_received   uint32
8       4     frames_applied    uint32
12      4     bad_crc           uint32
16      4     bad_frame         uint32
20      4     dropped_pending   uint32
24      4     output_fps        uint32
```
**IMPORTANT**: Exactly 28 bytes. Pi parser must check `len >= 28`.

### BLACKOUT payload (1 byte)
```
Offset  Size  Field
0       1     state     0x00 = off (resume), 0x01 = on (blackout)
```
Empty payload treated as 0x01 (on) for backward safety.

### BRIGHTNESS payload (1 byte)
```
Offset  Size  Field
0       1     value     0-255 master brightness scalar
```

### TEST_PATTERN payload (1 byte)
```
Offset  Size  Field
0       1     pattern_id   TestPattern enum value
```

### CONFIG payload (1+ bytes)
```
Offset  Size  Field
0       1     color_order   COLOR_ORDER enum
```

## 2. Auth configuration

Location: `system.yaml`
```yaml
auth:
  token: "your-secret-token-here"
```

- If `auth.token` is missing, empty, or equals placeholder → fail closed
- Token passed as `Authorization: Bearer <token>` header
- WebSocket: `?token=<token>` query parameter

## 3. Brightness configuration and state

### Config (system.yaml)
```yaml
brightness:
  manual_cap: 0.8           # 0.0–1.0, safety maximum
  auto_enabled: false        # enable solar automation
  location:
    latitude: 37.7749
    longitude: -122.4194
    timezone: "America/Los_Angeles"
  solar:
    night_brightness: 0.3    # brightness during full night
    dawn_offset_minutes: 30  # transition half-width
    dusk_offset_minutes: 30
```

### Runtime state (state.json)
```json
{
  "brightness": {
    "manual_cap": 0.8,
    "auto_enabled": false
  }
}
```

### API response shape
```json
{
  "manual_cap": 0.8,
  "auto_enabled": false,
  "effective_brightness": 0.8,
  "solar_factor": 1.0,
  "phase": "DAY",
  "next_transition": "2026-03-31T19:30:00-07:00"
}
```

### Phases enum
```
NIGHT = 0
DAWN = 1
DAY = 2
DUSK = 3
```

## 4. Media metadata schema

### Stored on disk (cache/{id}/metadata.json)
```json
{
  "id": "a1b2c3d4",
  "name": "sunset.gif",
  "media_type": "gif",
  "frame_count": 48,
  "fps": 15.0,
  "width": 40,
  "height": 172
}
```
**Key**: `media_type`, NOT `type`. Consistent with MediaItem constructor.

### API response shape
```json
{
  "id": "a1b2c3d4",
  "name": "sunset.gif",
  "type": "gif",
  "frame_count": 48,
  "fps": 15.0,
  "width": 40,
  "height": 172
}
```
Note: API returns `type` for cleaner JSON. MediaItem.to_dict() maps
`media_type` → `type`.

## 5. Diagnostics/stats API response

### GET /api/diagnostics/stats
```json
{
  "transport": {
    "connected": true,
    "port": "/dev/ttyACM0",
    "caps": {
      "firmware_version": "1.0.0",
      "protocol_version": 1,
      "outputs": 5,
      "leds_per_strip": 344,
      "color_order": "GRB"
    },
    "frames_sent": 12345,
    "send_errors": 0,
    "reconnect_count": 1
  },
  "render": {
    "manual_cap": 0.8,
    "effective_brightness": 0.8,
    "target_fps": 60,
    "actual_fps": 59.8,
    "current_scene": "rainbow_rotate",
    "blackout": false,
    "frames_rendered": 50000,
    "frames_sent": 49998,
    "frames_dropped": 2,
    "last_frame_time_ms": 3.2
  },
  "teensy": {
    "uptime_ms": 600000,
    "frames_received": 49998,
    "frames_applied": 49990,
    "bad_crc": 0,
    "bad_frame": 0,
    "dropped_pending": 8,
    "output_fps": 60
  }
}
```

## 6. System status API response

### GET /api/system/status
```json
{
  "transport": { "connected": true, "port": "/dev/ttyACM0" },
  "render": { "actual_fps": 59.8, "current_scene": "rainbow_rotate" },
  "brightness": { "manual_cap": 0.8, "effective_brightness": 0.8, "phase": "DAY" },
  "scenes_count": 5,
  "media_count": 3
}
```

## 7. REST API endpoints

### Public (no auth required)
```
GET  /                              → UI index.html
GET  /static/*                      → static assets
GET  /api/system/status             → system overview
GET  /api/scenes/list               → available effects
GET  /api/scenes/presets            → saved presets
GET  /api/media/list                → media library
GET  /api/audio/devices             → audio input devices
GET  /api/diagnostics/stats         → full diagnostics
GET  /api/transport/status          → transport status
GET  /api/brightness/status         → brightness state
WS   /ws                            → live updates (token in query)
```

### Protected (Bearer token required)
```
POST   /api/scenes/activate           → set active scene
POST   /api/scenes/presets/save       → save preset
POST   /api/scenes/presets/load/:name → load preset
DELETE /api/scenes/presets/:name      → delete preset
POST   /api/display/brightness        → set manual brightness
POST   /api/display/fps               → set target FPS
POST   /api/display/blackout          → set blackout state
POST   /api/brightness/config         → update brightness settings
POST   /api/media/upload              → upload media file
POST   /api/media/play/:id            → play media item
DELETE /api/media/:id                 → delete media item
POST   /api/audio/config              → configure audio
POST   /api/audio/start               → start audio capture
POST   /api/audio/stop                → stop audio capture
POST   /api/diagnostics/test-pattern  → run test pattern
POST   /api/system/restart-app        → restart service
POST   /api/system/reboot             → reboot Pi
```
