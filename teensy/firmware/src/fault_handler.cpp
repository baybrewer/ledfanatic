/*
 * Fault path: park LEDs, preserve the record, drop into fault-idle (or halt). Never reset.
 *
 * The core's unused_interrupt_vector() reboots the board 8 s after a fault, which is
 * the one thing this project must never do on its own (the Pi owns resets). Every
 * vector that still points at it is redirected to fault_halt below.
 *
 * Nothing on the recording path may execute a floating-point instruction: with lazy
 * FP state preservation pending, the first FP instruction writes the FP frame to the
 * ORIGINAL (possibly damaged) stack. fault_halt and fault_record are therefore compiled
 * general-regs-only and test_native/run.sh disassembles them to prove it.
 */

#include <Arduino.h>
#include <imxrt.h>
#include <OctoWS2811.h>
#include <string.h>
#include "fault_handler.h"
#include "diag.h"

extern OctoWS2811 leds;                       // main.cpp
extern "C" void unused_interrupt_vector(void);  // startup.c: the core's rebooting handler

// Top-of-OCRAM crash area shared with the core / CrashReport (imxrt.h:10396).
#define FAULT_RECORD_ADDR  0x2027FF80u
#define FAULT_EXT_ADDR     (FAULT_RECORD_ADDR + sizeof(CrashCore))   // 0x2027FFAC
#define FAULT_AREA_BYTES   (sizeof(CrashCore) + sizeof(CrashExt))    // 80, spans 3 cache lines
#define CORE_RECORD_WORDS  (sizeof(CrashCore) / 4)                   // 11; the core stores len = 11

// Emergency stack in DTCM (.bss): the original stack may be what faulted. The top
// FAULT_STACK_FRAME_RESERVE bytes are never used by fault_record so the fresh exception
// frame built there cannot be overwritten by fault_record's own locals.
#define FAULT_STACK_BYTES          4096
#define FAULT_STACK_FRAME_RESERVE  64
extern "C" {
  uint32_t faultStack[FAULT_STACK_BYTES / 4] __attribute__((aligned(8)));
}

// --- Retained across soft resets (OCRAM .dmabuffers is NOLOAD: never zeroed by startup) ---
#define BOOT_MAGIC 0x4C454446u  // "LEDF"
struct RetainedBoot {
  uint32_t magic;
  uint32_t counter;
  uint32_t counterInv;
  uint32_t faultsLifetime;
  uint32_t faultsLifetimeInv;
  uint32_t seed;              // drawn once per power-on so two power-ons never share a boot_id
  uint32_t pad[2];
};
static_assert(sizeof(RetainedBoot) == 32, "one cache line: a single flush covers it");
DMAMEM static RetainedBoot bootGen __attribute__((aligned(32)));

static volatile uint32_t faultsThisBoot = 0;   // plain .bss: the per-boot recovery guard
volatile uint32_t cachedTempBits = 0;
static CrashRecord bootCrash;                  // copy of the previous boot's record

// Same algorithm as startup.c / CrashReport.cpp::isvalid (and protocol.py core_fault_record_crc).
static uint32_t core_record_crc(const volatile uint32_t* words) {
  uint32_t crc = 0xFFFFFFFF;
  for (uint32_t i = 0; i < CORE_RECORD_WORDS - 1; i++) {
    crc ^= words[i];
    for (int bit = 0; bit < 32; bit++) crc = (crc >> 1) ^ (crc & 1) * 0xEDB88320;
  }
  return crc;
}

static bool core_record_valid(const volatile uint32_t* words) {
  return words[0] == CORE_RECORD_WORDS && words[CORE_RECORD_WORDS - 1] == core_record_crc(words);
}

static void copy_ram_record(CrashRecord* out) {
  const volatile uint32_t* core = (const volatile uint32_t*)FAULT_RECORD_ADDR;
  const volatile uint32_t* ext = (const volatile uint32_t*)FAULT_EXT_ADDR;
  uint32_t* coreOut = (uint32_t*)&out->core;
  uint32_t* extOut = (uint32_t*)&out->ext;
  for (uint32_t i = 0; i < CORE_RECORD_WORDS; i++) coreOut[i] = core[i];
  for (uint32_t i = 0; i < sizeof(CrashExt) / 4; i++) extOut[i] = ext[i];
}

// --- Boot-time API ---------------------------------------------------------------

void crash_record_capture_at_boot(uint32_t srcGpr5, uint32_t srcSrsr) {
  volatile uint32_t* core = (volatile uint32_t*)FAULT_RECORD_ADDR;
  memset(&bootCrash, 0, sizeof(bootCrash));
  bootCrash.srcGpr5 = srcGpr5;
  bootCrash.srcSrsr = srcSrsr;
  if (core_record_valid(core)) {
    copy_ram_record(&bootCrash);
    bootCrash.valid = 1;
    bootCrash.source = CRASH_SOURCE_PREV_BOOT;
  }
  // Invalidate so the next boot does not re-report it. OCRAM is write-back cached: without
  // the flush the stale valid record would still be what the next boot reads.
  core[0] = 0;
  core[CORE_RECORD_WORDS - 1] = 0;
  arm_dcache_flush_delete((void*)FAULT_RECORD_ADDR, FAULT_AREA_BYTES);
}

void crash_record_current(CrashRecord* out, bool liveFault) {
  if (liveFault) {
    const volatile uint32_t* core = (const volatile uint32_t*)FAULT_RECORD_ADDR;
    memset(out, 0, sizeof(*out));
    out->srcGpr5 = bootCrash.srcGpr5;
    out->srcSrsr = bootCrash.srcSrsr;
    if (core_record_valid(core)) {
      copy_ram_record(out);
      out->valid = 1;
      out->source = CRASH_SOURCE_LIVE;
    }
    return;
  }
  *out = bootCrash;
}

uint32_t boot_retained_init(uint32_t seed) {
  bool counterValid = bootGen.magic == BOOT_MAGIC && bootGen.counter == ~bootGen.counterInv;
  if (!counterValid) {
    // Power-on (or corrupted): start a new lineage.
    bootGen.counter = 0;
    bootGen.seed = seed;
    bootGen.faultsLifetime = 0;
  } else if (bootGen.faultsLifetime != ~bootGen.faultsLifetimeInv) {
    bootGen.faultsLifetime = 0;  // evidence only; a bad complement just loses the count
  }
  bootGen.counter++;
  bootGen.counterInv = ~bootGen.counter;
  bootGen.faultsLifetimeInv = ~bootGen.faultsLifetime;
  bootGen.magic = BOOT_MAGIC;
  arm_dcache_flush_delete(&bootGen, sizeof(bootGen));
  return (bootGen.counter << 12) ^ (bootGen.seed & 0xFFF);
}

uint32_t faults_this_boot() { return faultsThisBoot; }
uint32_t faults_lifetime() {
  return bootGen.faultsLifetime == ~bootGen.faultsLifetimeInv ? bootGen.faultsLifetime : 0;
}

// --- Fault path ------------------------------------------------------------------

// Returns the fresh exception frame to resume on (thread-mode first fault) or never returns.
extern "C" __attribute__((target("general-regs-only"), used, noinline))
uint32_t* fault_record(uint32_t* frame, uint32_t excReturn) {
  // Stop the waveform first: DMA driven from a dead main loop would keep clocking stale
  // buffer halves onto every strip (residual (d): at most one torn pixel per output).
  leds.parkOutputsIdle();

  uint32_t cfsr = SCB_CFSR;
  uint32_t framePtr = (uint32_t)frame;
  // The frame is only trustworthy if stacking itself did not fault and it lies inside
  // DTCM or OCRAM with the full 32 bytes in bounds.
  bool stackingFault = (cfsr & (0x10u | 0x1000u)) != 0;   // MSTKERR | STKERR
  bool inDtcm = framePtr >= 0x20000000u && framePtr <= 0x20080000u - 32u;
  bool inOcram = framePtr >= 0x20200000u && framePtr <= 0x20280000u - 32u;
  bool frameUsable = !stackingFault && (inDtcm || inOcram) && (framePtr & 3u) == 0;

  uint32_t ipsr;
  asm volatile("mrs %0, ipsr" : "=r"(ipsr));

  volatile uint32_t* core = (volatile uint32_t*)FAULT_RECORD_ADDR;
  core[0] = CORE_RECORD_WORDS;
  core[1] = ipsr;
  core[2] = cfsr;
  core[3] = SCB_HFSR;
  core[4] = SCB_MMFAR;
  core[5] = SCB_BFAR;
  core[6] = frameUsable ? frame[6] : 0;   // stacked pc
  core[7] = frameUsable ? frame[7] : 0;   // stacked xpsr
  core[8] = cachedTempBits;               // never tempmonGetTemp() here (FP)
  core[9] = rtc_get();
  core[10] = core_record_crc(core);

  volatile uint32_t* ext = (volatile uint32_t*)FAULT_EXT_ADDR;
  for (uint32_t i = 0; i < 8; i++) ext[i] = frameUsable ? frame[i] : 0;
  ext[8] = excReturn;                     // mode/stack/FP-frame type survive even a stacking fault
  arm_dcache_flush_delete((void*)FAULT_RECORD_ADDR, FAULT_AREA_BYTES);

  faultsThisBoot = faultsThisBoot + 1;
  if (bootGen.faultsLifetime == ~bootGen.faultsLifetimeInv) {
    bootGen.faultsLifetime++;
    bootGen.faultsLifetimeInv = ~bootGen.faultsLifetime;
    arm_dcache_flush_delete(&bootGen, sizeof(bootGen));
  }

  // Decide from EXC_RETURN only (bit 3: 1 = thread mode), never from the frame.
  bool threadMode = (excReturn & 0x8u) != 0;
  if (threadMode && faultsThisBoot == 1) {
    SCB_CFSR = SCB_CFSR;   // sticky w1c status: clear so the next fault's record is clean
    SCB_HFSR = SCB_HFSR;

    // Always resume on a fresh frame at the top of faultStack: a readable frame does not
    // prove the original stack has room left (an exhausted stack presents a clean frame and
    // overflows again the moment sendPacket allocates its buffers).
    uint32_t* fresh = faultStack + FAULT_STACK_BYTES / 4 - 8;
    for (int i = 0; i < 6; i++) fresh[i] = 0;          // r0-r3, r12, lr
    fresh[6] = (uint32_t)&fault_idle_loop & ~1u;         // pc; T comes from xPSR bit 24
    fresh[7] = 0x01000000u;                              // Thumb, no IT/ICI, no stack-align pad

    // A basic-frame return must not leave lazy FP state pending for the dead stack.
    SCB_FPCCR &= ~1u;                                    // LSPACT
    asm volatile(
      "mrs r0, control\n"
      "bic r0, r0, #4\n"                                 // FPCA
      "msr control, r0\n"
      "isb\n" ::: "r0", "memory");
    return fresh;
  }

  // Fault inside an ISR, or a second fault this boot: halt with interrupts off and the
  // record preserved (residual (b): recovery is the USB power-cycle button).
  for (;;) asm volatile("wfi");
}

// Naked entry shim. An ordinary C++ prologue would clobber LR (EXC_RETURN) and push onto
// the stack that may have just faulted, so the frame pointer and EXC_RETURN are captured
// and MSP is moved to faultStack before any C code runs.
extern "C" __attribute__((naked, target("general-regs-only"), used))
void fault_halt(void) {
  asm volatile(
    "cpsid i\n"
    "mov   r1, lr\n"                 // EXC_RETURN
    "tst   lr, #4\n"                 // bit 2: which stack holds the frame
    "ite   eq\n"
    "mrseq r0, msp\n"
    "mrsne r0, psp\n"
    "ldr   r2, =faultStack+%c0\n"
    "msr   msp, r2\n"
    "isb\n"
    "bl    fault_record\n"           // r0 = fresh frame (or never returns)
    "msr   msp, r0\n"
    "ldr   lr, =0xFFFFFFF9\n"        // thread mode, MSP, basic frame
    "bx    lr\n"
    ".ltorg\n"
    :: "i"(FAULT_STACK_BYTES - FAULT_STACK_FRAME_RESERVE));
}

void install_fault_handlers() {
  // Covers NMI, HardFault, MemManage, BusFault, UsageFault, SVC, reserved slots and every
  // unattached IRQ. PendSV/SysTick are core-owned and never point at the default handler.
  for (unsigned i = 1; i < NVIC_NUM_INTERRUPTS + 16; i++) {
    if (_VectorsRam[i] == unused_interrupt_vector) _VectorsRam[i] = fault_halt;
  }
  // tempmon's Panic_Temp_isr calls the rebooting handler directly; a thermal panic must
  // still park the outputs and halt instead.
  _VectorsRam[IRQ_TEMPERATURE_PANIC + 16] = fault_halt;
  asm volatile("dsb\nisb" ::: "memory");
}
