"""
Setup API routes — segment listing and test patterns.

Read-only segment listing from the compiled layout.
Full layout CRUD will be in the layout routes (Task 8).
"""

import logging

from fastapi import APIRouter, Depends, HTTPException

logger = logging.getLogger(__name__)


def create_router(deps, require_auth, broadcast_state) -> APIRouter:
    router = APIRouter(prefix="/api/setup", tags=["setup"])

    @router.get("/strips")
    async def get_strips():
        """List segments from the layout config (legacy 'strips' name)."""
        layout_config = deps.layout_config
        if layout_config is None:
            return {"strips": []}
        strips = []
        for output in layout_config.outputs:
            for seg in output.segments:
                if seg.enabled:
                    strips.append({
                        "id": seg.id,
                        "output": output.id,
                        "channel": output.channel,
                    })
        return {"strips": strips}

    @router.get("/installation")
    async def get_installation():
        """Legacy endpoint — returns segment info from layout."""
        layout_config = deps.layout_config
        if layout_config is None:
            return {"strips": []}
        strips = []
        for output in layout_config.outputs:
            for seg in output.segments:
                if seg.enabled:
                    strips.append({
                        "id": seg.id,
                        "output": output.id,
                        "channel": output.channel,
                    })
        return {"strips": strips}

    @router.post("/strips/{segment_id}/test", dependencies=[Depends(require_auth)])
    async def test_strip(segment_id: str):
        layout = deps.compiled_layout
        if layout is None:
            raise HTTPException(404, "No layout loaded")
        # Verify segment exists in renderer cache
        if segment_id not in deps.renderer._segment_positions:
            raise HTTPException(404, f"Segment '{segment_id}' not found")
        deps.renderer.set_test_strip(segment_id)
        return {"status": "ok", "segment_id": segment_id, "duration": 5}

    return router
