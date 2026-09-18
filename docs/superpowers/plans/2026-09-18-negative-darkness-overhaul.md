# Negative Ripples + Darkness Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add SR Negative Ripples and SR Negative Rain 2, and unify the Darkness slider across the negative family so darkness = volume of darkness (true black at max), never background dimming.

**Architecture:** One new module-level helper `apply_darkness(frame_f, field, darkness)` in `negative_space.py` becomes the single composition point for all negative effects (4 standalone + 10 spin-offs via their shared base + 2 new). Param ranges widen to 0.1–1.0. `SRNegativeRain` is frozen — zero lines of it may change.

**Tech Stack:** Python 3 / NumPy; effects framework in `pi/app/effects/` (Effect base, `_plasma_bg`, `AudioCompatAdapter`).

**Spec:** `docs/superpowers/specs/2026-09-18-negative-darkness-overhaul-design.md`

## Global Constraints

- 2-space indentation.
- Darkness param everywhere: `_P("Darkness", "darkness", 0.1, 1.0, 0.05, 0.95)` (spin-offs keep per-effect defaults via `_common_params(darkness=...)`).
- Composition formula (SSOT, in the helper): `occ = clip(field × (0.5 + darkness), 0, 1) × darkness; frame × (1 − occ)`. At darkness=1.0 a saturated field pixel goes to exactly 0.
- Audio-driven field terms (e.g. `× (0.7 + level*0.5)`) stay — they shape the FIELD, not the background.
- **`SRNegativeRain` (class at `pi/app/effects/negative_space.py:456-598`) must not change: `git diff` for that line range must be empty.** `sr_negative_rain` stays registered unchanged.
- New effects register in `NEGATIVE_SPACE_EFFECTS`; `registry.py` and `main.py` catalog loops pick the dict up automatically — verify, don't duplicate registration.
- Tests: `cd pi && source .venv/bin/activate && PYTHONPATH=. pytest ...`. Known pre-existing failures to ignore: palette, ambient-count, media-metadata asyncio, width-policy, matrix-rain perf flake.
- Commit per task. Deploy at the end (`bash pi/scripts/deploy.sh ledfanatic.local`) — no firmware change.

---

### Task 1: `apply_darkness` helper (SSOT for the new semantics)

**Files:**
- Modify: `pi/app/effects/negative_space.py` (module level, after `_plasma_bg`)
- Test: `pi/tests/test_negative_darkness.py` (create)

**Interfaces:**
- Produces: `apply_darkness(frame_f: np.ndarray (w,h,3) float32, field: np.ndarray (w,h) float32 0..1, darkness: float) -> np.ndarray (w,h,3) float32`. Later tasks call it from every negative effect.

- [ ] **Step 1: Write the failing tests** — create `pi/tests/test_negative_darkness.py`:

```python
"""Darkness semantics: darkness drives element opacity + presence, never background."""

import numpy as np

from app.effects.negative_space import apply_darkness


class TestApplyDarkness:
  def test_full_darkness_saturated_field_is_black(self):
    frame = np.full((4, 4, 3), 200.0, dtype=np.float32)
    field = np.ones((4, 4), dtype=np.float32)
    out = apply_darkness(frame, field, 1.0)
    assert out.max() == 0.0

  def test_presence_boost_saturates_partial_field_at_max(self):
    # field 0.7 * (0.5 + 1.0) = 1.05 -> clipped 1.0 -> full black at darkness=1
    frame = np.full((2, 2, 3), 200.0, dtype=np.float32)
    field = np.full((2, 2), 0.7, dtype=np.float32)
    out = apply_darkness(frame, field, 1.0)
    assert out.max() == 0.0

  def test_zero_field_leaves_background_untouched(self):
    frame = np.full((3, 3, 3), 180.0, dtype=np.float32)
    field = np.zeros((3, 3), dtype=np.float32)
    for darkness in (0.1, 0.5, 1.0):
      out = apply_darkness(frame, field, darkness)
      assert np.allclose(out, frame)  # background independent of slider

  def test_monotonic_in_darkness(self):
    frame = np.full((2, 2, 3), 200.0, dtype=np.float32)
    field = np.full((2, 2), 0.5, dtype=np.float32)
    lo = apply_darkness(frame, field, 0.1).mean()
    mid = apply_darkness(frame, field, 0.5).mean()
    hi = apply_darkness(frame, field, 1.0).mean()
    assert lo > mid > hi
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=. pytest tests/test_negative_darkness.py -v`
Expected: FAIL with `ImportError: cannot import name 'apply_darkness'`

- [ ] **Step 3: Implement** — in `pi/app/effects/negative_space.py`, directly after the `_plasma_bg` function, add:

```python
def apply_darkness(frame_f: np.ndarray, field: np.ndarray, darkness: float) -> np.ndarray:
  """Occlude a bright frame with a darkness field (SSOT for the negative family).

  darkness controls the VOLUME of darkness: element presence (field boosted by
  0.5 + darkness) and opacity (scaled by darkness; 1.0 = true black at cores).
  It never dims the background — zero-field pixels are untouched.
  """
  occ = np.clip(field * (0.5 + darkness), 0.0, 1.0) * darkness
  return frame_f * (1.0 - occ[:, :, np.newaxis])
```

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=. pytest tests/test_negative_darkness.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add pi/app/effects/negative_space.py pi/tests/test_negative_darkness.py
git commit -m "feat: apply_darkness helper - unified negative-family darkness semantics"
```

---

### Task 2: Convert the 4 standalone negative_space effects + 10 spin-offs

**Files:**
- Modify: `pi/app/effects/negative_space.py` (SRShadowPulse, SRVoidBreath, SRLightningGap, SRSilhouette — params + composition lines only)
- Modify: `pi/app/effects/negative_spinoffs.py` (`_common_params` + `_NegativeFieldEffect.render` composition)
- Test: `pi/tests/test_negative_darkness.py` (append)

**Interfaces:**
- Consumes: `apply_darkness` from Task 1.
- Produces: all 14 effects honor the new semantics; **`SRNegativeRain` untouched** (verify with `git diff` on lines 456-598).

- [ ] **Step 1: Write the failing parametrized test** — append to `pi/tests/test_negative_darkness.py`:

```python
from app.core.renderer import RenderState
from app.effects.negative_space import NEGATIVE_SPACE_EFFECTS
from app.effects.negative_spinoffs import NEGATIVE_SPINOFF_EFFECTS

import pytest

LOUD = {
  'level': 0.9, 'bass': 0.9, 'mid': 0.7, 'high': 0.5,
  'beat': True, 'beat_frame_id': 1, 'bpm': 120.0, 'spectrum': [0.8] * 16,
}

AFFECTED = {**NEGATIVE_SPACE_EFFECTS, **NEGATIVE_SPINOFF_EFFECTS}
AFFECTED.pop('sr_negative_rain')  # frozen — original semantics


def _loud_state():
  state = RenderState()
  state._audio_lock_free = dict(LOUD)
  state._beat_this_frame = True
  return state


def _run_effect(cls, darkness, frames=90):
  eff = cls(width=20, height=40, params={'darkness': darkness})
  state = _loud_state()
  out = None
  for i in range(frames):
    out = eff.render(i / 30.0, state)
  return out.astype(np.float32)


class TestFamilyDarknessSemantics:
  @pytest.mark.parametrize("name,cls", sorted(AFFECTED.items()))
  def test_max_darkness_darker_than_low(self, name, cls):
    hi = _run_effect(cls, 1.0)
    lo = _run_effect(cls, 0.1)
    # More darkness volume => darker darkest-pixel and lower mean
    assert hi.min() < lo.min() - 5, f"{name}: min {hi.min()} vs {lo.min()}"
    assert hi.mean() < lo.mean(), name

  @pytest.mark.parametrize("name,cls", sorted(AFFECTED.items()))
  def test_max_darkness_reaches_near_black(self, name, cls):
    hi = _run_effect(cls, 1.0)
    assert hi.min() <= 8, f"{name}: darkest pixel {hi.min()} not near black"
    assert hi.mean() > 40, f"{name}: background collapsed (mean {hi.mean()})"

  @pytest.mark.parametrize("name,cls", sorted(AFFECTED.items()))
  def test_darkness_param_range(self, name, cls):
    p = next(p for p in cls.PARAMS if p.attr == 'darkness')
    assert p.lo == 0.1 and p.hi == 1.0, name
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=. pytest tests/test_negative_darkness.py -v -k Family`
Expected: FAILs (param range 0.5, near-black unreachable with old 0.95-capped composition). If `test_max_darkness_darker_than_low` already passes for some effects, that's fine — range and near-black must fail.

- [ ] **Step 3: Convert `negative_space.py` effects.** Exact edits (line numbers pre-Task-1; adjust for the helper insertion):

Param lines — change all four (`SRShadowPulse` ~108, `SRVoidBreath` ~212, `SRLightningGap` ~313, `SRSilhouette` ~615; do NOT touch SRNegativeRain's ~470):

```python
    _P("Darkness", "darkness", 0.1, 1.0, 0.05, 0.95),
```

Composition lines — replace each final multiply with the helper:

SRShadowPulse (~189): `frame *= (1.0 - darkness[:, :, np.newaxis] * darkness_strength)` →
```python
      frame = apply_darkness(frame, darkness, darkness_strength)
```
SRVoidBreath (~273): `frame_f *= (1.0 - void_mask[:, :, np.newaxis] * darkness_strength)` →
```python
    frame_f = apply_darkness(frame_f, void_mask, darkness_strength)
```
SRLightningGap (~438): `frame_f *= (1.0 - darkness[:, :, np.newaxis] * darkness_strength)` →
```python
      frame_f = apply_darkness(frame_f, darkness, darkness_strength)
```
SRSilhouette (~745): `frame_f *= (1.0 - darkness[:, :, np.newaxis] * darkness_strength)` →
```python
      frame_f = apply_darkness(frame_f, darkness, darkness_strength)
```

Keep every `np.clip(darkness * (0.7 + level * 0.5), 0, 1)` audio term as-is (it shapes the field).

- [ ] **Step 4: Convert the spin-offs.** In `pi/app/effects/negative_spinoffs.py`:

Import (line 25): `from .negative_space import _P, _plasma_bg, _noise_2d` →
```python
from .negative_space import _P, _plasma_bg, _noise_2d, apply_darkness
```

`_common_params` (line 34): `_P("Darkness", "darkness", 0.5, 1.0, 0.05, darkness),` →
```python
    _P("Darkness", "darkness", 0.1, 1.0, 0.05, darkness),
```

`_NegativeFieldEffect.render` composition (lines 127-128):
```python
    d = np.clip(self._dark, 0.0, 1.0) * self._param('darkness', 0.9)
    frame *= (1.0 - d[:, :, np.newaxis])
```
→
```python
    frame = apply_darkness(frame, np.clip(self._dark, 0.0, 1.0),
                           self._param('darkness', 0.9))
```

- [ ] **Step 5: Run tests**

Run: `PYTHONPATH=. pytest tests/test_negative_darkness.py tests/test_sound_variants.py -v 2>&1 | tail -8`
Expected: all new parametrized tests PASS (42 = 14 effects × 3), sound-variant tests unaffected. If a specific effect fails `near_black` because its field never reaches ≥0.67 even under LOUD audio, boost that effect's FIELD amplitude at its stamp/mask site (not the background, not the helper) and note it in the commit message.

- [ ] **Step 6: Verify SRNegativeRain untouched**

Run: `git diff -U0 pi/app/effects/negative_space.py | grep -A2 -B2 "^@@" | head -40` and confirm no hunk falls inside the `SRNegativeRain` class range; also `PYTHONPATH=. pytest tests/ -q -k negative 2>&1 | tail -3`.

- [ ] **Step 7: Commit**

```bash
git add pi/app/effects/negative_space.py pi/app/effects/negative_spinoffs.py pi/tests/test_negative_darkness.py
git commit -m "feat: darkness overhaul - true black + presence scaling across negative family (rain untouched)"
```

---

### Task 3: SR Negative Ripples

**Files:**
- Modify: `pi/app/effects/negative_space.py` (new class after SRSilhouette + registry dict entry)
- Test: `pi/tests/test_negative_darkness.py` (append)

**Interfaces:**
- Consumes: `apply_darkness`, `_plasma_bg`, `_P` (same module); `AudioCompatAdapter` from `app.audio.adapter` (fields: `bass`, `mids`, `highs`, `beat`, `beat_energy`, `is_downbeat`, `is_phrase`).
- Produces: `SRNegativeRipples` registered as `'sr_negative_ripples'`.

- [ ] **Step 1: Write the failing tests** — append to `pi/tests/test_negative_darkness.py`:

```python
from app.effects.negative_space import SRNegativeRipples


class TestNegativeRipples:
  def test_registered(self):
    assert NEGATIVE_SPACE_EFFECTS['sr_negative_ripples'] is SRNegativeRipples

  def test_renders_dark_rings_on_bright_bg(self):
    out = _run_effect(SRNegativeRipples, 1.0)
    assert out.min() <= 8       # dark ring cores near black
    assert out.mean() > 40      # plasma background alive

  def test_silence_stays_bright(self):
    eff = SRNegativeRipples(width=20, height=40, params={'darkness': 1.0})
    state = RenderState()  # default silent audio
    out = None
    for i in range(60):
      out = eff.render(i / 30.0, state)
    assert out.astype(np.float32).mean() > 60  # no onsets -> no rings
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=. pytest tests/test_negative_darkness.py -v -k Ripples`
Expected: FAIL with ImportError (`SRNegativeRipples`)

- [ ] **Step 3: Implement** — add to `pi/app/effects/negative_space.py` after `SRSilhouette`, and add `'sr_negative_ripples': SRNegativeRipples,` to `NEGATIVE_SPACE_EFFECTS`:

```python
class SRNegativeRipples(Effect):
  """Bright plasma with dark beat-tracked ripples — negative of SR Sound Ripples."""

  CATEGORY = "sound"
  DISPLAY_NAME = "SR Negative Ripples"
  DESCRIPTION = "Dark rings ripple through a bright world — kicks low, snares mid, hats high"
  PALETTE_SUPPORT = False
  AUDIO_REQUIRES = ('level', 'bass', 'mid', 'high', 'beat')

  PARAMS = [
    _P("Gain", "gain", 0.2, 5.0, 0.1, 2.0),
    _P("Speed", "speed", 0.3, 4.0, 0.1, 1.5),
    _P("Decay", "decay", 0.85, 0.99, 0.01, 0.93),
    _P("Sensitivity", "sensitivity", 0.02, 0.5, 0.02, 0.15),
    _P("Darkness", "darkness", 0.1, 1.0, 0.05, 0.95),
  ]

  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    from ..audio.adapter import AudioCompatAdapter
    self._audio_adapter = AudioCompatAdapter()
    self._ripples: list = []   # [cx, cy, radius, intensity, ring_width]
    self._bass_prev = 0.0
    self._mids_prev = 0.0
    self._highs_prev = 0.0
    self._last_t = None
    self._prev_frame = None
    xs = np.arange(width, dtype=np.float32)
    ys = np.arange(height, dtype=np.float32)
    self._gx, self._gy = np.meshgrid(xs, ys, indexing='ij')

  def render(self, t: float, state) -> np.ndarray:
    if self._last_t is None:
      self._last_t = t
    dt = float(np.clip(t - self._last_t, 1e-4, 0.05))
    self._last_t = t
    elapsed = self.elapsed(t)

    audio = self._audio_adapter.adapt(state._audio_lock_free, t)
    gain = self.params.get('gain', 2.0)
    speed = self.params.get('speed', 1.5)
    decay = self.params.get('decay', 0.93)
    sens = self.params.get('sensitivity', 0.15)
    darkness_strength = self.params.get('darkness', 0.95)

    # Onset detection — frequency-mapped spawn heights (screen coords, y=0 top)
    bass_delta = audio.bass - self._bass_prev
    if bass_delta > sens or audio.beat:
      intensity = float(np.clip(max(bass_delta, audio.beat_energy * 0.3) * 2 * gain, 0, 1.5))
      self._ripples.append([self.width / 2.0, self.height * 0.85, 0.0, intensity, 5.0])
    self._bass_prev = audio.bass

    mids_delta = audio.mids - self._mids_prev
    if mids_delta > sens * 1.5:
      self._ripples.append([np.random.uniform(1, self.width - 2), self.height * 0.5,
                            0.0, float(np.clip(mids_delta * 3 * gain, 0, 1.2)), 3.0])
    self._mids_prev = audio.mids

    highs_delta = audio.highs - self._highs_prev
    if highs_delta > sens * 0.8:
      self._ripples.append([np.random.uniform(0, self.width - 1), self.height * 0.15,
                            0.0, float(np.clip(highs_delta * 2 * gain, 0, 1.0)), 2.0])
    self._highs_prev = audio.highs

    if audio.is_phrase:
      self._ripples.append([self.width / 2.0, self.height / 2.0, 0.0, 1.5, 8.0])
    elif audio.is_downbeat:
      self._ripples.append([self.width / 2.0, self.height * 0.7, 0.0, 1.0, 6.0])

    # Expand, decay, cull
    field = np.zeros((self.width, self.height), dtype=np.float32)
    alive = []
    for r in self._ripples:
      r[2] += speed * 80 * dt
      r[3] *= decay ** (dt * 60)
      if r[3] > 0.015 and r[2] < self.height * 1.5:
        alive.append(r)
        dx = (self._gx - r[0]) * (self.height / self.width)  # aspect correction
        dy = self._gy - r[1]
        dist = np.sqrt(dx * dx + dy * dy)
        ring = np.exp(-((dist - r[2]) ** 2) / (2.0 * r[4] * r[4])) * min(r[3], 1.0)
        np.maximum(field, ring, out=field)
    self._ripples = alive

    frame_f = _plasma_bg(self._gx, self._gy, elapsed, self.width, self.height).astype(np.float32)
    frame_f = apply_darkness(frame_f, field, darkness_strength)
    result = np.clip(frame_f, 0, 255).astype(np.uint8)

    # Temporal blend for smooth trails
    if self._prev_frame is not None:
      result = (result.astype(np.float32) * 0.7
                + self._prev_frame.astype(np.float32) * 0.3).astype(np.uint8)
    self._prev_frame = result
    return result
```

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=. pytest tests/test_negative_darkness.py -v -k Ripples`
Expected: 3 PASS. (If `test_renders_dark_rings_on_bright_bg` misses near-black, the LOUD fixture's constant bass means `bass_delta` is 0 after frame 1 but `audio.beat` stays truthy — rings spawn every frame at the kick point and saturate; verify `AudioCompatAdapter.adapt` yields `beat=True` from the fixture before weakening the assertion.)

- [ ] **Step 5: Commit**

```bash
git add pi/app/effects/negative_space.py pi/tests/test_negative_darkness.py
git commit -m "feat: SR Negative Ripples - dark beat-tracked ripples on plasma"
```

---

### Task 4: SR Negative Rain 2

**Files:**
- Modify: `pi/app/effects/negative_space.py` (new class `SRNegativeRain2` directly after `SRNegativeRain` + registry entry `'sr_negative_rain2'`)
- Test: `pi/tests/test_negative_darkness.py` (append)

**Interfaces:**
- Consumes: `apply_darkness`; the existing `SRNegativeRain` source (copy origin).
- Produces: `SRNegativeRain2` registered as `'sr_negative_rain2'`; original class byte-identical.

- [ ] **Step 1: Write the failing tests** — append:

```python
from app.effects.negative_space import SRNegativeRain, SRNegativeRain2


class TestNegativeRain2:
  def test_registered_and_original_untouched(self):
    assert NEGATIVE_SPACE_EFFECTS['sr_negative_rain2'] is SRNegativeRain2
    assert NEGATIVE_SPACE_EFFECTS['sr_negative_rain'] is SRNegativeRain
    # Original keeps OLD param range (0.5 floor) — proof it wasn't converted
    p = next(p for p in SRNegativeRain.PARAMS if p.attr == 'darkness')
    assert p.lo == 0.5
    p2 = next(p for p in SRNegativeRain2.PARAMS if p.attr == 'darkness')
    assert p2.lo == 0.1 and p2.hi == 1.0

  def test_rain2_reaches_near_black(self):
    out = _run_effect(SRNegativeRain2, 1.0)
    assert out.min() <= 8
    assert out.mean() > 40
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=. pytest tests/test_negative_darkness.py -v -k Rain2`
Expected: FAIL with ImportError (`SRNegativeRain2`)

- [ ] **Step 3: Implement.** Copy the entire `SRNegativeRain` class source verbatim to a new class `SRNegativeRain2` placed directly after it, then apply EXACTLY these deltas to the copy (and nothing else):

1. Class name/docstring/labels:
```python
class SRNegativeRain2(Effect):
  """Negative Rain with the new darkness semantics — more darkness on tap."""
```
   `DISPLAY_NAME = "SR Negative Rain 2"`, `DESCRIPTION = "Negative Rain with a true-black darkness dial"`.
2. Darkness param line → `_P("Darkness", "darkness", 0.1, 1.0, 0.05, 0.95),`
3. Spawn rate gains the presence factor — original:
```python
    spawn_rate = density * (0.5 + bass * 3.0) * self.width * 0.5
```
   copy becomes:
```python
    spawn_rate = density * (0.5 + bass * 3.0) * self.width * 0.5 * (0.5 + darkness_strength)
```
4. Composition — original:
```python
      darkness = np.clip(darkness * (0.7 + level * 0.5), 0, 1)
      frame_f *= (1.0 - darkness[:, :, np.newaxis] * darkness_strength)
```
   copy becomes:
```python
      darkness = np.clip(darkness * (0.7 + level * 0.5), 0, 1)
      frame_f = apply_darkness(frame_f, darkness, darkness_strength)
```

Register: `'sr_negative_rain2': SRNegativeRain2,` in `NEGATIVE_SPACE_EFFECTS` (after the rain entry).

- [ ] **Step 4: Run tests + verify freeze**

Run: `PYTHONPATH=. pytest tests/test_negative_darkness.py -v 2>&1 | tail -5`
Expected: all PASS. Then `git diff pi/app/effects/negative_space.py | grep "^-" | grep -v "^---"` — confirm no removed lines belong to the original `SRNegativeRain` class body.

- [ ] **Step 5: Commit**

```bash
git add pi/app/effects/negative_space.py pi/tests/test_negative_darkness.py
git commit -m "feat: SR Negative Rain 2 - clone with true-black darkness dial"
```

---

### Task 5: Full suite, deploy, live verification

- [ ] **Step 1:** `PYTHONPATH=. pytest tests/ -q 2>&1 | tail -4` — expect only the known pre-existing failures; new count of passing tests grows by the new file's tests.
- [ ] **Step 2:** Confirm both new effects appear in the catalog: `python -c "from app.effects.catalog import EffectCatalogService"` is NOT sufficient — instead run the registry check: `PYTHONPATH=. python -c "from app.effects.registry import ALL_EFFECTS; assert 'sr_negative_ripples' in ALL_EFFECTS and 'sr_negative_rain2' in ALL_EFFECTS; print('registered ok')"`.
- [ ] **Step 3:** `git push origin main && bash pi/scripts/deploy.sh ledfanatic.local`.
- [ ] **Step 4:** Live check with the user: play music; SR Negative Ripples shows kick ripples from the bottom / snare mid / hat top with dark rings; crank Darkness to 1.0 on any converted effect and confirm true-black cores with the plasma background still vivid; confirm SR Negative Rain (original) looks exactly as before; both new effects reachable via the pattern knob (Sound Reactive category grew by 2 — note positions shift).
