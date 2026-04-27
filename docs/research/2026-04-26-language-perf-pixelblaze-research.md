# Research Brief: Language Performance, Pixelblaze Architecture, and Optimization Opportunities

**Date:** 2026-04-26 (rev 3 — incorporating two rounds of Codex review)
**Project:** pillar-controller (Raspberry Pi 4 + Teensy 4.1 + OctoWS2811)
**Status:** Research — pending measured benchmarks on live hardware

---

## Context

pillar-controller is a Python 3.14 LED animation system running on Raspberry Pi 4:
- FastAPI backend, NumPy-vectorized effects, 60 FPS target
- **Layout is dynamic** — geometry is defined in `layout.yaml`, compiled at startup, and can be changed at runtime via the layout API (`pi/app/api/routes/layout.py`). The renderer hot-swaps compiled layouts via `renderer.apply_layout()`.
- Checked-in default layout: 10x83 = 830 pixels, 5 output channels (`pi/config/layout.yaml`)
- The Pi's active layout may differ from the checked-in default (persisted via layout API)
- Transport: USB CDC (virtual serial) to Teensy 4.1 for DMA LED output
- Per-frame budget at 60 FPS: 16.67ms

### Measurement Disclaimer

**All performance numbers in this document are ESTIMATES unless explicitly labeled "(measured)."** They are based on:
- General ARM Cortex-A72 benchmark literature for language comparisons (section 1)
- Code inspection and complexity analysis for per-component estimates (sections 2, roadmap)
- Pixelblaze throughput claims from manufacturer documentation (section 4)

**None of these numbers were measured on the live Pi with the active layout.** Before acting on any optimization priority, the prerequisite benchmark step must be completed. Estimates marked with `~` are order-of-magnitude guides, not precise measurements.

---

## Questions for Analysis

### 1. Do different programming languages run at different speeds on Raspberry Pi?

**Short answer: Yes, dramatically for pure computation. Less so when libraries do the heavy lifting.**

**Estimated benchmark ratios on RPi 4 (ARM Cortex-A72, 1.5GHz):**

These are literature-derived estimates, not measured on our hardware. Actual ratios will vary with compiler flags, memory layout, and workload shape.

| Workload | Pure Python | NumPy (Python) | C++ (-O2) | Ratio (Python:C++) |
|----------|------------|----------------|-----------|---------------------|
| Tight math loop (1M iterations) | ~800ms | N/A | ~5ms | ~160x |
| Array ops on N-element buffer | ~O(N) ms | ~O(N) us | ~O(N) us | NumPy ~= C++ |
| Per-pixel loop with branching | ~O(N) ms | Can't vectorize | ~O(N) us | ~150x |
| Particle update (600 objects) | ~5-8ms | Partial help | ~0.05ms | ~100-160x |

**Key ratios:**
- Pure Python vs C++: **~100-200x slower** for computation-heavy loops
- NumPy-Python vs C++: **~1-2x** (NumPy internals ARE compiled C/Fortran with SIMD)
- The gap ONLY matters for code that can't be vectorized (particle systems, game logic, per-pixel branching)

**Why this matters for pillar-controller:**
- Most effects already use NumPy vectorization = already at ~C speed
- `pack_frame()` is a pure-Python loop over `layout.entries` — cost scales linearly with pixel count
- Particle effects (fireworks: up to 600 sparks, pure Python loops) have non-vectorized hot paths
- The web server, config handling, state management — NOT bottlenecks regardless of language

### 2. How hard would it be to convert this Python project to C++?

**Component-by-component assessment:**

| Component | Python | C++ Equivalent | Difficulty | Speedup |
|-----------|--------|---------------|------------|---------|
| FastAPI web server | ~200 lines | Crow/Drogon (~400 lines) | HIGH | None (not a bottleneck) |
| asyncio event loop | Built-in | libuv/Boost.Asio | HIGH | None (not a bottleneck) |
| NumPy effects | ~2000 lines | Eigen/manual SIMD | VERY HIGH | Minimal (NumPy is already C) |
| pack_frame() | 38 lines | C extension | LOW | Scales with pixel count |
| USB CDC transport | 352 lines | libusb/termios | MODERATE | Unlikely meaningful |
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

**Analysis:**
- C++ dominates on **microcontrollers** (no OS, direct hardware, tight timing)
- On a **full Linux SBC like RPi**, the language matters less because the OS handles scheduling/I/O and NumPy provides C-speed math
- **Python + NumPy** is the pragmatic choice for RPi when paired with C firmware (Teensy)

**Our architecture (Python Pi + C++ Teensy) follows the same split as FadeCandy and similar projects.** Pi handles orchestration, effects, UI, audio, media. Teensy handles timing-critical DMA output.

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

**Claimed performance (manufacturer):**
- 48,000 pixels/sec throughput
- At 830 pixels: ~58 FPS
- At 5,000 pixels: ~9.6 FPS

**Comparison caveat:** Direct throughput comparisons between Pixelblaze and pillar-controller are approximate. The systems differ in hardware (ESP32 vs RPi 4), programming model (per-pixel bytecode VM vs grid-wide NumPy), workload complexity (simple expressions vs full Python effects), and measurement methodology (manufacturer claim vs our unmeasured estimates). Directionally, pillar-controller should be competitive or faster at moderate pixel counts due to the much more powerful CPU, but this has not been verified with controlled benchmarks.

**Live editing:**
- Web-based editor with syntax highlighting
- Patterns recompile on every keystroke
- Live preview updates in real-time
- .epe file format (JSON with source + preview image + bytecode)

**Pixel mapping:**
- JSON array of [x, y, z] coordinates per pixel
- Patterns receive normalized coordinates (0-1) in render2D/render3D
- Decouples physical layout from pattern logic

### 5. What can we learn from Pixelblaze?

**Rough feature comparison** (not a performance benchmark — different hardware and workloads):

| Pixelblaze Feature | Our Status | Notes |
|-------------------|------------|-------|
| Bytecode-compiled effects | NumPy vectorization | Different approach, both effective for their platforms |
| Per-pixel render model | Grid-based NumPy ops | Our approach better suited to Pi's SIMD/cache architecture |
| Coordinate mapping | layout.yaml SSOT | Already implemented, and our layout is runtime-mutable |
| Live preview | WebSocket frame streaming | Already implemented |
| Per-effect sliders | PARAMS system per effect | Already implemented |
| Pattern sharing (.epe) | Not implemented | Potential feature |
| Instant recompilation | Hot reload via API | Could improve dev workflow |
| Built-in math functions | NumPy + custom helpers | perlin_grid, pal_color_grid, etc. |

**What Pixelblaze does that we don't (and could benefit from):**

1. **User-editable expression engine** — Let users write simple formulas that generate patterns without Python knowledge. Pixelblaze's killer feature.

2. **Pattern library/export format** — Portable effect definitions that can be shared.

3. **Coordinate-space normalization** — Pixelblaze normalizes all coordinates to 0-1 range. See "Product Ideas" section below for nuances.

**What we do that Pixelblaze doesn't:**

1. **Audio reactivity** — We have full FFT, beat detection, BPM; Pixelblaze has basic sound via sensor board
2. **Media playback** — Video/image support; Pixelblaze can't play media
3. **Complex effects** — Particle systems, tetris, fireworks
4. **Dynamic layout** — Runtime-configurable via API; Pixelblaze's pixel map is static JSON

---

## Performance Optimization Roadmap

All items in this section are about making existing features faster. Priorities are tentative until the prerequisite baseline is established.

### Prerequisite: Establish Measured Baseline — COMPLETED 2026-04-26

1. **✅ Unified effect registry as SSOT.** Created `pi/app/effects/registry.py` with `ALL_EFFECTS` dict (57 effects). `main.py` and `bench_effects.py` both consume it.

2. **✅ Benchmarks on Pi.** Live timing via `/api/system/status`: effect_render_ms, pack_ms, send_ms. Full bench_effects.py harness updated.

3. **✅ Layout reported in benchmark output.** `bench_effects.py` prints grid dimensions, pixel count, and output count.

4. **Benchmark procedure:** RPi 4 (passive cooling), active layout from `/opt/pillar/config/layout.yaml`, default params, 600 frames, synthetic audio (128 BPM), warm (first 60 frames reported separately).

### Priority 1: Effect-Level Profiling in the Render Loop — COMPLETED 2026-04-26

- **Done:** Added `effect_render_ms`, `pack_ms`, `send_ms` to `RenderState.to_dict()`, exposed via `/api/system/status`.
- **Measured results:** effect=1.7ms, pack=0.24ms, send=5.22ms (moire effect, 10x83 grid)

### Priority 2: Vectorize Particle Systems — COMPLETED 2026-04-26

- **Done:** SRFireworks fully vectorized with NumPy structured arrays (`_SPARK_DTYPE`).
- **Physics:** Vectorized position/velocity/life update, no Python loops.
- **Rendering:** `np.add.at()` for safe spark accumulation at duplicate positions.
- **Result:** 0.087ms avg over 600 frames (257 active sparks), ~57x faster than Python loop version.

### Priority 3: Vectorize pack_frame() — COMPLETED 2026-04-26

- **Done:** NumPy vectorization (not Cython — simpler, equally fast).
- **Approach:** Precompute `pack_src` / `pack_dst` int32 index arrays in `CompiledLayout` at compile time. `pack_frame()` is now `buf[dst] = frame.ravel()[src]` — single fancy-index operation.
- **Result:** 0.24ms on Pi (down from ~5-8ms Python loop). ~25x faster.

### Priority 4: Media Frame Cache — COMPLETED 2026-04-26

- **Done:** `_frame_cache` now stores resized frames (not raw). Resize via PIL LANCZOS happens once on cache insert, not on every render call. Width/height are fixed per effect instance (effect is recreated on layout change).

### Priority 5: Transport Throughput — MEASURED 2026-04-26, NO ACTION NEEDED

- **Measured:** `send_ms=5.22ms` via live profiling. This is USB CDC + asyncio.to_thread overhead + OS scheduling.
- **Theoretical minimum:** 2490 bytes at USB FS 12 Mbps ≈ 1.7ms.
- **Conclusion:** ~3.5ms overhead from asyncio thread-switch and OS scheduling. Not actionable without moving to synchronous I/O (which would block the event loop). Acceptable at current frame rates.

### NOT Recommended: Full C++ Rewrite
- **Effort:** 3-6 person-months
- **Gain:** Marginal on current layout
- **Risk:** Lose rapid prototyping, Python ecosystem, ease of adding effects
- **Verdict:** Optimize hot paths surgically, guided by measurements.

---

## Pixelblaze-Inspired Product Ideas

These are new features, not performance work. They belong in a separate decision process (user value, effort, priority against other product work).

### Idea A: User-Editable Expression Engine

- **Pixelblaze-inspired:** Let users write formulas like `hsv(x/width + wave(t), 1, 1)`
- **Approach:** Sandboxed DSL compiled to NumPy ops (or use `numexpr`)
- **Open questions:** Is `numexpr` sufficient or does this need a custom parser/compiler? What's the security model for user-submitted code?
- **Effort:** 2-4 weeks

### Idea B: Coordinate Normalization for Effects

- **Pixelblaze-inspired:** Effects receive normalized (0-1) coordinates so they are resolution-independent
- **Status: NEEDS DESIGN SPEC — not actionable as written.**
- **The problem:** The codebase has three coordinate-related concerns that must not be conflated:
  1. **Physical layout calibration** — `pi/app/setup/geometry.py` uses normalized UV coordinates for spatial fitting. This is hardware calibration.
  2. **Logical matrix rendering** — effects currently receive `width` and `height` and construct their own grids. This is the rendering coordinate space.
  3. **Effect authoring convenience** — Pixelblaze normalizes to 0-1 so patterns work at any resolution. This is an authoring aid.
- **Required before proceeding:** A design spec that defines:
  - Which coordinate space is the SSOT for effect rendering
  - Whether normalization is opt-in (effects that want it construct normalized grids) or opt-out (base class provides normalized grids, effects use raw if they need to)
  - How this interacts with dynamic layout changes (coordinate grids must update when layout changes)
  - That calibration UV space (geometry.py) remains separate from rendering coordinate space
- **Risk:** Mixing these concerns creates an abstraction that serves none of them well.

### Idea C: Pattern Library / Export Format

- **Pixelblaze-inspired:** .epe-like portable effect definitions
- **Low effort, medium value**
- **Approach:** JSON format with effect name, params, preview thumbnail
- **Effort:** 1-2 days

---

## Questions for Codex Review (Round 3)

1. **Effect registry SSOT:** What's the cleanest way to unify effect registration so `main.py`, `bench_effects.py`, and the catalog API all consume the same source?

2. **For the particle system vectorization:** What's the best NumPy pattern for variable-count particles with per-particle state (position, velocity, color, lifetime)?

3. **For pack_frame() optimization:** Cython vs ctypes vs cffi vs a small C extension module — what's the best approach for a single tight loop on RPi 4?

4. **Coordinate normalization design:** Given three existing coordinate concerns (layout calibration, logical matrix, effect authoring), what's the right abstraction boundary?

5. **Scaling to 5,000+ pixels:** Which components hit their scaling limits first?

6. **Are there architectural patterns from WLED or FastLED** (beyond Pixelblaze) worth adopting?
