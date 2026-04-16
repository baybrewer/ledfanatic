"""Color palettes — ported from led_sim.py.

10 standard palettes, 17 Feldstein custom palettes, and fire palette.
"""

import numpy as np

from .color import clamp

# ─── Palette builder ─────────────────────────────────────────────


def _make_pal(stops):
  """Build 256-entry palette from gradient stops [(pos, r, g, b), ...]."""
  pal = [(0, 0, 0)] * 256
  for i in range(256):
    t = i / 255.0
    for j in range(len(stops) - 1):
      if stops[j][0] <= t <= stops[j + 1][0]:
        s0, s1 = stops[j], stops[j + 1]
        f = (t - s0[0]) / max(0.001, s1[0] - s0[0])
        pal[i] = (
          clamp(s0[1] + f * (s1[1] - s0[1])),
          clamp(s0[2] + f * (s1[2] - s0[2])),
          clamp(s0[3] + f * (s1[3] - s0[3])),
        )
        break
  return pal


# ─── 10 standard palettes ────────────────────────────────────────

PALETTES = [
  ("Rainbow", _make_pal([(0, 255, 0, 0), (0.16, 255, 160, 0), (0.33, 255, 255, 0), (0.5, 0, 255, 0), (0.66, 0, 0, 255), (0.83, 160, 0, 255), (1, 255, 0, 0)])),
  ("Ocean", _make_pal([(0, 0, 0, 30), (0.25, 0, 40, 150), (0.5, 0, 120, 200), (0.75, 60, 200, 255), (1, 180, 255, 255)])),
  ("Sunset", _make_pal([(0, 30, 0, 50), (0.2, 150, 0, 80), (0.4, 255, 40, 0), (0.6, 255, 150, 0), (0.8, 255, 220, 50), (1, 255, 255, 150)])),
  ("Forest", _make_pal([(0, 0, 15, 0), (0.3, 0, 80, 20), (0.6, 40, 180, 40), (0.8, 120, 220, 60), (1, 200, 255, 120)])),
  ("Lava", _make_pal([(0, 15, 0, 0), (0.2, 120, 0, 0), (0.4, 255, 60, 0), (0.65, 255, 180, 0), (0.85, 255, 255, 80), (1, 255, 255, 220)])),
  ("Ice", _make_pal([(0, 0, 0, 30), (0.25, 20, 60, 160), (0.5, 80, 160, 255), (0.75, 180, 220, 255), (1, 255, 255, 255)])),
  ("Neon", _make_pal([(0, 255, 0, 80), (0.25, 0, 255, 180), (0.5, 255, 0, 255), (0.75, 0, 180, 255), (1, 255, 255, 0)])),
  ("Cyberpunk", _make_pal([(0, 0, 0, 0), (0.2, 80, 0, 180), (0.4, 255, 0, 80), (0.6, 0, 180, 255), (0.8, 255, 0, 255), (1, 0, 255, 180)])),
  ("Pastel", _make_pal([(0, 255, 180, 180), (0.25, 180, 220, 255), (0.5, 200, 255, 200), (0.75, 255, 230, 180), (1, 255, 180, 220)])),
  ("Vapor", _make_pal([(0, 20, 0, 40), (0.2, 80, 0, 120), (0.4, 255, 80, 180), (0.6, 80, 200, 255), (0.8, 255, 150, 255), (1, 180, 255, 255)])),
]

PALETTE_NAMES = [name for name, _ in PALETTES]
NUM_PALETTES = len(PALETTES)


def pal_color(pal_idx, t):
  """Get (r, g, b) from palette at position t (0.0-1.0)."""
  p = PALETTES[pal_idx % NUM_PALETTES][1]
  return p[clamp(int(t * 255))]


# ─── 17 Feldstein custom palettes ────────────────────────────────
# Each entry: (name, [(hue_offset, saturation, _), ...]) — 3 CHSV layers

FELDSTEIN_PALETTES = [
  ("Original", [(0, 255, 0), (96, 255, 0), (160, 255, 0)]),
  ("Rainbow", [(0, 255, 0), (85, 255, 0), (170, 255, 0)]),
  ("Ocean", [(140, 255, 0), (170, 255, 0), (200, 240, 0)]),
  ("Fire", [(0, 255, 0), (15, 255, 0), (35, 240, 0)]),
  ("Acid", [(75, 255, 0), (120, 255, 0), (200, 255, 0)]),
  ("Pastel", [(0, 100, 0), (96, 100, 0), (160, 100, 0)]),
  ("Monochrome", [(0, 0, 0), (0, 0, 0), (0, 0, 0)]),
  ("Sunset", [(250, 255, 0), (10, 255, 0), (30, 230, 0)]),
  ("Aurora", [(85, 255, 0), (105, 240, 0), (170, 255, 0)]),
  ("Cyberpunk", [(200, 255, 0), (230, 255, 0), (170, 240, 0)]),
  ("Deep Sea", [(150, 255, 0), (165, 255, 0), (140, 220, 0)]),
  ("Ember", [(0, 255, 0), (8, 240, 0), (16, 220, 0)]),
  ("Neon", [(55, 255, 0), (160, 255, 0), (220, 255, 0)]),
  ("Forest", [(70, 255, 0), (85, 255, 0), (105, 240, 0)]),
  ("Vapor", [(180, 230, 0), (210, 200, 0), (240, 240, 0)]),
  ("Blood Moon", [(250, 255, 0), (5, 240, 0), (0, 255, 0)]),
  ("Ice Storm", [(130, 200, 0), (155, 220, 0), (180, 180, 0)]),
]

FELDSTEIN_PALETTE_NAMES = [name for name, _ in FELDSTEIN_PALETTES]
NUM_FELDSTEIN_PALETTES = len(FELDSTEIN_PALETTES)


# ─── Fire palette (tuned to campfire) ────────────────────────────

def _build_fire_palette():
  pal = []
  for i in range(256):
    t = i / 255.0
    if t < 0.08:
      f = t / 0.08
      r, g, b = f * 50, 0, 0
    elif t < 0.22:
      f = (t - 0.08) / 0.14
      r, g, b = 50 + f * 180, f * 15, 0
    elif t < 0.40:
      f = (t - 0.22) / 0.18
      r, g, b = 230 + f * 25, 15 + f * 110, 0
    elif t < 0.60:
      f = (t - 0.40) / 0.20
      r, g, b = 255, 125 + f * 80, 0       # orange, not yellow
    elif t < 0.80:
      f = (t - 0.60) / 0.20
      r, g, b = 255, 205 + f * 40, f * 15  # amber peak, minimal blue
    else:
      f = min(1.0, (t - 0.80) / 0.20)
      r, g, b = 255, 245, 15 + f * 30      # warm white cap, not pure white
    pal.append((clamp(r), clamp(g), clamp(b)))
  return pal


FIRE_PALETTE = _build_fire_palette()


def fire_color(h01):
  """Get fire color at intensity h (0-1)."""
  return FIRE_PALETTE[clamp(int(h01 * 255))]


# ─── Pre-computed numpy arrays for vectorized palette lookup ───────

_PAL_ARRAYS = [np.array(pal, dtype=np.uint8) for _, pal in PALETTES]
_FIRE_PAL_ARRAY = np.array(FIRE_PALETTE, dtype=np.uint8)


def pal_color_grid(pal_idx, t_array):
  """Vectorized palette lookup. t_array is float 0-1, returns (..., 3) uint8."""
  pal = _PAL_ARRAYS[pal_idx % NUM_PALETTES]
  idx = np.clip((t_array * 255).astype(np.int32), 0, 255)
  return pal[idx]


def fire_color_grid(h_array):
  """Vectorized fire palette lookup. h_array is float 0-1, returns (..., 3) uint8."""
  idx = np.clip((h_array * 255).astype(np.int32), 0, 255)
  return _FIRE_PAL_ARRAY[idx]
