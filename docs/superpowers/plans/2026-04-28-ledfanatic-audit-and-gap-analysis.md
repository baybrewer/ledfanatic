# LED Fanatic — Repository Audit & Gap Analysis

> **Date:** 2026-04-28
> **Auditor:** Claude Opus 4.6
> **Repo:** github.com/baybrewer/ledfanatic
> **Context:** Audit against the Live Rendering Engine Opus Planning Packet

---

## Executive Summary

The LED Fanatic codebase is significantly more mature than the planning packet assumed. **80% of the requested architecture already exists** with clean separation of concerns, comprehensive testing (434 passing tests), and measured 59.5 FPS performance.

The packet was written blind to the current state and proposes rebuilding systems that are already production-quality. This audit identifies the **actual gaps** that need implementation vs the false gaps that the packet creates.

---

## What Already Exists (No Work Needed)

### Layout SSOT — COMPLETE
- `pi/app/layout/schema.py` — Pydantic data models (LayoutConfig, OutputConfig, LinearSegment, ExplicitSegment)
- `pi/app/layout/compiler.py` — Validates config, compiles to CompiledLayout with forward/reverse LUTs, precomputed NumPy pack indices
- `pi/app/layout/packer.py` — Vectorized NumPy packer (0.24ms per frame via fancy indexing)
- `pi/app/layout/__init__.py` — Public API: load_layout, save_layout, compile_layout, validate_layout, pack_frame
- `pi/config/layout.yaml` — Declarative YAML config (SSOT for LED geometry)
- Per-segment color_order override supported
- Layout hot-swappable at runtime via `POST /api/layout/apply`

### Render Engine — COMPLETE
- `pi/app/core/renderer.py` — Main render loop at target FPS (default 60)
- Per-frame profiling: `effect_render_ms`, `pack_ms`, `send_ms`
- Brightness engine with solar automation
- Gamma LUT (precomputed)
- Y-axis flip for physical origin
- Test pattern overlay system (segment, strip, probe)
- Scene activation with state preservation

### Effect Lifecycle — COMPLETE
- `pi/app/effects/base.py` — `Effect` ABC: `__init__(width, height, params)`, `render(t, state) -> ndarray`, `update_params(params)`, `elapsed(t)`
- `pi/app/effects/registry.py` — `ALL_EFFECTS` canonical dict (63 effects)
- `pi/app/effects/catalog.py` — `EffectMeta` dataclass with param schema, palette support, audio requirements
- Effects render in screen coordinates; renderer handles physical mapping
- RENDER_SCALE support for supersampled effects

### Live Preview — COMPLETE
- `pi/app/preview/service.py` — `PreviewService` with binary WebSocket frame streaming
- Binary protocol: 10-byte header (type, frame_id, width, height, encoding) + pixel bytes
- Independent effect instance (doesn't mutate live state)
- Client set management, FPS control
- Routes: `POST /api/preview/start`, `GET /api/preview/status`

### Performance Profiling — COMPLETE
- RenderState exposes: effect_render_ms, pack_ms, send_ms, actual_fps, frames_dropped
- `/api/system/status` returns all metrics
- Benchmark harness: `python -m tools.bench_effects` (uses ALL_EFFECTS from registry)
- Measured baseline on Pi: 59.5 FPS sustained with full effects

### Hardware Backend — COMPLETE
- `pi/app/transport/usb.py` — TeensyTransport with USB CDC serial, COBS framing, CRC32
- Auto-reconnect loop, handshake with capability exchange
- `send_frame()` via `asyncio.to_thread()` (non-blocking)
- CONFIG packet sent on connect/reconnect

### Scene & State Management — COMPLETE
- `pi/app/core/state.py` — StateManager with debounced persistence, atomic writes
- Per-effect parameter memory (get_effect_params/set_effect_params)
- Schema-versioned state.json with migration support
- Config precedence: code defaults < yaml < state.json < API overrides

### Diagnostic Test Patterns — COMPLETE
- `pi/app/diagnostics/patterns.py` — 6 patterns: StripIdentify, BottomToTopSweep, ChannelIdentify, RGBOrderTest, SeamTest, SerpentineChase
- LED Probe tool: `pi/app/ui/static/probe.html`
- Segment identify / strip identify via renderer overlay

### UI — COMPLETE (but tab-based)
- Single-page app: `pi/app/ui/static/index.html` + `js/app.js` (66KB)
- 7 tabs: Live, Effects, Media, Audio, Sim, Game, System
- Auto-generated parameter controls from EffectMeta
- PWA manifest for home-screen install
- Auth token management via localStorage

### API — COMPLETE
- FastAPI with router composition (10 route modules)
- Bearer token auth (fail-closed)
- Routes for: system, scenes, effects, brightness, media, audio, diagnostics, transport, preview, layout, WebSocket

### Testing — COMPREHENSIVE
- 434 passing tests across 27 test files
- Layout, effects, rendering, protocol, API contract, audio, brightness, state, migrations
- Benchmark harness for all 63 effects

---

## Actual Gaps (Work Needed)

### Gap 1: Compositor & Layer System — HIGH PRIORITY
**Current state:** Only AnimationSwitcher with cross-fade between sequential effects. No general-purpose layer stack.
**What's needed:**
- Layer model: ordered list of (effect, params, opacity, blend_mode, enabled)
- Compositor: combines layers into single frame
- Blend modes: normal, add, multiply, screen, max
- Scene schema upgrade: single-effect → layer stack
- UI: layer panel with reorder, enable/disable, opacity, blend mode

### Gap 2: Effect Error Isolation — HIGH PRIORITY
**Current state:** `effect.render()` call at renderer.py:334 has no try/except. A crashing effect stops the entire render loop frame.
**What's needed:**
- Wrap `effect.render()` in try/except
- On crash: return last-good frame or black frame
- Mark effect as failed, show error in UI
- Continue rendering without restart

### Gap 3: Studio UI Workspaces — MEDIUM PRIORITY
**Current state:** Tab-based interface (Live, Effects, Media, Audio, Sim, Game, System)
**What's needed:**
- Workspace layout: Live (large preview + controls), Studio (effect editor + layers), Mapper (layout config), Profiler (timing charts)
- Better preview that shows actual physical layout shape
- Parameter inspector generated from schema (partially exists)

### Gap 4: Expression Engine — LOW PRIORITY (FUTURE)
**Current state:** Effects are Python classes only
**What's needed:**
- Sandboxed DSL for user-editable per-pixel formulas
- Compile to NumPy ops for performance
- Security model for user code

### Gap 5: Camera-Assisted Mapping — LOW PRIORITY (FUTURE)
**Current state:** Vision spec written but implementation paused
**What's needed:**
- Pi camera integration
- LED detection and position mapping
- Calibration UI

---

## Risk Register

| Risk | Severity | Mitigation |
|------|----------|------------|
| Compositor adds per-frame cost | Medium | Vectorized NumPy blend, skip single-layer case |
| UI rewrite breaks existing functionality | Medium | Incremental: add workspaces alongside tabs, migrate gradually |
| Expression engine security | High | Defer to future phase; sandboxing is hard |
| Preview divergence from hardware | Low | Already using same frame buffer; compositor must maintain this |
| Layer stack performance at 60 FPS | Low | 2-3 layers on 10x83 grid is trivial for NumPy |

---

## Recommended Phase Sequence

1. **Effect Error Isolation** (1 hour) — wrap render() in try/except
2. **Compositor & Blend Modes** (2-3 days) — layer model, compositor, blend modes, scene schema upgrade
3. **Studio UI: Live Workspace** (2-3 days) — large preview with physical layout shape, layer panel
4. **Studio UI: Profiler Workspace** (1 day) — timing charts, per-effect breakdown
5. **Studio UI: Mapper Workspace** (1-2 days) — layout editor with visual preview
6. **Expression Engine** (future) — requires design spec first
7. **Camera Mapping** (future) — requires hardware availability

---

## Packet Alignment Notes

The planning packet proposes 10 phases. Here's how they map to reality:

| Packet Phase | Status | Action |
|---|---|---|
| Phase 0: Repo Audit | This document | Complete |
| Phase 1: Layout SSOT | Already exists | Skip — verify only |
| Phase 2: FrameBuffer/RenderContext | Already exists | Skip — effects already render to NumPy arrays |
| Phase 3: Live Preview MVP | Already exists | Skip — PreviewService works |
| Phase 4: Parameter Schema | Already exists | Skip — EffectMeta/catalog works |
| Phase 5: Profiler | Already exists | Enhance UI only |
| Phase 6: Effects Migration | Mostly done | Performance tuning done (59.5 FPS) |
| Phase 7: Compositor | **GAP — implement** | First real work item |
| Phase 8: Studio UI | **GAP — implement** | Second real work item |
| Phase 9: Pi-to-Teensy | Already exists | OctoWS2811 via USB CDC already working |
