#!/bin/bash
# Build the Teensy firmware on this machine, flash it through the Pi, and verify the
# running firmware reports the version compiled into include/config.h.
# Usage: pi/scripts/flash_teensy.sh [pi-hostname-or-ip]
#
# Requires on the Pi: teensy_loader_cli (sudo), the ledfanatic systemd service.
# Override the serial device with TEENSY_DEV=/dev/ttyACMn if it is not ttyACM0.

set -euo pipefail

PI_HOST="${1:-ledfanatic.local}"
PI_USER="jim"
REPO_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
FW_DIR="${REPO_DIR}/teensy/firmware"
PIO="${PIO:-$HOME/.platformio/penv/bin/pio}"
HEX="${FW_DIR}/.pio/build/teensy41/firmware.hex"
REMOTE_HEX="/tmp/ledfanatic-firmware.hex"
TEENSY_DEV="${TEENSY_DEV:-/dev/ttyACM0}"
VERIFY_TIMEOUT_S=30

# config.h is the single source of the version string; the check below compares against it.
EXPECTED_VERSION="$(sed -n 's/^#define FIRMWARE_VERSION[[:space:]]*"\([^"]*\)".*/\1/p' "${FW_DIR}/include/config.h")"
if [[ -z "${EXPECTED_VERSION}" ]]; then
  echo "flash_teensy: FIRMWARE_VERSION not found in ${FW_DIR}/include/config.h" >&2
  exit 1
fi

echo "Building firmware ${EXPECTED_VERSION}..."
"${PIO}" run -d "${FW_DIR}"
[[ -f "${HEX}" ]] || { echo "flash_teensy: ${HEX} not produced" >&2; exit 1; }

echo "Copying hex to ${PI_USER}@${PI_HOST}..."
scp -q "${HEX}" "${PI_USER}@${PI_HOST}:${REMOTE_HEX}"

echo "Flashing on the Pi..."
ssh "${PI_USER}@${PI_HOST}" "TEENSY_DEV='${TEENSY_DEV}' REMOTE_HEX='${REMOTE_HEX}' bash -s" <<'REMOTE'
set -euo pipefail
if ! command -v teensy_loader_cli >/dev/null 2>&1; then
  echo "flash_teensy: teensy_loader_cli is not installed on the Pi" >&2
  exit 1
fi

# The service owns the serial port; it must let go before HalfKay can be requested.
# Whatever happens after this point (loader timeout, error, Ctrl-C), the Pi must not be
# left with its frame service stopped — the EXIT trap restarts it and keeps the failure
# status so the caller still sees a bad flash.
sudo systemctl stop ledfanatic
trap 'rc=$?; sudo systemctl start ledfanatic || true; exit $rc' EXIT

# Opening the CDC port at 134 baud is the Teensy's "reboot to bootloader" request.
# It may already be in the bootloader (no ttyACM) — then there is nothing to ask. If the
# port exists but refuses the touch, the loader would wait on a Teensy that was never
# asked to reboot, so fail now instead.
if [[ -e "${TEENSY_DEV}" ]]; then
  if ! stty -F "${TEENSY_DEV}" 134; then
    echo "flash_teensy: bootloader request on ${TEENSY_DEV} failed" >&2
    exit 1
  fi
  sleep 1
fi

# -w waits for the bootloader to enumerate (forever by itself), -v reports progress.
# A bounded wait is what turns "Teensy never showed up" into a reported failure.
if ! sudo timeout 60 teensy_loader_cli --mcu=TEENSY41 -w -v "${REMOTE_HEX}"; then
  echo "flash_teensy: teensy_loader_cli failed or timed out after 60s" >&2
  exit 1
fi
REMOTE

echo "Waiting for firmware ${EXPECTED_VERSION} to report via /api/system/status..."
deadline=$((SECONDS + VERIFY_TIMEOUT_S))
reported=""
while (( SECONDS < deadline )); do
  reported="$(curl -fsS --max-time 3 "http://${PI_HOST}/api/system/status" 2>/dev/null \
    | python3 -c 'import json,sys
try:
  d = json.load(sys.stdin)
  print(((d.get("transport") or {}).get("caps") or {}).get("firmware_version") or "")
except Exception:
  print("")' 2>/dev/null || true)"
  if [[ "${reported}" == "${EXPECTED_VERSION}" ]]; then
    echo "firmware: ${reported} OK"
    exit 0
  fi
  sleep 1
done

echo "flash_teensy: expected firmware ${EXPECTED_VERSION}, status reports '${reported:-none}' after ${VERIFY_TIMEOUT_S}s" >&2
exit 1
