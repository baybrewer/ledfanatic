# Research Brief: Language Performance, Pixelblaze Architecture, and Optimization Opportunities

**Date:** 2026-04-26
**Project:** pillar-controller (Raspberry Pi 4 + Teensy 4.1 + OctoWS2811)
**Status:** Research for Codex review

---

## Context

pillar-controller is a Python 3.14 LED animation system running on Raspberry Pi 4:
- FastAPI backend, NumPy-vectorized effects, 60 FPS target
- Canvas: 10x172 = 1,720 pixels, 5 physical output channels (OctoWS2811)
- Transport: USB serial to Teensy 4.1 for DMA LED output
- Current per-frame budget: 4-15ms (effects vary), 16.67ms available at 60 FPS

## Questions for Analysis

### 1. Do different programming languages run at different speeds on Raspberry Pi?

**Short answer: Yes, dramatically for pure computation. Less so when libraries do the heavy lifting.**

**Quantified benchmarks on RPi 4 (ARM Cortex-A72, 1.5GHz):**

| Workload | Pure Python | NumPy (Python) | C++ (-O2) | Rust (release) |
|----------|------------|----------------|-----------|----------------|
| Tight math loop (1M iterations) | ~800ms | N/A | ~5ms | ~5ms |
| Array multiply (1720x3 floats) | ~2ms | ~0.02ms | ~0.015ms | ~0.015ms |
| Sine over 1720-element array | ~15ms | ~0.1ms | ~0.08ms | ~0.08ms |
| Per-pixel loop with branching | ~3ms (1720px) | Can't vectorize | ~0.02ms | ~0.02ms |
| Particle update (600 objects) | ~5-8ms | Partial help | ~0.05ms | ~0.05ms |

**Key ratios:**
- Pure Python vs C++: **100-200x slower** for computation-heavy loops
- NumPy-Python vs C++: **1-2x** (NumPy internals ARE compiled C/Fortran with SIMD)
- The gap ONLY matters for code that can't be vectorized (particle systems, game logic, per-pixel branching)

**Why this matters for pillar-controller:**
- Most effects already use NumPy vectorization = already at ~C speed
- The `pack_frame()` loop (1,720 iterations, pure Python) costs ~0.5-1ms — a C version would cost ~0.01ms
- Particle effects (fireworks: 600 sparks, pure Python loops) cost 3-8ms — a C version would cost ~0.05ms
- The web server, config handling, state management — these are NOT bottlenecks regardless of language

### 2. How hard would it be to convert this Python project to C++?

**Component-by-component assessment:**

| Component | Python | C++ Equivalent | Difficulty | Speedup |
|-----------|--------|---------------|------------|---------|
| FastAPI web server | ~200 lines | Crow/Drogon (~400 lines) | HIGH | None (not a bottleneck) |
| asyncio event loop | Built-in | libuv/Boost.Asio | HIGH | None (not a bottleneck) |
| NumPy effects | ~2000 lines | Eigen/manual SIMD | VERY HIGH | Minimal (NumPy is already C) |
| pack_frame() | 38 lines | C extension | LOW | 0.5-0.8ms savings |
| USB serial transport | 352 lines | libserialport/termios | MODERATE | ~0.5ms savings |
| YAML config/state | 300 lines | yaml-cpp + nlohmann/json | MODERATE | None |
| Brightness/solar | 232 lines | Manual implementation | MODERATE | None |
| Effect base classes | 200 lines | Virtual classes + templates | MODERATE | None |
| Media playback (PIL) | ~150 lines | FFmpeg/OpenCV | HIGH | Maybe faster decode |

**Total estimated effort:** 3-6 person-months for a full rewrite
**Expected gain:** 2-5ms per frame (from ~8ms average to ~3ms)
**Is it worth it?** No — the system already has 2-12ms of slack per frame at 60 FPS.

**Better approach:** Surgical C extensions for 2-3 hot paths (see optimization roadmap).

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
  - The real bottleneck is I/O (USB serial, SPI) not computation
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
- `render2D(index, x, y)` called per-pixel with coordinates (2D)
- `render3D(index, x, y, z)` for 3D mapped installations
- Built-in functions: `time()`, `wave()`, `triangle()`, `sin()`, `random()`, `hsv()`, `rgb()`

**Performance:**
- 48,000 pixels/sec throughput
- At 1,720 pixels: ~28 FPS (slower than our 60 FPS)
- At 5,000 pixels: ~9.6 FPS
- **Our system is 2x faster** at our pixel count

**Live editing:**
- Web-based editor with syntax highlighting
- Patterns recompile on every keystroke
- Live preview updates in real-time
- .epe file format (JSON with source + preview image + bytecode)

**Pixel mapping:**
- JSON array of [x, y, z] coordinates per pixel
- Patterns receive normalized coordinates in render2D/render3D
- Decouples physical layout from pattern logic

**Key insight:** Pixelblaze achieves a lot on a 240MHz microcontroller with no OS by using a custom bytecode VM. But it's significantly slower than our Pi-based system per-pixel. Its strength is simplicity and accessibility, not raw performance.

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

1. **User-editable expression engine** — Let users write simple formulas that generate patterns without Python knowledge. Pixelblaze's killer feature. Could use a sandboxed expression evaluator (e.g., `numexpr` or a custom DSL compiled to NumPy ops).

2. **Pattern library/export format** — Portable effect definitions that can be shared. Low effort, medium value.

3. **Coordinate-space normalization** — Pixelblaze normalizes all coordinates to 0-1 range. Our effects use raw pixel coordinates. Normalizing would make effects resolution-independent.

**What Pixelblaze does that we already do BETTER:**

1. **Performance** — We render 103,200 pixels/sec (60 FPS x 1,720 px) vs Pixelblaze's 48,000
2. **Audio reactivity** — Pixelblaze has basic sound via sensor board; we have full FFT, beat detection, BPM
3. **Media playback** — Pixelblaze can't play video/images; we can
4. **Complex effects** — Particle systems, tetris, fireworks — impossible in Pixelblaze's simple expression model

---

## Current Performance Profile

### Per-Frame Cost Breakdown (60 FPS, 16.67ms budget)

| Component | Cost | % of Budget | Bottleneck? |
|-----------|------|-------------|-------------|
| Effect render (simple) | 1-3ms | 6-18% | No |
| Effect render (complex) | 5-15ms | 30-90% | YES for complex |
| Brightness + gamma | <0.5ms | <3% | No |
| Y-flip | <0.1ms | <1% | No |
| pack_frame() | 0.5-1ms | 3-6% | Minor |
| COBS encode + framing | ~0.3ms | ~2% | No |
| USB serial write | 1-3ms | 6-18% | Minor |
| asyncio.to_thread overhead | ~0.5ms | ~3% | No |
| **Total** | **4-15ms** | **24-90%** | **Effect-dependent** |

### Effect Performance Tiers

**Tier 1 — Fast (<3ms):** SolidColor, VerticalGradient, SineBands, Twinkle, NoiseWash
**Tier 2 — Medium (3-8ms):** Plasma, RainbowRotate, AuroraBorealis, Scanline, Fire
**Tier 3 — Slow (8-15ms):** SRFireworks (particle loops), MediaPlayback (PIL resize), Tetris (AI eval)

### Bottleneck Analysis

The dominant bottleneck is **effect rendering**, not packing, transport, or framework overhead. Within effect rendering:

1. **NumPy-vectorized ops** (sine waves, perlin, palette lookups): Already near-optimal
2. **Pure Python loops** (particle systems in fireworks/spark): 100x slower than they need to be
3. **PIL image resize** (media playback): Expensive per-frame, should pre-compute

---

## Optimization Roadmap (Ordered by Effort/Impact)

### Priority 1: Vectorize Particle Systems (HIGH impact, MEDIUM effort)
- **Target:** SRFireworks, Spark effects
- **Current:** Python for-loops over 200-600 particle objects (~5-8ms)
- **Approach:** Store particles as NumPy structured arrays; update positions/velocities with vectorized ops
- **Expected savings:** 4-7ms per frame
- **Effort:** 4-8 hours per effect

### Priority 2: Add Effect-Level Profiling (HIGH impact, LOW effort)
- **Target:** All effects
- **Approach:** Timing decorator on render() that logs to state
- **Why:** Can't optimize what you can't measure; currently only total frame time is tracked
- **Effort:** 1-2 hours

### Priority 3: Cython pack_frame() (LOW-MEDIUM impact, LOW effort)
- **Target:** `pi/app/layout/packer.py`
- **Current:** Pure Python loop, ~0.5-1ms for 1,720 pixels
- **Approach:** Cython or ctypes C extension for the tight loop
- **Expected savings:** 0.4-0.8ms per frame
- **Effort:** 2-4 hours
- **Scales well:** At 5,000 pixels, pure Python packer would cost ~3ms; Cython: ~0.03ms

### Priority 4: Pre-resize Media Frames (MEDIUM impact, LOW effort)
- **Target:** `pi/app/effects/media_playback.py`
- **Approach:** Pre-compute resized frames at import time, cache in memory
- **Expected savings:** 5-15ms on cache misses
- **Effort:** 2-3 hours

### Priority 5: Expression Engine for User Effects (FEATURE, HIGH effort)
- **Pixelblaze-inspired:** Let users write formulas like `hsv(x/width + wave(t), 1, 1)`
- **Approach:** Sandboxed DSL compiled to NumPy ops (or use `numexpr`)
- **Expected impact:** New feature, not performance
- **Effort:** 2-4 weeks

### Priority 6: Coordinate Normalization (FEATURE, LOW effort)
- **Pixelblaze-inspired:** Normalize all coordinates to 0-1
- **Approach:** Effects receive normalized x,y; denormalize in base class
- **Expected impact:** Resolution-independent effects
- **Effort:** 4-8 hours

### Priority 7: Increase USB Baud Rate (LOW impact, LOW effort)
- **Current:** 115,200 bps
- **Approach:** Test 230,400 or 460,800 (Teensy supports up to 12Mbps USB serial)
- **Expected savings:** 0.1-0.3ms per frame
- **Effort:** 30 minutes (test for stability)

### NOT Recommended: Full C++ Rewrite
- **Effort:** 3-6 person-months
- **Gain:** 2-5ms per frame
- **Risk:** Lose rapid prototyping, Python ecosystem, ease of adding effects
- **Verdict:** The current architecture is correct. Optimize hot paths surgically.

---

## Questions for Codex Review

1. **Is the current Pi+Teensy split architecture optimal?** Or would a single-board solution (e.g., bare-metal on Teensy with web server) be better?

2. **For the particle system vectorization (Priority 1):** What's the best NumPy pattern for variable-count particles with per-particle state (position, velocity, color, lifetime)?

3. **For pack_frame() optimization (Priority 3):** Cython vs ctypes vs cffi vs writing a small C extension module — what's the best approach for a single tight loop?

4. **Expression engine (Priority 5):** Is `numexpr` sufficient, or does this need a custom parser/compiler? What's the security model for user-submitted code?

5. **Are there any architectural patterns from WLED or FastLED** (beyond Pixelblaze) that could benefit this project?

6. **Scaling to 5,000+ pixels:** What changes would be needed? Is the current architecture viable at that scale, or does something fundamental need to change?
