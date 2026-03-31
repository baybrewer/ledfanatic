# Edge Cases and Risks

## Protocol edge cases

### Blackout during frame transmission
- **Scenario**: Blackout ON arrives while Teensy is mid-DMA transfer
- **Handling**: Teensy sets blackout flag immediately; on next `!leds.busy()`
  check, it clears and shows. The in-flight DMA frame completes normally,
  then the very next loop iteration shows black.
- **Risk**: One extra frame of non-black output (10ms). Acceptable.

### Stale pending frame after blackout off
- **Scenario**: Blackout ON, Pi stops rendering. Blackout OFF, Teensy has
  no pending frame.
- **Handling**: Teensy's blackout=false + no pending = just keeps clearing
  until Pi sends a new frame. Pi should resume rendering immediately when
  blackout is turned off.
- **Risk**: Brief black gap between blackout off and first new frame (~16ms).

### Stats request during high frame rate
- **Scenario**: Pi sends PING while Teensy is processing frames
- **Handling**: Teensy handles PING in the same packet loop. Response is
  queued in USB Serial output buffer. No frame processing delay.
- **Risk**: Stats may be 1 frame stale. Acceptable for diagnostics.

## Brightness edge cases

### Latitude at extreme (polar regions)
- **Scenario**: Location above Arctic Circle, no sunrise for months
- **Handling**: `astral` returns events that may not exist. Code catches
  `ValueError` from astral and falls back to manual cap.
- **Risk**: None — graceful degradation.

### Timezone change while running
- **Scenario**: User changes timezone via API while system is running
- **Handling**: BrightnessEngine recalculates on next tick. No restart needed.
- **Risk**: Brief incorrect phase during the update cycle (~1s). Acceptable.

### Clock skew / NTP unavailable
- **Scenario**: Pi has no network time, clock is wrong
- **Handling**: Solar calculation uses system time. If wrong, phases are wrong.
  Manual cap still works.
- **Risk**: Incorrect auto brightness. Documented limitation.
  Mitigation: setup script should configure NTP for fallback RTC.

### Dawn/dusk overlap
- **Scenario**: Very high latitude where dawn and dusk periods overlap
- **Handling**: BrightnessEngine clamps solar_factor to [night_brightness, 1.0].
  If dawn and dusk overlap, the higher factor wins.
- **Risk**: Slightly brighter than expected during overlap. Acceptable.

## Auth edge cases

### Token in git history
- **Risk**: Previous commits contain `system.yaml` with `password: "pillar2026"`.
  This is a WiFi password, not the auth token (which is new).
- **Mitigation**: Document that the WiFi password should be changed on
  deployment. Auth token was never committed. History rewrite not required
  for a WiFi AP password on a local network.

### Browser stores token
- **Handling**: Token stored in localStorage. If device is shared, token
  is accessible.
- **Risk**: Acceptable for a local LED controller. Not a banking app.

### Token rotation
- **Handling**: Change token in system.yaml, restart service.
- **Risk**: Connected browsers will get 401 until they re-enter token.
  UI should handle this gracefully.

## Media edge cases

### Corrupt cached metadata
- **Scenario**: Power loss during metadata write
- **Handling**: scan_library wraps each file load in try/except, skips corrupt.
- **Risk**: Lost media item. User can re-upload.

### Very large GIF (1000+ frames)
- **Scenario**: User uploads 10-second GIF at 30fps = 300 frames
- **Handling**: Each frame cached as .npy file. 300 × (40 × 172 × 3) ≈ 6MB.
  Manageable on Pi.
- **Risk**: Import takes several seconds. Not blocking render loop (async).

### Video without PyAV installed
- **Scenario**: `av` package not installed
- **Handling**: Import raises ImportError, caught in import_file, returns None.
  API returns 400 with message.
- **Risk**: Video import unavailable. Image and GIF still work.

## Upload edge cases

### Slow upload
- **Scenario**: Phone on weak WiFi, upload takes 60 seconds
- **Handling**: Streaming to temp file, render loop not blocked.
- **Risk**: Timeout possible. uvicorn default timeout handles this.

### Concurrent uploads
- **Scenario**: Two browsers upload simultaneously
- **Handling**: Each gets unique temp file and item ID. No conflict.
- **Risk**: Disk space if both are large. Size limit mitigates.

## Deployment risks

### Dev mode on production
- **Risk**: If `PILLAR_DEV=1` is set on Pi, wrong port used.
- **Mitigation**: setup.sh never sets this var. Only manually activated.

### systemd restart loop
- **Risk**: If app crashes immediately on start, systemd restarts every 3s.
- **Mitigation**: `StartLimitBurst=5` and `StartLimitIntervalSec=60` in
  service file to prevent infinite restart.

## Concurrency risks

### Audio thread writes during render read
- **Mitigation**: Lock-protected snapshot copy. Render reads snapshot, audio
  writes under lock. Lock held for microseconds (dict copy).
- **Residual risk**: Audio values may be 1 frame stale. Acceptable for
  visual effects.

### State save during API request
- **Mitigation**: Debounced save means disk I/O happens on periodic task,
  not in request handler. mark_dirty() is instant.
- **Residual risk**: Up to 1 second of state loss on crash. Acceptable.
