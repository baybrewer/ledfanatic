# Negative Ripples + Darkness Semantics Overhaul — Design

**Date:** 2026-09-18
**Status:** Approved pending review

## Goal

1. New effect **SR Negative Ripples**: darkness version of SR Sound Ripples.
2. New effect **SR Negative Rain 2**: clone of SR Negative Rain with the new darkness semantics (original untouched).
3. Unify the **Darkness slider** across the negative family: darkness controls the *volume of darkness* (opacity to true black + element coverage), never the background brightness.

## Darkness semantics (applies to every effect below)

- Param: `_P("Darkness", "darkness", 0.1, 1.0, 0.05, 0.95)` — range widened; **1.0 = pure black at dark cores** (today occlusion caps at 0.95 and never reaches black).
- Darkness scales two things, only on the negative elements:
  - **Opacity**: final occlusion factor = element intensity × darkness, unclamped below 1.0 so darkness=1.0 with full-intensity element → pixel exactly 0.
  - **Presence**: spawn rates / element sizes / coverage scale by `(0.5 + darkness)` (per-effect judgment on which knob: ring width, drop count, void radius, crack density, particle count).
- **Background decoupled**: the darkness slider must not appear in any background-brightness term. Existing couplings (e.g. occlusion scaled by `0.7 + level*0.5` stays — that's audio; any `* darkness` on background terms goes).

## Affected effects

- `pi/app/effects/negative_space.py`: `sr_shadow_pulse`, `sr_void_breath`, `sr_lightning_gap`, `sr_silhouette` — individual edits.
- `pi/app/effects/negative_spinoffs.py`: all 10 (`jets, flow, fireworks, snow, comets, vortex, aurora, bubbles, waves, orbits`) — primarily one change in the shared `_NegativeFieldEffect` base (Darkness param at base line ~34 + wherever the base composes occlusion); per-subclass touch-ups only where a subclass re-scales darkness locally.
- **`sr_negative_rain` is NOT modified in any way** (user: it's perfect).

## New effect 1: SR Negative Ripples (`sr_negative_ripples`, negative_space.py)

- Background: shared `_plasma_bg` (same as Negative Rain).
- Audio: `AudioCompatAdapter` (same pattern as SoundRipples in `pi/app/effects/imported/sound.py:663`), consuming onset deltas:
  - bass onset (`bass_delta > sensitivity` or beat) → dark ring from bottom (`y ≈ 0.85·h`, x center)
  - mids onset (`> 1.5×sens`) → dark ring from mid-height, random x
  - highs onset (`> 0.8×sens`) → small dark ring from top
  - phrase beat → one huge full-frame dark ring from center; downbeat → medium ring from `y ≈ 0.7·h`
- Rings expand at `speed`, intensity decays by `decay`, rendered as occlusion into the plasma background (ring profile = soft gaussian band around radius, aspect-corrected distance like the source effect). Temporal blend (~0.3 prev frame) for smooth trails.
- Params: Gain (0.2–5.0, 2.0), Speed (0.3–4.0, 1.5), Decay (0.85–0.99, 0.93), Sensitivity (0.02–0.5, 0.15), Darkness (new semantics). `PALETTE_SUPPORT = False`, `CATEGORY = "sound"`, `AUDIO_REQUIRES = ('level','bass','mid','high','beat')`.
- Registered in `NEGATIVE_SPACE_EFFECTS` dict; catalog metadata flows through the same registration path as its siblings (verify how negative_space effects reach `registry.py`/catalog and mirror it).

## New effect 2: SR Negative Rain 2 (`sr_negative_rain2`, negative_space.py)

- Verbatim copy of `SRNegativeRain` as a new class `SRNegativeRain2` (`DISPLAY_NAME = "SR Negative Rain 2"`), then apply the new darkness semantics to the copy:
  - Darkness param 0.1–1.0 default 0.95; occlusion reaches pure black at 1.0.
  - Spawn rate and drop trail length gain the `(0.5 + darkness)` presence factor.
  - Background stays exactly as the original.
- Original `SRNegativeRain` remains byte-for-byte untouched.

## Testing

- Render-smoke test for both new effects: nonzero output; frame contains dark pixels while elements active; background regions stay bright.
- Parametrized darkness test across all 16 affected/new effects (14 modified + 2 new): with synthetic loud audio state and `darkness=1.0`, at least one pixel reaches ≤ 2/255 while the frame mean stays > 60 (background bright); with `darkness=0.1` the frame minimum is substantially higher.
- Existing tests (`test_sound_variants.py`, imported-effect smoke tests) keep passing; `sr_negative_rain`'s rendered output is asserted unchanged via direct comparison against a pre-change capture (seeded RNG) OR simply by zero-diff on its code (git).
- Live: deploy, play music, verify by eye; knob category count unchanged (both new effects land in Sound Reactive automatically).

## Out of scope

- No changes to audio analyzer, brightness pipeline, or non-negative effects.
- No UI changes (params surface through existing param metadata automatically).
