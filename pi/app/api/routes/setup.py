"""
Setup API routes — live strip-to-channel mapping.

Each strip edit validates, recompiles the output plan, hot-applies
to the renderer, and persists to installation.yaml.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException

from ..schemas import StripConfigRequest
from ...config.installation import (
  StripMapping, save_installation,
  VALID_COLOR_ORDERS, VALID_DIRECTIONS, MAX_LEDS_PER_CHANNEL,
)
from ...hardware_constants import LEDS_PER_STRIP
from ...mapping.runtime_plan import compile_strip_plan

logger = logging.getLogger(__name__)


def _recompile_and_apply(deps):
  """Validate, recompile, hot-apply, persist."""
  errors = deps.installation.validate()
  if errors:
    raise HTTPException(422, f"Validation failed: {'; '.join(errors)}")
  plan = compile_strip_plan(deps.installation, deps.controller_profile)
  deps.renderer.apply_output_plan(plan)
  save_installation(deps.installation, deps.config_dir)


def create_router(deps, require_auth, broadcast_state) -> APIRouter:
    router = APIRouter(prefix="/api/setup", tags=["setup"])

    @router.get("/strips")
    async def get_strips():
        return {"strips": deps.installation.strips_api_list()}

    @router.post("/strips/{strip_id}", dependencies=[Depends(require_auth)])
    async def update_strip(strip_id: int, req: StripConfigRequest):
        strip = next((s for s in deps.installation.strips if s.id == strip_id), None)
        if strip is None:
            raise HTTPException(404, f"Strip {strip_id} not found")

        if req.channel is not None:
            if not 0 <= req.channel < 8:
                raise HTTPException(422, "channel must be 0-7")
            strip.channel = req.channel
        if req.offset is not None:
            if req.offset < 0:
                raise HTTPException(422, "offset must be >= 0")
            strip.offset = req.offset
        if req.direction is not None:
            if req.direction not in VALID_DIRECTIONS:
                raise HTTPException(422, f"direction must be one of: {', '.join(sorted(VALID_DIRECTIONS))}")
            strip.direction = req.direction
        if req.led_count is not None:
            if not 1 <= req.led_count <= MAX_LEDS_PER_CHANNEL:
                raise HTTPException(422, f"led_count must be 1-{MAX_LEDS_PER_CHANNEL}")
            strip.led_count = req.led_count
        if req.color_order is not None:
            if req.color_order not in VALID_COLOR_ORDERS:
                raise HTTPException(422, f"color_order must be one of: {', '.join(sorted(VALID_COLOR_ORDERS))}")
            strip.color_order = req.color_order
        if req.brightness is not None:
            if not 0.0 <= req.brightness <= 1.0:
                raise HTTPException(422, "brightness must be 0.0-1.0")
            strip.brightness = req.brightness

        _recompile_and_apply(deps)
        logger.info(f"Strip {strip_id} updated: ch{strip.channel}+{strip.offset} {strip.direction} {strip.led_count}LEDs {strip.color_order}")
        return {"status": "ok", "strips": deps.installation.strips_api_list()}

    @router.post("/strips", dependencies=[Depends(require_auth)])
    async def add_strip(req: StripConfigRequest):
        new_id = deps.installation.next_id()
        strip = StripMapping(
            id=new_id,
            channel=req.channel if req.channel is not None else 0,
            offset=req.offset if req.offset is not None else 0,
            direction=req.direction if req.direction is not None else 'bottom_to_top',
            led_count=req.led_count if req.led_count is not None else LEDS_PER_STRIP,
            color_order=req.color_order if req.color_order is not None else 'BGR',
        )
        deps.installation.strips.append(strip)

        try:
            _recompile_and_apply(deps)
        except HTTPException:
            deps.installation.strips.pop()
            raise

        logger.info(f"Strip {new_id} added: ch{strip.channel}+{strip.offset}")
        return {"status": "ok", "strips": deps.installation.strips_api_list()}

    @router.delete("/strips/{strip_id}", dependencies=[Depends(require_auth)])
    async def delete_strip(strip_id: int):
        strip = next((s for s in deps.installation.strips if s.id == strip_id), None)
        if strip is None:
            raise HTTPException(404, f"Strip {strip_id} not found")

        deps.installation.strips.remove(strip)
        deps.installation.renumber_ids()

        _recompile_and_apply(deps)
        logger.info(f"Strip {strip_id} deleted, {len(deps.installation.strips)} remaining")
        return {"status": "ok", "strips": deps.installation.strips_api_list()}

    @router.post("/strips/{strip_id}/test", dependencies=[Depends(require_auth)])
    async def test_strip(strip_id: int):
        strip = next((s for s in deps.installation.strips if s.id == strip_id), None)
        if strip is None:
            raise HTTPException(404, f"Strip {strip_id} not found")
        deps.renderer.set_test_strip(strip_id)
        return {"status": "ok", "strip_id": strip_id, "duration": 5}

    return router
