# Teensy Link Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Pi→Teensy frame link never wedges, never flashes, and never resets itself; if it ever does fail, one manual reset from the UI captures every byte of evidence needed to make that failure the last one.

**Architecture:** Three layers. (1) Firmware v1.3.0 makes the receive path provably bounded (non-blocking byte reads, decoder reset on every delimiter, oversize discard, per-reason verify counters), stops touching DMA/EEPROM/blackout on reconnect, and exposes a DIAG packet with its full internal state including reset reason. (2) The Pi transport records a rolling trace of every packet it sends/receives and every anomaly, detects stalls, and *only reports them* — no automatic reboot anywhere. (3) An IncidentRecorder writes a forensic bundle (Pi trace + Teensy DIAG + render state + journal tail) whenever a stall is detected or the user presses **Reset Teensy** in the System tab; the reset itself is the existing bootloader reboot, run manually, with a post-reset DIAG appended to the bundle.

**Tech Stack:** Teensy 4.1 / OctoWS2811 / PlatformIO (`framework-arduinoteensy` 1.160+), Python 3.11 FastAPI + pyserial on the Pi, vanilla JS UI.

## Why the panel went dark/flashed (what we know)

| Event | Evidence | Status |
|---|---|---|
| 2026-11-04 ~14:56 and 16:29: Teensy wedged (loop alive, POTS streaming, RX ignored; second time host writes timed out) | journal, raw serial probe | **Root cause unknown.** This plan instruments it. |
| ~4 % of frames rejected by Teensy CRC | `bad_crc` 3882/100k | **Fixed** 49d8707: Pi COBS encoder dropped the 0x00 after a 254-byte run. |
| 19:58 "black": brightness 33 % → 9 % LED output | frame dump | **Fixed** 7495e4f: brightness after gamma. |
| Overnight white flashes while blacked out | user report, journal lost (non-persistent) | Hypothesis: Pi auto-recovery rebooted the Teensy; WS2812 data lines float during reset → strips latch garbage (often white). Journal is now persistent; Task 5 removes auto-reboot. |

**Hardware note (outside this plan):** data lines floating while the Teensy is in reset is a WS2812 property. A 10 kΩ pull-down from each data line to GND keeps the strips idle through a reset. Software can't do this.

## Global Constraints

- **No automatic reset of the Teensy, ever** — not from the Pi, not from a firmware watchdog. Detection only; reset is a user action.
- Every reset (manual) and every detected stall produces a forensic bundle before anything else happens.
- Protocol: `PacketHeader` 24 bytes, CRC32, COBS, STATS stays exactly 28 bytes (golden vectors in `pi/tests/test_protocol.py` must keep passing). New capability = new packet type, never a changed payload.
- Blackout is explicit (0x01/0x00), never toggled; the Pi owns blackout/brightness/test-pattern state — the firmware never changes them on its own.
- Never edit `/opt/ledfanatic/config/*` on the Pi without the user's permission. Firmware flashing is permitted (user asked for this work); confirm with user before the first flash.
- Pi service user is `pillar` (dialout). No sudo in the service path; `teensy_loader_cli` works via udev rule `49-teensy.rules` (already installed).
- Style: 2-space indent, descriptive names, comments say *why*.
- After every Pi change: `cd pi && PYTHONPATH=. pytest tests/ -q --deselect tests/test_matrix_rain_perf.py` (4 pre-existing failures: generative_palette, imported ambient_a_6, migrations metadata, width_policy spectrum) then `bash pi/scripts/deploy.sh ledfanatic.local`.

---

### Task 0: Restore the firmware toolchain

PlatformIO's `penv` is gone on both the Mac (pointed at a deleted Python 3.10) and the Pi. Toolchain packages (`toolchain-gccarmnoneeabi-teensy`, `framework-arduinoteensy`, `tool-teensy`) are still present on both.

**Files:**
- Create: `pi/scripts/flash_teensy.sh`

- [ ] **Step 1: Recreate PlatformIO's venv on the Mac**

```bash
python3 -m venv ~/.platformio/penv && ~/.platformio/penv/bin/pip install -q platformio
~/.platformio/penv/bin/pio --version
```
Expected: `PlatformIO Core, version 6.x`.

- [ ] **Step 2: Build the current firmware unchanged, confirm it reproduces v1.2.0**

```bash
cd teensy/firmware && ~/.platformio/penv/bin/pio run 2>&1 | tail -5
```
Expected: `SUCCESS`, RAM/Flash summary printed. Do **not** flash this.

- [ ] **Step 3: Write the flash script** (build on Mac → scp → bootloader → flash → verify CAPS version via the running service's `/api/system/status`)

```bash
#!/usr/bin/env bash
# Build firmware on this machine, flash the Teensy attached to the Pi, verify version.
# Usage: pi/scripts/flash_teensy.sh ledfanatic.local
set -euo pipefail
HOST="${1:?pi host}"
HERE="$(cd "$(dirname "$0")/../.." && pwd)"
HEX="$HERE/teensy/firmware/.pio/build/teensy41/firmware.hex"
VERSION="$(grep -oE 'FIRMWARE_VERSION\s+"[^"]+"' "$HERE/teensy/firmware/include/config.h" | grep -oE '[0-9.]+')"

~/.platformio/penv/bin/pio run -d "$HERE/teensy/firmware" | tail -3
scp -q "$HEX" "jim@$HOST:/tmp/firmware-$VERSION.hex"
ssh "jim@$HOST" bash -s "$VERSION" <<'EOF'
set -e
V="$1"
sudo systemctl stop ledfanatic
# 134 baud = Teensy's "enter HalfKay bootloader" signal; the sketch isn't involved
stty -F /dev/ttyACM0 134 || true
sleep 2
sudo teensy_loader_cli --mcu=TEENSY41 -w -v "/tmp/firmware-$V.hex"
sleep 3
sudo systemctl start ledfanatic
sleep 8
python3 - "$V" <<'PY'
import json, sys, urllib.request
want = sys.argv[1]
got = json.load(urllib.request.urlopen("http://localhost/api/system/status"))["transport"]["caps"]["firmware_version"]
print("firmware:", got, "OK" if got == want else "MISMATCH"); sys.exit(0 if got == want else 1)
PY
EOF
```

- [ ] **Step 4: Commit**

```bash
git add pi/scripts/flash_teensy.sh && git commit -m "build: flash_teensy.sh — build on Mac, flash via Pi bootloader, verify version"
```

---

### Task 1: Firmware — bounded, self-describing receive path (v1.3.0)

**Files:**
- Modify: `teensy/firmware/include/config.h` (version, DIAG id, decoder cap)
- Modify: `teensy/firmware/include/protocol.h` (COBSDecoder overflow flag; `verify_packet` returns a reason)
- Modify: `teensy/firmware/src/protocol.cpp:113-138`
- Modify: `teensy/firmware/src/main.cpp` (loop RX, handlePacket, handleHello, handleConfig, setup flash, new counters)

**Interfaces:**
- Produces: `PKT_DIAG = 0x32` request (empty payload) → `PKT_DIAG` response, 24 × uint32 LE (96 bytes), layout in Step 4. `VERIFY_OK=0, VERIFY_SHORT=1, VERIFY_MAGIC=2, VERIFY_VERSION=3, VERIFY_LEN=4, VERIFY_CRC=5`.

- [ ] **Step 1: config.h**

```c
#define FIRMWARE_VERSION "1.3.0"
#define PKT_DIAG               0x32
#define DIAG_FIELDS            24
// A valid packet is never bigger than this; anything longer is corrupt input,
// not a bigger frame — discard it instead of letting it hold the decoder.
#define MAX_PACKET_BYTES       (HEADER_SIZE + MAX_PAYLOAD_SIZE + CRC_SIZE)
#define RX_STALL_MS            2000   // bytes arriving but no valid packet → stall flag (report only)
```

- [ ] **Step 2: protocol.h — decoder overflow is a state, not silent truncation**

Replace the `feed` body's two `if (_out_len < sizeof(_output))` guards with:
```c
      if (_out_len < sizeof(_output)) { _output[_out_len++] = byte; } else { _overflow = true; }
```
(and the same for the implicit-zero insert). Add to `reset()`: `_overflow = false;`. Add members/accessors:
```c
  bool overflowed() const { return _overflow; }
  bool started() const { return _started; }
  uint8_t remaining() const { return _remaining; }
private:
  bool _overflow;
```
Change the verify declaration:
```c
enum VerifyResult : uint8_t { VERIFY_OK = 0, VERIFY_SHORT, VERIFY_MAGIC, VERIFY_VERSION, VERIFY_LEN, VERIFY_CRC };
VerifyResult verify_packet(const uint8_t* data, size_t len, PacketHeader* header, const uint8_t** payload);
```

- [ ] **Step 3: protocol.cpp — return the reason**

```c
VerifyResult verify_packet(const uint8_t* data, size_t len, PacketHeader* header, const uint8_t** payload) {
  if (len < HEADER_SIZE + CRC_SIZE) return VERIFY_SHORT;
  memcpy(header, data, sizeof(PacketHeader));
  if (header->magic[0] != MAGIC_0 || header->magic[1] != MAGIC_1 ||
      header->magic[2] != MAGIC_2 || header->magic[3] != MAGIC_3) return VERIFY_MAGIC;
  if (header->version != PROTOCOL_VERSION) return VERIFY_VERSION;
  size_t expected = HEADER_SIZE + header->payload_len + CRC_SIZE;
  if (len < expected) return VERIFY_LEN;
  uint32_t computed = crc32(data, HEADER_SIZE + header->payload_len);
  uint32_t stored;
  memcpy(&stored, data + HEADER_SIZE + header->payload_len, sizeof(uint32_t));
  if (computed != stored) return VERIFY_CRC;
  *payload = data + HEADER_SIZE;
  return VERIFY_OK;
}
```

- [ ] **Step 4: main.cpp — diagnostics state**

Add after the `stats` struct:
```c
// Everything DIAG reports. Counters are monotonic since boot; "ms since" fields
// are computed at send time. Never reset by the firmware itself.
struct Diag {
  uint32_t resetReason;     // SRC_SRSR captured at boot (bit 0 POR, 4 WDOG, 6 SW/JTAG)
  uint32_t rxBytes;
  uint32_t pktOk;
  uint32_t verifyFail[6];   // indexed by VerifyResult (index 0 unused)
  uint32_t oversize;        // decoder overflowed before the delimiter
  uint32_t emptyPackets;    // delimiter with nothing decoded
  uint32_t lastPktType;
  uint32_t lastRxByteMs;
  uint32_t lastGoodPktMs;
  uint32_t loopsThisSecond, loopsPerSec;
  uint32_t loopStartUs, maxLoopUs;
  uint32_t maxSendUs;
  uint32_t lastShowMs;
  uint32_t stallFlags;      // bit0: bytes arriving but no valid packet for RX_STALL_MS
  uint32_t octoBegins;
  uint32_t eepromWrites;
} diag;
#define STALL_RX_NO_VALID_PACKET 0x1

static uint32_t stackFreeEstimate() {
  extern unsigned long _ebss;
  uint8_t marker;
  return (uint32_t)&marker - (uint32_t)&_ebss;
}
```

- [ ] **Step 5: main.cpp — bounded non-blocking RX, decoder reset on every delimiter**

Replace the `// --- Read USB bytes ---` block in `loop()`:
```c
  // --- Read USB bytes (bounded per loop, never blocks: Stream::readBytes has a
  // 1 s timeout we must never be exposed to) ---
  diag.loopStartUs = micros();
  for (int n = 0; n < USB_READ_CHUNK; n++) {
    int c = Serial.read();
    if (c < 0) break;
    diag.rxBytes++;
    diag.lastRxByteMs = millis();
    if (c == 0) {
      // Delimiter: hand over whatever we have and ALWAYS start clean, so no
      // byte pattern can leave the decoder holding stale state.
      if (decoder.overflowed()) diag.oversize++;
      else if (decoder.feed(0)) handlePacket(decoder.data(), decoder.length());
      else diag.emptyPackets++;
      decoder.reset();
    } else {
      decoder.feed((uint8_t)c);
    }
  }
```
Add at the end of `loop()` (before the closing brace):
```c
  // --- Loop health ---
  uint32_t loopUs = micros() - diag.loopStartUs;
  if (loopUs > diag.maxLoopUs) diag.maxLoopUs = loopUs;
  diag.loopsThisSecond++;
  if (now - lastFpsTime == 0) { /* handled in FPS block */ }
  // RX stall: data keeps coming but nothing valid for RX_STALL_MS (report only)
  if (diag.rxBytes && now - diag.lastRxByteMs < 500 && now - diag.lastGoodPktMs > RX_STALL_MS) {
    diag.stallFlags |= STALL_RX_NO_VALID_PACKET;
  }
```
In the existing FPS block (`if (now - lastFpsTime >= STATS_INTERVAL_MS)`), add `diag.loopsPerSec = diag.loopsThisSecond; diag.loopsThisSecond = 0;`.
Where `leds.show()` is called for an applied frame, add `diag.lastShowMs = millis();`.

- [ ] **Step 6: main.cpp — handlePacket records reasons; DIAG handler**

```c
void handlePacket(const uint8_t* data, size_t len) {
  PacketHeader header;
  const uint8_t* payload;
  VerifyResult v = verify_packet(data, len, &header, &payload);
  if (v != VERIFY_OK) {
    diag.verifyFail[v]++;
    if (v == VERIFY_CRC) stats.badCrc++;   // keep STATS semantics
    return;
  }
  diag.pktOk++;
  diag.lastPktType = header.type;
  diag.lastGoodPktMs = millis();
  switch (header.type) {
    ...existing cases...
    case PKT_DIAG:
      sendDiag();
      break;
  }
}

void sendDiag() {
  uint32_t now = millis();
  uint32_t f[DIAG_FIELDS] = {
    now, diag.resetReason, diag.rxBytes, diag.pktOk,
    diag.verifyFail[VERIFY_MAGIC], diag.verifyFail[VERIFY_VERSION], diag.verifyFail[VERIFY_LEN], diag.verifyFail[VERIFY_CRC],
    diag.oversize, diag.emptyPackets, diag.lastPktType,
    now - diag.lastRxByteMs, now - diag.lastGoodPktMs,
    (uint32_t)decoder.length(), (uint32_t)decoder.remaining(),
    diag.loopsPerSec, diag.maxLoopUs, diag.maxSendUs, now - diag.lastShowMs,
    diag.stallFlags, stackFreeEstimate(), stats.framesReceived, stats.framesApplied, diag.octoBegins,
  };
  sendPacket(PKT_DIAG, (const uint8_t*)f, sizeof(f));
}
```
`sendPacket` wraps `Serial.write` with `uint32_t t0 = micros(); ... diag.maxSendUs = max(diag.maxSendUs, micros() - t0);`. `sendPacket`'s `raw[HEADER_SIZE + 128 + CRC_SIZE]` is already ≥ 96+28.

- [ ] **Step 7: main.cpp — the Pi owns state; reconnect must be a no-op for the hardware**

`handleHello`: delete `blackout = false;` and `activeTestPattern = -1;` (keep `sendCaps()`).
`handleConfig`: replace the apply section with
```c
  bool changed = false;
  for (int i = 0; i < TOTAL_OUTPUTS; i++) changed |= (ledsPerOutput[i] != newLeds[i]);
  for (int i = 0; i < TOTAL_OUTPUTS; i++) ledsPerOutput[i] = newLeds[i];
  recalcGeometry();
  configReceived = true;
  if (changed) {
    // Re-initialising OctoWS2811's DMA and rewriting EEPROM on every Pi
    // reconnect was pointless risk; geometry rarely changes.
    reconfigureOcto(); diag.octoBegins++;
    saveConfigToEEPROM(); diag.eepromWrites++;
    pendingFrameReady = false;
    activeTestPattern = -1;
  }
  sendAck();
```
`setup()`: capture reset reason first thing: `diag.resetReason = SRC_SRSR; SRC_SRSR = SRC_SRSR;` (write-1-to-clear so the next boot reports only its own cause). Delete the "Brief startup flash" block (dim green) — the user wants zero flashes.

- [ ] **Step 8: Build, flash, verify**

```bash
bash pi/scripts/flash_teensy.sh ledfanatic.local
```
Expected: `firmware: 1.3.0 OK`, and journal shows `CONFIG ACK received`. Then confirm the panel shows the current overhead effect (ask user) and that a DIAG probe works (Task 2 adds the Pi parser; until then use `rtk recall` of a raw probe is unnecessary — proceed to Task 2 immediately).

- [ ] **Step 9: Commit**

```bash
git add teensy/firmware && git commit -m "firmware 1.3.0: bounded RX, DIAG packet, reconnect no longer touches DMA/EEPROM/blackout"
```

---

### Task 2: Pi protocol + transport — DIAG, trace, stall *detection only*

**Files:**
- Modify: `pi/app/models/protocol.py` (PacketType.DIAG, `DIAG_FIELDS`, `parse_diag_payload`, `decode_reset_reason`)
- Create: `pi/app/transport/trace.py`
- Modify: `pi/app/transport/usb.py` (remove `_recover`/auto-reboot; add `request_diag`, trace hooks, `stalled`, `on_stall` callback, `async reboot()`)
- Test: `pi/tests/test_protocol.py`, `pi/tests/test_transport_trace.py`, `pi/tests/test_transport_recovery.py` (rewrite: detection, no reboot)

**Interfaces:**
- `parse_diag_payload(bytes) -> dict | None` keys: `uptime_ms, reset_reason, rx_bytes, pkt_ok, bad_magic, bad_version, bad_len, bad_crc, oversize, empty_packets, last_pkt_type, ms_since_rx_byte, ms_since_good_pkt, decoder_out_len, decoder_remaining, loops_per_sec, max_loop_us, max_send_us, ms_since_show, stall_flags, stack_free, frames_received, frames_applied, octo_begins` plus `reset_reason_text` (e.g. `"power_on"`, `"software"`, `"watchdog"`, `"unknown(0x..)"`).
- `TransportTrace(maxlen=4000)`: `.tx(ptype, nbytes, write_ms)`, `.rx(ptype, nbytes)`, `.event(kind, **detail)`, `.snapshot() -> list[dict]` (each `{t, kind, ...}` with wall-clock `t`), `.summary() -> dict` (counts per type, max write_ms, last 10 events).
- `TeensyTransport`: `.trace: TransportTrace`, `.last_teensy_diag: dict|None`, `.stalled: dict|None` (`{reason, since, last_stats, last_diag}`), `.on_stall: Optional[Callable[[dict], Awaitable]]`, `async request_diag() -> dict|None`, `async reboot(port=None) -> bool` (used **only** by the manual reset in Task 3). Health check keeps running every 5 s: PING, and every 6th check (30 s) a DIAG, stored to `last_teensy_diag` and `trace.event('diag', **diag)`; any nonzero `stall_flags`, `oversize`, or growth in `bad_*` is logged at WARNING with deltas.
- Stall detection unchanged (3 missed PINGs / 2 failed handshakes with port present / CONFIG timeout) but the consequence is: set `stalled`, `trace.event('stall', reason=...)`, `await on_stall(stalled)` once per incident. `stalled` clears on the next successful handshake+CONFIG ACK.
- `send_frame` records `trace.tx(FRAME, len, write_ms)` and logs at WARNING when a single write takes > 50 ms (USB backpressure is the earliest visible symptom of the Teensy falling behind).

Tests (write first):
- `parse_diag_payload` round-trips a `struct.pack('<24I', ...)` and decodes reset reason bits (0x1→power_on, 0x40→software, 0x10→watchdog).
- Trace: maxlen honoured, `summary()` counts, `write_ms` max.
- Recovery tests: 3 missed PINGs → `stalled` set, `on_stall` awaited exactly once, **`reboot_teensy` never called**; healthy PING after reconnect clears `stalled`; second stall after clear fires `on_stall` again.

Commit: `transport: DIAG, packet trace, stall detection reports instead of rebooting`.

---

### Task 3: IncidentRecorder + manual reset + API

**Files:**
- Create: `pi/app/core/incidents.py`, `pi/app/core/teensy_maintenance.py`
- Create: `pi/app/api/routes/teensy.py`; Modify: `pi/app/api/server.py`, `pi/app/api/deps.py`, `pi/app/main.py` (wire `transport.on_stall = incidents.capture_stall`), `pi/scripts/setup.sh` (`usermod -aG systemd-journal pillar`; create `/opt/ledfanatic/logs/incidents`)
- Test: `pi/tests/test_incidents.py`, `pi/tests/test_teensy_routes.py`

**Interfaces:**
- `IncidentRecorder(transport, render_state, state_manager, brightness_engine, logs_dir: Path)`
  - `async capture(trigger: str, reason: str) -> Path` writes `<logs_dir>/incidents/<YYYYmmdd-HHMMSS>-<trigger>.json` containing: `{schema_version:1, trigger, reason, at, transport: get_status(), stalled, last_stats, last_diag, live_diag (request_diag() with 0.5 s timeout, may be null), trace: trace.snapshot(), render: {current_scene, params, target_fps, actual_fps, blackout, brightness_effective, frames_dropped}, system: {uptime, loadavg, mem_available_kb, usb: dmesg tail is root-only → omitted, journal: last 300 lines of journalctl -u ledfanatic --no-pager (subprocess, 3 s timeout; empty string if unavailable)}}`.
  - `async capture_stall(stalled: dict)` → `capture('stall', stalled['reason'])` (the `on_stall` hook).
  - `list() -> list[dict]` (`name, size, at, trigger`), `path_for(name) -> Path|None` (rejects anything not matching `^[0-9]{8}-[0-9]{6}-[a-z-]+\.json$`).
- `TeensyMaintenance(transport, incidents)`
  - `async reset(reason: str = 'manual') -> dict`: `incident = await incidents.capture('manual-reset', reason)`; `ok = await transport.reboot()`; wait up to 10 s for `transport.connected and transport._last_config_ack`; `post = await transport.request_diag()`; append `{"post_reset": {ok, reconnected, diag: post}}` to the incident JSON; return `{incident: name, rebooted: ok, reconnected, reset_reason: post['reset_reason_text']}`.
- Routes (`/api/teensy`): `GET /diag` (live DIAG or 503), `GET /incidents`, `GET /incidents/{name}` (FileResponse), `POST /reset` (auth) → `TeensyMaintenance.reset()` then `broadcast_state()`.
- `build_state_payload` gains `transport: {connected, firmware_version, stalled: {reason, since} | null}`.

Tests: capture writes a well-formed bundle with a fake transport (trace snapshot present, live_diag null when `request_diag` returns None); `path_for` rejects `../x`; `POST /api/teensy/reset` calls `TeensyMaintenance.reset` once and returns its dict; `GET /api/teensy/incidents` lists bundles.

Commit: `feat: forensic incident bundles + manual Teensy reset API`.

---

### Task 4: UI — stall banner and Reset Teensy

**Files:**
- Modify: `pi/app/ui/static/index.html` (System → Status: "Teensy" card; global banner element), `js/app.js`, `css/app.css`

- Global banner (top of page, all tabs) when `state.transport.stalled`: "Teensy not responding since HH:MM — evidence saved. [Reset Teensy]". Hidden otherwise.
- System tab card: connection + firmware version + last DIAG summary (bad counters, stall flags, reset reason), **Reset Teensy** button (confirm dialog: "Captures a diagnostic bundle, then reboots the Teensy. The panel will go dark ~5 s and may flash."), and an Incidents list with download links (`/api/teensy/incidents/<name>`).
- Button disabled while a reset is in flight; result toast shows `reset_reason` and incident name.
- Run `/fda:quick` on the new elements and fix severity ≥ 3.

Commit: `ui: Teensy stall banner, manual reset, incident downloads`.

---

### Task 5: Renderer guard + reconnect sequence

**Files:**
- Modify: `pi/app/core/renderer.py` (frame-size guard), `pi/app/main.py:_on_teensy_connect`

- Renderer: compute `self._expected_frame_bytes = sum(output_config_list(layout)) * 3` when the layout is (re)built; in `_render_frame`, if `len(pixel_bytes) != self._expected_frame_bytes` → `logger.error` (rate-limited to once per 10 s), `trace.event('bad_frame_len', got=..., want=...)`, skip send. Test with a layout whose packer output is deliberately truncated (monkeypatch `pack_frame`).
- `_on_teensy_connect` order: CONFIG → `pin_teensy_brightness()` → `set_blackout(render_state.blackout)` → send `TEST_PATTERN_NONE` (0xFF) so a reconnect always leaves the Teensy in the Pi's known state (firmware no longer clears these itself). On `stalled` clearing, log `INFO Teensy link healthy again (incident <name>)`.

Commit: `renderer: refuse wrong-size frames; reconnect restores full Pi-owned state`.

---

### Task 6: Soak verification (acceptance)

No code. Run on the Pi, 60 fps, Rainbow Ink (the heaviest effect), for ≥ 2 hours, then 90 fps for ≥ 2 hours:

```bash
ssh jim@ledfanatic.local 'python3 -c "
import json,urllib.request; d=json.load(urllib.request.urlopen(\"http://localhost/api/teensy/diag\")); print(json.dumps(d, indent=1))"'
```
Pass criteria for each soak: `bad_crc == bad_len == bad_magic == oversize == 0`, `stall_flags == 0`, `pkt_ok` ≈ frames sent + pings, `max_loop_us < 20000`, `max_send_us < 5000`, no `stall` events in `GET /api/teensy/incidents`, no "write took" warnings in the journal, `frames_received == frames_applied ± 1`. If 90 fps passes, restore `target_fps` to 90 via `/api/display/fps`; otherwise stay at 60 and record why in the incident notes.

Update memory `teensy_rx_wedge_recovery.md` with the outcome and the DIAG reading procedure.

---

## Self-review

- Coverage: no auto-reset (Task 2 removes it, firmware never had one, Global Constraints forbid adding one) ✔; manual reset button with full capture (Tasks 3–4) ✔; "capture every bit of data" — Pi trace, Teensy DIAG pre/post, render state, journal (Task 3) ✔; flash elimination — startup flash removed, reconnect no longer resets DMA or blackout (Task 1), floating-line flash documented as hardware ✔; root-cause path — DIAG reset_reason + stall flags + verify reasons will classify the next wedge on first occurrence ✔.
- Known gap, stated honestly: this plan cannot *prove* 100.0000 % until a soak passes and the next real wedge (if any) is classified by the bundle. That is the point of the instrumentation.
