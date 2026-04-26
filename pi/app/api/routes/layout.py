"""
Layout API routes — get, apply, validate, test-segment.

All mutations: validate -> compile -> send CONFIG to Teensy -> ACK gate -> commit.
"""

import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...layout import (
    load_layout, save_layout, validate_layout, compile_layout,
    output_config_list, pack_frame,
)
from ...layout.schema import parse_layout

logger = logging.getLogger(__name__)


class LayoutApplyRequest(BaseModel):
    """Full layout config as JSON (same structure as layout.yaml)."""
    version: int = 1
    matrix: dict
    outputs: list[dict]


def create_router(deps, require_auth) -> APIRouter:
    router = APIRouter(prefix="/api/layout", tags=["layout"])

    @router.get("/")
    async def get_layout():
        """Return current layout config + compiled stats."""
        config = deps.layout_config
        compiled = deps.compiled_layout
        if config is None:
            return {"error": "No layout loaded", "outputs": []}
        return {
            "version": config.version,
            "matrix": {
                "width": config.matrix.width,
                "height": config.matrix.height,
                "origin": config.matrix.origin,
            },
            "outputs": [
                {
                    "id": o.id,
                    "channel": o.channel,
                    "chipset": o.chipset,
                    "color_order": o.color_order,
                    "max_pixels": o.max_pixels,
                    "segments": [_serialize_segment(s) for s in o.segments],
                }
                for o in config.outputs
            ],
            "compiled": {
                "width": compiled.width if compiled else 0,
                "height": compiled.height if compiled else 0,
                "total_mapped": compiled.total_mapped if compiled else 0,
                "output_sizes": output_config_list(compiled) if compiled else [0] * 8,
            },
        }

    @router.post("/apply", dependencies=[Depends(require_auth)])
    async def apply_layout(req: LayoutApplyRequest):
        """Replace entire layout config."""
        try:
            staged = parse_layout(req.model_dump())
        except (ValueError, KeyError) as e:
            raise HTTPException(422, detail=str(e))

        errors = validate_layout(staged)
        if errors:
            raise HTTPException(422, detail=errors)

        compiled = compile_layout(staged)
        oc = output_config_list(compiled)

        config_ok = await deps.transport.send_config(oc)
        if not config_ok:
            raise HTTPException(502, detail="Teensy rejected CONFIG or timed out")

        # ACK received — commit
        deps.layout_config = staged
        deps.compiled_layout = compiled
        deps.renderer.apply_layout(compiled, staged)
        save_layout(staged, deps.config_dir)

        logger.info(f"Layout applied: {compiled.width}x{compiled.height}, {compiled.total_mapped} LEDs")
        return {"status": "ok", "width": compiled.width, "height": compiled.height, "total_mapped": compiled.total_mapped}

    @router.post("/validate")
    async def validate_layout_endpoint(req: LayoutApplyRequest):
        """Validate without applying."""
        try:
            staged = parse_layout(req.model_dump())
        except (ValueError, KeyError) as e:
            return {"valid": False, "errors": [str(e)]}
        errors = validate_layout(staged)
        return {"valid": len(errors) == 0, "errors": errors}

    @router.post("/test-segment/{seg_id}", dependencies=[Depends(require_auth)])
    async def test_segment(seg_id: str):
        """Light a single segment with gradient for identification (5 seconds)."""
        compiled = deps.compiled_layout
        if compiled is None:
            raise HTTPException(404, "No layout loaded")
        if seg_id not in deps.renderer._segment_positions:
            raise HTTPException(404, f"Segment '{seg_id}' not found")
        deps.renderer.set_test_strip(seg_id, duration=5.0)
        return {"status": "ok", "segment": seg_id}

    @router.post("/test-segments", dependencies=[Depends(require_auth)])
    async def test_segments_identify():
        """Light ALL segments, each with a unique color matching the UI swatch."""
        if deps.compiled_layout is None:
            raise HTTPException(404, "No layout loaded")
        deps.renderer.set_test_identify("segment_identify", duration=10.0)
        return {"status": "ok", "mode": "segment_identify"}

    @router.post("/test-strips", dependencies=[Depends(require_auth)])
    async def test_strips_identify():
        """Light each output channel a uniform color (one color per strip)."""
        if deps.compiled_layout is None:
            raise HTTPException(404, "No layout loaded")
        deps.renderer.set_test_identify("strip_identify", duration=10.0)
        return {"status": "ok", "mode": "strip_identify"}

    @router.post("/test-off", dependencies=[Depends(require_auth)])
    async def test_off():
        """Cancel any active test pattern."""
        deps.renderer.set_test_strip(None)
        return {"status": "ok"}

    return router


def _serialize_segment(seg) -> dict:
    from ...layout.schema import ExplicitSegment
    if isinstance(seg, ExplicitSegment):
        return {
            "id": seg.id,
            "type": "explicit",
            "points": [{"x": p[0], "y": p[1]} for p in seg.points],
            "physical_offset": seg.physical_offset,
            "enabled": seg.enabled,
        }
    return {
        "id": seg.id,
        "type": "linear",
        "start": {"x": seg.start[0], "y": seg.start[1]},
        "direction": seg.direction,
        "length": seg.length,
        "physical_offset": seg.physical_offset,
        "enabled": seg.enabled,
    }
