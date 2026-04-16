# Sound-Reactive Effects Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 5 sound-reactive LED effect variants (SR Feldstein, SR Lava Lamp, SR Matrix Rain, SR Moire, SR Flow Field), fix 2 bugs (Spectral Glow fade direction, Energy Ring spectrogram thickness), and add gain params to effects that lack one.

**Architecture:** Each new SR variant is a full fork of its base effect class into `sound_variants.py`, with audio modulation applied via `AudioCompatAdapter`. Registration via `SOUND_VARIANTS_EFFECTS` dict merged into `IMPORTED_EFFECTS`. Two bugs fixed directly in `audio_reactive.py`. Gain added to Beat Pulse / Particle Burst / Strobe Chaos / Spectral Glow / Energy Ring.

**Tech Stack:** Python, numpy (vectorized), existing AudioCompatAdapter + _get_pal_idx pattern

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `pi/app/effects/imported/sound_variants.py` | Create | 5 new SR classes + SOUND_VARIANTS_EFFECTS dict |
| `pi/app/effects/imported/__init__.py` | Modify | Merge SOUND_VARIANTS_EFFECTS into IMPORTED_EFFECTS |
| `pi/app/effects/audio_reactive.py` | Modify | Fix Spectral Glow fade, rewrite Energy Ring, add gain to both |
| `pi/app/effects/imported/sound.py` | Modify | Add `gain` param to Beat Pulse, Particle Burst, Strobe Chaos |
| `pi/tests/test_sound_variants.py` | Create | Tests for SR variants and fixes |

---

### Task 1: Gain Audit — Add Gain Param to 3 Sound Effects

**Files:**
- Modify: `pi/app/effects/imported/sound.py` (Beat Pulse, Particle Burst, Strobe Chaos)

- [ ] **Step 1: Add gain to Beat Pulse**

Find the Beat Pulse PARAMS list (around line 271). Replace:
```python
  PARAMS = [
    _Param("Decay", "decay", 0.8, 0.99, 0.01, 0.92),
    _Param("Flash", "flash", 0.3, 2.0, 0.1, 1.0),
  ]
  _SCALAR_PARAMS = {"decay": 0.92, "flash": 1.0}
```
With:
```python
  PARAMS = [
    _Param("Gain", "gain", 0.2, 5.0, 0.1, 1.0),
    _Param("Decay", "decay", 0.8, 0.99, 0.01, 0.92),
    _Param("Flash", "flash", 0.3, 2.0, 0.1, 1.0),
  ]
  _SCALAR_PARAMS = {"gain": 1.0, "decay": 0.92, "flash": 1.0}
```

Then in Beat Pulse's `render` method find:
```python
    if audio.beat:
      self._energy = flash * (1 + audio.buildup)
```
Replace with:
```python
    gain = self.params.get("gain", 1.0)
    if audio.beat:
      self._energy = flash * gain * (1 + audio.buildup)
```

- [ ] **Step 2: Add gain to Particle Burst**

Find Particle Burst PARAMS (around line 988). Replace:
```python
  PARAMS = [
    _Param("Gravity", "gravity", 0.0, 2.0, 0.1, 0.5),
    _Param("Speed", "speed", 0.3, 3.0, 0.1, 1.0),
    _Param("Count", "count", 5, 60, 5, 30),
  ]
  _SCALAR_PARAMS = {"gravity": 0.5, "speed": 1.0, "count": 30}
```
With:
```python
  PARAMS = [
    _Param("Gain", "gain", 0.2, 5.0, 0.1, 1.0),
    _Param("Gravity", "gravity", 0.0, 2.0, 0.1, 0.5),
    _Param("Speed", "speed", 0.3, 3.0, 0.1, 1.0),
    _Param("Count", "count", 5, 60, 5, 30),
  ]
  _SCALAR_PARAMS = {"gain": 1.0, "gravity": 0.5, "speed": 1.0, "count": 30}
```

In Particle Burst's `render`, find:
```python
    # Normal beat: single burst
    if audio.beat and not audio.drop:
      cx = random.uniform(1, cols - 2)
      cy = random.uniform(rows * 0.3, rows * 0.7)
      adjusted_count = int(count * (1 + audio.buildup))
      self._spawn_burst(cx, cy, adjusted_count, random.random())
```
Replace with:
```python
    gain = self.params.get("gain", 1.0)
    # Normal beat: single burst
    if audio.beat and not audio.drop:
      cx = random.uniform(1, cols - 2)
      cy = random.uniform(rows * 0.3, rows * 0.7)
      adjusted_count = int(count * gain * (1 + audio.buildup))
      self._spawn_burst(cx, cy, adjusted_count, random.random())
```

- [ ] **Step 3: Add gain to Strobe Chaos**

Find Strobe Chaos PARAMS (around line 1187). Replace:
```python
  PARAMS = [
    _Param("Intensity", "intensity", 0.1, 1.0, 0.05, 0.8),
    _Param("Segments", "segments", 1, 10, 1, 4),
  ]
  _SCALAR_PARAMS = {"intensity": 0.8, "segments": 4}
```
With:
```python
  PARAMS = [
    _Param("Gain", "gain", 0.2, 5.0, 0.1, 1.0),
    _Param("Intensity", "intensity", 0.1, 1.0, 0.05, 0.8),
    _Param("Segments", "segments", 1, 10, 1, 4),
  ]
  _SCALAR_PARAMS = {"gain": 1.0, "intensity": 0.8, "segments": 4}
```

In Strobe Chaos's `render`, find:
```python
    if audio.beat:
      self._flash = intensity * (1 + audio.buildup)
```
Replace with:
```python
    gain = self.params.get("gain", 1.0)
    if audio.beat:
      self._flash = min(1.0, intensity * gain * (1 + audio.buildup))
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/jim/ai/pillar-controller/pi && source .venv/bin/activate && PYTHONPATH=. pytest tests/ -x -q --ignore=tests/test_matrix_rain_perf.py --ignore=tests/test_migrations.py`

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add pi/app/effects/imported/sound.py
git commit -m "feat: add gain param to Beat Pulse, Particle Burst, Strobe Chaos"
```

---

### Task 2: Fix Spectral Glow + Add Gain

**Files:**
- Modify: `pi/app/effects/audio_reactive.py` (SpectralGlow class)

- [ ] **Step 1: Add gain to catalog for audio_reactive effects**

The `audio_reactive.py` effects (SpectralGlow, EnergyRing) don't declare their own PARAMS — their UI sliders come from `effects/catalog.py` `_EFFECT_PARAMS`. Add gain entries there.

In `pi/app/effects/catalog.py`, find the `_EFFECT_PARAMS` dict. Add entries for `spectral_glow` and `energy_ring`:

```python
    'spectral_glow': (
      {'name': 'gain', 'label': 'Gain', 'min': 0.2, 'max': 5.0, 'step': 0.1, 'default': 1.0, 'type': 'slider'},
    ),
    'energy_ring': (
      {'name': 'gain', 'label': 'Gain', 'min': 0.2, 'max': 5.0, 'step': 0.1, 'default': 1.0, 'type': 'slider'},
      {'name': 'speed', 'label': 'Speed', 'min': 0.1, 'max': 5.0, 'step': 0.1, 'default': 2.0, 'type': 'slider'},
    ),
```

Place these inside the `_EFFECT_PARAMS` dict alongside the other entries.

- [ ] **Step 3: Fix Spectral Glow fade + use gain**

In `pi/app/effects/audio_reactive.py`, replace the `SpectralGlow` class entirely with:

```python
class SpectralGlow(Effect):
  """Columns glow based on spectral energy. Bars grow upward from the bottom,
  brightest at the top of each bar (like a flame tip)."""

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)
    gain = self.params.get('gain', 1.0)
    frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)

    bands = [state.audio_bass, state.audio_mid, state.audio_high]

    # Compute per-column fill heights via interpolation
    col_pos = np.arange(self.width, dtype=np.float64) / self.width * (len(bands) - 1)
    band_idx = np.minimum(col_pos.astype(np.int32), len(bands) - 2)
    frac = col_pos - band_idx
    band_arr = np.array(bands, dtype=np.float64)
    levels = band_arr[band_idx] * (1 - frac) + band_arr[band_idx + 1] * frac
    fill_heights = np.clip((levels * gain * self.height).astype(np.int32), 0, self.height)

    # Per-column hue
    hue_base = (np.arange(self.width, dtype=np.float64) / self.width + elapsed * 0.05) % 1.0

    # Lit mask: y < fill_height (y=0 is bottom in logical coords, so bars grow up)
    y_grid = np.arange(self.height, dtype=np.int32)[np.newaxis, :]
    fill_grid = fill_heights[:, np.newaxis]
    lit_mask = y_grid < fill_grid

    # Inverted fade: brightest at the TOP of each column (y=height-1 → 1.0),
    # dimmer near the base (y=0 → 0.5). Fixes perceived upside-down look.
    y_frac = np.arange(self.height, dtype=np.float64) / self.height
    fade = 0.5 + y_frac * 0.5

    hue_grid = np.broadcast_to(hue_base[:, np.newaxis], (self.width, self.height))
    rgb = _hsv_array_to_rgb(hue_grid, 0.8, 1.0)
    rgb_faded = (rgb.astype(np.float32) * fade[np.newaxis, :, np.newaxis]).astype(np.uint8)

    frame[lit_mask] = rgb_faded[lit_mask]
    return frame
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/jim/ai/pillar-controller/pi && source .venv/bin/activate && PYTHONPATH=. pytest tests/ -x -q --ignore=tests/test_matrix_rain_perf.py --ignore=tests/test_migrations.py`

- [ ] **Step 5: Commit**

```bash
git add pi/app/effects/audio_reactive.py pi/app/effects/catalog.py
git commit -m "fix: Spectral Glow fade direction + add gain param"
```

---

### Task 3: Rewrite Energy Ring with Spectrum-Driven Thickness

**Files:**
- Modify: `pi/app/effects/audio_reactive.py` (EnergyRing class)

- [ ] **Step 1: Replace EnergyRing class**

In `pi/app/effects/audio_reactive.py`, replace the `EnergyRing` class entirely with:

```python
class EnergyRing(Effect):
  """Horizontal ring that sweeps vertically. Ring thickness varies around
  the cylinder based on the 16-bin FFT spectrum resampled to 10 bands —
  loud frequencies produce a thicker ring segment at that column."""

  def _resample_16_to_10(self, spectrum):
    """Resample 16-bin spectrum to 10 bands via mean pooling."""
    src = np.asarray(spectrum, dtype=np.float32) if spectrum else np.zeros(16, dtype=np.float32)
    if len(src) != 16:
      return np.zeros(10, dtype=np.float32)
    out = np.zeros(10, dtype=np.float32)
    ratio = 16 / 10  # 1.6
    for i in range(10):
      lo = i * ratio
      hi = (i + 1) * ratio
      lo_i = int(lo)
      hi_i = min(int(hi) + 1, 16)
      out[i] = float(np.mean(src[lo_i:hi_i])) if hi_i > lo_i else 0.0
    return out

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)
    speed = self.params.get('speed', 2.0)
    gain = self.params.get('gain', 1.0)
    frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)

    # Ring sweep position (no longer driven by audio — that caused stutter)
    ring_y = int(elapsed * speed * 10) % self.height

    # Per-column thickness from 10-band spectrum
    spectrum = getattr(state, 'audio_spectrum', None)
    bands_10 = self._resample_16_to_10(spectrum)
    col_widths = np.maximum(1, (bands_10 * 30 * gain).astype(np.int32))

    # Vectorize: compute toroidal distance from ring_y for every row
    y_coords = np.arange(self.height, dtype=np.int32)
    d1 = np.abs(y_coords - ring_y)
    d2 = self.height - d1
    dists = np.minimum(d1, d2)  # (height,) toroidal distance

    # Per-column hue (drifts over time)
    hue_col = (np.arange(self.width, dtype=np.float64) / self.width + elapsed * 0.1) % 1.0

    # For each column, compute fade where dist < width
    for x in range(self.width):
      w = int(col_widths[x])
      if w <= 0:
        continue
      within = dists < w
      if not np.any(within):
        continue
      fades = 1.0 - dists[within].astype(np.float64) / w  # (k,)
      ys = y_coords[within]
      # Per-column hue
      hue = hue_col[x]
      # HSV → RGB (scalar hue, per-pixel value)
      r, g, b = _hsv_array_to_rgb(np.full_like(fades, hue), 1.0, 1.0)[..., 0], _hsv_array_to_rgb(np.full_like(fades, hue), 1.0, 1.0)[..., 1], _hsv_array_to_rgb(np.full_like(fades, hue), 1.0, 1.0)[..., 2]
      # Simpler: compute one RGB triple, modulate by fade per pixel
      base_rgb = _hsv_array_to_rgb(np.array([hue]), 1.0, 1.0)[0]  # (3,) uint8
      for i, y in enumerate(ys):
        f = fades[i]
        frame[x, y] = (int(base_rgb[0] * f), int(base_rgb[1] * f), int(base_rgb[2] * f))

    return frame
```

Note: the inner per-column loop over `ys` is O(width * ring_thickness). With width=10 and max thickness ~30, that's ~300 pixels per frame — fine for 60 FPS.

- [ ] **Step 2: Run tests**

Run: `cd /Users/jim/ai/pillar-controller/pi && source .venv/bin/activate && PYTHONPATH=. pytest tests/ -x -q --ignore=tests/test_matrix_rain_perf.py --ignore=tests/test_migrations.py`

- [ ] **Step 3: Commit**

```bash
git add pi/app/effects/audio_reactive.py
git commit -m "feat: Energy Ring thickness from 16-bin FFT spectrum"
```

---

### Task 4: SR Feldstein

**Files:**
- Create: `pi/app/effects/imported/sound_variants.py` (initial skeleton + SRFeldstein)

- [ ] **Step 1: Create sound_variants.py with imports and SRFeldstein**

Create `pi/app/effects/imported/sound_variants.py` with:

```python
"""
Sound-reactive variants of ambient/classic effects.

Each variant forks the base effect's render loop and layers audio modulation
on top via AudioCompatAdapter. Separate classes (not subclasses) for clarity
and safety — editing these cannot break the originals.
"""

import math
import random

import numpy as np

from ..base import Effect
from ..engine.buffer import LEDBuffer
from ..engine.color import hsv2rgb
from ..engine.palettes import (
  pal_color, NUM_PALETTES, PALETTE_NAMES, pal_color_grid,
  FELDSTEIN_PALETTES, FELDSTEIN_PALETTE_NAMES, NUM_FELDSTEIN_PALETTES,
)
from ..engine.noise import cyl_noise, perlin_grid
from ...audio.adapter import AudioCompatAdapter
from ...mapping.cylinder import N


def _get_pal_idx(params, default=0, names=PALETTE_NAMES, count=NUM_PALETTES):
  val = params.get('palette', default)
  if isinstance(val, str):
    try:
      return names.index(val) % count
    except ValueError:
      return default % count
  return int(val) % count


class _Param:
  __slots__ = ('label', 'attr', 'lo', 'hi', 'step', 'default')
  def __init__(self, label, attr, lo, hi, step, default):
    self.label = label
    self.attr = attr
    self.lo = lo
    self.hi = hi
    self.step = step
    self.default = default


# ═══════════════════════════════════════════════════════════════════
#  SR FELDSTEIN
# ═══════════════════════════════════════════════════════════════════

class SRFeldstein(Effect):
  """Sound-reactive Feldstein: bass drives speed, beat shifts hue, buildup increases fade."""

  CATEGORY = "sound"
  DISPLAY_NAME = "SR Feldstein"
  DESCRIPTION = "Audio-reactive Feldstein OG — bass speed, beat hue pulse"
  PALETTE_SUPPORT = False  # uses FELDSTEIN_PALETTES

  PARAMS = [
    _Param("Gain", "gain", 0.2, 5.0, 0.1, 1.0),
    _Param("Speed", "speed", 0.04, 0.6, 0.02, 0.2),
    _Param("Fade/Dark", "fade", 10, 200, 5, 48),
  ]
  _SCALAR_PARAMS = {"gain": 1.0, "speed": 0.2, "fade": 48, "palette": 0}
  NATIVE_WIDTH = 10

  def __init__(self, width=10, height=N, params=None):
    super().__init__(width, height, params)
    self._audio_adapter = AudioCompatAdapter()
    self.buf = LEDBuffer(width, height)
    self._rng = random.Random()
    self._xo = self._rng.randint(0, 65535)
    self._yo = self._rng.randint(0, 65535)
    self._zo = self._rng.randint(0, 65535)
    self._hue = 0
    self._hue_accum = 0.0
    self._elapsed_ms = 0.0
    self._last_t = None

  def render(self, t, state):
    dt_ms = self._calc_dt_ms(t)
    raw = state._audio_lock_free
    audio = self._audio_adapter.adapt(raw, t)

    gain = self.params.get("gain", 1.0)
    base_speed = self.params.get("speed", 0.2)
    base_fade = int(self.params.get("fade", 48))
    pi = _get_pal_idx(self.params, names=FELDSTEIN_PALETTE_NAMES, count=NUM_FELDSTEIN_PALETTES)

    # Audio modulation
    speed = base_speed * (1.0 + audio.bass * gain * 2.0)
    fade = int(max(10, min(200, base_fade - audio.buildup * gain * 30)))
    if audio.beat:
      self._hue = (self._hue + int(38 * gain)) % 256  # ~0.15 radians on beat (38/256)

    self._elapsed_ms += dt_ms
    time_val = int(self._elapsed_ms * speed) // 7 + self._zo
    SCALE = 180

    self._hue_accum += dt_ms
    if self._hue_accum >= 1000:
      self._hue = (self._hue + 1) % 256
      self._hue_accum -= 1000
    h = self._hue

    _pname, layers = FELDSTEIN_PALETTES[pi]
    h1_off, s1, _ = layers[0]
    h2_off, s2, _ = layers[1]
    h3_off, s3, _ = layers[2]

    self.buf.fade_by(fade)

    cols, rows = self.width, self.height
    x_idx = np.arange(cols, dtype=np.float64)
    y_idx = np.arange(rows, dtype=np.float64)
    xS = (x_idx * SCALE + self._xo)[:, np.newaxis] * np.ones(rows)
    yS = np.ones(cols)[:, np.newaxis] * (y_idx * SCALE + self._yo)

    for h_off, sat, (nx_div, ny_div, ny_off, nz_val) in [
      (h1_off, s1, (10, 50, time_val // 2, float(time_val))),
      (h2_off, s2, (10, 50, time_val // 2, float(time_val + 100 * SCALE))),
      (h3_off, s3, (100, 40, 0, float(time_val // 10 + 300 * SCALE))),
    ]:
      px = xS / nx_div / 256.0
      py = (yS / ny_div + ny_off) / 256.0
      pz = np.full_like(px, nz_val / 256.0)
      raw = (perlin_grid(px, py, pz) + 1.0) * 127.5
      raw = np.clip(raw, 0, 255).astype(np.int32)
      vals = raw - 128
      vals = np.clip(vals, 0, 255)
      vals = vals + ((vals * 128) >> 8)
      vals = np.clip(vals, 0, 255).astype(np.uint8)

      hue = (h + h_off) & 255
      r_c, g_c, b_c = hsv2rgb(hue, sat, 255)
      layer = np.zeros((cols, rows, 3), dtype=np.uint8)
      if r_c + g_c + b_c > 0:
        layer[..., 0] = (vals.astype(np.uint16) * r_c // 255).astype(np.uint8)
        layer[..., 1] = (vals.astype(np.uint16) * g_c // 255).astype(np.uint8)
        layer[..., 2] = (vals.astype(np.uint16) * b_c // 255).astype(np.uint8)
      self.buf.data = np.clip(
        self.buf.data.astype(np.int16) + layer.astype(np.int16), 0, 255
      ).astype(np.uint8)

    return self.buf.get_frame()

  def _calc_dt_ms(self, t):
    if self._last_t is None:
      self._last_t = t
      return 16.67
    dt = (t - self._last_t) * 1000.0
    self._last_t = t
    return max(0.0, dt)


SOUND_VARIANTS_EFFECTS = {
  'sr_feldstein': SRFeldstein,
}
```

- [ ] **Step 2: Register in imported/__init__.py**

In `pi/app/effects/imported/__init__.py`, modify to:

```python
"""Imported LED animations — 27 effects ported from led_sim.py.

Registers all effects into a single IMPORTED_EFFECTS dict.
"""

from .classic import CLASSIC_EFFECTS
from .ambient_a import AMBIENT_A_EFFECTS
from .ambient_b import AMBIENT_B_EFFECTS
from .sound import SOUND_EFFECTS
from .sound_variants import SOUND_VARIANTS_EFFECTS

IMPORTED_EFFECTS = {
  **CLASSIC_EFFECTS,
  **AMBIENT_A_EFFECTS,
  **AMBIENT_B_EFFECTS,
  **SOUND_EFFECTS,
  **SOUND_VARIANTS_EFFECTS,
}
```

- [ ] **Step 3: Run tests — verify nothing broke**

Run: `cd /Users/jim/ai/pillar-controller/pi && source .venv/bin/activate && PYTHONPATH=. pytest tests/ -x -q --ignore=tests/test_matrix_rain_perf.py --ignore=tests/test_migrations.py`

- [ ] **Step 4: Commit**

```bash
git add pi/app/effects/imported/sound_variants.py pi/app/effects/imported/__init__.py
git commit -m "feat: SR Feldstein — audio-reactive Feldstein OG variant"
```

---

### Task 5: SR Lava Lamp

**Files:**
- Modify: `pi/app/effects/imported/sound_variants.py` (append SRLavaLamp)

- [ ] **Step 1: Append SRLavaLamp class**

Add to `pi/app/effects/imported/sound_variants.py` (before the SOUND_VARIANTS_EFFECTS dict):

```python
# ═══════════════════════════════════════════════════════════════════
#  SR LAVA LAMP
# ═══════════════════════════════════════════════════════════════════

class SRLavaLamp(Effect):
  """Sound-reactive Lava Lamp: bass scales blob size, beat pulls blobs
  toward vertical center, drops temporarily add blobs (max 12)."""

  CATEGORY = "sound"
  DISPLAY_NAME = "SR Lava Lamp"
  DESCRIPTION = "Audio-reactive blobs — bass size, beat pulse, drop surge"
  PALETTE_SUPPORT = True

  PARAMS = [
    _Param("Gain", "gain", 0.2, 5.0, 0.1, 1.0),
    _Param("Speed", "speed", 0.05, 2.0, 0.05, 0.3),
    _Param("Blobs", "blobs", 2, 12, 1, 5),
    _Param("Size", "size", 0.3, 3.0, 0.1, 1.0),
  ]
  _SCALAR_PARAMS = {"gain": 1.0, "speed": 0.3, "blobs": 5, "size": 1.0, "palette": 0}
  NATIVE_WIDTH = 10

  def __init__(self, width=10, height=N, params=None):
    super().__init__(width, height, params)
    self._audio_adapter = AudioCompatAdapter()
    self.buf = LEDBuffer(width, height)
    self._t = 0.0
    self._last_t = None
    self._blob_seeds = [
      (random.random() * 100, random.random() * 100) for _ in range(12)
    ]
    self._drop_timer = 0.0
    self._beat_pull = 0.0  # fades from 1.0 → 0 after beat

  def render(self, t, state):
    dt_ms = self._calc_dt_ms(t)
    dt_s = dt_ms * 0.001
    raw = state._audio_lock_free
    audio = self._audio_adapter.adapt(raw, t)

    gain = self.params.get("gain", 1.0)
    speed = self.params.get("speed", 0.3)
    base_blobs = int(self.params.get("blobs", 5))
    base_size = self.params.get("size", 1.0)
    pal_idx = _get_pal_idx(self.params)

    # Audio modulations
    size = base_size * (1.0 + audio.bass * gain * 1.0)
    if audio.drop:
      self._drop_timer = 1.5
    self._drop_timer = max(0.0, self._drop_timer - dt_s)
    extra_blobs = int(4 * min(1.0, self._drop_timer / 1.5))
    num_blobs = min(12, base_blobs + extra_blobs)

    if audio.beat:
      self._beat_pull = 1.0
    self._beat_pull *= 0.9 ** (dt_s * 60)  # decay per second

    self._t += dt_s * speed
    tt = self._t
    cols = self.width
    rows = self.height

    x_g = np.arange(cols, dtype=np.float64)[:, np.newaxis]
    y_g = np.arange(rows, dtype=np.float64)[np.newaxis, :]

    size_x = max(1.0, size * 2)
    size_y = max(1.0, size * 25)

    val = np.zeros((cols, rows), dtype=np.float64)
    for bi in range(num_blobs):
      sx, sy = self._blob_seeds[bi]
      bx = (cols / 2) + math.sin(tt * 0.7 + sx * 6.28) * cols * 0.4
      by = (rows / 2) + math.sin(tt * 0.3 + sy * 6.28) * rows * 0.4
      # Beat pull: blend by toward rows/2
      by = by * (1.0 - self._beat_pull * 0.5 * gain) + (rows / 2) * (self._beat_pull * 0.5 * gain)
      dx = (x_g - bx) / size_x
      dy = (y_g - by) / size_y
      dist_sq = dx * dx + dy * dy
      val += 1.0 / (1.0 + dist_sq * 3)

    val = np.clip(val, 0.0, 1.0)
    hue = val * 0.8 + 0.1
    rgb = pal_color_grid(pal_idx, hue)
    self.buf.data = (rgb.astype(np.float32) * val[..., np.newaxis]).clip(0, 255).astype(np.uint8)

    return self.buf.get_frame()

  def _calc_dt_ms(self, t):
    if self._last_t is None:
      self._last_t = t
      return 16.67
    dt = (t - self._last_t) * 1000.0
    self._last_t = t
    return max(0.0, dt)
```

Then update the SOUND_VARIANTS_EFFECTS dict at the bottom:

```python
SOUND_VARIANTS_EFFECTS = {
  'sr_feldstein': SRFeldstein,
  'sr_lava_lamp': SRLavaLamp,
}
```

- [ ] **Step 2: Run tests**

Run: `cd /Users/jim/ai/pillar-controller/pi && source .venv/bin/activate && PYTHONPATH=. pytest tests/ -x -q --ignore=tests/test_matrix_rain_perf.py --ignore=tests/test_migrations.py`

- [ ] **Step 3: Commit**

```bash
git add pi/app/effects/imported/sound_variants.py
git commit -m "feat: SR Lava Lamp — audio-reactive blobs"
```

---

### Task 6: SR Matrix Rain

**Files:**
- Modify: `pi/app/effects/imported/sound_variants.py` (append SRMatrixRain)

- [ ] **Step 1: Append SRMatrixRain class**

Add to `pi/app/effects/imported/sound_variants.py` (before SOUND_VARIANTS_EFFECTS dict):

```python
# ═══════════════════════════════════════════════════════════════════
#  SR MATRIX RAIN
# ═══════════════════════════════════════════════════════════════════

class SRMatrixRain(Effect):
  """Sound-reactive Matrix Rain: bass multiplies drop speed, beat spikes
  spawn density, buildup lengthens trails."""

  CATEGORY = "sound"
  DISPLAY_NAME = "SR Matrix Rain"
  DESCRIPTION = "Audio-reactive digital rain — bass speed, beat burst, buildup trails"
  PALETTE_SUPPORT = True
  NATIVE_WIDTH = 10

  PARAMS = [
    _Param("Gain", "gain", 0.2, 5.0, 0.1, 1.0),
    _Param("Speed", "speed", 0.2, 4.0, 0.1, 1.0),
    _Param("Density", "density", 0.1, 1.0, 0.05, 0.4),
    _Param("Trail", "trail", 5, 60, 1, 25),
  ]
  _SCALAR_PARAMS = {"gain": 1.0, "speed": 1.0, "density": 0.4, "trail": 25, "palette": 3}

  _MAX_DROPS = 200

  def __init__(self, width=10, height=N, params=None):
    super().__init__(width, height, params)
    if "palette" not in self.params:
      self.params["palette"] = 3
    self._audio_adapter = AudioCompatAdapter()
    self.buf = LEDBuffer(width, height)
    self._last_t = None

    cap = self._MAX_DROPS
    self._drop_x = np.zeros(cap, dtype=np.int32)
    self._drop_y = np.zeros(cap, dtype=np.float64)
    self._drop_speed = np.zeros(cap, dtype=np.float64)
    self._drop_bright = np.zeros(cap, dtype=np.float64)
    self._active_mask = np.zeros(cap, dtype=np.bool_)

  def render(self, t, state):
    dt_ms = self._calc_dt_ms(t)
    dt = dt_ms * 0.001
    raw = state._audio_lock_free
    audio = self._audio_adapter.adapt(raw, t)

    gain = self.params.get("gain", 1.0)
    base_speed = self.params.get("speed", 1.0)
    base_density = self.params.get("density", 0.4)
    base_trail = int(self.params.get("trail", 25))
    pal_idx = _get_pal_idx(self.params, default=3)

    # Audio modulations
    speed = base_speed * (1.0 + audio.bass * gain * 2.0)
    trail = int(min(60, base_trail * (1.0 + audio.buildup * gain)))
    # Beat spawn spike: triple density for this frame only
    density = base_density * (3.0 if audio.beat else 1.0)

    cols = self.width
    rows = self.height

    self.buf.clear()

    # Spawn new drops
    for x in range(cols):
      if random.random() < density * dt * 3:
        slot = self._find_free_slot()
        if slot < 0:
          continue
        r = random.random()
        if r < 0.5:
          spd = random.uniform(6, 20)
        elif r < 0.85:
          spd = random.uniform(20, 50)
        else:
          spd = random.uniform(50, 90)
        self._drop_x[slot] = x
        self._drop_y[slot] = -1.0
        self._drop_speed[slot] = spd * speed
        self._drop_bright[slot] = random.uniform(0.5, 1.0)
        self._active_mask[slot] = True

    # Update positions
    active = self._active_mask
    self._drop_y[active] += self._drop_speed[active] * dt

    # Cull dead drops
    heads = self._drop_y.astype(np.int32)
    dead = active & ((heads - trail) >= rows)
    self._active_mask[dead] = False

    # Draw trails
    fade_lut = np.arange(trail, dtype=np.float64)
    fade_factors = (1.0 - fade_lut / trail) ** 1.5
    pal_colors = pal_color_grid(pal_idx, fade_factors).astype(np.float64)

    active_indices = np.where(self._active_mask)[0]
    n_active = len(active_indices)
    if n_active > 0:
      a_heads = self._drop_y[active_indices].astype(np.int32)
      a_brights = self._drop_bright[active_indices]
      a_xs = self._drop_x[active_indices]
      trail_offsets = np.arange(trail, dtype=np.int32)

      py_grid = a_heads[:, np.newaxis] - trail_offsets[np.newaxis, :]
      valid = (py_grid >= 0) & (py_grid < rows)
      bright_grid = fade_factors[np.newaxis, :] * a_brights[:, np.newaxis]
      rgb_grid = (pal_colors[np.newaxis, :, :] * bright_grid[:, :, np.newaxis]).astype(np.int32)

      drop_idx, trail_idx = np.where(valid)
      xs = a_xs[drop_idx]
      ys = py_grid[drop_idx, trail_idx]
      rgbs = rgb_grid[drop_idx, trail_idx]

      buf16 = self.buf.data.astype(np.uint16)
      np.add.at(buf16, (xs, ys, 0), rgbs[:, 0].astype(np.uint16))
      np.add.at(buf16, (xs, ys, 1), rgbs[:, 1].astype(np.uint16))
      np.add.at(buf16, (xs, ys, 2), rgbs[:, 2].astype(np.uint16))
      np.clip(buf16, 0, 255, out=buf16)
      self.buf.data[:] = buf16.astype(np.uint8)

    return self.buf.get_frame()

  def _find_free_slot(self):
    inactive = np.where(~self._active_mask)[0]
    if len(inactive) == 0:
      return -1
    return int(inactive[0])

  def _calc_dt_ms(self, t):
    if self._last_t is None:
      self._last_t = t
      return 16.67
    dt = (t - self._last_t) * 1000.0
    self._last_t = t
    return max(0.0, dt)
```

Then update the SOUND_VARIANTS_EFFECTS dict:

```python
SOUND_VARIANTS_EFFECTS = {
  'sr_feldstein': SRFeldstein,
  'sr_lava_lamp': SRLavaLamp,
  'sr_matrix_rain': SRMatrixRain,
}
```

- [ ] **Step 2: Run tests**

Run: `cd /Users/jim/ai/pillar-controller/pi && source .venv/bin/activate && PYTHONPATH=. pytest tests/ -x -q --ignore=tests/test_matrix_rain_perf.py --ignore=tests/test_migrations.py`

- [ ] **Step 3: Commit**

```bash
git add pi/app/effects/imported/sound_variants.py
git commit -m "feat: SR Matrix Rain — audio-reactive digital rain"
```

---

### Task 7: SR Moire

**Files:**
- Modify: `pi/app/effects/imported/sound_variants.py` (append SRMoire)

- [ ] **Step 1: Append SRMoire class**

Add to `pi/app/effects/imported/sound_variants.py`:

```python
# ═══════════════════════════════════════════════════════════════════
#  SR MOIRE
# ═══════════════════════════════════════════════════════════════════

class SRMoire(Effect):
  """Sound-reactive Moire: bass tightens rings, beat pulses centers inward,
  drop expands ring scale."""

  CATEGORY = "sound"
  DISPLAY_NAME = "SR Moire"
  DESCRIPTION = "Audio-reactive ring interference — bass density, beat pulse, drop expand"
  PALETTE_SUPPORT = True

  PARAMS = [
    _Param("Gain", "gain", 0.2, 5.0, 0.1, 1.0),
    _Param("Speed", "speed", 0.05, 2.0, 0.05, 0.4),
    _Param("Scale", "scale", 0.3, 3.0, 0.1, 1.0),
    _Param("Centers", "centers", 2, 5, 1, 3),
  ]
  _SCALAR_PARAMS = {"gain": 1.0, "speed": 0.4, "scale": 1.0, "centers": 3, "palette": 0}
  NATIVE_WIDTH = 10

  def __init__(self, width=10, height=N, params=None):
    super().__init__(width, height, params)
    self._audio_adapter = AudioCompatAdapter()
    self.buf = LEDBuffer(width, height)
    self._t = 0.0
    self._last_t = None
    self._beat_pull = 0.0
    self._drop_boost = 0.0

  def render(self, t, state):
    dt_ms = self._calc_dt_ms(t)
    dt_s = dt_ms * 0.001
    raw = state._audio_lock_free
    audio = self._audio_adapter.adapt(raw, t)

    gain = self.params.get("gain", 1.0)
    speed = self.params.get("speed", 0.4)
    base_sc = self.params.get("scale", 1.0)
    nc = int(self.params.get("centers", 3))
    pal_idx = _get_pal_idx(self.params)

    # Audio modulations
    sc = base_sc * (1.0 + audio.bass * gain * 1.5)
    if audio.beat:
      self._beat_pull = 1.0
    self._beat_pull *= 0.88 ** (dt_s * 60)

    if audio.drop:
      self._drop_boost = 1.0
    self._drop_boost *= 0.9 ** (dt_s * 60)
    sc *= 1.0 + self._drop_boost * gain  # drop expands scale

    self._t += dt_s * speed
    tt = self._t

    cols = self.width
    rows = self.height

    centers = []
    for i in range(nc):
      phase = i * 6.28 / nc
      cx = (math.sin(tt * 0.7 + phase) * 0.5 + 0.5) * cols
      cy = rows / 2 + math.sin(tt * 0.3 + phase * 1.7) * rows * 0.35
      # Beat pull: centers move toward pillar center
      pull = self._beat_pull * 0.6 * gain
      cx = cx * (1.0 - pull) + (cols / 2) * pull
      cy = cy * (1.0 - pull) + (rows / 2) * pull
      centers.append((cx, cy))

    x_g = np.arange(cols, dtype=np.float64)[:, np.newaxis]
    y_g = np.arange(rows, dtype=np.float64)[np.newaxis, :]

    val = np.zeros((cols, rows), dtype=np.float64)
    for cx, cy in centers:
      dx = x_g - cx
      dx = np.where(np.abs(dx) > cols / 2, dx - np.sign(dx) * cols, dx)
      dy = (y_g - cy) * (cols / rows) * 5
      dist = np.sqrt(dx ** 2 + dy ** 2)
      val += np.sin(dist * sc * 3 + tt * 2)
    val /= nc

    hue = (val + 1) * 0.5
    bright = np.clip((np.abs(val) ** 0.5) * 0.9 + 0.1, 0.0, 1.0)

    rgb = pal_color_grid(pal_idx, hue)
    self.buf.data = (rgb.astype(np.float32) * bright[..., np.newaxis]).clip(0, 255).astype(np.uint8)

    return self.buf.get_frame()

  def _calc_dt_ms(self, t):
    if self._last_t is None:
      self._last_t = t
      return 16.67
    dt = (t - self._last_t) * 1000.0
    self._last_t = t
    return max(0.0, dt)
```

Then update SOUND_VARIANTS_EFFECTS dict:

```python
SOUND_VARIANTS_EFFECTS = {
  'sr_feldstein': SRFeldstein,
  'sr_lava_lamp': SRLavaLamp,
  'sr_matrix_rain': SRMatrixRain,
  'sr_moire': SRMoire,
}
```

- [ ] **Step 2: Run tests**

Run: `cd /Users/jim/ai/pillar-controller/pi && source .venv/bin/activate && PYTHONPATH=. pytest tests/ -x -q --ignore=tests/test_matrix_rain_perf.py --ignore=tests/test_migrations.py`

- [ ] **Step 3: Commit**

```bash
git add pi/app/effects/imported/sound_variants.py
git commit -m "feat: SR Moire — audio-reactive ring interference"
```

---

### Task 8: SR Flow Field

**Files:**
- Modify: `pi/app/effects/imported/sound_variants.py` (append SRFlowField)

- [ ] **Step 1: Append SRFlowField class**

Add to `pi/app/effects/imported/sound_variants.py`:

```python
# ═══════════════════════════════════════════════════════════════════
#  SR FLOW FIELD
# ═══════════════════════════════════════════════════════════════════

class SRFlowField(Effect):
  """Sound-reactive Flow Field: bass speeds flow, beat flashes existing
  particles full-bright, buildup boosts trail brightness. No extra
  particle spawning (keeps 60 FPS budget)."""

  CATEGORY = "sound"
  DISPLAY_NAME = "SR Flow Field"
  DESCRIPTION = "Audio-reactive flow field — bass velocity, beat flash, buildup glow"
  PALETTE_SUPPORT = True
  NATIVE_WIDTH = 10

  PARAMS = [
    _Param("Gain", "gain", 0.2, 5.0, 0.1, 1.0),
    _Param("Speed", "speed", 0.05, 2.0, 0.05, 0.3),
    _Param("Particles", "particles", 10, 200, 10, 80),
    _Param("Fade", "fade", 0.8, 0.99, 0.01, 0.92),
    _Param("Noise Scale", "noise_scale", 0.3, 3.0, 0.1, 1.0),
  ]
  _SCALAR_PARAMS = {"gain": 1.0, "speed": 0.3, "particles": 80, "fade": 0.92, "noise_scale": 1.0, "palette": 0}

  def __init__(self, width=10, height=N, params=None):
    super().__init__(width, height, params)
    self._audio_adapter = AudioCompatAdapter()
    self.buf = LEDBuffer(width, height)
    self._t = 0.0
    self._pts = []
    for _ in range(200):
      self._pts.append([
        random.uniform(0, width),
        random.uniform(0, height),
        random.random(),
      ])
    self._beat_flash = 0.0
    self._last_t = None

  def render(self, t, state):
    dt_ms = self._calc_dt_ms(t)
    dt = dt_ms * 0.001
    raw = state._audio_lock_free
    audio = self._audio_adapter.adapt(raw, t)

    gain = self.params.get("gain", 1.0)
    base_speed = self.params.get("speed", 0.3)
    count = int(self.params.get("particles", 80))
    fade = self.params.get("fade", 0.92)
    ns = self.params.get("noise_scale", 1.0)
    pal_idx = _get_pal_idx(self.params)

    # Audio modulations
    speed = base_speed * (1.0 + audio.bass * gain * 1.5)
    if audio.beat:
      self._beat_flash = 1.0
    self._beat_flash *= 0.85 ** (dt * 60)
    # Brightness boost: baseline 0.8, +buildup*gain (capped)
    bright_scale = 0.8 + min(0.8, audio.buildup * gain + self._beat_flash * 0.8)

    self._t += dt * speed
    self.buf.fade(fade)

    for p in self._pts[:count]:
      angle = cyl_noise(p[0], p[1], self._t * 0.5, ns, 0.008 * ns) * 6.28
      p[0] += math.cos(angle) * 30 * dt * speed
      p[1] += math.sin(angle) * 30 * dt * speed
      p[0] = p[0] % self.width
      if p[1] < 0 or p[1] >= self.height:
        p[0] = random.uniform(0, self.width)
        p[1] = random.uniform(0, self.height)
        p[2] = random.random()
      c = pal_color(pal_idx, p[2])
      self.buf.add_led(int(p[0]), int(p[1]),
                       c[0] * bright_scale, c[1] * bright_scale, c[2] * bright_scale)

    return self.buf.get_frame()

  def _calc_dt_ms(self, t):
    if self._last_t is None:
      self._last_t = t
      return 16.67
    dt = (t - self._last_t) * 1000.0
    self._last_t = t
    return max(0.0, dt)
```

Then update SOUND_VARIANTS_EFFECTS dict:

```python
SOUND_VARIANTS_EFFECTS = {
  'sr_feldstein': SRFeldstein,
  'sr_lava_lamp': SRLavaLamp,
  'sr_matrix_rain': SRMatrixRain,
  'sr_moire': SRMoire,
  'sr_flow_field': SRFlowField,
}
```

- [ ] **Step 2: Run tests**

Run: `cd /Users/jim/ai/pillar-controller/pi && source .venv/bin/activate && PYTHONPATH=. pytest tests/ -x -q --ignore=tests/test_matrix_rain_perf.py --ignore=tests/test_migrations.py`

- [ ] **Step 3: Commit**

```bash
git add pi/app/effects/imported/sound_variants.py
git commit -m "feat: SR Flow Field — audio-reactive particle flow"
```

---

### Task 9: Smoke Tests — All 5 SR Variants Render

**Files:**
- Create: `pi/tests/test_sound_variants.py`

- [ ] **Step 1: Write tests**

Create `pi/tests/test_sound_variants.py`:

```python
"""Smoke tests: all SR variants instantiate and render a frame."""

import numpy as np
import pytest

from app.effects.imported.sound_variants import (
  SRFeldstein, SRLavaLamp, SRMatrixRain, SRMoire, SRFlowField,
  SOUND_VARIANTS_EFFECTS,
)
from app.core.renderer import RenderState


class FakeState:
  """Minimal state object for effect testing."""
  def __init__(self):
    self._audio_lock_free = {
      'level': 0.3, 'bass': 0.4, 'mid': 0.2, 'high': 0.1,
      'beat': False, 'bpm': 120.0,
      'spectrum': [0.1] * 16,
    }


@pytest.mark.parametrize("name,cls", list(SOUND_VARIANTS_EFFECTS.items()))
def test_variant_renders_frame(name, cls):
  """Each SR variant should produce a valid frame of the right shape."""
  effect = cls(width=10, height=172, params={})
  state = FakeState()
  frame = effect.render(0.0, state)
  assert frame.shape == (10, 172, 3), f"{name}: bad shape {frame.shape}"
  assert frame.dtype == np.uint8, f"{name}: bad dtype {frame.dtype}"


@pytest.mark.parametrize("name,cls", list(SOUND_VARIANTS_EFFECTS.items()))
def test_variant_handles_beat(name, cls):
  """Each SR variant should render correctly with a beat event."""
  effect = cls(width=10, height=172, params={})
  state = FakeState()
  state._audio_lock_free['beat'] = True
  frame = effect.render(0.016, state)
  assert frame.shape == (10, 172, 3), f"{name}: bad shape"


@pytest.mark.parametrize("name,cls", list(SOUND_VARIANTS_EFFECTS.items()))
def test_variant_has_gain_param(name, cls):
  """Every SR variant must expose a gain param."""
  params = [p.attr for p in cls.PARAMS]
  assert 'gain' in params, f"{name}: missing gain param"


def test_all_5_variants_registered():
  """Registration dict has exactly the 5 expected variants."""
  assert set(SOUND_VARIANTS_EFFECTS.keys()) == {
    'sr_feldstein', 'sr_lava_lamp', 'sr_matrix_rain', 'sr_moire', 'sr_flow_field',
  }


def test_variants_in_imported_effects():
  """Variants are merged into IMPORTED_EFFECTS so the catalog picks them up."""
  from app.effects.imported import IMPORTED_EFFECTS
  for name in SOUND_VARIANTS_EFFECTS:
    assert name in IMPORTED_EFFECTS
```

- [ ] **Step 2: Run new tests**

Run: `cd /Users/jim/ai/pillar-controller/pi && source .venv/bin/activate && PYTHONPATH=. pytest tests/test_sound_variants.py -v`

Expected: all pass (17 tests: 5 render + 5 beat + 5 gain + 1 registration + 1 IMPORTED_EFFECTS = 17).

- [ ] **Step 3: Run full test suite**

Run: `cd /Users/jim/ai/pillar-controller/pi && source .venv/bin/activate && PYTHONPATH=. pytest tests/ -x -q --ignore=tests/test_matrix_rain_perf.py --ignore=tests/test_migrations.py`

- [ ] **Step 4: Commit**

```bash
git add pi/tests/test_sound_variants.py
git commit -m "test: smoke tests for SR variants"
```

---

### Task 10: Catalog Metadata for SR Variants

**Files:**
- Modify: `pi/app/main.py` (already handles imported effect registration via IMPORTED_EFFECTS loop)

This task is a verification-only task — no code changes. The existing `main.py` loop at lines 131-161 iterates `IMPORTED_EFFECTS.items()` and builds the catalog from each effect's `PARAMS`, `DISPLAY_NAME`, `CATEGORY`, `PALETTE_SUPPORT`, and `DESCRIPTION`. The SR variants declare all of these, so they'll appear automatically.

- [ ] **Step 1: Verify catalog picks up the SR variants**

Start the dev server and check the catalog:
```bash
cd /Users/jim/ai/pillar-controller/pi && PYTHONPATH=. python3 -c "
from app.effects.catalog import EffectCatalogService
from app.effects.imported import IMPORTED_EFFECTS

svc = EffectCatalogService()
for name, cls in IMPORTED_EFFECTS.items():
  if name.startswith('sr_'):
    svc.register_imported(name, type('M', (), {
      'name': name,
      'label': getattr(cls, 'DISPLAY_NAME', name),
      'group': 'sound',
      'description': getattr(cls, 'DESCRIPTION', ''),
      'preview_supported': True,
      'imported': True,
      'geometry_aware': False,
      'audio_requires': (),
      'params': tuple(
        {'name': p.attr.lower(), 'label': p.label, 'min': p.lo, 'max': p.hi,
         'step': p.step, 'default': p.default, 'type': 'slider'}
        for p in cls.PARAMS
      ),
      'palettes': (),
      'palette_support': getattr(cls, 'PALETTE_SUPPORT', False),
      'to_dict': lambda self: {},
    })())

catalog = svc.get_catalog()
sr = [n for n in catalog if n.startswith('sr_')]
print(f'SR variants in catalog: {sr}')
assert len(sr) == 5
print('OK')
"
```

If output is `SR variants in catalog: ['sr_feldstein', 'sr_lava_lamp', 'sr_matrix_rain', 'sr_moire', 'sr_flow_field']` followed by `OK`, registration works.

Actually simpler: the real `main.py` registration path handles all of this. Just verify deploy works in the next task.

---

### Task 11: Deploy and Verify

- [ ] **Step 1: Deploy**

```bash
cd /Users/jim/ai/pillar-controller && bash pi/scripts/deploy.sh ledfanatic.local
```

- [ ] **Step 2: Verify the 5 SR variants appear in the catalog**

```bash
ssh jim@ledfanatic.local "curl -s http://localhost:80/api/effects/catalog | python3 -c \"
import sys, json
d = json.load(sys.stdin)
sr = sorted([n for n in d['effects'] if n.startswith('sr_')])
print('SR effects:', sr)
print('count:', len(sr))
\""
```

Expected: 5 effects listed.

- [ ] **Step 3: Verify Spectral Glow and Energy Ring have gain**

```bash
ssh jim@ledfanatic.local "curl -s http://localhost:80/api/effects/spectral_glow | python3 -m json.tool | grep gain"
ssh jim@ledfanatic.local "curl -s http://localhost:80/api/effects/energy_ring | python3 -m json.tool | grep gain"
```

Expected: each returns a `"name": "gain"` line.

- [ ] **Step 4: Activate each SR variant and verify no render errors**

```bash
for effect in sr_feldstein sr_lava_lamp sr_matrix_rain sr_moire sr_flow_field; do
  echo "Testing $effect..."
  ssh jim@ledfanatic.local "curl -s -X POST http://localhost:80/api/scenes/activate -H 'Content-Type: application/json' -d '{\"effect\":\"$effect\"}' | head -c 100"
  echo
  sleep 2
  ssh jim@ledfanatic.local "sudo journalctl -u pillar --no-pager -n 5 | grep -i 'error\|traceback' || echo 'no errors'"
done
```

Expected: each activates successfully with no errors.

- [ ] **Step 5: Test Spectral Glow (bars should grow upward from bottom, brightest at top)**

```bash
ssh jim@ledfanatic.local "curl -s -X POST http://localhost:80/api/scenes/activate -H 'Content-Type: application/json' -d '{\"effect\":\"spectral_glow\"}'"
```

Play audio and visually verify bars grow from bottom up with brightest tip.

- [ ] **Step 6: Test Energy Ring (ring with varying thickness per column)**

```bash
ssh jim@ledfanatic.local "curl -s -X POST http://localhost:80/api/scenes/activate -H 'Content-Type: application/json' -d '{\"effect\":\"energy_ring\"}'"
```

Play audio — ring should have thicker segments at columns whose frequency bands are loud.
