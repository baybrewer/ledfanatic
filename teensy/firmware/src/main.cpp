/*
 * LED Fanatic — Teensy 4.1 Firmware 1.3.0
 *
 * Transport + output engine for the OctoWS2811-driven panel. Receives COBS-framed
 * packets over USB Serial from the Raspberry Pi and drives the strips via DMA.
 *
 * Design rule: nothing in here is autonomous. The Pi owns blackout, brightness,
 * test patterns, geometry and resets. When frames stop, the panel holds the last
 * frame. Every wait in loop() is bounded, every rejected byte is counted, and a
 * DIAG snapshot is pushed once a second so evidence never depends on the RX path
 * that may have failed. A CPU fault parks the outputs and drops into
 * fault_idle_loop() (see fault_handler.cpp) — it never reboots.
 */

#include <Arduino.h>
#include <imxrt.h>
#include <OctoWS2811.h>
#include <string.h>
#include "config.h"
#include "protocol.h"
#include "diag.h"
#include "fault_handler.h"

// --- OctoWS2811 setup (max-sized DMA buffers) ---
// Each int holds one bit-time for all 8 outputs; 24 bit-times per LED → ledsPerStrip * 6 ints.
DMAMEM int displayMemory[MAX_LEDS_PER_OUTPUT * 6];
int drawingMemory[MAX_LEDS_PER_OUTPUT * 6];
const int octoConfig = WS2811_BGR | WS2811_800kHz;
OctoWS2811 leds(DEFAULT_LEDS_PER_STRIP, displayMemory, drawingMemory, octoConfig);

// --- Runtime geometry (committed by CONFIG) ---
static bool configReceived = false;
static uint8_t activeOutputs = DEFAULT_ACTIVE_OUTPUTS;
static uint16_t ledsPerOutput[TOTAL_OUTPUTS] = {
  DEFAULT_LEDS_PER_STRIP, DEFAULT_LEDS_PER_STRIP,
  DEFAULT_LEDS_PER_STRIP, DEFAULT_LEDS_PER_STRIP,
  DEFAULT_LEDS_PER_STRIP, 0, 0, 0
};
static uint16_t maxLedsPerStrip = DEFAULT_LEDS_PER_STRIP;
static uint32_t totalLeds = DEFAULT_ACTIVE_OUTPUTS * DEFAULT_LEDS_PER_STRIP;
static uint32_t frameSize = DEFAULT_ACTIVE_OUTPUTS * DEFAULT_LEDS_PER_STRIP * 3;

// Staged CONFIG: applied only when the LED path is idle (a begin() while DMA runs would
// spin), ACKed only after it is committed so the Pi never streams frames into the wrong size.
static bool geometryStaged = false;
static uint16_t stagedLeds[TOTAL_OUTPUTS];
static uint32_t stagedReqId = 0;

// --- Pending frame buffer (max-sized) ---
static uint8_t pendingFrame[MAX_LEDS_PER_OUTPUT * TOTAL_OUTPUTS * 3];

// --- State ---
static COBSDecoder decoder;
static bool pendingFrameReady = false;
static uint8_t masterBrightness = 255;
static bool blackout = false;
static bool blackShown = false;   // show black once per blackout, not every iteration
static int activeTestPattern = -1;  // -1 = none

Diag diag;

// --- Stats (STATS payload is exactly 28 bytes; protocol rule) ---
struct Stats {
  uint32_t uptimeMs;
  uint32_t framesReceived;
  uint32_t framesApplied;
  uint32_t badCrc;
  uint32_t badFrame;
  uint32_t droppedPending;
  uint32_t outputFps;
} stats;

static uint32_t fpsCounter = 0;
static uint32_t lastSecondMs = 0;

// --- Pot inputs (raw 10-bit, exponentially smoothed) ---
static int32_t potFiltered[3] = {-1, -1, -1};
static uint32_t lastPotsSend = 0;
static const uint8_t POT_PINS[3] = {POT_PIN_BRIGHTNESS, POT_PIN_MENU, POT_PIN_PATTERN};

enum ShowKind { SHOW_BLACK, SHOW_TEST, SHOW_FRAME };

// --- Forward declarations ---
void pollRx();
void handlePacket(const uint8_t* data, size_t len);
void handleFrame(const uint8_t* payload, size_t len);
void handleConfig(const uint8_t* payload, size_t len, uint32_t reqId);
void handleTestPattern(const uint8_t* payload, size_t len);
void handleBrightness(const uint8_t* payload, size_t len);
void sendCaps(uint32_t reqId);
void sendStats(uint32_t reqId);
void sendDiag(uint32_t reqId);
void sendCrash(uint32_t reqId);
void sendAck(uint32_t reqId, uint32_t generation);
void sendNak(uint32_t reqId);
void sendPacket(uint8_t type, const uint8_t* payload, size_t len, uint32_t reqId);
void readPots();
void sendPots();
void applyPendingFrame();
void recalcGeometry();
void commitGeometry();
void startShow(ShowKind kind);
void runTestPattern();
void setPixel(int strip, int pixel, uint8_t r, uint8_t g, uint8_t b);
void clearAll();

// -------------------------------------------------------------------
// Small helpers

static inline bool inFaultMode() { return (diag.stallLatched & STALL_FAULTED) != 0; }

// last_op is frozen at the faulting value once in fault mode: it is the breadcrumb that
// says where the main loop was when the fault hit.
static inline void setOp(uint32_t op) {
  if (!inFaultMode()) diag.lastOp = op;
}

static inline void setStall(uint32_t bit) {
  diag.stallActive |= bit;
  diag.stallLatched |= bit;
}

static inline void clearStall(uint32_t bit) { diag.stallActive &= ~bit; }

static inline void noteReject(uint32_t reason, uint32_t len) {
  diag.lastReject.reason = reason;
  diag.lastReject.len = len;
  diag.lastReject.ms = millis();
}

static inline void put32(uint8_t* dst, uint32_t v) { memcpy(dst, &v, 4); }

static inline uint32_t floatBits(float f) {
  uint32_t bits;
  memcpy(&bits, &f, 4);
  return bits;
}

// -------------------------------------------------------------------
// Recalculate derived geometry values from ledsPerOutput[]
void recalcGeometry() {
  maxLedsPerStrip = 0;
  totalLeds = 0;
  activeOutputs = 0;
  for (int i = 0; i < TOTAL_OUTPUTS; i++) {
    if (ledsPerOutput[i] > 0) {
      activeOutputs++;
    }
    totalLeds += ledsPerOutput[i];
    if (ledsPerOutput[i] > maxLedsPerStrip) {
      maxLedsPerStrip = ledsPerOutput[i];
    }
  }
  frameSize = totalLeds * 3;
}

// Runs only when the LED path is idle and before any show in that iteration.
void commitGeometry() {
  setOp(OP_GEOMETRY_COMMIT);
  if (memcmp(stagedLeds, ledsPerOutput, sizeof(ledsPerOutput)) != 0) {
    memcpy(ledsPerOutput, stagedLeds, sizeof(ledsPerOutput));
    recalcGeometry();
    setOp(OP_OCTO_BEGIN);
    // No clearAll()/show() here: the next frame covers every pixel, and a show() right
    // after begin() would spin on Octo's reset-time wait computed from the new length.
    leds.begin(maxLedsPerStrip, displayMemory, drawingMemory, octoConfig);
    diag.octoBegins++;
    pendingFrameReady = false;  // any frame staged for the old size is the wrong length
  }
  diag.geometryGeneration++;
  configReceived = true;
  geometryStaged = false;
  sendAck(stagedReqId, diag.geometryGeneration);
}

// -------------------------------------------------------------------
void setup() {
  install_fault_handlers();  // first: everything below may fault and must not reboot

  // Reset cause: w1c bits clear by writing them back as 1; the tempsense bit is w0c and
  // clears by writing 0, hence the mask.
  uint32_t srsr = SRC_SRSR;
  uint32_t gpr5 = SRC_GPR5;
  SRC_SRSR = srsr & ~SRC_SRSR_TEMPSENSE_RST_B;
  diag.resetReason = srsr;

  // Before anything else touches the top of OCRAM.
  crash_record_capture_at_boot(gpr5, srsr);

  uint32_t seed = ARM_DWT_CYCCNT ^ micros() ^ ((uint32_t)analogRead(A10) << 20)
                  ^ ((uint32_t)analogRead(A11) << 10) ^ (uint32_t)analogRead(A12);
  diag.bootId = boot_retained_init(seed);

  cachedTempBits = floatBits(tempmonGetTemp());

  Serial.begin(115200);  // baud ignored on Teensy USB

  // Defaults only; the Pi sends CONFIG on every connect. No startup flash, no black
  // frame: the strips keep whatever they show until the Pi says otherwise.
  leds.begin();

  lastSecondMs = millis();
  diag.lastRxByteMs = lastSecondMs;
  diag.lastGoodPktMs = lastSecondMs;
  diag.showStartMs = lastSecondMs;
}

// -------------------------------------------------------------------
// Bounded, non-blocking RX. Serial.read() byte-at-a-time (never readBytes: timeout
// semantics); the decoder is reset on EVERY delimiter so a bad packet can never poison
// the next one.
void pollRx() {
  setOp(OP_RX);
  for (int n = 0; n < USB_READ_CHUNK; n++) {
    int c = Serial.read();
    if (c < 0) break;
    diag.rxBytes++;
    diag.lastRxByteMs = millis();
    if (c == 0) {
      if (decoder.overflowed()) {
        diag.oversize++;
        noteReject(REJECT_OVERSIZE, decoder.length());
      } else if (decoder.remaining() > 0) {
        // The last COBS code promised bytes that never came.
        diag.badFraming++;
        noteReject(REJECT_INCOMPLETE_BLOCK, decoder.length());
      } else if (decoder.length() == 0) {
        diag.emptyPackets++;
      } else {
        handlePacket(decoder.data(), decoder.length());
      }
      decoder.reset();
    } else {
      decoder.feed((uint8_t)c);
    }
  }
}

// -------------------------------------------------------------------
void loop() {
  diag.loopStartUs = micros();
  diag.loopsThisSecond++;

  pollRx();

  // --- LED path: DMA-aware, geometry commit first, never blocks ---
  bool idle = !leds.busy();  // patched busy(): false while DMA incomplete
  if (idle) {
    diag.showPending = false;
    clearStall(STALL_DMA);
    if (geometryStaged) {
      commitGeometry();  // nothing else this iteration: the next busy() defers any show
    } else if (blackout) {
      if (!blackShown) startShow(SHOW_BLACK);
    } else if (activeTestPattern >= 0) {
      startShow(SHOW_TEST);
    } else if (pendingFrameReady) {
      startShow(SHOW_FRAME);
    }
  } else if (diag.showPending && millis() - diag.showStartMs > 1000) {
    setStall(STALL_DMA);
  }

  // --- Pot inputs ---
  uint32_t now = millis();
  readPots();
  if (now - lastPotsSend >= POTS_INTERVAL_MS) {
    setOp(OP_POTS);
    sendPots();
    lastPotsSend = now;
  }

  // --- Once-per-second housekeeping ---
  if (now - lastSecondMs >= STATS_INTERVAL_MS) {
    stats.outputFps = fpsCounter * 1000 / (now - lastSecondMs);
    fpsCounter = 0;
    diag.loopsPerSec = diag.loopsThisSecond;
    diag.loopsThisSecond = 0;

    if (diag.txDroppedThisSecond > 0) setStall(STALL_TX); else clearStall(STALL_TX);
    diag.txDroppedThisSecond = 0;

    // Bytes are arriving but nothing has parsed for 2 s: the link is up and the stream is garbage.
    if (diag.rxBytes && now - diag.lastRxByteMs < 500 && now - diag.lastGoodPktMs > 2000) {
      setStall(STALL_RX);
    }

    // Float here, in the main loop, so the fault recorder never has to.
    cachedTempBits = floatBits(tempmonGetTemp());

    sendDiag(0);
    lastSecondMs = now;
  }
  stats.uptimeMs = now;

  uint32_t loopUs = micros() - diag.loopStartUs;
  if (loopUs > diag.maxLoopUs) diag.maxLoopUs = loopUs;
}

// -------------------------------------------------------------------
// Fault-idle mode: entered by exception return from fault_record() on faultStack after a
// thread-mode fault. Only probes are served so the Pi can read the live record and the
// UI's 134-baud reset still works (the USB ISR keeps running).
extern "C" void fault_idle_loop(void) {
  __enable_irq();  // exception return does not clear the PRIMASK the shim set
  diag.stallLatched |= STALL_FAULTED;
  uint32_t lastPush = millis();
  for (;;) {
    pollRx();
    uint32_t now = millis();
    if (now - lastPush >= STATS_INTERVAL_MS) {
      sendDiag(0);
      lastPush = now;
    }
  }
}

// -------------------------------------------------------------------
void handlePacket(const uint8_t* data, size_t len) {
  PacketHeader header;
  const uint8_t* payload;

  VerifyResult v = verify_packet(data, len, &header, &payload);
  if (v != VERIFY_OK) {
    diag.verifyFail[v]++;
    noteReject(v, len);
    if (v == VERIFY_CRC) stats.badCrc++;  // STATS compatibility
    return;
  }
  diag.pktOk++;

  uint32_t reqId = header.frame_id;
  bool probe = header.type == PKT_PING || header.type == PKT_DIAG || header.type == PKT_CRASH;
  if (!probe) {
    diag.lastPktType = header.type;
    diag.lastGoodPktMs = millis();
    clearStall(STALL_RX);
  }

  if (inFaultMode()) {
    switch (header.type) {
      case PKT_PING:  sendStats(reqId); break;
      case PKT_DIAG:  sendDiag(reqId); break;
      case PKT_CRASH: sendCrash(reqId); break;
      case PKT_HELLO: sendCaps(reqId); break;
      default: diag.ignoredInFaultMode++; break;
    }
    return;
  }

  switch (header.type) {
    case PKT_HELLO:
      sendCaps(reqId);  // only this: blackout/test state belongs to the Pi
      break;

    case PKT_FRAME:
      handleFrame(payload, header.payload_len);
      break;

    case PKT_CONFIG:
      handleConfig(payload, header.payload_len, reqId);
      break;

    case PKT_PING:
      sendStats(reqId);
      break;

    case PKT_DIAG:
      sendDiag(reqId);
      break;

    case PKT_CRASH:
      sendCrash(reqId);
      break;

    case PKT_TEST_PATTERN:
      handleTestPattern(payload, header.payload_len);
      break;

    case PKT_BLACKOUT:
      // Explicit: 0x01 = on, 0x00 = off. Empty payload treated as on for safety.
      blackout = header.payload_len >= 1 ? (payload[0] != 0) : true;
      if (blackout) blackShown = false;
      activeTestPattern = -1;
      break;

    case PKT_BRIGHTNESS:
      handleBrightness(payload, header.payload_len);
      break;

    case PKT_REBOOT_BOOTLOADER:
      _reboot_Teensyduino_();  // explicit user action from the Pi, not autonomous
      break;
  }
}

// -------------------------------------------------------------------
void handleFrame(const uint8_t* payload, size_t len) {
  setOp(OP_HANDLE_FRAME);
  const uint8_t* pixelData;
  size_t pixelLen;

  if (configReceived) {
    pixelData = payload;
    pixelLen = len;
  } else {
    // Legacy pre-CONFIG format: channels(1) + leds_per_ch(2) + pixel data
    if (len < 3) {
      stats.badFrame++;
      return;
    }
    pixelData = payload + 3;
    pixelLen = len - 3;
  }

  if (pixelLen != frameSize) {
    stats.badFrame++;
    return;
  }

  memcpy(pendingFrame, pixelData, pixelLen);

  if (pendingFrameReady) {
    stats.droppedPending++;
  }
  pendingFrameReady = true;
  stats.framesReceived++;
  activeTestPattern = -1;
}

void handleConfig(const uint8_t* payload, size_t len, uint32_t reqId) {
  setOp(OP_HANDLE_CONFIG);
  // CONFIG payload: active_outputs(u8) + leds_per_output(u16 x 8) = 17 bytes
  const size_t CONFIG_SIZE = 1 + TOTAL_OUTPUTS * 2;
  if (len < CONFIG_SIZE) {
    sendNak(reqId);
    return;
  }

  uint8_t newActive = payload[0];
  uint16_t newLeds[TOTAL_OUTPUTS];
  for (int i = 0; i < TOTAL_OUTPUTS; i++) {
    newLeds[i] = payload[1 + i * 2] | (payload[2 + i * 2] << 8);
  }

  if (newActive == 0 || newActive > TOTAL_OUTPUTS) {
    sendNak(reqId);
    return;
  }
  for (int i = 0; i < TOTAL_OUTPUTS; i++) {
    if (newLeds[i] > MAX_LEDS_PER_OUTPUT) {
      sendNak(reqId);
      return;
    }
  }

  // Stage; a newer CONFIG simply replaces the staged one. ACK comes from commitGeometry().
  memcpy(stagedLeds, newLeds, sizeof(stagedLeds));
  stagedReqId = reqId;
  geometryStaged = true;
}

#ifdef FAULT_INJECT_ISR
static IntervalTimer faultInjectTimer;
static void faultInjectIsr() {
  faultInjectTimer.end();
  *(volatile uint32_t*)0xFFFFFFF0 = 1;  // handler-mode fault: the halt branch must engage
}
#endif

#ifdef FAULT_INJECT_EXHAUST
static uint32_t faultInjectRecurse(uint32_t depth) {
  volatile uint32_t pad[8] = {depth};
  uint32_t sp;
  asm volatile("mov %0, sp" : "=r"(sp));
  if (sp <= (uint32_t)&_ebss + 64) {
    *(volatile uint32_t*)0xFFFFFFF0 = 1;  // fault with a readable frame on an exhausted stack
    return pad[0];
  }
  return faultInjectRecurse(depth + 1) + pad[0];
}
#endif

// Compiled out by default. Test pattern 0xFE triggers the configured injection so the
// fault path can be exercised from the Pi without touching a debugger.
static void faultInjectHook() {
#if defined(FAULT_INJECT)
  // Guarded by a volatile so the compiler emits a conditional (IT block) around the store.
  static volatile uint32_t armed = 1;
  if (armed) *(volatile uint32_t*)0xFFFFFFF0 = 1;
#elif defined(FAULT_INJECT_ISR)
  faultInjectTimer.begin(faultInjectIsr, 1000);
#elif defined(FAULT_INJECT_STACK)
  // FP active (lazy state pending), then an unmapped MSP so the next call stacks into nothing.
  static volatile float fpA = 1.5f, fpB = 2.5f;
  fpA = fpA * fpB;
  asm volatile("mov.w r0, #0x60000000\nmsr msp, r0\nisb\nbl faultInjectCallee" ::: "r0", "lr", "memory");
#elif defined(FAULT_INJECT_EXHAUST)
  faultInjectRecurse(0);
#endif
}

#ifdef FAULT_INJECT_STACK
extern "C" __attribute__((noinline, used)) void faultInjectCallee() {
  volatile uint32_t sink[4] = {1, 2, 3, 4};
  (void)sink;
}
#endif

void handleTestPattern(const uint8_t* payload, size_t len) {
  if (len < 1) return;
  if (payload[0] == TEST_PATTERN_FAULT_INJECT) {
    faultInjectHook();  // no-op unless built with -DFAULT_INJECT*
    return;
  }
  if (payload[0] == TEST_PATTERN_NONE) {
    activeTestPattern = -1;  // leave pendingFrameReady alone: the Pi's next frame is still valid
    return;
  }
  activeTestPattern = payload[0];
  pendingFrameReady = false;
}

void handleBrightness(const uint8_t* payload, size_t len) {
  if (len >= 1) {
    masterBrightness = payload[0];
  }
}

// -------------------------------------------------------------------
// Responses. Byte offsets mirror pi/app/models/protocol.py (the wire SSOT).

void sendCaps(uint32_t reqId) {
  uint8_t payload[56];
  memset(payload, 0, sizeof(payload));
  strncpy((char*)payload, FIRMWARE_VERSION, 16);
  payload[16] = PROTOCOL_VERSION;
  payload[17] = activeOutputs;
  payload[18] = maxLedsPerStrip & 0xFF;
  payload[19] = (maxLedsPerStrip >> 8) & 0xFF;
  strncpy((char*)payload + 20, "BGR", 4);
  // Bytes 24-41 were zero-filled before 1.3.0.
  put32(payload + 24, millis());
  put32(payload + 28, diag.resetReason);
  put32(payload + 32, diag.bootId);
  CrashRecord crash;
  crash_record_current(&crash, inFaultMode());
  payload[36] = crash.valid ? 1 : 0;
  put32(payload + 37, diag.geometryGeneration);
  payload[41] = inFaultMode() ? 1 : 0;
  sendPacket(PKT_CAPS, payload, sizeof(payload), reqId);
}

void sendStats(uint32_t reqId) {
  uint8_t payload[28];
  put32(payload + 0, stats.uptimeMs);
  put32(payload + 4, stats.framesReceived);
  put32(payload + 8, stats.framesApplied);
  put32(payload + 12, stats.badCrc);
  put32(payload + 16, stats.badFrame);
  put32(payload + 20, stats.droppedPending);
  put32(payload + 24, stats.outputFps);
  sendPacket(PKT_STATS, payload, sizeof(payload), reqId);
}

void sendDiag(uint32_t reqId) {
  uint32_t now = millis();
  uint32_t dmaCsrEs = 0, tmr = 0;
  leds.debugState(&dmaCsrEs, &tmr);
  CrashRecord crash;
  crash_record_current(&crash, inFaultMode());
  // In fault mode we run on faultStack (inside .bss), so measure against its base instead.
  uint32_t stackFree = stackFreeEstimate();
  if (inFaultMode()) {
    uint32_t local;
    stackFree = (uint32_t)&local - (uint32_t)faultStack;
  }
  uint32_t usbIrq = (NVIC_IS_ENABLED(IRQ_USB1) ? 1u : 0u) | (NVIC_IS_PENDING(IRQ_USB1) ? 2u : 0u);

  uint32_t f[DIAG_FIELDS] = {
    now,                                   // 0 uptime_ms
    diag.resetReason,                      // 1
    diag.bootId,                           // 2
    diag.rxBytes,                          // 3
    diag.pktOk,                            // 4
    diag.verifyFail[VERIFY_SHORT],         // 5
    diag.verifyFail[VERIFY_MAGIC],         // 6
    diag.verifyFail[VERIFY_VERSION],       // 7
    diag.verifyFail[VERIFY_LEN],           // 8
    diag.verifyFail[VERIFY_CRC],           // 9
    diag.oversize,                         // 10
    diag.emptyPackets,                     // 11
    diag.lastPktType,                      // 12
    now - diag.lastRxByteMs,               // 13
    now - diag.lastGoodPktMs,              // 14
    (uint32_t)decoder.length(),            // 15 live_decoder_out_len
    (uint32_t)decoder.remaining(),         // 16 live_decoder_remaining
    diag.lastReject.reason,                // 17
    diag.lastReject.len,                   // 18
    diag.lastReject.reason ? now - diag.lastReject.ms : 0,  // 19
    diag.loopsPerSec,                      // 20
    diag.maxLoopUs,                        // 21
    diag.maxTxUs,                          // 22
    diag.txDropped,                        // 23
    now - diag.showStartMs,                // 24
    diag.stallActive,                      // 25
    diag.stallLatched,                     // 26
    stackFree,                             // 27
    stats.framesReceived,                  // 28
    stats.framesApplied,                   // 29
    stats.droppedPending,                  // 30 frames_pending_dropped
    diag.octoBegins,                       // 31
    (uint32_t)Serial.available(),          // 32 rx_backlog
    USB1_USBSTS,                           // 33
    usbIrq,                                // 34
    diag.lastOp,                           // 35
    dmaCsrEs,                              // 36
    tmr,                                   // 37
    crash.valid,                           // 38 crash_present
    diag.geometryGeneration,               // 39
    diag.badFraming,                       // 40
    pendingFrameReady ? 1u : 0u,           // 41 frames_pending
    diag.ignoredInFaultMode,               // 42
    faults_lifetime(),                     // 43 fault_count
  };
  uint8_t payload[DIAG_PAYLOAD_SIZE];
  for (int i = 0; i < DIAG_FIELDS; i++) put32(payload + i * 4, f[i]);
  sendPacket(PKT_DIAG, payload, sizeof(payload), reqId);
}

void sendCrash(uint32_t reqId) {
  CrashRecord crash;
  crash_record_current(&crash, inFaultMode());
  sendPacket(PKT_CRASH, (const uint8_t*)&crash, sizeof(crash), reqId);
}

void readPots() {
  for (int i = 0; i < 3; i++) {
    int32_t raw = analogRead(POT_PINS[i]);  // 10-bit default: 0-1023
    if (potFiltered[i] < 0) {
      potFiltered[i] = raw;
    } else {
      potFiltered[i] += (raw - potFiltered[i]) >> 3;  // EMA, alpha = 1/8
    }
  }
}

void sendPots() {
  uint8_t payload[6];
  for (int i = 0; i < 3; i++) {
    // Inverted: pots are wired with 3.3V/GND swapped, so raw max = knob CCW.
    uint16_t v = 1023 - (uint16_t)potFiltered[i];
    payload[i * 2] = v & 0xFF;
    payload[i * 2 + 1] = (v >> 8) & 0xFF;
  }
  sendPacket(PKT_POTS, payload, sizeof(payload), 0);
}

void sendAck(uint32_t reqId, uint32_t generation) {
  uint8_t payload[4];
  put32(payload, generation);
  sendPacket(PKT_CONFIG_ACK, payload, sizeof(payload), reqId);
}

void sendNak(uint32_t reqId) {
  sendPacket(PKT_CONFIG_NAK, nullptr, 0, reqId);
}

// Non-blocking: a host that stopped reading must never stall the loop, so a packet that
// does not fit the free USB TX space is dropped and counted rather than waited on.
void sendPacket(uint8_t type, const uint8_t* payload, size_t len, uint32_t reqId) {
  uint8_t raw[HEADER_SIZE + 256 + CRC_SIZE];  // largest responses: DIAG 176 B, CRASH 96 B
  size_t rawLen = build_packet(type, payload, len, raw, sizeof(raw), reqId);
  if (rawLen == 0) return;

  uint8_t encoded[sizeof(raw) + sizeof(raw) / 254 + 2];
  size_t encLen = cobs_encode(raw, rawLen, encoded, sizeof(encoded) - 1);
  if (encLen == 0) return;
  encoded[encLen++] = 0x00;  // delimiter in the same write: the frame is atomic on the wire

  if (Serial.availableForWrite() < (int)encLen) {
    diag.txDropped++;
    diag.txDroppedThisSecond++;
    return;
  }
  setOp(OP_SEND);
  uint32_t t0 = micros();
  if (Serial.write(encoded, encLen) != encLen) diag.txDropped++;
  uint32_t dt = micros() - t0;
  if (dt > diag.maxTxUs) diag.maxTxUs = dt;
}

// -------------------------------------------------------------------
// LED output

void startShow(ShowKind kind) {
  setOp(OP_SHOW);
  switch (kind) {
    case SHOW_BLACK:
      clearAll();
      blackShown = true;
      break;
    case SHOW_TEST:
      runTestPattern();
      fpsCounter++;
      break;
    case SHOW_FRAME:
      applyPendingFrame();
      pendingFrameReady = false;
      stats.framesApplied++;
      fpsCounter++;
      break;
  }
  leds.show();
  diag.showPending = true;
  diag.showStartMs = millis();
}

void applyPendingFrame() {
  // pendingFrame is packed as contiguous blocks: for each output, ledsPerOutput[ch] * 3 bytes.
  size_t srcOffset = 0;
  for (int ch = 0; ch < TOTAL_OUTPUTS; ch++) {
    uint16_t count = ledsPerOutput[ch];
    for (uint16_t px = 0; px < count; px++) {
      uint8_t r = pendingFrame[srcOffset];
      uint8_t g = pendingFrame[srcOffset + 1];
      uint8_t b = pendingFrame[srcOffset + 2];
      srcOffset += 3;

      if (masterBrightness < 255) {
        r = (uint16_t)r * masterBrightness / 255;
        g = (uint16_t)g * masterBrightness / 255;
        b = (uint16_t)b * masterBrightness / 255;
      }

      leds.setPixel(ch * maxLedsPerStrip + px, r, g, b);
    }
    // Clear remaining pixels on this output (beyond count, up to maxLedsPerStrip)
    for (uint16_t px = count; px < maxLedsPerStrip; px++) {
      leds.setPixel(ch * maxLedsPerStrip + px, 0);
    }
  }
}

void clearAll() {
  for (int i = 0; i < TOTAL_OUTPUTS * (int)maxLedsPerStrip; i++) {
    leds.setPixel(i, 0);
  }
}

void setPixel(int strip, int pixel, uint8_t r, uint8_t g, uint8_t b) {
  if (strip < 0 || strip >= TOTAL_OUTPUTS) return;
  if (pixel < 0 || pixel >= (int)ledsPerOutput[strip]) return;
  int idx = strip * (int)maxLedsPerStrip + pixel;
  leds.setPixel(idx, r, g, b);
}

// -------------------------------------------------------------------
void runTestPattern() {
  uint32_t t = millis();

  clearAll();

  switch (activeTestPattern) {
    case TEST_ALL_BLACK:
      // Already cleared
      break;

    case TEST_ALL_WHITE: {
      uint8_t v = masterBrightness / 4;  // Safe brightness
      for (int ch = 0; ch < (int)activeOutputs; ch++) {
        for (int px = 0; px < (int)ledsPerOutput[ch]; px++) {
          setPixel(ch, px, v, v, v);
        }
      }
      break;
    }

    case TEST_RGB_ORDER: {
      // Cycle through R, G, B every 2 seconds
      int phase = (t / 2000) % 3;
      uint8_t r = (phase == 0) ? 128 : 0;
      uint8_t g = (phase == 1) ? 128 : 0;
      uint8_t b = (phase == 2) ? 128 : 0;
      for (int ch = 0; ch < (int)activeOutputs; ch++) {
        for (int px = 0; px < (int)ledsPerOutput[ch]; px++) {
          setPixel(ch, px, r, g, b);
        }
      }
      break;
    }

    case TEST_CHANNEL_CHASE: {
      // One pixel chase per channel
      for (int ch = 0; ch < (int)activeOutputs; ch++) {
        int pos = (t / 10) % (int)ledsPerOutput[ch];
        uint8_t hue = (ch * 51) % 256;  // Different color per channel
        // Simple hue to RGB
        uint8_t r, g, b;
        if (hue < 85) { r = hue * 3; g = 255 - hue * 3; b = 0; }
        else if (hue < 170) { hue -= 85; r = 255 - hue * 3; g = 0; b = hue * 3; }
        else { hue -= 170; r = 0; g = hue * 3; b = 255 - hue * 3; }
        setPixel(ch, pos, r, g, b);
      }
      break;
    }

    case TEST_PIXEL_CHASE: {
      // Single pixel chasing across all channels
      int totalPixels = (int)totalLeds;
      int pos = (t / 5) % totalPixels;
      // Find which channel and pixel offset
      int remaining = pos;
      for (int ch = 0; ch < (int)activeOutputs; ch++) {
        if (remaining < (int)ledsPerOutput[ch]) {
          setPixel(ch, remaining, 255, 255, 255);
          break;
        }
        remaining -= (int)ledsPerOutput[ch];
      }
      break;
    }

    case TEST_BOTTOM_TO_TOP: {
      // White sweep from bottom to top on each channel
      for (int ch = 0; ch < (int)activeOutputs; ch++) {
        int pos = (t / 15) % (int)ledsPerOutput[ch];
        setPixel(ch, pos, 255, 255, 255);
        // Trail
        for (int trail = 1; trail <= 5; trail++) {
          int tp = pos - trail;
          if (tp >= 0) {
            uint8_t v = 255 - trail * 45;
            setPixel(ch, tp, v, v, v);
          }
        }
      }
      break;
    }

    case TEST_CHANNEL_IDENTIFY: {
      // Light each channel one at a time, cycling
      int activeChannel = (t / 2000) % (int)activeOutputs;
      for (int px = 0; px < (int)ledsPerOutput[activeChannel]; px++) {
        setPixel(activeChannel, px, 0, 128, 0);
      }
      // Show channel number with colored pixels at bottom
      for (int i = 0; i <= activeChannel; i++) {
        setPixel(activeChannel, i, 255, 0, 0);
      }
      break;
    }

    case TEST_HEARTBEAT: {
      // Gentle breathing pulse
      float phase = sin(t / 500.0) * 0.5 + 0.5;
      uint8_t v = (uint8_t)(phase * 64);
      for (int ch = 0; ch < (int)activeOutputs; ch++) {
        for (int px = 0; px < (int)ledsPerOutput[ch]; px++) {
          setPixel(ch, px, 0, v, v / 2);
        }
      }
      break;
    }

    case TEST_STRIP_IDENTIFY: {
      // Each physical strip pair gets a distinct color
      // Even strip (first half) = bright, odd strip (second half) = dimmer
      uint8_t colors[][3] = {
        {255, 0, 0}, {0, 255, 0}, {0, 0, 255}, {255, 255, 0}, {255, 0, 255}
      };
      for (int ch = 0; ch < (int)activeOutputs && ch < 5; ch++) {
        uint16_t half = ledsPerOutput[ch] / 2;
        // First half = "even" strip
        for (int px = 0; px < (int)half; px++) {
          setPixel(ch, px, colors[ch][0], colors[ch][1], colors[ch][2]);
        }
        // Second half = "odd" strip (dimmer)
        for (int px = (int)half; px < (int)ledsPerOutput[ch]; px++) {
          setPixel(ch, px, colors[ch][0] / 4, colors[ch][1] / 4, colors[ch][2] / 4);
        }
      }
      break;
    }

    case TEST_SEAM_MARKER: {
      // Mark the seam channels (first output first half, last output second half)
      float pulse = sin(t / 300.0) * 0.5 + 0.5;
      uint8_t v = (uint8_t)(pulse * 200);

      int lastCh = (int)activeOutputs - 1;
      uint16_t halfFirst = ledsPerOutput[0] / 2;
      uint16_t halfLast = ledsPerOutput[lastCh] / 2;

      // CH0, first half = red
      for (int px = 0; px < (int)halfFirst; px++) {
        setPixel(0, px, v, 0, 0);
      }

      // Last channel, second half = blue
      for (int px = (int)halfLast; px < (int)ledsPerOutput[lastCh]; px++) {
        setPixel(lastCh, px, 0, 0, v);
      }
      break;
    }
  }
}
