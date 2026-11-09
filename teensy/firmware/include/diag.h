#pragma once

// Link-health evidence exported by PKT_DIAG (44 x uint32 LE, see
// pi/app/models/protocol.py DIAG_FIELD_NAMES — that file is the wire SSOT).
// Everything here is counted, never acted on: the Pi decides what a number means.

#include <stdint.h>
#include <stddef.h>

#define DIAG_FIELDS 44
#define DIAG_PAYLOAD_SIZE (DIAG_FIELDS * 4)

// stall_active / stall_latched bits. Active bits clear when the condition clears;
// latched bits are set-only for the life of the boot.
#define STALL_RX       (1u << 0)  // bytes arriving but no valid non-probe packet for >= 2 s
#define STALL_DMA      (1u << 1)  // show() started >= 1 s ago and DMA still incomplete
#define STALL_TX       (1u << 2)  // a TX packet was dropped in the last second
#define STALL_FAULTED  (1u << 3)  // latched only: this boot is running fault_idle_loop()

// last_op breadcrumb: which phase the main loop was in when evidence was taken.
enum LastOp : uint32_t {
  OP_NONE = 0,
  OP_RX = 1,
  OP_HANDLE_FRAME = 2,
  OP_HANDLE_CONFIG = 3,
  OP_GEOMETRY_COMMIT = 4,
  OP_SHOW = 5,
  OP_SEND = 6,
  OP_POTS = 7,
  OP_OCTO_BEGIN = 8,
};

// last_reject_reason: VerifyResult values 1..5 (protocol.h) plus the two COBS-level rejects.
#define REJECT_OVERSIZE          6
#define REJECT_INCOMPLETE_BLOCK  7

struct Diag {
  uint32_t resetReason;        // SRC_SRSR raw at boot (cleared after capture)
  uint32_t bootId;
  uint32_t rxBytes;
  uint32_t pktOk;
  uint32_t verifyFail[6];      // indexed by VerifyResult; [0] unused
  uint32_t oversize;
  uint32_t emptyPackets;
  uint32_t badFraming;         // delimiter arrived while the last COBS code still owed bytes
  uint32_t lastPktType;        // non-probe only: probes read evidence, they must not become it
  uint32_t lastRxByteMs;
  uint32_t lastGoodPktMs;      // non-probe only
  struct {
    uint32_t reason;
    uint32_t len;
    uint32_t ms;
  } lastReject;
  uint32_t loopsPerSec;
  uint32_t loopsThisSecond;
  uint32_t loopStartUs;
  uint32_t maxLoopUs;
  uint32_t maxTxUs;
  uint32_t txDropped;
  uint32_t txDroppedThisSecond;
  bool     showPending;        // show() started and DMA completion not yet observed
  uint32_t showStartMs;
  uint32_t stallActive;
  uint32_t stallLatched;
  uint32_t octoBegins;
  uint32_t geometryGeneration; // increments per committed CONFIG; echoed in CONFIG_ACK
  uint32_t ignoredInFaultMode;
  volatile uint32_t lastOp;    // volatile: read from the fault handler
};

extern Diag diag;

// End of .bss from the linker script; the main stack grows down toward it.
extern "C" unsigned long _ebss;

static inline uint32_t stackFreeEstimate() {
  uint32_t local;
  return (uint32_t)&local - (uint32_t)&_ebss;
}
