# R2 Implementation Plan

## Phase 1: Transport correctness

### 1a. Replace Teensy COBS (P0-01, P0-02)
- Replace COBSDecoder in protocol.h with correct byte-at-a-time decoder
- Replace cobs_encode in protocol.cpp with correct encoder
- Key bug: zero-length blocks (code=0x01) never insert implicit zeros
- Add golden-vector test data that Python and Teensy must agree on
- Add Python-side test that encodes packets with flags=0x0000 and verifies COBS

### 1b. Fix stats sequencing (P0-03)
- Change Teensy: PING handler sends only STATS (not PONG then STATS)
- Change Pi: request_stats expects STATS directly, not PONG
- Keep PONG as a separate lightweight ping (no stats payload)

### 1c. Fix test-pattern exit (P0-04)
- Teensy clears activeTestPattern on valid FRAME receipt
- Add TEST_PATTERN payload value 0xFF = clear/none
- Add API endpoint to clear test patterns
- UI gets "Return to normal" button in diagnostics

## Phase 2: SSOT and constants

### 2a. Hardware constants module (P0-05)
- Create pi/app/hardware_constants.py that reads hardware.yaml
- Export: STRIPS, LEDS_PER_STRIP, TOTAL_LEDS, CHANNELS, LEDS_PER_CHANNEL,
  INTERNAL_WIDTH, OUTPUT_WIDTH, HEIGHT
- Replace all magic numbers in Python code
- Add test that validates Python constants match hardware.yaml
- Add test that validates Teensy config.h values match hardware.yaml

## Phase 3: Transport and metrics

### 3a. Fix serial concurrency (P1-01)
- Use asyncio.Lock around all serial read/write operations
- Add asyncio.to_thread for blocking serial.write in send_frame

### 3b. Fix FPS measurement (P1-02)
- Measure wall-clock interval between successful frame sends
- Report render_cost_ms separately from delivered_fps

## Phase 4: Effects and rendering

### 4a. Fix Fire overflow (P1-06)
- Clamp all RGB channels to [0,255] before uint8 assignment

### 4b. Vectorize slow effects (P1-05)
- Plasma: already partially vectorized, finish removing Python loops
- RainbowRotate: vectorize with numpy meshgrid
- NoiseWash: vectorize sine calculations
- CylinderRotate: vectorize
- Target: all default effects under 8ms on host CPU

### 4c. Unify scene activation (P1-07)
- Single activate_scene() in renderer handles all types
- Media playback registered in effect registry
- Diagnostics go through same path
- All activations update state + broadcast

### 4d. Fix config precedence (P1-08)
- StateManager.load() only overlays keys that were explicitly saved
- Config values are initial defaults
- Persisted state wins only for user-changed values

### 4e. Wire effects.yaml (P1-09)
- Effect registry merges code defaults with effects.yaml params
- UI can send empty params to get yaml defaults

## Phase 5: Firmware fixes

### 5a. Fix colorOrder (P1-11)
- Delete the dead colorOrder state and CONFIG handler
- OctoWS2811 config is set at compile time (WS2811_GRB)

### 5b. Fix brightness split-brain (P1-12)
- Pi sends BRIGHTNESS command to Teensy when effective brightness changes
- Teensy applies masterBrightness to test patterns AND received frames

### 5c. Fix short frame handling (P1-13)
- Clear pendingFrame before copying new data

### 5d. Track broadcast task (P1-14)
- Move periodic broadcast to main.py task list

## Phase 6: Deployment and ops

### 6a. Fix deployment consistency (P0-07)
- Single model: repo is at /opt/pillar/src, pip install -e from there
- setup.sh and deploy.sh use same path
- Add sudoers.d rule for pillar user to run systemctl restart/reboot
- Remove direct sudo from API, use polkit or sudoers

### 6b. Implement hotspot provisioning (P0-06)
- setup.sh installs NetworkManager
- setup.sh creates AP profile from config values
- Document that config must exist before setup

## Phase 7: UI/API and cleanup

### 7a. Fix UI/API contract (P1-10)
- Remove dead UI elements or wire them
- System status returns transport.caps
- Audio device selection posts to API

### 7b. P2 cleanup
- Fix Pydantic models with proper Field types (P2-01)
- Remove dead code (P2-03)
- Update docs to match reality (P2-06)
