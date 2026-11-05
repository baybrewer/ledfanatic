# Teensy Link Reliability Implementation Plan (v2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Pi→Teensy frame link never wedges, never flashes, and never resets itself; if it ever does fail, one manual reset from the UI captures every byte of evidence needed to make that failure the last one.

**Architecture:** (1) Firmware v1.3.0: every wait in the loop is bounded (non-blocking USB RX/TX, DMA-aware `busy()`, no re-init/EEPROM/black-show on reconnect), every rejected packet is counted by reason, a halting (non-rebooting) fault handler preserves the crash record, and the Teensy *pushes* a DIAG snapshot once a second so evidence does not depend on the RX path that failed. (2) Pi transport: one RX dispatcher (the pots drain loop) routes replies to futures so no request ever holds the frame lock while waiting; a bounded trace records every packet, write outcome and rejected byte-chunk; stalls, write timeouts, link loss, frame-progress stalls and unexpected Teensy reboots are *detected and reported* — nothing on the Pi reboots the Teensy. (3) `IncidentRecorder` writes a collision-proof forensic bundle on every detection and on the manual **Reset Teensy** action (server-side single-flight), appending post-reset evidence.

**Tech Stack:** Teensy 4.1 / OctoWS2811 (vendored, patched) / PlatformIO 6 (`framework-arduinoteensy` 1.160 on the Mac venv `~/.platformio/penv`, restored 2026-11-05), Python 3.11 FastAPI + pyserial, vanilla JS. Host-side C++ harness via `g++ -fsanitize=address,undefined`.

## Why the panel went dark/flashed (what we know)

| Event | Evidence | Status |
|---|---|---|
| 2026-11-04 ~14:56 and 16:29: Teensy wedged (loop alive, POTS streaming, RX ignored; second time host writes timed out) | journal, raw serial probe | **Root cause unknown**; two plausible mechanisms found below. This plan instruments every path. |
| ~4 % of frames rejected by Teensy | `bad_crc` 3882/100k | **Fixed** 49d8707: Pi COBS encoder dropped the 0x00 after a 254-byte run. |
| 19:58 "black": brightness 33 % → 9 % LED output | frame dump | **Fixed** 7495e4f: brightness after gamma. |
| Overnight white flashes while blacked out | user report; journal was non-persistent (now persistent) | Hypothesis: Pi auto-recovery (since removed by this plan) rebooted the Teensy; WS2812 data lines float during reset → strips latch garbage. |

**Found in the installed library/core sources while planning (all addressed in Task 1):**
- `OctoWS2811::show()` spins `while (!dma3.complete())`; the firmware's blackout branch calls `clearAll(); leds.show();` every loop iteration unguarded by `busy()`, so during blackout each loop lasts one LED frame time (~10 ms @333 LEDs). With USB RX capped at 4096 B/loop that is ~400 KB/s capacity; 90 fps needs ~453 KB/s → Teensy falls behind → USB RX buffers fill → host writes time out (wedge #2's symptom).
- `OctoWS2811::busy()` has `if (!dma3.complete()) ;` — an empty statement — so it reports ready on elapsed time alone; if DMA ever fails to complete, the next `show()` spins forever (loop dead, no evidence).
- `handleConfig` → `reconfigureOcto()` re-inits TMR4/DMA, shows black, then writes EEPROM (flash programming with interrupts disabled) on **every** Pi reconnect, regardless of in-flight DMA.
- Teensyduino's `unused_interrupt_vector()` (`startup.c:624+`) stores a crash record then **reboots after 8 s** — an automatic reset the owner forbids.
- `usb_serial_write` blocks up to 120 ms when the host stops reading; `sendPacket` ignores short writes and writes the delimiter separately (truncated packets possible).
- `verify_packet` computes `HEADER + payload_len + CRC` in 32-bit; `payload_len=0xffffffe5` wraps to 1 and passes, then CRC runs over ~4 GB.

**Hardware note (outside this plan):** data lines float while the Teensy is in reset/bootloader; a 10 kΩ pull-down from each WS2812 data line to GND keeps strips idle through any reset. Software cannot do this.

## Global Constraints

- **No automatic reset of the Teensy, ever** — not from the Pi, not from a firmware watchdog, not from the core fault handler. Detection and evidence only; reset is a user action.
- Every detected stall, write timeout, link loss, frame-progress stall, unexpected reboot and manual reset produces an incident bundle. Capture failures are logged and retried; they never kill a background task.
- Protocol: `PacketHeader` 24 B, CRC32, COBS, STATS exactly 28 B; golden vectors in `pi/tests/test_protocol.py` keep passing. New capability = new packet type or zero-filled CAPS bytes; never a changed existing payload.
- Blackout explicit (0x01/0x00). The Pi owns blackout/brightness/test-pattern state; the firmware never changes them on its own.
- Never edit `/opt/ledfanatic/config/*` without user permission. Ask the user before the first firmware flash.
- Service user `pillar` (dialout, systemd-journal after Task 3). No sudo in the service path.
- Style: 2-space indent, descriptive names, comments say *why*. Firmware files use the existing 2-space style.
- After every Pi change: `cd pi && PYTHONPATH=. pytest tests/ -q --deselect tests/test_matrix_rain_perf.py` (4 pre-existing failures: generative_palette, imported ambient_a_6, migrations metadata, width_policy spectrum), then `bash pi/scripts/deploy.sh ledfanatic.local`.

---

### Task 0: Toolchain, flash script, host-side C++ protocol harness

PlatformIO venv restored on the Mac (`~/.platformio/penv`, PlatformIO 6.2.0; current firmware builds: 398 KB RAM1 free).

**Files:** Create `pi/scripts/flash_teensy.sh`, `teensy/firmware/test_native/test_protocol.cpp`, `teensy/firmware/test_native/run.sh`; Test `pi/tests/test_firmware_protocol_native.py`.

- [ ] **flash_teensy.sh** — build on Mac → scp hex → stop service → `stty -F /dev/ttyACM0 134` (HalfKay) → `sudo teensy_loader_cli --mcu=TEENSY41 -w -v` → start service → poll `/api/system/status` until `transport.caps.firmware_version` equals `FIRMWARE_VERSION` from `config.h` (exit 1 on mismatch/timeout 30 s). Usage `pi/scripts/flash_teensy.sh ledfanatic.local`.
- [ ] **test_native** — compiles the *real* `teensy/firmware/src/protocol.cpp` + `include/protocol.h` with `g++ -std=gnu++17 -fsanitize=address,undefined -I include -DNATIVE_TEST` (config.h has no Arduino deps; `protocol.h` must compile without Arduino — guard any Arduino include behind `#ifndef NATIVE_TEST`). Cases: (a) golden vectors from `pi/tests/test_protocol.py` (copy the hex literals), (b) consecutive/empty delimiters, (c) 254-run-then-zero, (d) over-cap input (MAX_PACKET_BYTES+1 non-zero bytes) then delimiter → `overflowed()`, then a valid frame decodes, (e) exact-max frame, (f) `payload_len` ∈ {0xffffffe5, 0xffffffff, MAX_PAYLOAD_SIZE+1} → `VERIFY_LEN`, no sanitizer report, (g) interrupted block (code byte says 10, delimiter after 3) → `VERIFY_SHORT`. `run.sh` prints `OK <n cases>` and exits non-zero otherwise.
- [ ] **pytest wrapper** `test_firmware_protocol_native.py`: `pytest.skip` if `g++` missing; runs `run.sh`, asserts exit 0.
- [ ] Commit `build: flash script + native sanitizer harness for firmware protocol`.

---

### Task 1: Firmware v1.3.0 — bounded loop, evidence, no self-reset

**Files:** Modify `teensy/firmware/include/config.h`, `include/protocol.h`, `src/protocol.cpp`, `src/main.cpp`; Create `teensy/firmware/lib/OctoWS2811/` (vendored copy of the installed library, one patch), `teensy/firmware/include/fault_handler.h`, `src/fault_handler.cpp`, `include/diag.h`.

**Interfaces produced (consumed by Task 2):**
- `PKT_DIAG = 0x32`: request (empty) → response; **also pushed unsolicited every 1000 ms**. Payload = `DIAG_FIELDS (32) × uint32 LE` = 128 B, order exactly:
  `0 uptime_ms, 1 reset_reason (SRC_SRSR raw), 2 boot_id, 3 rx_bytes, 4 pkt_ok, 5 bad_short, 6 bad_magic, 7 bad_version, 8 bad_len, 9 bad_crc, 10 oversize, 11 empty_packets, 12 last_pkt_type (non-DIAG), 13 ms_since_rx_byte, 14 ms_since_good_pkt (non-DIAG), 15 decoder_out_len (snapshot before this packet), 16 decoder_remaining, 17 loops_per_sec, 18 max_loop_us, 19 max_tx_us, 20 tx_dropped, 21 ms_since_show, 22 stall_flags, 23 stack_free, 24 frames_received, 25 frames_applied, 26 octo_begins, 27 eeprom_writes, 28 rx_backlog (Serial.available()), 29 usb_sts (USB1_USBSTS), 30 usb_irq (bit0 NVIC_IS_ENABLED(IRQ_USB1), bit1 NVIC_IS_PENDING), 31 last_op (breadcrumb enum)`.
  `stall_flags`: bit0 RX bytes arriving but no valid packet ≥2 s; bit1 DMA not complete ≥1 s after show (LED path stalled); bit2 TX dropped for backpressure in the last second.
  `last_op`: 1 rx, 2 handle_frame, 3 handle_config, 4 show, 5 send, 6 eeprom, 7 octo_begin, 8 pots.
- `PKT_CRASH = 0x33`: request → response = ASCII crash report text from the previous boot (empty payload if none). Firmware reads it once at boot via `CrashReport` (core API over the retained struct at 0x2027FF80) into a 1 KB buffer.
- `CAPS` bytes 24–27 = `uptime_ms`, 28–31 = `reset_reason`, 32–35 = `boot_id` (previously zero-filled; parser reads fixed offsets → backward compatible). `boot_id` = `ARM_DWT_CYCCNT ^ micros()` sampled in `setup()` (random enough to detect a reboot between two connects).
- Reset-reason semantics (Pi decodes all masks, keeps combinations): bit0 power_on, bit1 software_or_lockup (`SCB_AIRCR` SYSRESETREQ — what the bootloader-reboot path and the core's fault reboot use), bit2 csu, bit3 user_pin, bit4 wdog1/2, bit5 jtag, bit6 jtag_sw, bit7 wdog3, bit8 tempsense.

- [ ] **Step 1: Vendor and patch OctoWS2811.** `cp -r ~/.platformio/packages/framework-arduinoteensy/libraries/OctoWS2811 teensy/firmware/lib/`; in `lib/OctoWS2811/OctoWS2811_imxrt.cpp::busy()` replace `if (!dma3.complete()) ;` with `if (!dma3.complete()) return 1;`. Remove `OctoWS2811` from `lib_deps` in `platformio.ini` so the local copy wins. Add a one-line `lib/OctoWS2811/LEDFANATIC_PATCH.md` naming the change. Build; confirm success.

- [ ] **Step 2: protocol.h/.cpp — decoder overflow state, verify reasons, overflow-proof length check.**
  - `COBSDecoder`: add `bool _overflow` (set when `_out_len == sizeof(_output)` on any store; cleared in `reset()`), accessors `overflowed()`, `remaining()`. Buffer size becomes `MAX_PACKET_BYTES + 1` (exact cap; one spare so "== cap" is detectable).
  - `enum VerifyResult : uint8_t { VERIFY_OK, VERIFY_SHORT, VERIFY_MAGIC, VERIFY_VERSION, VERIFY_LEN, VERIFY_CRC }`.
  ```c
  VerifyResult verify_packet(const uint8_t* data, size_t len, PacketHeader* header, const uint8_t** payload) {
    if (len < HEADER_SIZE + CRC_SIZE) return VERIFY_SHORT;
    memcpy(header, data, sizeof(PacketHeader));
    if (header->magic[0] != MAGIC_0 || header->magic[1] != MAGIC_1 ||
        header->magic[2] != MAGIC_2 || header->magic[3] != MAGIC_3) return VERIFY_MAGIC;
    if (header->version != PROTOCOL_VERSION) return VERIFY_VERSION;
    // Bound BEFORE any arithmetic: a corrupt 32-bit length must not wrap.
    if (header->payload_len > MAX_PAYLOAD_SIZE) return VERIFY_LEN;
    if (len != HEADER_SIZE + (size_t)header->payload_len + CRC_SIZE) return VERIFY_LEN;
    uint32_t computed = crc32(data, HEADER_SIZE + header->payload_len);
    uint32_t stored; memcpy(&stored, data + HEADER_SIZE + header->payload_len, 4);
    if (computed != stored) return VERIFY_CRC;
    *payload = data + HEADER_SIZE;
    return VERIFY_OK;
  }
  ```
  (Exact-length equality is correct: the Pi encoder emits exactly `header+payload+crc`; the Python golden vectors confirm.)

- [ ] **Step 3: fault_handler.{h,cpp} — halt, don't reboot.** In `setup()` first line: `install_fault_handlers();` which, for vector indices 3–6 (HardFault, MemManage, BusFault, UsageFault), 7–10, 12, 13 and every `i ≥ 16` whose `_VectorsRam[i] == unused_interrupt_vector`, sets `_VectorsRam[i] = fault_halt;` (`extern "C" void unused_interrupt_vector(void); extern void (*_VectorsRam[])(void);`). `fault_halt` copies the core's record-storing body (`startup.c:624–690`: ipsr, stack regs, CFSR/HFSR/MMFAR/BFAR, pc/lr/xpsr, time, len, crc into `arm_fault_info_struct*` at `0x2027FF80`) and then `for (;;) { asm volatile("wfi"); }` — no `SCB_AIRCR`. Interrupts stay disabled; USB stops; the Pi sees link loss → incident; the record survives the manual bootloader reset (it is in DMAMEM/OCRAM, not cleared by soft reset) and is served via `PKT_CRASH` on the next boot. Verification: temporary `#ifdef FAULT_INJECT` in handleTestPattern for pattern id 0xFE → `*(volatile uint32_t*)0xFFFFFFF0 = 1;` — build once with `-DFAULT_INJECT`, trigger, confirm the Teensy stays down > 30 s, reset manually, confirm `PKT_CRASH` text; rebuild without the flag.

- [ ] **Step 4: diag.h + main.cpp globals.** `struct Diag` with the fields of the DIAG layout plus internals: `verifyFail[6]`, `loopsThisSecond`, `loopStartUs`, `lastShowStartMs`, `showPending` (true between `show()` and `dma complete`), `txDroppedThisSecond`, `lastPushMs`. `stackFreeEstimate()` = `(uint32_t)&local - (uint32_t)&_ebss`. `volatile uint8_t lastOp` breadcrumb set at the start of each phase.

- [ ] **Step 5: RX — bounded, non-blocking, decoder reset on every delimiter.**
  ```c
  lastOp = OP_RX;
  for (int n = 0; n < USB_READ_CHUNK; n++) {       // USB_READ_CHUNK raised to 8192
    int c = Serial.read();                          // never Stream::readBytes (1 s timeout semantics)
    if (c < 0) break;
    diag.rxBytes++; diag.lastRxByteMs = millis();
    if (c == 0) {
      // Snapshot decoder state BEFORE dispatch so DIAG can report the state that preceded a failure
      diag.decoderOutLenSnap = decoder.length(); diag.decoderRemainingSnap = decoder.remaining();
      if (decoder.overflowed()) diag.oversize++;
      else if (decoder.length() == 0) diag.emptyPackets++;
      else handlePacket(decoder.data(), decoder.length());
      decoder.reset();                                // ALWAYS — no byte pattern can leave stale state
    } else decoder.feed((uint8_t)c);
  }
  ```
  `handlePacket`: `v = verify_packet(...)`; on failure `diag.verifyFail[v]++` (`badCrc` also increments STATS for compatibility) and return; on success `diag.pktOk++`; **only if type != PKT_DIAG && type != PKT_CRASH**: `lastPktType`, `lastGoodPktMs` updated (probes must not overwrite the evidence they read). New cases: `PKT_DIAG → sendDiag()`, `PKT_CRASH → sendCrash()`. `PKT_TEST_PATTERN` with `0xFF` **no longer clears `pendingFrameReady`** (leaving test mode must not drop a queued frame).

- [ ] **Step 6: LED path — never block, never show black on reconnect.**
  ```c
  if (!leds.busy()) {                      // patched busy(): true while DMA incomplete
    if (showPending) { showPending = false; }
    if (blackout) {
      if (!blackShown) { clearAll(); leds.show(); blackShown = true; showPending = true; lastShowStartMs = millis(); }
    } else if (activeTestPattern >= 0) { runTestPattern(); leds.show(); showPending = true; lastShowStartMs = millis(); fpsCounter++; }
    else if (pendingFrameReady) { applyPendingFrame(); leds.show(); showPending = true; lastShowStartMs = millis(); /* existing bookkeeping */ }
  } else if (showPending && millis() - lastShowStartMs > 1000) {
    diag.stallFlags |= STALL_DMA;          // report only; loop keeps servicing USB
  }
  ```
  `blackShown = false` whenever `blackout` is set true (PKT_BLACKOUT case). `handleHello`: remove `blackout = false;` and `activeTestPattern = -1;`. Delete the startup dim-green flash in `setup()`.

- [ ] **Step 7: CONFIG — stage geometry; re-init only when changed, only when idle; no black frame; EEPROM only when idle.**
  ```c
  // in handleConfig after validation:
  bool changed = memcmp(ledsPerOutput, newLeds, sizeof(newLeds)) != 0;
  if (changed) { memcpy(stagedLeds, newLeds, sizeof(newLeds)); geometryStaged = true; }
  configReceived = true; sendAck();
  // in loop(), before the LED path:
  if (geometryStaged && !leds.busy() && !showPending) {
    lastOp = OP_OCTO_BEGIN; memcpy(ledsPerOutput, stagedLeds, sizeof(stagedLeds)); recalcGeometry();
    leds.begin(maxLedsPerStrip, displayMemory, drawingMemory, octoConfig); diag.octoBegins++;   // no clearAll/show: next frame fills everything
    lastOp = OP_EEPROM; saveConfigToEEPROM(); diag.eepromWrites++;   // LEDs idle → interrupts-off flash write is harmless
    pendingFrameReady = false; geometryStaged = false;
  }
  ```
  `reconfigureOcto()` keeps its startup use only (no `clearAll(); show();` there either — the first frame or blackout show handles it).

- [ ] **Step 8: TX — non-blocking, atomic framed writes, backpressure counted.**
  ```c
  void sendPacket(uint8_t type, const uint8_t* payload, size_t len) {
    uint8_t raw[HEADER_SIZE + 1024 + CRC_SIZE];
    size_t rawLen = build_packet(type, payload, len, raw, sizeof(raw)); if (!rawLen) return;
    uint8_t encoded[sizeof(raw) + sizeof(raw) / 254 + 2];
    size_t encLen = cobs_encode(raw, rawLen, encoded, sizeof(encoded) - 1); if (!encLen) return;
    encoded[encLen++] = 0x00;                                   // delimiter inside the same write
    if (Serial.availableForWrite() < (int)encLen) { diag.txDropped++; diag.txDroppedThisSecond++; return; }  // host not reading: never block the loop
    lastOp = OP_SEND; uint32_t t0 = micros();
    size_t written = Serial.write(encoded, encLen);
    if (written != encLen) diag.txDropped++;                    // availableForWrite guaranteed room; count anyway
    uint32_t dt = micros() - t0; if (dt > diag.maxTxUs) diag.maxTxUs = dt;
  }
  ```
  Per-second housekeeping (existing FPS block): `loopsPerSec = loopsThisSecond; loopsThisSecond = 0; if (txDroppedThisSecond) stallFlags |= STALL_TX_BACKPRESSURE; else stallFlags &= ~STALL_TX_BACKPRESSURE; txDroppedThisSecond = 0;` and **push `sendDiag()`** (unsolicited, 128 B/s). RX-stall flag: `if (rxBytes && now - lastRxByteMs < 500 && now - lastGoodPktMs > 2000) stallFlags |= STALL_RX_NO_VALID_PACKET;`.

- [ ] **Step 9: CAPS/boot.** `setup()`: `install_fault_handlers(); diag.resetReason = SRC_SRSR; SRC_SRSR = diag.resetReason /* w1c */; diag.bootId = ARM_DWT_CYCCNT ^ micros(); if (CrashReport) { CrashReport.printTo(crashBuf); }` (`crashBuf` is a 1 KB `Print` subclass). `sendCaps()` fills bytes 24–35 as specified.

- [ ] **Step 10: Build, native harness, flash, verify.** `bash teensy/firmware/test_native/run.sh` → `OK`; `bash pi/scripts/flash_teensy.sh ledfanatic.local` → `firmware: 1.3.0 OK`; journal shows `CONFIG ACK received`; ask the user to confirm the panel shows the current effect with no flash. Commit `firmware 1.3.0: bounded RX/TX/LED paths, DIAG push, halting fault handler, no re-init on reconnect`.

---

### Task 2: Pi protocol + transport — dispatcher, trace, detection only

**Files:** Modify `pi/app/models/protocol.py`, `pi/app/transport/usb.py`; Create `pi/app/transport/trace.py`, `pi/app/transport/dispatcher.py`; Tests `pi/tests/test_protocol.py`, `test_transport_trace.py`, `test_transport_dispatch.py`, `test_transport_detection.py` (replaces `test_transport_recovery.py`).

**Interfaces:**
- `PacketType.DIAG = 0x32`, `CRASH = 0x33`; `DIAG_FIELDS = 32`; `parse_diag_payload(bytes) -> dict|None` (names = Task 1 list, snake_case) + `reset_reason_text: str` (joined names of all set `SRC_SRSR` bits, e.g. `"power_on"`, `"software_or_lockup"`, `"power_on+jtag"`, `"unknown(0x200)"`), `stall_flags_text: list[str]`, `last_op_text`. `parse_caps_payload` additionally returns `uptime_ms`, `reset_reason`, `boot_id` (0 when zero-filled by old firmware).
- `TransportTrace(maxlen=4000)`: `.tx(ptype, nbytes, write_ms, frame_id=None, ok=True)`, `.rx(ptype, nbytes)`, `.rejected(reason, head_hex)` (first 64 B of an undecodable/invalid chunk), `.event(kind, **detail)`, `.snapshot()`, `.freeze() -> list` (copy taken *before* any probe runs, so probes don't mutate the evidence), `.summary()`.
- `RxDispatcher`: the **only** reader once connected. `drain()` reads all packets under the write lock (brief), then *outside* the lock: POTS → `on_pots` callback, DIAG → `transport.last_teensy_diag` (+ `trace.rx`), CRASH/STATS/CAPS/CONFIG_ACK/NAK → resolve `waiters[ptype]` future if any, else `trace.event('unsolicited', ...)`. `async request(ptype_req, ptype_resp, payload=b'', timeout=0.5)`: register future → write under lock (≤ a few ms) → `await asyncio.wait_for(fut, timeout)` **without** the lock → on timeout remove waiter, return None. Cancellation-safe: writes happen in `to_thread` only for FRAME; command writes are synchronous short writes. `send_config`, `request_stats`, `request_diag`, `request_crash` all use `request()`. Handshake (CAPS) also uses it (`drain()` becomes active immediately after port open; the `caps is None` guard is removed because the dispatcher now owns CAPS routing).
- Detection (all produce exactly one `incident` per episode via `on_incident(kind, detail)`, never a reboot): `stall` (3 missed PINGs | 2 failed handshakes with port present | CONFIG timeout); `write_timeout` (`send_frame` raises `SerialTimeoutException` → `connected=False`, incident with the exception, the last 50 trace entries and `last_teensy_diag`); `link_lost` (port disappears while `connected`); `frame_stall` (over 10 s not in blackout/test mode: Pi `frames_sent` grew ≥ 100 but Teensy `frames_received` (from pushed DIAG) grew 0); `unexpected_reboot` (on connect: CAPS `boot_id` differs from the previous connection's and the Pi did not initiate a reset → incident includes the *previous* trace/DIAG preserved before replacement and the `PKT_CRASH` text); `teensy_stall_flag` (pushed DIAG shows nonzero `stall_flags` or growth in `oversize`/`bad_*`/`tx_dropped`; one incident per flag-set per 10 min). Health check keeps PING every 5 s; DIAG now arrives pushed so no DIAG polling.
- `transport.stalled: dict|None` latched on stall/write_timeout/link_lost/frame_stall, cleared on a healthy PING after a successful HELLO+CONFIG. `transport.maintenance: bool` — while true the reconnect loop does not open the port (set by Task 3 around reset). `async reboot()`: disconnect under lock + `reboot_teensy(port)` (existing module); **called only by `TeensyMaintenance.reset`**.
- `on_incident` is invoked via `asyncio.create_task(_safe(cb))` with try/except + `logger.exception`; a failing recorder never propagates into `reconnect_loop`/`drain`.

Tests: DIAG/CAPS parsing incl. every reset bit and combination; trace maxlen/freeze/rejected; dispatcher: concurrent `send_frame` + `request_stats` never hold the lock while waiting (assert lock is free during the await via a probe task), unsolicited DIAG cached, waiter timeout cleans up, POTS routed; detection: each trigger fires `on_incident` exactly once per episode, a raising callback leaves the loop alive, `reboot_teensy` is **never** awaited by any detection path, `boot_id` change → `unexpected_reboot` with previous trace attached, `frame_stall` not raised during blackout.

Commit: `transport: RX dispatcher with futures, bounded trace, stall/write-timeout/link-loss/reboot detection (no auto-reset)`.

---

### Task 3: IncidentRecorder + manual reset (single-flight) + API

**Files:** Create `pi/app/core/incidents.py`, `pi/app/core/teensy_maintenance.py`, `pi/app/api/routes/teensy.py`; Modify `pi/app/api/server.py`, `deps.py`, `main.py` (`transport.on_incident = incidents.capture_event`; `transport.on_pots` = pots loop), `pi/scripts/setup.sh` (`usermod -aG systemd-journal pillar`; `mkdir -p /opt/ledfanatic/logs/incidents`); Tests `pi/tests/test_incidents.py`, `test_teensy_routes.py`.

**Interfaces:**
- `IncidentRecorder(transport, render_state, state_manager, brightness_engine, logs_dir)`
  - `async capture(trigger, reason, extra=None) -> str` (incident id `YYYYmmdd-HHMMSS-<trigger>-<6 hex>`; file created with `open(..., 'x')`, written to `.tmp` then `os.replace`). Bundle `schema_version: 1`: `trigger, reason, at, transport.get_status(), stalled, last_stats, last_diag (pushed), trace (frozen first), live_diag (request_diag 0.5 s; null + 'timeout' marker on failure), crash_text (request_crash), render {scene, params, target_fps, actual_fps, blackout, brightness_effective}, system {uptime, loadavg, mem_available_kb}, journal_service (journalctl -u ledfanatic -n 300), journal_kernel (journalctl -k --since -10min), each journal entry = {rc, text|error}`.
  - `async capture_event(kind, detail)` → `capture(kind, detail.get('reason', kind), detail)`; bounded retry (3×, 2 s apart) on OSError; failures logged, never raised.
  - `async append(incident_id, key, value)` (atomic rewrite), `list()`, `path_for(id)` (regex `^\d{8}-\d{6}-[a-z_]+-[0-9a-f]{6}\.json$`).
- `TeensyMaintenance(transport, incidents)`: `asyncio.Lock` single-flight; `async reset(reason='manual') -> dict`: if locked → raise `BusyError` (route → 409 with the in-flight incident id). Else: `transport.maintenance = True`; `incident = await incidents.capture('manual_reset', reason)`; `ok = await transport.reboot()`; `transport.maintenance = False`; wait ≤ 10 s for a **new** `boot_id` + CONFIG ACK; `post = {rebooted: ok, reconnected, boot_id, reset_reason_text (None if DIAG/CAPS unavailable), crash_text, error}`; `await incidents.append(incident, 'post_reset', post)`; return `{incident, **post}` — never raises for a failed reboot/absent DIAG. Sets `transport.expected_reboot = True` so Task 2's `unexpected_reboot` detector ignores this one.
- Routes `/api/teensy`: `GET /diag` (last pushed DIAG + age; 503 if none), `GET /incidents`, `GET /incidents/{id}` (FileResponse), `POST /reset` (auth) → 200 dict | 409 busy.
- `build_state_payload` adds `transport: {connected, firmware_version, stalled: {kind, reason, since, incident} | null, maintenance}`.

Tests: bundle shape with fake transport; two captures in the same second produce two files; `path_for('../x')` → None; recorder OSError retried then logged; `reset` concurrent → second gets `BusyError`; `reset` with `reboot()` False and `request_diag()` None returns a dict with `rebooted: false, reset_reason_text: null`; routes map these to 200/409.

Commit: `feat: forensic incident bundles + single-flight manual Teensy reset`.

---

### Task 4: UI — banner, Reset Teensy, incidents

**Files:** `pi/app/ui/static/index.html`, `js/app.js`, `css/app.css`.

- Global banner on every tab when `state.transport.stalled`: "Teensy {kind} since HH:MM — evidence saved as {incident}. [Reset Teensy]".
- System → Status: "Teensy" card: connected/firmware/boot reason; last DIAG summary (`bad_*`, `oversize`, `tx_dropped`, `stall_flags_text`, `rx_backlog`, `max_loop_us`); **Reset Teensy** (confirm: "Saves a diagnostic bundle, then reboots the Teensy. The panel goes dark ~5 s and may flash while the data lines float."); button disabled while in flight; 409 → toast with the running incident id; result toast shows `reset_reason_text` and incident id. Incidents list with download links.
- Run `/fda:quick` on the new elements; fix severity ≥ 3.

Commit: `ui: Teensy stall banner, manual reset, incident downloads`.

---

### Task 5: Renderer frame guard + reconnect sequence

**Files:** `pi/app/core/renderer.py`, `pi/app/main.py:_on_teensy_connect`.

- Renderer: `self._expected_frame_bytes = sum(output_config_list(layout)) * 3`, recomputed where the layout is (re)built; in `_render_frame`, if `len(pixel_bytes) != expected` → `logger.error` (once per 10 s), `trace.event('bad_frame_len', got, want)`, skip send. Test by monkeypatching `pack_frame` to return a short buffer.
- `_on_teensy_connect`: CONFIG → `pin_teensy_brightness()` → `set_blackout(render_state.blackout)`. **No** `TEST_PATTERN_NONE` (BLACKOUT already clears firmware test state, and firmware 1.3.0 no longer alters Pi-owned state on HELLO). Log `INFO Teensy link healthy again (incident <id>)` when `stalled` clears.

Commit: `renderer: refuse wrong-size frames; reconnect restores Pi-owned state only`.

---

### Task 6: Soak acceptance

60 fps Rainbow Ink ≥ 2 h, then 90 fps ≥ 2 h, then 2 h in **blackout** at 90 fps (the starvation case). Read `GET /api/teensy/diag` at start/end. Pass per soak: `bad_short=bad_magic=bad_version=bad_len=bad_crc=oversize=tx_dropped=0`, `stall_flags=0`, `rx_backlog < 8192` at every sample, `max_loop_us < 20000`, `max_tx_us < 5000`, `frames_received == frames_applied ± 1` (outside blackout), `GET /api/teensy/incidents` adds nothing, journal has no `write took` warnings. If all pass at 90 fps restore `target_fps` 90; else stay at 60 and record the DIAG that failed. Update memory `teensy_rx_wedge_recovery.md` with outcome + DIAG reading procedure.

---

## Self-review

- Owner requirements: no auto-reset (Pi recovery removed Task 2; firmware never had a watchdog; core fault reboot replaced by halting handler Task 1) ✔; manual reset button with complete capture, single-flight (Tasks 3–4) ✔; "every bit of data": Pi trace incl. rejected bytes + write outcomes, pushed Teensy DIAG independent of RX, crash record across reset, both journals (Tasks 1–3) ✔; never flash: startup flash removed, no black show on reconnect/geometry change, DMA never re-init mid-transfer (Task 1); residual float-on-reset flash is hardware and only occurs on a user-initiated reset ✔; never freeze: every loop wait bounded (RX budget, TX availableForWrite, patched busy(), staged geometry), lengths bounded before arithmetic, verified by a sanitizer harness on the real decoder (Tasks 0–1) ✔.
- Codex round-1 findings (19) each map to a Task: fault handler→1.3; length overflow→1.2; busy()→1.1/1.6; CONFIG re-init/EEPROM/black→1.7; TX blocking→1.8; lock model→2 dispatcher; stall triggers→2 detection; unexpected resets→1.9+2; RX-independent evidence→1.8 push + USB regs; reset single-flight→3; reset-reason bits→1 interfaces+2 tests; DIAG self-overwrite→1.5; raw trace→2 trace.rejected; post-reset None→3; incident id collision→3; kernel journal→3; native harness→0; TEST_PATTERN_NONE→1.5+5; on_stall isolation→2.
- Honest gap: 100 % cannot be asserted until the soaks pass and, if a wedge ever recurs, its bundle classifies it. The instrumentation exists so that happens on the first recurrence, not the tenth.
