#!/bin/bash
# Build and run the host-side protocol harness under ASan/UBSan.
# Usage: teensy/firmware/test_native/run.sh [harness args...]   (e.g. --emit-vectors)
# Prints "OK <n>" on success; any sanitizer report or failed CHECK exits nonzero.

set -euo pipefail

FW_DIR="$(cd "$(dirname "$0")/.." && pwd)"
# Lives under .pio/ so it is already gitignored.
BUILD_DIR="${FW_DIR}/.pio/native_test"
BIN="${BUILD_DIR}/test_protocol"
CXX="${CXX:-g++}"

mkdir -p "${BUILD_DIR}"

# -DNATIVE_TEST: protocol.h / config.h stay Arduino-free; the define is the contract that
# keeps it that way if an Arduino include is ever added behind a guard.
"${CXX}" -std=gnu++17 -g -O1 -fno-omit-frame-pointer \
  -fsanitize=address,undefined -fno-sanitize-recover=all \
  -Wall -Wextra \
  -DNATIVE_TEST \
  -I "${FW_DIR}/include" \
  "${FW_DIR}/src/protocol.cpp" \
  "${FW_DIR}/test_native/test_protocol.cpp" \
  -o "${BIN}"

# halt_on_error so a sanitizer finding is the exit status, not a log line.
ASAN_OPTIONS="${ASAN_OPTIONS:-halt_on_error=1:detect_leaks=0}" \
UBSAN_OPTIONS="${UBSAN_OPTIONS:-halt_on_error=1:print_stacktrace=1}" \
  "${BIN}" "$@"

# --- Fault-path FP check ---
# The fault recording path must contain no floating-point instruction: with lazy FP
# state preservation pending, the first FP instruction writes the FP frame to the
# original (possibly damaged) stack. Disassemble the firmware ELF (if a build exists)
# and fail on any v* mnemonic in the shim, the recorder, the Octo park routine and the
# RTC read it calls.
ELF="${FW_DIR}/.pio/build/teensy41/firmware.elf"
OBJDUMP="${OBJDUMP:-${HOME}/.platformio/packages/toolchain-gccarmnoneeabi-teensy/bin/arm-none-eabi-objdump}"
if [[ -f "${ELF}" && -x "${OBJDUMP}" ]]; then
  fp_fail=0
  for sym in fault_halt fault_record _ZN9OctoWS281115parkOutputsIdleEv rtc_get; do
    listing="$("${OBJDUMP}" -d --disassemble="${sym}" "${ELF}")"
    if ! grep -q "<${sym}>:" <<<"${listing}"; then
      echo "FP check: symbol ${sym} not found in ${ELF}" >&2
      fp_fail=1
      continue
    fi
    # objdump rows: "  addr:<tab>hex bytes<tab>mnemonic ..." — match the mnemonic column only.
    if grep -E $':\t[0-9a-f ]+\tv[a-z]' <<<"${listing}"; then
      echo "FP check: floating-point instruction in ${sym}" >&2
      fp_fail=1
    fi
  done
  if (( fp_fail )); then
    exit 1
  fi
  echo "FP check: fault path is general-regs-only OK"
else
  echo "FP check: skipped (no firmware.elf or objdump)"
fi
