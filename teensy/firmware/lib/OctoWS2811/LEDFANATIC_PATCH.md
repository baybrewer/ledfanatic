# OctoWS2811 — vendored and patched for LED Fanatic

Source: OctoWS2811 1.5 as shipped in PlatformIO `framework-arduinoteensy` 1.162.0
(`~/.platformio/packages/framework-arduinoteensy/libraries/OctoWS2811`). Only the
library sources and metadata were copied; `examples/`, `docs/` and `extras/` were not.
The library was removed from `platformio.ini` `lib_deps` so this copy is the one that builds.

Every change is marked `LEDFANATIC PATCH` in the source. All patches are Teensy 4.x
(`OctoWS2811_imxrt.cpp`) only; the Teensy 3.x code path is untouched.

## 1. `busy()` ignored unfinished DMA

Upstream:

```c
if (!dma3.complete()) ; // DMA still running
```

The empty statement meant `busy()` returned 0 while the transfer was still in flight.
The firmware then called `show()`, whose first line spins `while (!dma3.complete())`
with no bound — if DMA ever stalls, the main loop (and USB RX) stalls with it.

Patched: `if (!dma3.complete()) return 1;` so the firmware can defer the show and
report a DMA stall instead of hanging.

## 2. `debugState(uint32_t *csr_es, uint32_t *tmr)`

Exports raw hardware state for the firmware's DIAG packet so a wedged LED path can be
diagnosed from the Pi:

- `*csr_es` = `dma3.TCD->CSR` in bits 31..16, `DMA_ES` bits 14..0 in bits 14..0, and
  `DMA_ES.VLD` (bit 31) in bit 15. `DMA_ES.ECX` (bit 16) is dropped because the firmware
  never cancels a transfer.
- `*tmr` = `TMR4_ENBL` in bits 31..16, `TMR4_SCTRL0` in bits 15..0.

## 3. `parkOutputsIdle()`

Called from the firmware's CPU fault handler. Disables all three DMA channels, disables
the TMR4 channels and forces their outputs low, then writes the library's own
`bitmask[]` to `GPIO1..4_DR_CLEAR` so every data line Octo drives rests at logic 0.
The pins are in standard GPIO mode (`begin()` clears their `IOMUXC_GPR_GPR26..29` bits), so
GPIO1–4 — not the fast GPIO6–9 aliases — are the registers that drive them.

Compiled with `__attribute__((target("general-regs-only")))`: in a fault handler, lazy FP
state preservation may still be pending and the first FP instruction would write the FP
frame onto the original, possibly damaged, stack.

Residual: a fault mid-transfer stops the waveform where it is; the single pixel whose bit
stream was cut may decode to an arbitrary colour. Accepted (see the link-reliability plan).

## Re-vendoring

If upstream OctoWS2811 is updated, re-copy the sources and re-apply the three patches
above (search this file's section titles). Diff against upstream:

```sh
diff -u ~/.platformio/packages/framework-arduinoteensy/libraries/OctoWS2811/OctoWS2811_imxrt.cpp \
        teensy/firmware/lib/OctoWS2811/OctoWS2811_imxrt.cpp
```
