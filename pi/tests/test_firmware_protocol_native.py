"""Runs the firmware's host-side protocol harness (ASan/UBSan) and cross-checks the C++
COBS encoder against the Python decoder. Skips when no C++ compiler is available."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.models.protocol import cobs_decode, cobs_encode

REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_SH = REPO_ROOT / "teensy" / "firmware" / "test_native" / "run.sh"

pytestmark = pytest.mark.skipif(
  shutil.which("g++") is None, reason="g++ not available for the native firmware harness"
)


def _run_harness(*args: str) -> subprocess.CompletedProcess:
  return subprocess.run(
    ["bash", str(RUN_SH), *args],
    cwd=REPO_ROOT,
    capture_output=True,
    text=True,
    timeout=300,
  )


def test_native_harness_passes():
  result = _run_harness()
  assert result.returncode == 0, f"harness failed:\n{result.stdout}\n{result.stderr}"
  # run.sh prints "OK <n>" for the protocol cases, then an FP-check line for the fault path
  match = re.search(r"^OK (\d+)$", result.stdout, re.MULTILINE)
  assert match, f"expected an 'OK <n>' line, got:\n{result.stdout}"
  assert int(match.group(1)) > 0


def test_cpp_encoder_matches_python():
  """C++ encode -> Python decode, and byte-identical to the Python encoder."""
  result = _run_harness("--emit-vectors")
  assert result.returncode == 0, result.stderr
  vectors = [line.split(" ") for line in result.stdout.strip().splitlines()]
  assert len(vectors) >= 20

  for raw_hex, encoded_hex in vectors:
    raw = b"" if raw_hex == "-" else bytes.fromhex(raw_hex)
    encoded = bytes.fromhex(encoded_hex)
    assert b"\x00" not in encoded
    assert cobs_decode(encoded) == raw, f"Python could not decode C++ output for {raw_hex[:40]}"
    assert cobs_encode(raw) == encoded, f"encoders diverge for {raw_hex[:40]}"
