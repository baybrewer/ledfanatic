# Codex Review Prompt — LED Fanatic Live Engine Plan

> Use this prompt with PAL MCP `codex-review` or manually via Codex to validate the implementation plan.

---

## Context

LED Fanatic (github.com/baybrewer/ledfanatic) is a real-time LED visual performance engine running on Raspberry Pi 4 + Teensy 4.1 + OctoWS2811. It currently has:
- 63 effects (generative, audio-reactive, simulation, imported)
- Layout SSOT (YAML → schema → compiler → precomputed NumPy pack indices)
- 59.5 FPS sustained on 10×83 (830 pixel) grid
- FastAPI backend with WebSocket preview
- Vanilla JS single-page UI

## What the Plan Proposes

### Phase 1: Compositor Foundation
- Add Layer dataclass (effect_name, params, opacity, blend_mode, enabled)
- Compositor class: renders ordered layer stack with blend modes
- 5 blend modes: normal, add, screen, multiply, max (NumPy vectorized)
- Error isolation: try/except around each layer's render()
- Backward compatible: existing single-effect activation unchanged
- Layer CRUD API: add, remove, update, reorder

### Phase 2: Studio UI Upgrade
- Workspace-based navigation (Live, Studio, Profiler, Mapper)
- Layer panel with drag reorder, opacity, blend mode controls
- Physical-layout-aware preview (cylinder visualization)

### Phase 3: Profiler & Mapper Workspace
- Real-time timing charts
- Visual layout editor

## Review Questions

1. **Architecture consistency:** Does the compositor design (sitting between effects and renderer) maintain the single-frame-buffer principle? Does it break preview/hardware parity?

2. **SSOT/DRY compliance:** Does the Layer model introduce duplicate sources of truth with the existing scene/state system? Should Layer be persisted via StateManager or a separate mechanism?

3. **Blend mode correctness:** Are the NumPy blend implementations mathematically correct? Should opacity be applied before or after the blend operation?

4. **Performance impact:** With 2-3 layers on a 10×83 grid, what's the estimated compositor cost? Is the `blend_normal` implementation (float32 conversion per frame) acceptable, or should we stay in uint8?

5. **Error isolation:** The plan wraps each layer's render() in try/except. Is there a risk of silently swallowing errors that should be surfaced? Should there be a max-consecutive-crash counter that disables an effect?

6. **API design:** The layer CRUD endpoints use `/api/scenes/layers/*`. Should this be a separate router (`/api/compositor/*`) or is scenes the right namespace?

7. **Migration safety:** How should existing saved scenes (single-effect) be migrated to the layer model? The plan says backward-compatible, but what about state.json?

8. **Missing edge cases:**
   - What happens when layout changes while compositor has active layers?
   - What happens when an effect in a layer is unregistered/deleted?
   - Should layers have unique IDs or just use index?

9. **Test coverage:** Are the proposed tests sufficient? What's missing?

10. **Phase sequencing:** Is it safe to build UI (Phase 2) before the compositor API is stable, or should Phase 1 include API stabilization?
