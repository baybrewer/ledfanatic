# LED Animations Integration Plan

## Goal

Port the complete `led-animations/led_sim.py` simulator (27 animations, 10 palettes, audio-reactive state machine, parameter system) into `pillar-controller` as a production-quality feature set. Add animation switcher, inline simulator preview, per-animation speed/palette controls, editable setup fields, and user-facing help text on every page.

## Source inventory

| Category | Count | Names |
|----------|-------|-------|
| Classic | 5 | Rainbow Cycle, Feldstein Equation, Feldstein OG, Brett's Favorite, Fireplace |
| Ambient | 12 | Plasma, Aurora Borealis, Lava Lamp, Ocean Waves, Starfield, Matrix Rain, Breathing, Fireflies, Nebula, Kaleidoscope, Flow Field, Moire |
| Sound | 10 | Spectrum, VU Meter, Beat Pulse, Bass Fire, Sound Ripples, Spectrogram, Sound Worm, Particle Burst, Sound Plasma, Strobe Chaos |

## Key additions beyond raw port

1. **Speed slider** on every animation (uniform `speed` param mapped to each anim's internal speed)
2. **Palette selector** dropdown on every palette-capable animation (10 standard + 16 Feldstein)
3. **Animation Switcher** — meta-animation: user picks N animations, system cross-fades between them on a timer (5–60s adjustable)
4. **Inline simulator** — right-side canvas showing real-time LED preview with small dot pixels matching physical strip layout from setup config
5. **Editable setup** — all strip fields (label, enabled, LED count, color order, direction, channel, slot) are editable inline with validation
6. **Help text** — every tab/page gets a collapsible instruction panel explaining what it does and how to use it

## Phases

| Phase | Description | Doc |
|-------|-------------|-----|
| 1 | Port core engine (noise, palettes, helpers, LED buffer) | `01_CORE_ENGINE.md` |
| 2 | Port all 27 animations as repo-native Effect classes | `02_ANIMATIONS.md` |
| 3 | Add speed/palette UI controls to Effects tab | `03_CONTROLS.md` |
| 4 | Build Animation Switcher meta-effect | `04_SWITCHER.md` |
| 5 | Upgrade Sim tab with inline pixel-dot preview | `05_SIMULATOR.md` |
| 6 | Make Setup fields editable with validation | `06_SETUP_EDIT.md` |
| 7 | Add help/instructions to all pages | `07_HELP_TEXT.md` |
| 8 | Integration test, deploy, verify | `08_VERIFY.md` |

## Non-negotiables

- No Pygame dependency in production — everything renders to numpy arrays
- Existing effects keep working unchanged
- Audio adapter (already built) provides the bridge for sound-reactive animations
- Preview runs in browser canvas, not desktop window
- Per-strip color order and LED count from installation.yaml are respected
- All 359 existing tests continue to pass
