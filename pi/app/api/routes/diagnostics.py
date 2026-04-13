"""Diagnostics routes — test patterns, stats."""

from fastapi import APIRouter, Depends, HTTPException

from ..schemas import TestPatternRequest
from ...diagnostics.patterns import DIAGNOSTIC_EFFECTS
from ...models.protocol import TestPattern


def create_router(deps, require_auth) -> APIRouter:
    router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])

    @router.post("/test-pattern", dependencies=[Depends(require_auth)])
    async def run_test_pattern(req: TestPatternRequest):
        teensy_patterns = {p.name.lower(): p.value for p in TestPattern}
        if req.pattern.lower() in teensy_patterns:
            await deps.transport.send_test_pattern(
                teensy_patterns[req.pattern.lower()],
            )
            return {"status": "ok", "target": "teensy"}

        if req.pattern in DIAGNOSTIC_EFFECTS:
            deps.renderer.activate_scene(req.pattern)
            deps.state_manager.current_scene = req.pattern
            return {"status": "ok", "target": "pi"}

        raise HTTPException(404, f"Unknown test pattern: {req.pattern}")

    @router.post("/clear", dependencies=[Depends(require_auth)])
    async def clear_test_pattern():
        await deps.transport.send_test_pattern(0xFF)
        return {"status": "ok"}

    @router.get("/stats")
    async def get_stats():
        teensy_stats = await deps.transport.request_stats()
        return {
            'transport': deps.transport.get_status(),
            'render': deps.render_state.to_dict(),
            'brightness': deps.brightness_engine.get_status(),
            'teensy': teensy_stats,
        }

    return router
