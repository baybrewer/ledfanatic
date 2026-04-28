# LED Fanatic Live Engine — Master Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade LED Fanatic from a single-effect renderer into a compositing visual performance engine with a studio-grade browser UI, while preserving all 63 existing effects and 59.5 FPS performance.

**Architecture:** Incremental enhancement of the existing render pipeline. The compositor wraps the current single-effect renderer, adding a layer stack with blend modes. The UI evolves from tabs to workspaces. No big-bang rewrites.

**Tech Stack:** Python 3.13 / FastAPI / NumPy / Pillow / WebSocket / Vanilla JS (no framework) / Raspberry Pi 4 / Teensy 4.1 + OctoWS2811

---

## Scope & Phasing

This master plan covers 4 implementation phases. Each phase has its own detailed plan document, stop/go gates, and acceptance criteria.

| Phase | Name | Effort | Dependencies |
|-------|------|--------|-------------|
| 1 | Error Isolation + Compositor Foundation | 2 days | None |
| 2 | Studio UI: Live + Layers Workspace | 3 days | Phase 1 |
| 3 | Studio UI: Profiler + Mapper Workspace | 2 days | Phase 2 |
| 4 | Expression Engine (future) | TBD | Phase 1-3 complete |

### Out of Scope (Documented for Future)
- Camera-assisted mapping (spec exists, hardware not available)
- Multi-controller sync
- Timeline/show control
- 3D coordinate layouts
- WASM-based browser-side effects

---

## Phase 1: Error Isolation + Compositor Foundation

**Detailed plan:** `2026-04-28-phase1-compositor.md`

### Deliverables
1. Effect error isolation in renderer (try/except around render())
2. Layer model: `Layer` dataclass (effect_name, params, opacity, blend_mode, enabled)
3. Scene schema upgrade: `Scene` = name + ordered list of Layers
4. Compositor: renders layers, applies blend modes, produces single frame
5. Blend modes: normal, add, screen, multiply, max
6. API: `/api/scenes/layers` CRUD
7. Backward compatibility: existing single-effect scenes work unchanged

### Stop/Go Gate
- [ ] Single-effect rendering unchanged (regression test)
- [ ] Two-layer scene renders at 59+ FPS on Pi
- [ ] Blend modes produce correct output (visual + unit test)
- [ ] Effect crash in one layer doesn't kill other layers or render loop
- [ ] Existing `/api/scenes/activate` still works for single effects

---

## Phase 2: Studio UI — Live + Layers Workspace

**Detailed plan:** `2026-04-28-phase2-studio-ui.md`

### Deliverables
1. Workspace navigation: Live / Studio / System (replacing current tabs)
2. Live workspace: large preview canvas showing physical layout shape
3. Studio workspace: effect selector + parameter inspector + layer panel
4. Layer panel: reorder, enable/disable, opacity slider, blend mode selector
5. Preview renderer: canvas with LED-blob rendering (not flat grid)
6. WebSocket upgrade: send layout metadata alongside frames for accurate preview

### Stop/Go Gate
- [ ] All existing tab functionality accessible from new workspaces
- [ ] Layer panel creates/removes/reorders layers with live preview
- [ ] Preview shows physical cylinder layout (not flat grid)
- [ ] Mobile-friendly (iPad operation at campsite)
- [ ] No regression in existing effects or scene activation

---

## Phase 3: Profiler + Mapper Workspace

**Detailed plan:** `2026-04-28-phase3-profiler-mapper.md`

### Deliverables
1. Profiler workspace: real-time timing charts (effect_render_ms, pack_ms, send_ms over time)
2. Per-effect timing history (sparkline or mini-chart)
3. Dropped frame counter with visual indicator
4. Mapper workspace: visual layout editor
5. Strip table: LED count, direction, color order, channel, enabled
6. Test pattern launcher from mapper UI
7. Layout validation warnings displayed inline

### Stop/Go Gate
- [ ] Profiler shows timing data updating in real-time
- [ ] Can identify slow effects from profiler view
- [ ] Mapper shows current layout visually
- [ ] Can launch test patterns from mapper
- [ ] Layout changes via mapper apply to live rendering

---

## Phase 4: Expression Engine (Future — Requires Design Spec)

**Not planned in detail yet.** Requires:
- Design spec for sandboxed DSL (see brainstorming session 2026-04-26)
- Security model for user-submitted code
- Decision: numexpr vs custom parser vs restricted Python
- Performance target: per-pixel formula at 60 FPS on 830 pixels

---

## Architecture Diagram (Target State After Phase 2)

```
iPad/Browser
  |
  | REST + WebSocket (binary frames)
  v
FastAPI Server (ledfanatic)
  |
  +-- Layout Module (SSOT)
  |     +-- layout.yaml → schema.py → compiler.py → CompiledLayout
  |     +-- Precomputed NumPy pack indices (0.24ms)
  |
  +-- Render Engine
  |     +-- Compositor (NEW)
  |     |     +-- Layer stack (ordered)
  |     |     +-- Blend modes (normal/add/screen/multiply/max)
  |     |     +-- Per-layer effect instances
  |     |     +-- Error isolation per layer
  |     +-- Renderer (existing)
  |     |     +-- Frame loop at target FPS
  |     |     +-- Brightness + gamma
  |     |     +-- Test pattern overlay
  |     |     +-- pack_frame (vectorized)
  |     +-- RenderState (existing)
  |           +-- FPS, timing, audio, scene state
  |
  +-- Preview Backend (existing)
  |     +-- PreviewService → binary WebSocket
  |     +-- Layout-aware preview (NEW)
  |
  +-- Hardware Backend (existing)
  |     +-- TeensyTransport → USB CDC → Teensy 4.1 → OctoWS2811
  |
  +-- Effects (existing, 63 effects)
  |     +-- Generative, Audio-reactive, Imported, Simulation, Diagnostic
  |     +-- ALL_EFFECTS registry (SSOT)
  |     +-- EffectMeta catalog (param schemas)
  |
  +-- Studio UI (NEW)
        +-- Live workspace (preview + controls)
        +-- Studio workspace (layers + params)
        +-- Profiler workspace (timing charts)
        +-- Mapper workspace (layout editor)
```

---

## Non-Negotiables (From Planning Packet, Validated)

| Rule | Current Status | Action |
|------|---------------|--------|
| No hardcoded physical layout | ✅ All effects use self.width/self.height | Maintain |
| No duplicate sources of truth | ✅ Layout SSOT, ALL_EFFECTS registry | Maintain |
| Preview/hardware share pipeline | ✅ Same frame buffer | Compositor must maintain this |
| Schema-driven parameters | ✅ EffectMeta with param tuples | Extend for layers |
| No separate preview effect code | ✅ PreviewService runs same effect classes | Maintain |
| Performance instrumentation | ✅ effect_render_ms, pack_ms, send_ms | Add compositor_ms |
| Error isolation | ⚠️ Gap — implement in Phase 1 | Fix immediately |

---

## Codex Validation

After each phase, generate a review packet for Codex validation via PAL MCP:
- Architecture consistency check
- SSOT/DRY/SOLID compliance
- Schema/API contract consistency
- Test coverage for new code
- Performance impact assessment

See `2026-04-28-codex-review-prompt.md` for the review prompt template.
