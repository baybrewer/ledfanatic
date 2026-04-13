# 05. Raspberry Pi Software Specification

## 5.1 Product role of the Pi

The Raspberry Pi is the **user-facing appliance**.

It should feel like a headless media controller with an LED-specific operating surface:
- plug in power
- connect phone to local Wi-Fi
- open the control site
- pick content
- pillar responds immediately

The Pi should not try to bit-bang LEDs directly.

## 5.2 Operating system baseline

### Recommended baseline
- Raspberry Pi OS Lite, 64-bit
- headless install
- SSH enabled for development
- systemd-managed app startup
- local persistent storage for media, config, and logs

Raspberry Pi documents headless setup as a supported workflow.[5]

### Hardware assumption
Assume **Pi 4B or newer** for full feature set.
A Pi 3 can be made to work for some cases, but it is the wrong baseline if you care about clean media and web responsiveness.

## 5.3 Networking / headless UX

### Required behavior
On boot, the Pi should:
1. bring up a dedicated Wi-Fi hotspot;
2. expose a fixed local IP;
3. run the web UI;
4. be operable from an iPhone with no other infrastructure.

### Recommended implementation
Use a persistent NetworkManager hotspot profile on `wlan0`.

NetworkManager documents hotspot creation via `nmcli dev wifi hotspot ...`.[6]

### Suggested network defaults
- SSID: `Pillar-Control`
- Password: project-specific
- Pi IP: `192.168.4.1`
- UI URL: `http://192.168.4.1`
- optional mDNS: `http://pillar.local`

### UX note
A captive portal is nice, but not required for v1.
The fixed IP and mDNS name are enough.

## 5.4 Service layout

### Preferred deployment
One main Python application service:

`pillar.service`

Modules:
- `api` — REST + WebSocket
- `state` — config, presets, scenes, playlists
- `renderer` — effects + media compositing
- `audio` — FFT / onset / beat detection
- `mapping` — logical-to-physical transform
- `transport` — USB protocol to Teensy
- `diagnostics` — previews, test patterns, logs
- `media` — import/transcode/cache

### Why one service
- simpler deployment
- simpler restart semantics
- no internal IPC tax
- easier to debug

Threads and asyncio tasks are enough here.

## 5.5 Suggested software stack

### Backend
- Python 3.11+
- FastAPI
- uvicorn
- Pydantic models
- WebSockets for live status and UI updates
- pyserial for Teensy transport
- NumPy for frame ops
- Pillow for image work
- PyAV or ffmpeg-based import/transcode
- aubio + NumPy for beat/onset/FFT
- watchdog or polling for media library updates

### Frontend
- responsive mobile-first web app
- static frontend served by FastAPI
- installable PWA optional
- live preview canvas
- large touch-friendly controls
- no desktop-specific assumptions

### UI framework
Any of these is acceptable:
- plain HTML/CSS/JS with a very small framework
- Svelte
- Vue

Do **not** choose a framework that creates a giant build chain for no gain.

### Recommendation
Use:
- **FastAPI backend**
- **vanilla JS or a light framework**
- **WebSocket for live state**
- **CSS grid/flex for phone layout**

This is enough.

## 5.6 UI information architecture

### Main screens

#### 1. Live
- current scene
- master brightness
- FPS
- connection status
- quick scene buttons
- preview

#### 2. Effects
- generative effect list
- per-effect parameters
- palette selection
- speed / scale / rotation / blend controls

#### 3. Media
- upload image/GIF/video
- preview
- fit mode
- playback rate
- loop / hold / bounce

#### 4. Audio
- select input device
- sensitivity
- gain
- smoothing
- beat/onset indicators
- effect modulation routing

#### 5. Mapping / Diagnostics
- strip numbering test
- orientation test
- RGB order test
- seam test
- brightness test
- identify physical channel

#### 6. Scenes / Presets
- save current state
- recall presets
- playlist builder
- autoplay / schedule hooks

> **Implementation note (v1):** Playlist builder, autoplay/schedule hooks, Wi-Fi settings UI, and client-mode Wi-Fi UX are backlog items not yet implemented. See §5.18 Backlog.

#### 7. System
- Wi-Fi settings
- hostname / mDNS
- logs
- restart app
- reboot Pi
- firmware version
- Teensy stats

## 5.7 Render model

### Canonical logical frame
The renderer outputs a logical RGB canvas:

- shape: `(height=172, width=10, channels=3)`

### Optional supersampled internal canvas
For smoother motion:
- internal width 40 or 60
- same height 172
- downsample to width 10 before physical mapping

### Scene processing order
1. base source
   - effect
   - video
   - image
   - diagnostic
2. optional modulation
   - audio envelope
   - beat trigger
   - timebase
3. optional transforms
   - rotate / wrap
   - hue / palette
   - mirror
   - fade / blend
4. postprocess
   - brightness clamp
   - gamma
   - dither optional
5. map to 5 electrical channels
6. serialize and send

## 5.8 Media handling strategy

### Strong recommendation
Do **not** rely exclusively on decoding arbitrary user video formats in the real-time loop.

Instead:

### On import
- decode source asset
- resize/crop to pillar-native or pillar-virtual size
- normalize frame rate
- store a cached project-native representation

### Project-native cache options
- compressed NumPy frame arrays
- zstd-packed RGB frames
- image sequence + metadata
- prebuilt sprite/video clip format

### Why
- deterministic playback
- easier scrubbing
- less CPU jitter
- faster preview

### Recommended v1 choice
Import media to:
- `virtual_width = 40`
- `height = 172`
- target playback rates: 30 or 60 FPS
- cache as compressed frame arrays + JSON metadata

## 5.9 Audio-reactive pipeline

### Inputs
Prefer one of:
- USB audio interface with line in
- USB microphone
- compatible I2S microphone

### Features to compute
- RMS / loudness
- FFT band energies
- beat/onset
- tempo estimate
- peak hold / decay

### Output controls
Audio features can modulate:
- brightness
- hue shift
- effect speed
- radial motion
- pulse size
- strobe triggers
- video blend amount

### Rule
Audio analysis runs on its own cadence.
It should update shared modulation state.
It should not block the frame loop.

## 5.10 Effect inventory for v1

### Base effects
- solid color
- vertical gradient
- rainbow rotate
- plasma
- twinkle
- spark
- noise wash
- color wipe
- scanline / comet
- fire-ish vertical effect
- sine-wave bands
- cylinder rotate
- seam pulse
- diagnostic labels

### Audio-reactive effects
- VU pulse
- low/mid/high color bands
- beat flash
- rotating energy ring
- spectral column glow
- kick-snare-hat mapper

### Media modes
- video clip
- animated GIF
- still image
- scrolling text
- playlist item with transition

## 5.11 Persistent data model

### Must persist
- system config
- hotspot config
- mapping config
- scene definitions
- presets
- playlists
- media metadata
- last used scene
- brightness cap
- calibration data

### Suggested layout
```text
/opt/pillar/
  src/       # pi/ source tree, installed editable
  venv/      # Python virtual environment
  config/    # system.yaml, hardware.yaml, effects.yaml
  media/     # uploaded media files
  cache/     # transcoded frame cache
  logs/      # application logs
```

## 5.12 API design

### Current v1 route table

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

> **Note:** `/api/display/brightness` is a legacy compatibility endpoint. `/api/effects/*` and `/api/mapping/*` do not exist in v1. See backlog section for planned future routes.

### Live updates
Use WebSocket for:
- current FPS
- Teensy online/offline
- current scene state
- audio meter
- upload progress
- error banners

## 5.13 Safety and guardrails

The Pi app must enforce:
- global brightness cap
- per-scene FPS target
- input validation for uploads
- no partial frame transmit
- crash-safe last-known config writes
- rate-limited reboot/system actions from UI

## 5.14 Observability

Expose:
- current FPS
- dropped frame count
- USB reconnect count
- frame send latency
- current scene name
- audio input status
- CPU temperature
- Pi CPU utilization
- Teensy firmware version

## 5.15 Product-level UX requirements

The UI must be usable one-handed on an iPhone.
That means:
- large tap targets
- no desktop hover dependencies
- minimal nested menus
- persistent quick-access controls for brightness and blackout
- scene recall in one tap
- diagnostics reachable without SSH

## 5.16 Non-negotiable implementation rules

1. Phone UI must work without internet.
2. Application must auto-start on boot.
3. A browser refresh must not interrupt playback.
4. Media upload/transcode must not block the live frame loop.
5. USB disconnect/reconnect must self-heal.
6. Mapping must be configurable in a file, not hard-coded in ten places.

## 5.18 Backlog (not implemented in v1)

The following features appear in the planning docs but are not yet implemented:

- `/api/effects/*` — per-effect parameter API
- `/api/mapping/*` — mapping configuration API
- Playlist API/UI
- Autoplay / scheduling
- Wi-Fi settings API/UI (hotspot is provisioned by setup.sh, not runtime API)
- Client-mode Wi-Fi UX
- Captive portal
- PWA install prompt
- Scrolling text media mode

## References

[5] Raspberry Pi headless setup  
[6] NetworkManager hotspot  
[1] PJRC OctoWS2811 library
