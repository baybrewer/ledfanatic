"""Effect benchmark harness — 10-second full-pipeline timing.

Usage:
  python -m tools.bench_effects                     # all effects
  python -m tools.bench_effects --effect matrix_rain # single effect
  python -m tools.bench_effects --frames 120        # quick pass
"""

import argparse
import sys
import time

import numpy as np
from unittest.mock import MagicMock

from pathlib import Path

from app.effects.registry import ALL_EFFECTS
from app.layout import load_layout, compile_layout, pack_frame
from app.core.renderer import _build_gamma_lut

# Load and compile layout for benchmarking
_config_dir = Path(__file__).parent.parent / "config"
_layout_config = load_layout(_config_dir)
_layout = compile_layout(_layout_config)
GRID_WIDTH = _layout.width
GRID_HEIGHT = _layout.height


def _make_state():
  """Synthetic render state with 128 BPM audio."""
  state = MagicMock()
  state._audio_lock_free = {
    'level': 0.5, 'bass': 0.6, 'mid': 0.4, 'high': 0.3,
    'beat': False, 'bpm': 128.0,
  }
  state.audio_level = 0.5
  state.audio_bass = 0.6
  state.audio_mid = 0.4
  state.audio_high = 0.3
  state.audio_beat = False
  state.audio_bpm = 128.0
  state.current_scene = 'bench'
  state.blackout = False
  return state


def bench_one(name, effect_cls, frames, gamma_lut, state):
  """Benchmark a single effect through the full pipeline."""
  native_w = getattr(effect_cls, 'NATIVE_WIDTH', None) or 40
  try:
    eff = effect_cls(width=native_w, height=GRID_HEIGHT)
  except Exception as e:
    return {'name': name, 'error': str(e)}

  t = time.monotonic()
  render_times = []
  post_times = []

  for i in range(frames):
    # Toggle beat every 28 frames (~128 BPM at 60 FPS)
    if i % 28 == 0:
      state.audio_beat = True
      state._audio_lock_free['beat'] = True
    else:
      state.audio_beat = False
      state._audio_lock_free['beat'] = False

    # Render
    r_start = time.perf_counter()
    try:
      internal_frame = eff.render(t, state)
    except Exception as e:
      return {'name': name, 'error': str(e)}
    r_end = time.perf_counter()

    # Post-processing pipeline
    p_start = r_end
    if internal_frame.shape[0] != GRID_WIDTH:
      # Downsample width to grid dimensions via simple area averaging
      factor = internal_frame.shape[0] // GRID_WIDTH
      if factor > 1:
        logical = internal_frame.reshape(GRID_WIDTH, factor, GRID_HEIGHT, 3).mean(axis=1).astype(np.uint8)
      else:
        logical = internal_frame[:GRID_WIDTH]
    else:
      logical = internal_frame
    logical = (logical * 0.8).astype(np.uint8)  # brightness
    logical = gamma_lut[logical]
    _ = pack_frame(logical, _layout)
    p_end = time.perf_counter()

    render_times.append(r_end - r_start)
    post_times.append(p_end - p_start)
    t += 1.0 / 60

  render_ms = [x * 1000 for x in render_times]
  post_ms = [x * 1000 for x in post_times]
  total_ms = [r + p for r, p in zip(render_ms, post_ms)]

  return {
    'name': name,
    'width': native_w,
    'frames': frames,
    'render_avg_ms': np.mean(render_ms),
    'render_p95_ms': np.percentile(render_ms, 95),
    'post_avg_ms': np.mean(post_ms),
    'total_avg_ms': np.mean(total_ms),
    'total_p95_ms': np.percentile(total_ms, 95),
    'total_max_ms': np.max(total_ms),
    'first60_ms': np.mean(total_ms[:60]) if frames >= 60 else np.mean(total_ms),
    'last60_ms': np.mean(total_ms[-60:]) if frames >= 60 else np.mean(total_ms),
    'implied_fps': 1000.0 / np.mean(total_ms) if np.mean(total_ms) > 0 else 9999,
  }


def main():
  parser = argparse.ArgumentParser(description='Effect benchmark harness')
  parser.add_argument('--effect', type=str, help='Single effect name to benchmark')
  parser.add_argument('--frames', type=int, default=600, help='Number of frames (default: 600)')
  parser.add_argument('--csv', action='store_true', help='Output as CSV')
  args = parser.parse_args()

  all_effects = ALL_EFFECTS
  gamma_lut = _build_gamma_lut(2.2)
  state = _make_state()

  # Report layout info
  print(f"Layout: {GRID_WIDTH}x{GRID_HEIGHT} grid, "
        f"{_layout.total_mapped} mapped LEDs, "
        f"{len(_layout_config.outputs)} outputs")
  print(f"Effects: {len(all_effects)} registered")
  print()

  if args.effect:
    if args.effect not in all_effects:
      print(f"Unknown effect: {args.effect}")
      print(f"Available: {', '.join(sorted(all_effects.keys()))}")
      sys.exit(1)
    targets = {args.effect: all_effects[args.effect]}
  else:
    targets = all_effects

  results = []
  for name, cls in sorted(targets.items()):
    result = bench_one(name, cls, args.frames, gamma_lut, state)
    results.append(result)
    if not args.csv:
      if 'error' in result:
        print(f"  {name}: ERROR — {result['error']}")
      else:
        status = "OK" if result['total_p95_ms'] < 16.7 else "SLOW"
        print(f"  {name}: avg={result['total_avg_ms']:.2f}ms "
              f"p95={result['total_p95_ms']:.2f}ms "
              f"first60={result['first60_ms']:.2f}ms "
              f"last60={result['last60_ms']:.2f}ms "
              f"[{status}]")

  if args.csv:
    print("name,width,render_avg_ms,total_avg_ms,total_p95_ms,total_max_ms,"
          "first60_ms,last60_ms,implied_fps,error")
    for r in results:
      if 'error' in r:
        print(f"{r['name']},,,,,,,,{r['error']}")
      else:
        print(f"{r['name']},{r['width']},{r['render_avg_ms']:.3f},"
              f"{r['total_avg_ms']:.3f},{r['total_p95_ms']:.3f},"
              f"{r['total_max_ms']:.3f},{r['first60_ms']:.3f},"
              f"{r['last60_ms']:.3f},{r['implied_fps']:.1f},")

  # Summary
  if not args.csv:
    slow = [r for r in results if 'error' not in r and r['total_p95_ms'] >= 16.7]
    errors = [r for r in results if 'error' in r]
    print(f"\n{len(results)} effects benchmarked, "
          f"{len(slow)} slow (p95 >= 16.7ms), "
          f"{len(errors)} errors")


if __name__ == '__main__':
  main()
