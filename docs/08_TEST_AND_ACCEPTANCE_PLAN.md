# 08. Test and Acceptance Plan

## 8.1 Bring-up test sequence

### Test 1 — power-off continuity and wiring review
Verify:
- strip labeling
- pair jumpers at the top
- PSU segmentation
- common ground plan
- no shared +5V rails between different supplies unless explicitly designed

### Test 2 — Teensy + Octo only
Run local Teensy diagnostics:
- CH0..CH4 identify
- RGB order
- white sweep
- blackout

Pass criteria:
- every expected strip lights on the correct channel
- no random flicker
- no swapped colors

### Test 3 — serpentine verification
Run a bottom-to-top sweep on each logical strip.

Pass criteria:
- S0 rises bottom→top
- S1 also appears bottom→top in logical view, even though its wiring is physically reversed
- same for all pairs

### Test 4 — seam verification
Display markers on S0 and S9.

Pass criteria:
- seam is exactly where expected in the mechanical build

## 8.2 Protocol tests

### Test 5 — handshake
- Pi connects
- Teensy reports firmware + capabilities via CAPS response
- UI shows connected status

### Test 6 — bad packet rejection
Inject malformed payloads.

Pass criteria:
- no crash
- bad packet counters increment
- last valid frame persists

### Test 7 — reconnect
Unplug/replug USB.

Pass criteria:
- Pi reconnects automatically
- playback resumes without operator SSH

## 8.2.1 Contract tests (automated)

The following automated test suites lock the current contracts:

- **API contract tests** (`pi/tests/test_api_contract.py`): verify all routes exist, return stable response shapes, and enforce auth correctly. Aspirational routes (e.g. `/api/effects/*`) are explicitly tested to NOT exist.
- **Protocol contract tests** (`pi/tests/test_protocol.py::TestProtocolContracts`): lock 24-byte header, 3-byte FRAME meta, packet type values, HELLO→CAPS / PING→STATS semantics, BLACKOUT explicit on/off, STATS 28-byte payload.
- **Persistence migration tests** (`pi/tests/test_migrations.py`): verify unversioned state.json and metadata.json load and upgrade correctly.
- **Hardware constant sync** (`pi/tests/test_protocol.py::TestHardwareConstants`): verify Python constants from hardware.yaml match Teensy config.h.

## 8.3 Performance tests

### Test 8 — static 60 FPS
Send constant frames at 60 FPS for 30 minutes.

Pass criteria:
- zero visible judder
- no protocol errors
- no unexplained flicker

### Test 9 — animated 60 FPS
Run heavy built-in effects for 30 minutes.

Pass criteria:
- frame time stable
- UI responsive
- no thermal crash

### Test 10 — media playback
Play cached clips at 30 and 60 FPS.

Pass criteria:
- no tearing
- no obvious stalls
- playback controls work

### Test 11 — stress mode
Run 75 or 90 FPS mode if implemented.

Pass criteria:
- acceptable stability
- if not stable, system gracefully falls back to 60

## 8.4 Audio tests

### Test 12 — live input selection
Switch between supported audio devices.

Pass criteria:
- meter updates
- audio-reactive scenes respond
- no render loop stalls

### Test 13 — beat/onset
Use strong transient music.

Pass criteria:
- beat-triggered scenes react cleanly
- sensitivity controls are meaningful

## 8.5 UX tests

### Test 14 — iPhone-only operation
Use only an iPhone after plugging in the system.

Pass criteria:
- join hotspot
- open UI
- select scenes
- upload media
- run diagnostics
- reboot app from UI if needed

### Test 15 — no-monitor recovery
Reboot entire system with no keyboard, mouse, or monitor.

Pass criteria:
- hotspot returns
- last scene or idle scene loads
- system is operable from phone

## 8.6 Safety tests

### Test 16 — brightness cap
Attempt to set max white and high brightness.

Pass criteria:
- software cap applies
- no PSU instability
- no major voltage drop artifacts

### Test 17 — brownout-ish behavior
Lower brightness margins / long-run test.

Pass criteria:
- if supply issues exist, diagnostics reveal them clearly
- system does not corrupt state

## 8.7 Final acceptance criteria

The system is accepted when all of the following are true:

1. 10-strip cylinder is mapped correctly.
2. Headless Pi boot produces a working local control network.
3. iPhone UI can control effects and media reliably.
4. Teensy output is stable at **60 FPS**.
5. Imported media playback works.
6. Audio-reactive modes work.
7. Reconnect/reboot behavior is appliance-grade.
8. Diagnostics make physical troubleshooting possible without recompiling code.

## 8.8 Nice-to-have acceptance targets

- 75/90 FPS optional mode
- PWA install prompt
- mDNS access works on iPhone
- captive portal redirect
- scene scheduling
