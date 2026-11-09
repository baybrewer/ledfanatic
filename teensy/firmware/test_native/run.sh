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
