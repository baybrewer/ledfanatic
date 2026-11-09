"""
Soak acceptance for the Teensy link (plan Task 6).

Samples the 1 Hz pushed DIAG via GET /api/teensy/diag for --minutes and
evaluates deltas over same-boot intervals. Blackout intervals only have to
satisfy the backlog/stall/boot criteria (frames are received but not applied
by design there).

  python -m tools.soak_check --host ledfanatic.local --minutes 120 [--blackout]
"""

import argparse
import json
import sys
import time
import urllib.request

ERROR_COUNTERS = ('bad_short', 'bad_magic', 'bad_version', 'bad_len', 'bad_crc',
                  'oversize', 'bad_framing', 'tx_dropped')
MAX_RX_BACKLOG = 8192
MAX_LOOP_US = 20000
MAX_TX_US = 5000


def fetch(host: str, path: str):
  with urllib.request.urlopen(f"http://{host}{path}", timeout=5) as resp:
    return json.load(resp)


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument('--host', default='ledfanatic.local')
  ap.add_argument('--minutes', type=float, default=120)
  ap.add_argument('--blackout', action='store_true',
                  help='frames are not applied during blackout; skip the apply criteria')
  args = ap.parse_args()

  deadline = time.time() + args.minutes * 60
  incidents_before = fetch(args.host, '/api/teensy/incidents')['count']
  first = prev = None
  failures = []
  maxima = {'rx_backlog': 0, 'max_loop_us': 0, 'max_tx_us': 0}
  samples = 0

  def fail(msg):
    if msg not in failures:
      failures.append(msg)
      print(f"FAIL {time.strftime('%H:%M:%S')} {msg}", flush=True)

  while time.time() < deadline:
    try:
      d = fetch(args.host, '/api/teensy/diag')
    except Exception as e:                       # 503 before first push, network blip
      fail(f"diag unavailable: {e}")
      time.sleep(1)
      continue
    diag, age = d['diag'], d.get('age_s', 0)
    samples += 1
    if age > 3:
      fail(f"pushed DIAG stale ({age:.1f}s)")
    if diag.get('stall_active_names'):
      fail(f"stall_active={diag['stall_active_names']}")
    for key in maxima:
      maxima[key] = max(maxima[key], diag.get(key, 0))
    if diag.get('rx_backlog', 0) >= MAX_RX_BACKLOG:
      fail(f"rx_backlog {diag['rx_backlog']}")
    if diag.get('max_loop_us', 0) >= MAX_LOOP_US:
      fail(f"max_loop_us {diag['max_loop_us']}")
    if diag.get('max_tx_us', 0) >= MAX_TX_US:
      fail(f"max_tx_us {diag['max_tx_us']}")

    if prev is not None:
      if diag['boot_id'] != prev['boot_id']:
        fail(f"boot_id changed {prev['boot_id']} -> {diag['boot_id']} (reboot)")
        first = None
      else:
        for key in ERROR_COUNTERS:
          if diag.get(key, 0) != prev.get(key, 0):
            fail(f"{key} grew {prev.get(key, 0)} -> {diag[key]}")
        if not args.blackout:
          d_recv = diag['frames_received'] - prev['frames_received']
          d_appl = diag['frames_applied'] - prev['frames_applied']
          d_drop = diag['frames_pending_dropped'] - prev['frames_pending_dropped']
          d_pend = diag['frames_pending'] - prev['frames_pending']
          if d_drop:
            fail(f"frames_pending_dropped grew by {d_drop}")
          if d_recv != d_appl + d_drop + d_pend:
            fail(f"frame accounting: recv {d_recv} != applied {d_appl} + dropped {d_drop} + pending {d_pend}")
    if first is None:
      first = diag
    prev = diag
    if samples % 300 == 0:
      print(f"{time.strftime('%H:%M:%S')} {samples} samples, maxima {maxima}, failures {len(failures)}", flush=True)
    time.sleep(1)

  incidents_after = fetch(args.host, '/api/teensy/incidents')['count']
  if incidents_after != incidents_before:
    fail(f"{incidents_after - incidents_before} new incident(s) during soak")

  print(json.dumps({'samples': samples, 'maxima': maxima, 'failures': failures,
                    'boot_id': prev['boot_id'] if prev else None}, indent=1))
  print('PASS' if not failures else 'FAIL')
  return 0 if not failures else 1


if __name__ == '__main__':
  sys.exit(main())
