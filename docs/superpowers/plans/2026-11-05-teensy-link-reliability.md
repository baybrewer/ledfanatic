# Teensy Link Reliability Implementation Plan (v8)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Pi→Teensy frame link never wedges, never flashes, and never resets itself; if it ever does fail, one manual reset from the UI captures every byte of evidence needed to make that failure the last one.

**Architecture:** (1) Firmware v1.3.0 removes every autonomous behaviour (watchdog fade, EEPROM writes, blackout clear on HELLO, core fault reboot, startup flash) and makes every wait in the loop bounded; it counts every rejected packet by reason, pushes a DIAG snapshot once a second so evidence does not depend on the RX path that failed, echoes request ids so replies can be correlated, and on a CPU fault parks the LED timers idle and halts with the crash record preserved. (2) The Pi transport has one serialized writer and one RX dispatcher routing replies to futures keyed by request id; frames are sent only in the `ready` state (HELLO → CONFIG committed → Pi state restored); a byte-budget trace records raw TX/RX chunks, write outcomes and rejected chunks; stalls, write timeouts, link loss, frame/output stalls, Teensy-side stall flags and unexpected reboots are *detected and reported* — nothing on the Pi reboots the Teensy. (3) `IncidentRecorder` writes collision-proof forensic bundles on every detection and on the manual **Reset Teensy** action (server-side single-flight, try/finally), appending post-reset evidence; an optional **Power-cycle USB** button covers a halted (faulted) Teensy whose USB can no longer accept the bootloader request.

**Tech Stack:** Teensy 4.1 / OctoWS2811 (vendored + patched) / PlatformIO 6 (venv `~/.platformio/penv`, restored 2026-11-05), Python 3.11 FastAPI + pyserial, vanilla JS, host C++ harness (`g++ -fsanitize=address,undefined`).

## Why the panel went dark/flashed (what we know)

| Event | Evidence | Status |
|---|---|---|
| 2026-11-04 ~14:56, 16:29: Teensy wedged (loop alive, POTS streaming, RX ignored; later host writes timed out) | journal, raw serial probe | **Root cause unknown**; mechanisms below are plausible. Every path is instrumented by this plan. |
| ~4 % frames rejected by Teensy | `bad_crc` 3882/100k | **Fixed** 49d8707 (Pi COBS encoder 254-run bug). Same bug exists in the firmware encoder → Task 1. |
| 19:58 "black": 33 % brightness → 9 % output | frame dump | **Fixed** 7495e4f. |
| Overnight white flashes while blacked out | user report; journal lost (now persistent) | Hypothesis: Pi auto-recovery (removed by this plan) rebooted the Teensy; floating data lines → strips latch garbage. |

**Found in installed library/core sources (all addressed in Task 1):** blackout branch re-shows black every loop and `show()` spins on DMA, starving USB RX at 90 fps; `OctoWS2811::busy()` ignores unfinished DMA (`if (!dma3.complete()) ;`) so `show()` can spin forever; `handleConfig` re-inits TMR4/DMA, shows black and writes EEPROM (interrupts off, unbounded flash poll) on every reconnect; core `unused_interrupt_vector()` reboots 8 s after a fault; `Panic_Temp_isr` calls it directly; `usb_serial_write` blocks ≤120 ms under host backpressure and `sendPacket` ignores short writes; `verify_packet` length arithmetic can wrap; firmware `cobs_encode` has the 254-run bug; `runWatchdog` fades to black after 750 ms and the first resumed frame is applied with `fadeLevel=0` (black).

**Hardware note (outside this plan):** data lines float while the Teensy is in reset/bootloader; a 10 kΩ pull-down from each WS2812 data line to GND keeps strips idle through any reset. Software cannot do this.

## Global Constraints

- **No automatic reset of the Teensy, ever** — not from the Pi, not a firmware watchdog, not the core fault handler. Detection and evidence only; reset is a user action.
- Every detected episode (stall, write timeout, link loss, frame/output stall, Teensy stall flag transition, unexpected reboot) and every manual reset produces an incident bundle. Capture failures are logged and retried, never propagate into background tasks, and never block a user-requested reset.
- Protocol: `PacketHeader` 24 B, CRC32, COBS, STATS exactly 28 B; golden vectors keep passing. New capability = new packet type, zero-filled CAPS bytes, or the previously unused header `frame_id` on request/response packets.
- Blackout explicit (0x01/0x00). The Pi owns blackout/brightness/test-pattern/geometry state; the firmware never changes them on its own and holds the last frame when frames stop.
- Never edit `/opt/ledfanatic/config/*` without user permission. Ask the user before the first firmware flash and before installing `uhubctl`.
- Service user `pillar` (dialout, systemd-journal after Task 3). No sudo in the service path.
- Style: 2-space indent, descriptive names, comments say *why*.
- After every Pi change: `cd pi && PYTHONPATH=. pytest tests/ -q --deselect tests/test_matrix_rain_perf.py` (4 pre-existing failures: generative_palette, imported ambient_a_6, migrations metadata, width_policy spectrum) then `bash pi/scripts/deploy.sh ledfanatic.local`.

**Accepted residuals (stated, not hidden):** (a) a CPU fault *before* `setup()` runs still hits the core's rebooting handler — the firmware has run for months without one, and such a fault would mean a firmware that never starts; (b) a Teensy that faulted *inside an interrupt handler* is fully halted and cannot execute the USB bootloader request; recovery is the USB power-cycle button (Pi 4 switches all its USB ports together) or unplugging, and its RAM crash record does not survive that power-cycle (the Pi's last pushed DIAG, ≤1 s old with the `last_op` breadcrumb, and the trace remain). A fault in the main loop (thread mode — all frame/packet handling) does **not** halt: see Task 1.3 fault-idle mode, where the record is served live and the UI reset works; (c) USB controller error bits are acknowledged inside the core ISR and cannot be latched without patching the core — DIAG exports backlog, IRQ state and our own counters instead; (d) a CPU fault that lands mid-transfer stops the waveform where it is: pixels before the cut show the new frame, pixels after it keep the previous frame, and **the single pixel whose bit stream was cut may decode to a third colour — on each active output, since Octo drives all strips in parallel from one bitstream** (a shortened "1" pulse can read as "0"). Completing the waveform from a fault context is not attempted (Octo refills its two-half buffer from an ISR every 40 bytes; letting DMA run on would transmit stale halves — random colours across many pixels). One possibly-wrong pixel per output, once, in an event that has never occurred, beats that; the fault-injection test asserts, per output, that every pixel except at most one shows a colour present in the previous or current frame.

---

### Task 0: Toolchain, flash script, host-side C++ protocol harness

**Files:** Create `pi/scripts/flash_teensy.sh`, `teensy/firmware/test_native/test_protocol.cpp`, `teensy/firmware/test_native/run.sh`; Test `pi/tests/test_firmware_protocol_native.py`.

- [ ] **flash_teensy.sh** — build on Mac → scp hex → stop service → `stty -F /dev/ttyACM0 134` (HalfKay) → `sudo teensy_loader_cli --mcu=TEENSY41 -w -v` → start service → poll `/api/system/status` ≤30 s until `transport.caps.firmware_version == FIRMWARE_VERSION` from `config.h`; exit 1 otherwise.
- [ ] **test_native** compiles the real `src/protocol.cpp` + `include/protocol.h` (`-DNATIVE_TEST` guards any Arduino include) with ASan/UBSan. Cases, both directions (C++ encode→Python/C++ decode and Python encode→C++ decode via vectors): golden vectors from `pi/tests/test_protocol.py`; `254*'A'+0x00+'B'` and runs of 253/254/255/508/509 ± trailing zeros (encoder fix); consecutive/empty delimiters; over-cap input then delimiter → `overflowed()`, then a valid frame decodes; exact-max frame; `payload_len ∈ {0xffffffe5, 0xffffffff, MAX_PAYLOAD_SIZE+1}` → `VERIFY_LEN`, no sanitizer report; interrupted block → `VERIFY_SHORT`; **CRC-valid packet whose final COBS code promises one more byte than arrives → rejected as incomplete framing (`decoder.remaining() > 0`), never handled**; a CRASH-sized (144 B payload) packet round-trips. `run.sh` prints `OK <n>`.
- [ ] pytest wrapper skips without `g++`. Commit `build: flash script + native sanitizer harness for firmware protocol`.

---

### Task 1: Firmware v1.3.0 — bounded loop, evidence, nothing autonomous

**Files:** Modify `include/config.h`, `include/protocol.h`, `src/protocol.cpp`, `src/main.cpp`, `platformio.ini`; Create `lib/OctoWS2811/` (vendored, patched), `include/diag.h`, `include/fault_handler.h`, `src/fault_handler.cpp`.

**Interfaces produced (consumed by Task 2):**
- **Request correlation:** for every request the Pi sets header `frame_id` = request id (nonzero, monotonic); the firmware echoes it in the response header (`CAPS, CONFIG_ACK/NAK, STATS, DIAG, CRASH`). Unsolicited packets (`POTS`, pushed `DIAG`) carry `frame_id = 0`. `build_packet(type, payload, len, out, max, uint32_t frame_id = 0)` gains the parameter.
- `PKT_DIAG = 0x32` (request → response; **pushed every 1000 ms**). `DIAG_FIELDS = 44` × uint32 LE = 176 B, order:
  `0 uptime_ms, 1 reset_reason (SRC_SRSR raw at boot), 2 boot_id, 3 rx_bytes, 4 pkt_ok, 5 bad_short, 6 bad_magic, 7 bad_version, 8 bad_len, 9 bad_crc, 10 oversize, 11 empty_packets, 12 last_pkt_type (non-probe), 13 ms_since_rx_byte, 14 ms_since_good_pkt (non-probe), 15 live_decoder_out_len, 16 live_decoder_remaining, 17 last_reject_reason, 18 last_reject_len, 19 last_reject_ms_ago, 20 loops_per_sec, 21 max_loop_us, 22 max_tx_us, 23 tx_dropped, 24 ms_since_show_start, 25 stall_active, 26 stall_latched, 27 stack_free, 28 frames_received, 29 frames_applied, 30 frames_pending_dropped (STATS droppedPending), 31 octo_begins, 32 rx_backlog (Serial.available()), 33 usb_sts (USB1_USBSTS), 34 usb_irq (bit0 enabled, bit1 pending), 35 last_op, 36 dma3_csr_es (vendored lib `debugState`), 37 tmr4_enbl_sctrl0, 38 crash_present (0/1), 39 geometry_generation, 40 bad_framing (incomplete COBS block), 41 frames_pending (0/1: `pendingFrameReady`), 42 ignored_in_fault_mode, 43 fault_count`.
  `stall_active`/`stall_latched` bits: 0 RX bytes arriving but no valid packet ≥2 s; 1 DMA incomplete ≥1 s after a show; 2 TX dropped in the last second; 3 FAULTED (latched only; this boot is in fault-idle mode). *Active* bits clear when the condition clears (valid packet / DMA complete / a second with no drops); *latched* bits are set-only since boot.
  `last_op` breadcrumb: 1 rx, 2 handle_frame, 3 handle_config, 4 geometry_commit, 5 show, 6 send, 7 pots, 8 octo_begin.
- `PKT_CRASH = 0x33` request → response (96 B) = `uint32 valid, uint32 source (1 live: this boot is in fault-idle mode, 2 previous boot), uint32 src_gpr5, uint32 src_srsr, 44 B core arm_fault_info_struct (imxrt.h:10397 layout, in order: uint32 len, ipsr, cfsr, hfsr, mmfar, bfar, ret, xpsr; float temp; uint32 time, crc), 36 B extension (stacked r0, r1, r2, r3, r12, lr, pc, xpsr, exc_return)` (the core's struct at `0x2027FF80`, CRC-validated by the firmware; `valid=0` → zeros). The Pi formats both parts — no `CrashReport.printTo()` (it clears the record and can power the board off).
- `CAPS` bytes 24–27 `uptime_ms`, 28–31 `reset_reason`, 32–35 `boot_id`, 36 `crash_present`, 37–40 `geometry_generation`, 41 `faulted` (1 = this boot is in fault-idle mode) (all previously zero-filled).
- `boot_id`: `DMAMEM` retained, 32-byte aligned `{magic, counter, ~counter}` written then `arm_dcache_flush_delete`'d (OCRAM is write-back cached; without the flush a reset can see the stale value) and validated at boot by magic + complement (survives soft reset; `counter++` per boot, re-seeded from `ARM_DWT_CYCCNT ^ micros() ^ (analogRead(A10)<<20) ^ (analogRead(A11)<<10) ^ analogRead(A12)` when magic is invalid, i.e. after power-on) → `boot_id = (counter << 12) ^ (seed & 0xFFF)`. Pi also treats `uptime_ms` rollback as a reboot (fallback).
- `geometry_generation`: increments on each committed CONFIG; `CONFIG_ACK` payload (previously empty) = `uint32 geometry_generation` of the *committed* geometry.

- [ ] **Step 1: Vendor + patch OctoWS2811.** Copy the installed library to `lib/OctoWS2811/`; remove it from `lib_deps`. Patches (documented in `lib/OctoWS2811/LEDFANATIC_PATCH.md`): `busy()`: `if (!dma3.complete()) return 1;`; add `void debugState(uint32_t* csr_es, uint32_t* tmr)` exporting `DMA_ES`, `dma3.TCD->CSR`, `TMR4_ENBL`, `TMR4_SCTRL0`; add `void parkOutputsIdle()` = the first part of `show()` (disable TMR4 channels, force outputs low, `dma3.disable()`) for use from the fault handler.

- [ ] **Step 2: protocol.h/.cpp.** `COBSDecoder`: `_overflow` flag (set when a store would exceed `MAX_PACKET_BYTES`; cleared by `reset()`), `overflowed()`, `remaining()`; buffer `MAX_PACKET_BYTES + 1`. `enum VerifyResult : uint8_t {VERIFY_OK, VERIFY_SHORT, VERIFY_MAGIC, VERIFY_VERSION, VERIFY_LEN, VERIFY_CRC}`; `verify_packet` returns it and bounds before arithmetic:
  ```c
  if (len < HEADER_SIZE + CRC_SIZE) return VERIFY_SHORT;
  ... magic/version ...
  if (header->payload_len > MAX_PAYLOAD_SIZE) return VERIFY_LEN;          // before any addition
  if (len != HEADER_SIZE + (size_t)header->payload_len + CRC_SIZE) return VERIFY_LEN;
  ```
  `cobs_encode`: fix the 254-run bug exactly as the Pi fix (49d8707): a full 254-byte block emits `0xFF` and must **not** consume a following zero. `build_packet` takes `frame_id`.

- [ ] **Step 3: fault_handler.{h,cpp} — park LEDs, preserve record, drop into fault-idle (or halt); never reset.**
  `install_fault_handlers()` (first statement of `setup()`): `#include <imxrt.h>` (uses the core's `volatile` `_VectorsRam` declaration; no redeclaration). For `i` in `[1, NVIC_NUM_INTERRUPTS + 16)`: if `_VectorsRam[i] == unused_interrupt_vector` → `fault_halt` (covers NMI 2, HardFault 3, MemManage 4, BusFault 5, UsageFault 6, 7–10, SVC 11, 12, 13, every unattached IRQ; PendSV 14/SysTick 15 are core-owned and never point at it). Then **`_VectorsRam[IRQ_TEMPERATURE_PANIC + 16] = fault_halt`** (tempmon's `Panic_Temp_isr` calls the rebooting handler directly; a thermal panic still halts output safely).
  `fault_halt` is a **naked assembly entry shim**: it captures `EXC_RETURN` (LR), selects the faulting frame pointer from `EXC_RETURN` bit 2 (MSP vs PSP) *before any C call* (an ordinary C++ prologue would clobber LR/MSP first), **switches MSP to a reserved 4 KB `faultStack` in DTCM** (the original stack may be the thing that faulted — the core traps stack overflow explicitly), sets PRIMASK, and calls `fault_record(uint32_t* frame, uint32_t exc_return)`.
  `fault_record()`: (1) `leds.parkOutputsIdle()` — implemented *inside the vendored OctoWS2811* so it uses the library's own `bitmask[]`/bank bookkeeping: disable all three DMA channels, disable the TMR4 channels, then clear the Octo pins on the GPIO banks Octo actually drives (`GPIO1–4` `DR_CLEAR`, per `OctoWS2811_imxrt.cpp:119-120`; standard-GPIO mode is what `begin()` selected) — residual (d) applies; (2) **validate the frame**: usable iff `SCB_CFSR` has no stacking-fault bits (`MSTKERR` 0x10, `STKERR` 0x1000) and `frame` lies inside DTCM (`0x20000000–0x2007FFFF`) or OCRAM (`0x20200000–0x2027FFFF`) with 32 B to spare; (3) store the **core's 44-byte `arm_fault_info_struct`** in its real layout (`imxrt.h:10397`: `uint32 len, ipsr, cfsr, hfsr, mmfar, bfar, ret, xpsr; float temp; uint32 time, crc` at `0x2027FF80`) as `startup.c:624–690` does, **except** `temp`, which is written from `cachedTempBits` (a `uint32` copy of the last `tempmonGetTemp()` taken in the main loop once per second) — the recording path must contain no floating-point instruction: with lazy FP state preservation pending, the first FP instruction writes the FP frame to the *original* (possibly damaged) stack. `fault_record` and the shim are compiled with `__attribute__((target("general-regs-only")))`, and the test harness disassembles them (`arm-none-eabi-objdump -d`) and asserts no `v*`/`vldr`/`vstr`/`vmov` instruction is present **plus our 36-byte extension** at `0x2027FFAC` (`stacked r0–r3, r12, lr, pc, xpsr, exc_return` — the core record has no LR; **`exc_return` is always the shim's captured value**; only the eight stacked registers are zeroed when the frame was unusable, so mode/stack/FP-frame type survive even a stacking fault), then `arm_dcache_flush_delete` both; `faultsThisBoot++` (plain zero-initialised `.bss`, the recovery guard) and `faultsLifetime++` (retained next to `bootGen`, validated by complement, evidence only); (4) **decide** using only `exc_return` (never the frame): interrupted mode = `EXC_RETURN` bit 3 (1 → thread mode: the main loop, i.e. all packet/frame handling; 0 → handler mode). If thread mode **and** `faultsThisBoot == 1`: clear the sticky fault status (`SCB_CFSR = SCB_CFSR; SCB_HFSR = SCB_HFSR`) and **always** enter fault-idle on a fresh frame — never return to the original stack, whether or not its frame was readable (a readable frame does not prove the stack is intact: an exhausted stack can present a clean frame and then overflow again the moment `sendPacket` allocates its ~570 B of buffers). Build an 8-word frame on `faultStack` (`r0–r3, r12, lr = 0`, `pc = fault_idle_loop | 1`, `xpsr = 0x01000000`), **clear any pending lazy FP state (`FPU_FPCCR &= ~FPU_FPCCR_LSPACT`) and the FPCA bit (`CONTROL &= ~0x4`)** so the basic-frame return is consistent, and return with `EXC_RETURN = 0xFFFFFFF9` (thread mode, MSP, no FP). Otherwise (fault inside an ISR, or a second fault this boot) → `for (;;) asm volatile("wfi");` with interrupts disabled (residual (b)). **Exception return in the shim:** `fault_record` returns `&freshFrame`; the shim sets `MSP = freshFrame` and executes `bx lr` with `0xFFFFFFF9`. `fault_idle_loop` therefore always runs on `faultStack` (4 KB; its only consumers are the bounded RX loop and `sendPacket`), with the original stack left exactly as the fault found it for the record.
  `fault_idle_loop()` never returns. Its **first statement is `__enable_irq()`** — exception return does not clear PRIMASK set by the shim, and with it set USB/SysTick would stay dead. It then runs only the bounded RX loop from Step 5 restricted to `PING`, `DIAG`, `CRASH` and `HELLO` (CAPS reports `faulted = 1` in byte 41) and the once-per-second DIAG push (`stall_latched` bit 3 `FAULTED`, `last_op` frozen at the faulting value); no LED output, no POTS, no CONFIG/FRAME handling (`FRAME`/`BLACKOUT`/`CONFIG` are counted as `ignored_in_fault_mode`, field 42). Because the USB ISR keeps running, the UI's 134-baud bootloader reset works and the crash record is read live; no EEPROM write is needed (the earlier EEPROM-in-fault-handler idea is withdrawn: `eeprom_write_byte` polls flash without a deadline and re-enables interrupts).
  At boot the firmware copies the RAM record + extension (if the core CRC validates), keeps the copy for the whole boot, serves it via `PKT_CRASH`, then invalidates the retained record (`len = 0; crc = 0;` **followed by `arm_dcache_flush_delete`** — write-back OCRAM would otherwise keep the valid old record visible to the next boot, as `CrashReport.cpp::cleardata()` also flushes). The Pi requests it on every connect and stores it in the `unexpected_reboot`/`manual_reset` incident.
  Verification, two separate sequences. **Thread-mode** (`-DFAULT_INJECT`: test pattern id 0xFE → `*(volatile uint32_t*)0xFFFFFFF0 = 1;` from the main loop, during an active frame stream, once during blackout, and once from inside an `if` guarded by a volatile so the compiler emits an IT block): the panel must hold (residual (d)); the Pi must raise a `fault` incident within 2 s (from the pushed DIAG `FAULTED`), including during blackout; in fault-idle mode `GET /api/teensy/crash` must return the live record (`source = 1`) and `GET /api/teensy/diag` must show `FAULTED`; the **UI Reset Teensy button must recover it** (no power-cycle); after the reset `PKT_CRASH` carries the same record with `source = 2`. Inject at three offsets (idle, early in a frame transfer, late in a transfer) and assert via the live preview + a photo, **per active output**, that every pixel but at most one shows a colour present in the previous or current frame. **Handler-mode** (`-DFAULT_INJECT_ISR`: the same store from a one-shot `IntervalTimer` ISR): the halt branch must engage, the Pi must raise an incident on timeouts, the UI reset must report "not reachable", and **Power-cycle USB** (Task 3) must recover it (the RAM record is lost — residual (b)). Also force a stacking fault once (`-DFAULT_INJECT_STACK`: set MSP to an unmapped address then call a function) **with FP active** (perform a float multiply immediately before corrupting MSP so lazy FP state is pending) and assert the CRASH extension still carries the captured `exc_return`; and once an **exhausted-stack fault with a readable frame** (`-DFAULT_INJECT_EXHAUST`: recurse until MSP is within 64 B of `_ebss`, then fault) and assert fault-idle serves DIAG/CRASH and the UI reset recovers it. Rebuild without the flags.

- [ ] **Step 4: diag.h + globals.** `struct Diag` with the DIAG fields plus internals (`verifyFail[6]`, `loopsThisSecond`, `loopStartUs`, `showPending`, `showStartMs`, `txDroppedThisSecond`, `lastReject{reason,len,ms}`, `lastOp`). `stackFreeEstimate()` = `(uint32_t)&local - (uint32_t)&_ebss`. Retained `DMAMEM struct {uint32_t magic, counter;} bootGen`. `crashRecord` copy made in `setup()` **before** anything else touches the region; after copying, zero the record's `len`/`crc` so the next boot does not re-report it; `SRC_SRSR` captured then cleared with `SRC_SRSR = v & ~SRC_SRSR_TEMPSENSE_RST_B` (w1c bits cleared by writing 1, the w0c tempsense bit cleared by writing 0).

- [ ] **Step 5: RX — bounded, non-blocking, reset on every delimiter, probes don't overwrite evidence.**
  ```c
  lastOp = OP_RX;
  for (int n = 0; n < USB_READ_CHUNK; n++) {          // 8192
    int c = Serial.read();                             // never Stream::readBytes (timeout semantics)
    if (c < 0) break;
    diag.rxBytes++; diag.lastRxByteMs = millis();
    if (c == 0) {
      if (decoder.overflowed()) { diag.oversize++; noteReject(REJECT_OVERSIZE, decoder.length()); }
      else if (decoder.remaining() > 0) { diag.badFraming++; noteReject(REJECT_INCOMPLETE_BLOCK, decoder.length()); }  // last COBS code promised bytes that never came
      else if (decoder.length() == 0) diag.emptyPackets++;
      else handlePacket(decoder.data(), decoder.length());
      decoder.reset();                                   // ALWAYS
    } else decoder.feed((uint8_t)c);
  }
  ```
  `handlePacket`: `v = verify_packet(...)`; failure → `diag.verifyFail[v]++`, `noteReject(v, len)` (stores reason, len, millis), `stats.badCrc++` only for `VERIFY_CRC` (STATS compatibility), return. Success → `diag.pktOk++`; **if type ∉ {DIAG, CRASH, PING}**: `lastPktType`, `lastGoodPktMs` updated (probes read evidence, they must not become it). `PKT_TEST_PATTERN` with `0xFF` no longer clears `pendingFrameReady`. Remove `runWatchdog()` and all `fadeLevel` code: the Pi owns output; when frames stop the panel holds the last frame (no fade-to-black, no black first frame on resume). `handleHello`: only `sendCaps(req_id)`.

- [ ] **Step 6: LED path — DMA-aware, geometry commit first, never blocks.**
  ```c
  bool idle = !leds.busy();                       // patched: false while DMA incomplete
  if (idle) {
    showPending = false;
    if (geometryStaged) { commitGeometry(); return_from_led_path = true; }  // begin() changed numbytes: a show() now would spin on Octo's reset-time wait computed from the NEW length; let the next iteration's busy() defer it
    else if (blackout) { if (!blackShown) startShow(SHOW_BLACK); }
    else if (activeTestPattern >= 0) startShow(SHOW_TEST);
    else if (pendingFrameReady) startShow(SHOW_FRAME);
    // (written as if/else-if so nothing follows commitGeometry() in the same iteration)
  } else if (showPending && millis() - showStartMs > 1000) setStall(STALL_DMA);
  if (idle) clearStall(STALL_DMA);
  ```
  `startShow(kind)`: `lastOp = OP_SHOW; clearAll()/runTestPattern()/applyPendingFrame(); leds.show(); showPending = true; showStartMs = millis();` plus the existing bookkeeping for frames (`framesApplied`, `fpsCounter`, `pendingFrameReady=false`) and `blackShown = true` for black. `blackShown = false` whenever `PKT_BLACKOUT` sets `blackout=true`. Delete the startup dim-green flash.

- [ ] **Step 7: CONFIG — staged transaction, ACK after commit, no EEPROM at runtime, no black frame.**
  `handleConfig` (after validation): `memcpy(stagedLeds, newLeds); stagedReqId = req_id; geometryStaged = true;` (a newer CONFIG simply replaces the staged one). `commitGeometry()` (runs only when `idle`, before any show): if `memcmp(stagedLeds, ledsPerOutput) != 0` → `memcpy; recalcGeometry(); lastOp = OP_OCTO_BEGIN; leds.begin(maxLedsPerStrip, displayMemory, drawingMemory, octoConfig); diag.octoBegins++; pendingFrameReady = false;` (no `clearAll(); show()` — the next frame covers every pixel); always `geometryGeneration++; configReceived = true; geometryStaged = false; sendAck(stagedReqId, geometryGeneration);`. Frames whose length ≠ `frameSize` are counted `badFrame` as today. **Delete `saveConfigToEEPROM()` and `loadConfigFromEEPROM()`**: the Pi sends CONFIG on every connect and (Task 2) sends no frames until it is committed; a runtime flash write is an unbounded interrupts-off wait with no benefit. `setup()` calls `leds.begin()` with defaults only.

- [ ] **Step 8: TX — non-blocking, atomic framed write, backpressure counted.**
  ```c
  void sendPacket(uint8_t type, const uint8_t* payload, size_t len, uint32_t req_id) {
    uint8_t raw[HEADER_SIZE + 256 + CRC_SIZE];                       // largest response: CRASH (140 B), DIAG (160 B)
    size_t rawLen = build_packet(type, payload, len, raw, sizeof(raw), req_id); if (!rawLen) return;
    uint8_t encoded[sizeof(raw) + sizeof(raw) / 254 + 2];
    size_t encLen = cobs_encode(raw, rawLen, encoded, sizeof(encoded) - 1); if (!encLen) return;
    encoded[encLen++] = 0x00;                                          // delimiter in the same write
    if (Serial.availableForWrite() < (int)encLen) { diag.txDropped++; diag.txDroppedThisSecond++; return; }  // never block on a host that stopped reading
    lastOp = OP_SEND; uint32_t t0 = micros();
    if (Serial.write(encoded, encLen) != encLen) diag.txDropped++;
    uint32_t dt = micros() - t0; if (dt > diag.maxTxUs) diag.maxTxUs = dt;
  }
  ```
  Once-per-second block: `loopsPerSec`, `STALL_TX` active ⇐ `txDroppedThisSecond > 0`, RX-stall active ⇐ `rxBytes && now - lastRxByteMs < 500 && now - lastGoodPktMs > 2000` (cleared when a valid non-probe packet arrives), `sendDiag(0)` push.

- [ ] **Step 9: Build, harness, flash, verify.** `test_native/run.sh` → `OK`; `flash_teensy.sh` → `firmware: 1.3.0 OK`; journal `CONFIG ACK received`; user confirms the current effect is showing with no flash; `GET /api/teensy/diag` (after Task 2) shows `stall_active = 0`. Commit `firmware 1.3.0: bounded RX/TX/LED paths, DIAG push, request ids, halting fault handler, nothing autonomous`.

---

### Task 2: Pi protocol + transport — writer, dispatcher, trace, detection only

**Files:** Modify `pi/app/models/protocol.py`, `pi/app/transport/usb.py`; Create `pi/app/transport/trace.py`, `pi/app/transport/dispatcher.py`, `pi/app/transport/detection.py`; Tests `test_protocol.py`, `test_transport_trace.py`, `test_transport_dispatch.py`, `test_transport_detection.py` (replaces `test_transport_recovery.py`).

**Interfaces:**
- protocol: `PacketType.DIAG=0x32, CRASH=0x33`; `DIAG_FIELDS=44`; `parse_diag_payload`, `parse_crash_payload` (→ dict with decoded cfsr/hfsr/… and `text`), `parse_caps_payload` adds `uptime_ms, reset_reason, boot_id, crash_present, geometry_generation, faulted`; `parse_config_ack_payload → geometry_generation|None`; `decode_reset_reason(raw) -> str` (all `SRC_SRSR_*` masks joined with `+`, `unknown(0x..)` for undefined bits); `build_packet(..., frame_id=)` already exists.
- `TransportTrace(byte_budget=512*1024, event_maxlen=4000)`: events `tx(ptype, req_id, nbytes_requested, nbytes_written, write_ms, ok, raw: bytes)`, `rx(ptype, req_id, raw: bytes)`, `rejected(reason, raw: bytes)`, `event(kind, **detail)`. Raw chunks live in a byte-budget ring (oldest evicted); each event records `raw_offset/len/truncated`. `freeze() -> dict` deep-copies events + raw hex **before any probe runs**.
- `SerialWriter` (in `usb.py`): the only code that calls `serial.write`, implemented as **one persistent task** consuming an `asyncio.Queue` of `WriteJob(framed, future, deadline)`. The task alone owns the port for writing: it runs each job's `serial.write` in `to_thread`, records the outcome in the trace **unconditionally**, then resolves the job's future only `if not job.future.done()` (a caller's `wait_for` timeout/cancellation cancels the future; an unguarded `set_result` would raise `InvalidStateError` and kill the writer — probed). Exceptions inside a job never end the task; it logs and continues with the next job. Callers `await asyncio.wait_for(job.future, deadline)`; a cancelled caller cancels nothing in the writer — if its job has not started, the writer skips it (`job.future.cancelled()`); if it has started, the write completes untouched. (`asyncio.shield` was rejected: cancellation of the shielded awaiter releases an `async with lock` while the worker keeps writing — probed.) Records `trace.tx`; `SerialTimeoutException` → `trace.event('write_timeout')`, state → `CLOSED`, job future gets `LinkError`. The reader never needs the write lock: it reads from its own thread-safe path (`serial.read(in_waiting)`), and pyserial permits concurrent read/write on POSIX.
- `RxDispatcher`: the only reader after port open. `drain()` (pots loop, 50 ms) reads under the lock then routes outside it: `POTS → on_pots`; `DIAG req_id==0 → last_teensy_diag (+ detection.on_diag)`; any response with `req_id≠0 → waiters.pop(req_id)` (resolve; CONFIG_NAK resolves the CONFIG waiter with `None`); unmatched → `trace.event('unsolicited')`. `async request(ptype, payload=b'', timeout=0.5) -> (header, payload)|None`: `req_id = next_id(); fut = waiters[req_id] = Future()`; `await writer.write(...)`; `await wait_for(fut, timeout)`; on timeout `waiters.pop(req_id, None)` only if it is still that future. Late replies after timeout are traced, not delivered. Concurrent requests never collide (unique ids).
- Connection state machine (replaces `connected`/`_last_config_ack` logic): `CLOSED → OPEN (port open, dispatcher running) → HELLO_OK (CAPS) → CONFIGURED (CONFIG_ACK with new geometry_generation) → READY (brightness pinned, blackout restored)`. `send_frame` sends **only in READY** and only the raw format (legacy 3-byte format removed; firmware 1.3.0 always committed before READY). Layout changes re-run CONFIG and drop to `CONFIGURED` until ACK. `get_status()` exposes `state`, `boot_id`, `geometry_generation`.
- `detection.py` — `LinkDetector(transport, on_incident)` evaluates every 1 s and on events; one incident per *episode* (opens on transition into a bad state, closes when the clearing condition holds; a new episode after close creates a new incident):
  - `stall`: 3 missed PINGs (every 5 s) | 2 failed handshakes with the port present | CONFIG not committed within 3 s.
  - `write_timeout`: `LinkError` from the writer.
  - `link_lost`: port disappears from any non-CLOSED state.
  - `output_stall`: in READY, not blackout/test, over a same-boot 10 s window: Pi frames written ≥100 but Teensy `frames_received` Δ=0 **or** `frames_applied` Δ=0; also `pushed DIAG age > 3 s` while READY (Teensy TX dead). Clears only when both deltas progress again.
  - `teensy_flag`: `stall_active` gains a bit, or `oversize/bad_*/tx_dropped` grow; closes when `stall_active` loses the bit / counters stop growing for 60 s.
  - `fault`: pushed DIAG `stall_latched` has `FAULTED`, or `fault_count` grows, or CAPS `faulted = 1` — **evaluated regardless of blackout/test mode** (fault-idle keeps PING/DIAG healthy, so no other detector would fire); one incident per boot; includes `GET /crash` live record.
  - `unexpected_reboot`: on CAPS, `boot_id` ≠ previous connection's **or** DIAG `uptime_ms` < previous, and no `ExpectedReboot(prev_boot_id, expires)` is armed for that previous boot → incident with the previous connection's frozen trace/DIAG/CAPS and the `PKT_CRASH` record. `ExpectedReboot` is armed by Task 3 only, consumed by the first matching new boot, expires after 20 s, cleared in `finally`.
  - `transport.stalled` = the open episode (kind, reason, since, incident id) for the UI; cleared when the episode closes.
- `on_incident` runs via `asyncio.create_task(_isolated(cb))` with `try/except logger.exception`.

Tests: parsing (every reset bit + combos, CAPS extension, CONFIG_ACK generation; **CRASH decoder against a fixture built from the real 44-byte layout with a valid core CRC**, plus the 36-byte extension); trace byte budget/eviction/truncation/freeze; writer: a caller cancelled mid-write does not let a second job start before the first `serial.write` returns (fake serial with a slow write records ordering), a job cancelled before start is skipped, **a caller that timed out while its write was in flight leaves the writer alive and the next job is written**, timeout → `LinkError`; dispatcher: two concurrent STATS resolve to their own callers, late reply after timeout is traced not delivered, CONFIG_NAK resolves `None`, pushed DIAG with req_id 0 updates the cache while a solicited DIAG resolves its waiter; state machine: `send_frame` refused before READY, reconnect to an already-configured Teensy sends no frame until CONFIG ACK; detection: each kind opens exactly one incident per episode, re-asserting within an episode adds no incident, clear + re-assert opens a new one, `frames_received` growing with `frames_applied` flat → `output_stall`, blackout suppresses `output_stall` **but not `fault`** (pushed DIAG with `FAULTED` during blackout → one `fault` incident), `boot_id` change with no `ExpectedReboot` → `unexpected_reboot` carrying the previous trace, with an armed matching `ExpectedReboot` → none, expired → incident; a raising callback leaves drain/reconnect alive; **`reboot_teensy` is never awaited by any detection path**.

Commit: `transport: serialized writer, request-id dispatcher, READY gating, byte-budget trace, episode detection (no auto-reset)`.

---

### Task 3: IncidentRecorder, manual reset (single-flight), USB power-cycle, API

**Files:** Create `pi/app/core/incidents.py`, `pi/app/core/teensy_maintenance.py`, `pi/app/api/routes/teensy.py`; Modify `server.py`, `deps.py`, `main.py`, `pi/scripts/setup.sh` (`usermod -aG systemd-journal pillar`; `mkdir -p /opt/ledfanatic/logs/incidents`; optional `apt-get install uhubctl` + sudoers line `pillar ALL=(ALL) NOPASSWD: /usr/sbin/uhubctl` — only with the user's OK); Tests `test_incidents.py`, `test_teensy_maintenance.py`, `test_teensy_routes.py`.

**Interfaces:**
- `IncidentRecorder(transport, render_state, state_manager, brightness_engine, logs_dir, fallback_dir=Path('/tmp/ledfanatic-incidents'))`
  - `async capture(trigger, reason, extra=None) -> CaptureResult(id, path|None, error|None)`; id `YYYYmmdd-HHMMSS-<trigger>-<6 hex>`; `open(..., 'x')`, write `.tmp`, `os.replace`; on OSError retry 3× (2 s) then try `fallback_dir`; never raises. Bundle `schema_version 2`: `trigger, reason, at, transport.get_status() (state, boot_id, geometry_generation), stalled, last_stats, last_diag (pushed, with age), trace (frozen first), live_diag (request 0.5 s; `{error:'timeout'}` on failure), crash (request_crash), render {scene, params, target_fps, actual_fps, blackout, brightness_effective}, system {uptime, loadavg, mem_available_kb}, journal_service {rc, text|error} (journalctl -u ledfanatic -n 300), journal_kernel {rc, text|error} (journalctl -k --since -10min), extra`.
  - `async capture_event(kind, detail)` (the `on_incident` hook), `async append(id, key, value) -> bool` (atomic rewrite; false + log on failure, value also logged so it is never lost), `list()` (both roots), `path_for(id)` (regex `^\d{8}-\d{6}-[a-z_]+-[0-9a-f]{6}\.json$`; searched in `logs_dir` then `fallback_dir`; the resolved path must stay inside the root it was found under).
- `TeensyMaintenance(transport, incidents, detector)`: single-flight via an owned task, not `async with lock` (which would *queue* a second request and run two resets — probed).
  - `async reset(reason='manual') -> dict`: `if self._task and not self._task.done(): raise BusyError(self._current_incident)`; else `self._task = asyncio.create_task(self._run_reset(reason))`; `return await asyncio.shield(self._task)` — the HTTP client disconnecting cancels only the awaiter, never the operation.
  - `_run_reset`:
    ```
    prev_boot = transport.boot_id
    cap = await incidents.capture('manual_reset', reason)        # proceeds even if cap.error (user asked for recovery; error is reported)
    self._current_incident = cap.id
    detector.arm_expected_reboot(prev_boot, ttl=20); transport.maintenance = True
    post = {rebooted: False, reconnected: False, error: None}
    try:
      post['rebooted'] = await transport.reboot()                 # teensy_reset.reboot_teensy: baud touch in to_thread (pyserial open has its own timeout); loader subprocess always awaited or killed+awaited (proc.kill(); await proc.wait()) — no orphaned subprocess/thread
      post['reconnected'] = await transport.wait_for(READY, timeout=10)
    except Exception as e: post['error'] = repr(e)
    finally:
      transport.maintenance = False; detector.disarm_expected_reboot_if_unconsumed()
      post.update(new_boot_id=transport.boot_id, reset_reason_text=..., crash=..., capture_error=cap.error)
      try: await incidents.append(cap.id, 'post_reset', post)     # terminal outcome is persisted on every exit path
      except Exception: logger.exception(...)
    return {incident: cap.id, **post}
    ```
  - `async usb_power_cycle(reason) -> dict` (only if `uhubctl` is installed and the user approved its sudoers line): capture → `sudo uhubctl -l 1-1 -a cycle -d 3` → wait READY ≤15 s → append. Documented caveat: the Pi 4 switches **all** USB ports together.
- Routes `/api/teensy`: `GET /diag` (last pushed DIAG + age, 503 if none), `GET /crash` (live record when `faulted`, else the previous-boot record), `GET /incidents`, `GET /incidents/{id}`, `POST /reset` (auth; 200 | 409 `{busy: true, incident}`), `POST /usb-power-cycle` (auth; 404 if unavailable).
- `build_state_payload` adds `transport: {state, firmware_version, boot_id, stalled: {kind, reason, since, incident}|null, maintenance, usb_power_cycle_available}`.

Tests: bundle shape (frozen trace precedes probe; live_diag timeout marker); two captures in one second → two files; `path_for` rejects traversal and resolves ids in **both** `logs_dir` and `fallback_dir` (containment checked per root; `list()` merges both; `append` finds fallback captures); OSError → retries → fallback dir → append/list/download still work; two concurrent `reset` calls → exactly one reboot, second gets `BusyError` immediately; HTTP awaiter cancelled mid-reset → operation completes and `post_reset` is appended; `reset` with `reboot()` False and `request_diag()` None → dict with `rebooted:false, reset_reason_text:null`; cancellation during `wait_for` clears `maintenance`; `append` failure returns False and logs the value; routes 200/409/404.

Commit: `feat: forensic incident bundles, single-flight manual reset, optional USB power-cycle`.

---

### Task 4: UI — banner, Reset Teensy, incidents

**Files:** `pi/app/ui/static/index.html`, `js/app.js`, `css/app.css`.

- Global banner on every tab while `state.transport.stalled`: "Teensy {kind} since HH:MM — evidence saved as {incident}. [Reset Teensy]" (+ "[Power-cycle USB]" when available).
- System → Status "Teensy" card: state/firmware/boot reason/boot id; last DIAG summary (`bad_*`, `oversize`, `tx_dropped`, `stall_active/latched` names, `rx_backlog`, `max_loop_us`, `last_op`); **Reset Teensy** (confirm: "Saves a diagnostic bundle, then reboots the Teensy. The panel goes dark ~5 s and may flash while the data lines float."); disabled while in flight; 409 → toast with the running incident id; result toast with `reset_reason_text`, `reconnected`, incident id, `capture_error` if any. Incidents list with download links.
- Run `/fda:quick`; fix severity ≥ 3. Commit `ui: Teensy stall banner, manual reset, incident downloads`.

---

### Task 5: Renderer frame guard + reconnect sequence

**Files:** `pi/app/core/renderer.py`, `pi/app/main.py:_on_teensy_connect`.

- Renderer: `_expected_frame_bytes = sum(output_config_list(layout)) * 3` recomputed where the layout is (re)built; mismatch → `logger.error` (once/10 s), `trace.event('bad_frame_len')`, skip. Renderer skips the send entirely while `transport.state != READY` (no frame is ever sent in a wrong format).
- `_on_teensy_connect`: CONFIG (wait for ACK, store `geometry_generation`) → `pin_teensy_brightness()` → `set_blackout(render_state.blackout)` → `transport.mark_ready()`. No `TEST_PATTERN_NONE`. Log `INFO Teensy link healthy again (incident <id>)` when an episode closes.

Commit: `renderer: frames only in READY; refuse wrong-size frames`.

---

### Task 6: Soak acceptance

`pi/tools/soak_check.py` (new) consumes the 1 Hz pushed DIAG via `GET /api/teensy/diag` every second for the soak duration and evaluates **deltas over same-boot, non-blackout intervals**: `Δbad_* = Δoversize = Δtx_dropped = 0`; `stall_active == 0` at every sample; `max rx_backlog < 8192`; `max_loop_us < 20000`; `max_tx_us < 5000`; `Δframes_received == Δframes_applied + Δframes_pending_dropped + (frames_pending_end − frames_pending_start)` with `Δframes_pending_dropped == 0` outside blackout; `boot_id` constant; no new incidents. Soaks: 60 fps Rainbow Ink ≥2 h; 90 fps ≥2 h; blackout at 90 fps ≥2 h (starvation case; here only backlog/stall/boot criteria apply). Pass at 90 → restore `target_fps` 90; else stay at 60 and record the failing sample. Update memory `teensy_rx_wedge_recovery.md`.

---

## Self-review (round-2 findings → resolution)

Fault paths NMI/SVC/tempmon → Task 1.3 replaces every vector equal to the core handler incl. SVC/NMI and overrides the temperature-panic vector; pre-`setup()` faults are an accepted residual. Halt blocks UI reset → USB power-cycle path (Task 3) + timeouts raise the incident; stated residual. Fault mid-transfer flashes → `parkOutputsIdle()` before halting. `_VectorsRam` type → use core declaration. Geometry starvation → commit runs first when idle, before any show. Writer blocking/cancellation → `SerialWriter` with shield, deadline incl. lock. Reconnect legacy-format frames → READY gating, legacy format removed. DIAG waiter never resolved → req-id routing resolves both solicited DIAG and cache. Watchdog black frame → watchdog removed. Maintenance latch → try/finally, structured errors. ACK before effective → ACK after commit with generation; staged replaced by latest; A→B→A compares staged vs current at commit. Waiter collisions/NAK → unique req ids; NAK resolves `None`. EEPROM unbounded → removed. `CrashReport.printTo` → raw record copy; tempsense bit handled. Boot id → retained counter + seed + uptime rollback. Trace raw bytes → byte-budget ring with offsets/outcomes. DMA/USB detail → vendored `debugState`, USB backlog/IRQ; USBSTS latching is a stated residual. Decoder evidence → live vs last-reject fields; probes excluded. Output stall → received *and* applied deltas, stale push, clears on progress. `expected_reboot` → scoped `ExpectedReboot(prev_boot, ttl)`, consumed/expired/cleared. Flag dedup → active/latched + episodes. Soak criteria → deltas, continuous sampling.

**Round-3 findings → resolution:** fault mid-transfer → park all three DMA channels + GPIO low; torn-frame residual (d) stated and tested. Crash record vs power-cycle → persisted to EEPROM in the fault handler (bounded), served with `source`. `shield` lock semantics → persistent writer task + queue. Reset cancellation/orphans → owned task, shielded awaiter, loader killed+reaped, outcome appended in `finally`. `async with lock` queues → `BusyError` on an undone task. DMAMEM cache → aligned, complemented, flushed. Incomplete COBS block → `remaining() > 0` rejected, harness case. Commit-then-show spin → nothing follows `commitGeometry()` in the same iteration. Fallback captures → both roots resolvable. Soak equality → pending-frame boundary term.

**Round-4 findings → resolution:** GPIO banks → `parkOutputsIdle()` lives in the vendored lib and clears GPIO1–4 with its own masks. Third-colour pixel → residual (d) corrected to "at most one pixel"; assertion updated. Unbounded `eeprom_write_byte` → EEPROM persistence withdrawn; replaced by fault-idle mode that keeps USB alive so the record is read live and the UI reset works (halt only for handler-mode/second faults, residual (b)). LR/MSP clobber → naked asm entry shim captures EXC_RETURN and selects the stack before any C call. Writer `InvalidStateError` → `done()` guards, unconditional trace, task never exits on a job error.

**Round-5 findings → resolution:** PRIMASK → `__enable_irq()` first in `fault_idle_loop`. IT/ICI → xPSR rebuilt as `(align bit 9) | Thumb`. `faultCount == 1` across boots → per-boot `faultsThisBoot` guard, retained `faultsLifetime` evidence-only. Damaged MSP/frame → emergency `faultStack`, frame validation (stacking-fault bits + RAM bounds), fresh frame + `EXC_RETURN 0xFFFFFFF9` fallback. Fault undetected in blackout → `fault` detector independent of blackout. 44-byte core struct → explicit 44 B core + 36 B extension wire layout. Un-flushed invalidation → flush after clearing. Contradictory acceptance → split thread-mode / handler-mode sequences.

**Round-6 findings → resolution:** MSP not restored → shim sets MSP = frame for MSP-based exceptions before `bx lr`. Mode from a possibly-unreadable frame → mode from `EXC_RETURN` bit 3 only. FP in the recording path → cached temperature bits, `general-regs-only` attribute, disassembly check, `LSPACT`/`FPCA` cleared before the basic-frame return. Core struct order → `len, ipsr, cfsr, hfsr, mmfar, bfar, ret, xpsr, float temp, time, crc` + real-layout fixture. Pixel assertion → per active output.

**Round-7 findings → resolution:** readable-but-exhausted stack → fault-idle always enters on a fresh frame on `faultStack` (patched-frame path removed), exhausted-stack injection test added. Zeroed `exc_return` → always stored from the shim; only stacked registers zeroed.
