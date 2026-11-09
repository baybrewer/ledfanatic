#pragma once

// CPU-fault path and retained-RAM bookkeeping (boot id, lifetime fault count, crash
// record). Nothing in here ever resets the board: a fault parks the LED outputs,
// preserves the evidence and either drops into fault_idle_loop() (thread-mode first
// fault — USB stays alive so the Pi reads the record and the UI reset works) or
// halts with interrupts off (fault inside an ISR, or a second fault this boot).

#include <stdint.h>

// --- Crash record served by PKT_CRASH (96 bytes on the wire) ---
// Layout mirrors pi/app/models/protocol.py parse_crash_payload exactly. All members are
// uint32 so the natural layout has no padding (the static_asserts below pin it).
struct CrashCore {   // the core's arm_fault_info_struct, imxrt.h:10397
  uint32_t len;
  uint32_t ipsr;
  uint32_t cfsr;
  uint32_t hfsr;
  uint32_t mmfar;
  uint32_t bfar;
  uint32_t ret;
  uint32_t xpsr;
  uint32_t tempBits;   // float bits; written as uint32 so the recorder has no FP instruction
  uint32_t time;
  uint32_t crc;
};

struct CrashExt {    // our extension, stored right after the core struct
  uint32_t r0, r1, r2, r3, r12, lr, pc, xpsr;
  uint32_t excReturn;  // always the shim's captured EXC_RETURN, even when the frame was unusable
};

struct CrashRecord {
  uint32_t valid;      // 1 when the core CRC validated at capture time
  uint32_t source;     // 1 = live (this boot faulted), 2 = previous boot, 0 = none
  uint32_t srcGpr5;
  uint32_t srcSrsr;
  CrashCore core;
  CrashExt ext;
};
static_assert(sizeof(CrashCore) == 44, "core record must match imxrt.h arm_fault_info_struct");
static_assert(sizeof(CrashExt) == 36, "extension layout is a wire contract");
static_assert(sizeof(CrashRecord) == 96, "CRASH payload is 96 bytes");

#define CRASH_SOURCE_NONE      0u
#define CRASH_SOURCE_LIVE      1u
#define CRASH_SOURCE_PREV_BOOT 2u

// Must be the first statement of setup(): every vector still pointing at the core's
// rebooting handler is replaced, then the temperature-panic vector (tempmon calls
// that handler directly) is overridden too.
void install_fault_handlers();

// Called once in setup() before anything else touches the top of OCRAM: copies the
// previous boot's record if its CRC validates, then invalidates it in RAM (and flushes
// the cache line, otherwise the next boot would still see it).
void crash_record_capture_at_boot(uint32_t srcGpr5, uint32_t srcSrsr);

// Fills out with what PKT_CRASH should carry right now: the live RAM record when this
// boot is in fault-idle mode, otherwise the copy taken at boot.
void crash_record_current(CrashRecord* out, bool liveFault);

// Retained boot generation: validates the DMAMEM counter by magic + complement, bumps it
// (re-seeding from `seed` after power-on), flushes, and returns boot_id.
uint32_t boot_retained_init(uint32_t seed);

// Evidence-only counters.
uint32_t faults_this_boot();
uint32_t faults_lifetime();

// uint32 bits of the last completed TEMPMON reading (non-blocking refreshCachedTemp(),
// once a second in the main loop) so the fault recorder never executes a floating-point
// instruction and never waits on the sensor.
extern volatile uint32_t cachedTempBits;

// Emergency stack the fault path and fault_idle_loop() run on (fault_handler.cpp).
extern "C" uint32_t faultStack[];

// Defined in main.cpp: bounded RX loop restricted to PING/DIAG/CRASH/HELLO plus the 1 Hz
// DIAG push. Entered via exception return on faultStack; never returns.
extern "C" void fault_idle_loop(void) __attribute__((noreturn));
