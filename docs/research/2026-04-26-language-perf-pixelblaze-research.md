# Research Brief: Language Performance, Pixelblaze Architecture, and Optimization Opportunities

**Date:** 2026-04-26 (revised after Codex review)
**Project:** pillar-controller (Raspberry Pi 4 + Teensy 4.1 + OctoWS2811)
**Status:** Research — pending measured benchmarks on live hardware

---

## Context

pillar-controller is a Python 3.14 LED animation system running on Raspberry Pi 4:
- FastAPI backend, NumPy-vectorized effects, 60 FPS target
- **Layout is dynamic** — geometry is defined in `layout.yaml`, compiled at startup, and can be changed at runtime via the layout API. All performance figures in this document are layout-dependent.
- Checked-in default layout: 10x83 = 830 pixels, 5 output channels (`pi/config/layout.yaml`)
- The Pi's active layout may differ from the checked-in default (persisted via layout API)
- Transport: USB serial (USB CDC) to Teensy 4.1 for DMA LED output
- Per-frame budget at 60 FPS: 16.67ms

**Important: Performance numbers in this document are estimates unless marked as "measured."** Before acting on optimization priorities, run `python -m tools.bench_effects` on the Pi with the active layout to get real numbers. See "Prerequisite: Establish Measured Baseline" below.

## Questions for Analysis

### 1. Do different programming languages run at different speeds on Raspberry Pi?

**Short answer: Yes, dramatically for pure computation. Less so when libraries do the heavy lifting.**

**Estimated benchmark ratios on RPi 4 (ARM Cortex-A72, 1.5GHz):**

| Workload | Pure Python | NumPy (Python) | C++ (-O2) | Ratio (Python:C++) |
|----------|------------|----------------|-----------|---------------------|
| Tight math loop (1M iterations) | ~800ms | N/A | ~5ms | ~160x |
| Array ops on 830-element buffer | ~1ms | ~0.01ms | ~0.008ms | NumPy ~= C++ |
| Sine over grid-sized array | ~8ms | ~0.05ms | ~0.04ms | NumPy ~= C++ |
| Per-pixel loop with branching | ~1.5ms (830px) | Can't vectorize | ~0.01ms | ~150x |
| Particle update (600 objects) | ~5-8ms | Partial help | ~0.05ms | ~100-160x |

**Key ratios:**
- Pure Python vs C++: **100-200x slower** for computation-heavy loops
- NumPy-Python vs C++: **1-2x** (NumPy internals ARE compiled C/Fortran with SIMD)
- The gap ONLY matters for code that can't be vectorized (particle systems, game logic, per-pixel branching)

**Why this matters for pillar-controller:**
- Most effects already use NumPy vectorization = already at ~C speed
- `pack_frame()` is a pure-Python loop over `layout.entries` — cost scales linearly with pixel count
- Particle effects (fireworks: up to 600 sparks, pure Python loops) have non-vectorized hot paths
- The web server, config handling, state management — these are NOT bottlenecks regardless of language

### 2. How hard would it be to convert this Python project to C++?

**Component-by-component assessment:**

| Component | Python | C++ Equivalent | Difficulty | Speedup |
|-----------|--------|---------------|------------|---------|
| FastAPI web server | ~200 lines | Crow/Drogon (~400 lines) | HIGH | None (not a bottleneck) |
| asyncio event loop | Built-in | libuv/Boost.Asio | HIGH | None (not a bottleneck) |
| NumPy effects | ~2000 lines | Eigen/manual SIMD | VERY HIGH | Minimal (NumPy is already C) |
| pack_frame() | 38 lines | C extension | LOW | Measurable at higher pixel counts |
| USB CDC transport | 352 lines | libusb/termios | MODERATE | Unlikely (see transport note) |
| YAML config/state | 300 lines | yaml-cpp + nlohmann/json | MODERATE | None |
| Brightness/solar | 232 lines | Manual implementation | MODERATE | None |
| Effect base classes | 200 lines | Virtual classes + templates | MODERATE | None |
| Media playback (PIL) | ~150 lines | FFmpeg/OpenCV | HIGH | Maybe faster decode |

**Total estimated effort:** 3-6 person-months for a full rewrite
**Is it worth it?** No. The system hits 60 FPS on the current layout. Surgical optimizations on measured bottlenecks are the right approach.

### 3. Is C++ the best language for LED animation?

**What serious LED projects actually use:**

| Project | Language | Platform | Approach |
|---------|----------|----------|----------|
| **FastLED** | C++ (Arduino) | ESP32/AVR | Direct hardware, no OS overhead |
| **WLED** | C++ (Arduino) | ESP32/ESP8266 | 180+ effects, web UI |
| **Pixelblaze** | Custom JS bytecode | ESP32 | Bytecode-compiled JS subset |
| **OctoWS2811** | C++ | Teensy | DMA output (what we use) |
| **pillar-controller** | Python + NumPy | RPi 4 + Teensy | Split architecture |
| **FadeCandy** | C (firmware) + any (host) | Teensy 3 | Similar split to ours |
| **Art-Net/sACN** | Various | Various | Protocol-based, language-agnostic |
| **LED Lab** | Rust | RPi/Linux | Newer, gaining traction |

**Analysis:**
- C++ dominates on **microcontrollers** (no OS, direct hardware, tight timing)
- On a **full Linux SBC like RPi**, the language matters less because:
  - The OS handles scheduling, I/O, networking
  - NumPy provides C-speed math
  - The real bottleneck is I/O and effect complexity, not language overhead
- **Rust** is viable but the LED ecosystem is small
- **Python + NumPy** is the sweet spot for RPi when paired with C firmware (Teensy)

**Our architecture (Python Pi + C++ Teensy) is the correct split.** The Pi handles orchestration, effects, UI, audio, media. The Teensy handles timing-critical DMA output. This is exactly what FadeCandy and similar projects do.

### 4. What does Pixelblaze use?

**Hardware:** ESP32 (240MHz dual-core Xtensa, 520KB SRAM, no OS)
**Language:** Custom subset of JavaScript with LED-specific extensions
**Execution model:**
- Patterns written in JS subset (control flow, loops, functions, arrays)
- Custom compiler converts JS to **bytecode**
- Custom **bytecode VM** executes on bare metal ESP32
- NOT interpreted — compiled to intermediate representation

**Pattern model:**
```javascript
// Pixelblaze pattern example
export function render(index) {
  // Called once per pixel per frame
  h = index / pixelCount + wave(time(0.1))
  s = 1
  v = wave(index / pixelCount * 2 + time(0.05))
  hsv(h, s, v)
}
```
- `render(index)` called per-pixel (1D)
- `render2D(index, x, y)` called per-pixel with normalized coordinates (2D)
- `render3D(index, x, y, z)` for 3D mapped installations
- Built-in functions: `time()`, `wave()`, `triangle()`, `sin()`, `random()`, `hsv()`, `rgb()`

**Performance:**
- 48,000 pixels/sec throughput
- At 830 pixels (our default layout): ~58 FPS — comparable to ours
- At 5,000 pixels: ~9.6 FPS
- At larger layouts our system should pull ahead significantly

**Live editing:**
- Web-based editor with syntax highlighting
- Patterns recompile on every keystroke
- Live preview updates in real-time
- .epe file format (JSON with source + preview image + bytecode)

**Pixel mapping:**
- JSON array of [x, y, z] coordinates per pixel
- Patterns receive normalized coordinates (0-1) in render2D/render3D
- Decouples physical layout from pattern logic

**Key insight:** Pixelblaze achieves a lot on a 240MHz microcontroller with no OS by using a custom bytecode VM. Its strength is simplicity and accessibility, not raw performance.

### 5. What can we learn from Pixelblaze to optimize this repo?

**Applicable Pixelblaze patterns:**

| Pixelblaze Feature | Our Status | Opportunity |
|-------------------|------------|-------------|
| Bytecode-compiled effects | NumPy vectorization (faster) | Expression engine for user effects (feature, not perf) |
| Per-pixel render model | Grid-based NumPy ops (better for Pi) | Already superior |
| Coordinate mapping | layout.yaml SSOT | Already implemented |
| Live preview | WebSocket frame streaming | Already implemented |
| Per-effect sliders | PARAMS system per effect | Already implemented |
| Pattern sharing (.epe) | Not implemented | LOW priority — nice-to-have |
| Instant recompilation | Hot reload via API | Could improve dev workflow |
| Built-in math functions | NumPy + custom helpers | Already have perlin_grid, pal_color_grid |

**What Pixelblaze does that we DON'T (and could benefit from):**

1. **User-editable expression engine** — Let users write simple formulas that generate patterns without Python knowledge. Pixelblaze's killer feature.

2. **Pattern library/export format** — Portable effect definitions that can be shared.

3. **Coordinate-space normalization** — Pixelblaze normalizes all coordinates to 0-1 range. See Priority 5 below for nuances.

**What Pixelblaze does that we already do BETTER:**

1. **Audio reactivity** — Pixelblaze has basic sound via sensor board; we have full FFT, beat detection, BPM
2. **Media playback** — Pixelblaze can't play video/images; we can
3. **Complex effects** — Particle systems, tetris, fireworks — impossible in Pixelblaze's simple expression model
4. **Dynamic layout** — Our layout is runtime-configurable via API; Pixelblaze's pixel map is static JSON

---

## Optimization Roadmap

### Prerequisite: Establish Measured Baseline (MUST DO FIRST)

Before acting on any optimization, we need real numbers from the live hardware with the active layout.

**Problem identified by review:** The existing benchmark harness (`pi/tools/bench_effects.py`) only enumerates `EFFECTS`, `AUDIO_EFFECTS`, and `IMPORTED_EFFECTS` from their respective module dicts. Effects registered ad-hoc in `main.py` — including `sr_fireworks`, `tetris`, `tetris_auto`, `scrolling_text`, and `animation_switcher` — are **not benchmarked**.

**Required actions:**

1. **Unify effect registration as SSOT.** Create a single canonical registry that `main.py` and `bench_effects.py` both consume. Currently `main.py` registers effects from 4 sources plus 5 ad-hoc registrations (lines 131-149). The benchmark only sees 3 of those sources.

2. **Run benchmarks on the Pi with active layout.** The checked-in `layout.yaml` is 10x83 = 830 pixels. The Pi's active layout may differ. Benchmarks must note which layout was active.

3. **Report layout in benchmark output.** `bench_effects.py` should print the active layout dimensions and pixel count alongside timing results.

**Effort:** 2-4 hours

### Priority 1: Effect-Level Profiling in the Render Loop (HIGH impact, LOW effort)

- **Target:** All effects, live on Pi
- **Approach:** Add timing measurement around `effect.render()` in `renderer._render_frame()`, expose via render state and the status API
- **Why:** Can't optimize what you can't measure. The benchmark harness is offline; we also need live per-frame timing to identify real-world bottlenecks (audio interaction, cache misses, etc.)
- **Effort:** 1-2 hours

### Priority 2: Vectorize Particle Systems (CONDITIONAL — measure first)

- **Target:** SRFireworks, Spark effects
- **Current:** Python for-loops over up to 600 particle objects
- **Approach:** Store particles as NumPy structured arrays; update positions/velocities with vectorized ops
- **Gating condition:** Only pursue if measured render time for these effects exceeds 5ms on the active layout. Codex review measured SRFireworks at only 0.31ms avg on the 10x83 layout — the Python loop overhead may be negligible at 830 pixels. At higher pixel counts or larger particle pools this changes.
- **Effort:** 4-8 hours per effect

### Priority 3: Cython pack_frame() (CONDITIONAL — scales with pixel count)

- **Target:** `pi/app/layout/packer.py`
- **Current:** Pure Python loop over `layout.entries`
- **Gating condition:** Cost is ~0.32ms at 830 pixels. At 5,000+ pixels it would be ~2ms+. Only worth doing if targeting larger layouts.
- **Approach:** Cython or ctypes C extension for the tight loop
- **Effort:** 2-4 hours

### Priority 4: Media Frame Cache Keyed by Geometry (MEDIUM impact, LOW effort)

- **Target:** `pi/app/effects/media_playback.py`
- **Problem (identified by review):** The original proposal ("pre-compute resized frames at import time") is wrong for a dynamic-layout system. `MediaPlayback` is instantiated with the current renderer's width/height, and layout can change at runtime via the setup screen. Pre-computing at import time would cache the wrong geometry.
- **Correct approach:** Cache resized frames keyed by `(item_id, frame_idx, width, height, fit_mode)`. When layout changes, the effect is recreated with new dimensions and the old cache entries naturally become unused. Do NOT couple cache shape to module import time.
- **Effort:** 2-3 hours

### Priority 5: Coordinate Normalization for Effects (FEATURE — needs design)

- **Pixelblaze-inspired:** Normalize coordinates to 0-1 so effects are resolution-independent
- **Problem (identified by review):** The base `Effect` class doesn't expose coordinates at all — effects receive only `width`, `height`, `params`, and render state. Separately, the setup/geometry pipeline already uses normalized UV coordinates for spatial fitting (`pi/app/setup/geometry.py`). These are different schemas for different concerns (calibration vs. rendering vs. effect authoring).
- **Required design work:** Define where the canonical coordinate system lives. Options:
  - A) Base class provides `self.normalized_x` / `self.normalized_y` grids (simple, effects opt in)
  - B) Effects receive a `CoordinateContext` object with both pixel and normalized coords
  - C) Renderer pre-computes normalized grids from the compiled layout and passes to effects
- **Risk:** Mixing physical-layout calibration, logical matrix rendering, and effect authoring into one abstraction. Keep these concerns separate.
- **Effort:** Needs spec before estimating

### Priority 6: Expression Engine for User Effects (FEATURE, HIGH effort)

- **Pixelblaze-inspired:** Let users write formulas like `hsv(x/width + wave(t), 1, 1)`
- **Approach:** Sandboxed DSL compiled to NumPy ops (or use `numexpr`)
- **Expected impact:** New feature, not performance
- **Effort:** 2-4 weeks

### Priority 7: Transport Throughput Measurement (LOW priority — replaced baud rate)

- **Original proposal was wrong (identified by review):** The brief proposed raising USB "baud rate" from 115200 to 230400 for frame savings. However, the Teensy connection is USB CDC (virtual serial), not a raw UART link. The baud rate parameter passed to `pyserial` may be advisory or irrelevant — USB CDC transfers at USB 2.0 Full Speed (12 Mbps) regardless of the configured baud.
- **Correct approach:** If transport latency is a concern, measure end-to-end throughput: `frame_packet()` + `serial.write()` + firmware consume time. Profile whether `asyncio.to_thread()` context-switch overhead is significant. Only then decide if transport changes are warranted.
- **Likely conclusion:** Transport is not a meaningful bottleneck. The ~1-3ms observed includes context-switch overhead and OS scheduling, not wire time.
- **Effort:** 1 hour to measure; likely no action needed

### NOT Recommended: Full C++ Rewrite
- **Effort:** 3-6 person-months
- **Gain:** Marginal on current layout
- **Risk:** Lose rapid prototyping, Python ecosystem, ease of adding effects
- **Verdict:** The current architecture is correct. Optimize hot paths surgically, guided by measurements.

---

## Questions for Codex Review (Round 2)

1. **Effect registry SSOT:** What's the cleanest way to unify effect registration so `main.py`, `bench_effects.py`, and the catalog API all consume the same source? A central `EFFECT_REGISTRY` dict in a dedicated module? Or a decorator-based auto-registration pattern?

2. **For the particle system vectorization:** What's the best NumPy pattern for variable-count particles with per-particle state (position, velocity, color, lifetime)? Structured arrays vs. parallel flat arrays?

3. **For pack_frame() optimization:** Cython vs ctypes vs cffi vs a small C extension module — what's the best approach for a single tight loop on RPi 4?

4. **Expression engine:** Is `numexpr` sufficient for a Pixelblaze-style expression language, or does this need a custom parser/compiler? What's the security model for user-submitted code?

5. **Coordinate normalization:** Given three existing coordinate concerns (layout calibration, logical matrix, effect authoring), what's the right abstraction boundary? Should effects even know about coordinates, or should the base class just provide normalized grids?

6. **Scaling to 5,000+ pixels:** What changes would be needed? Which components hit their scaling limits first?
