"""Overhead light routes — thin HTTP wrapper over OverheadService."""

from fastapi import APIRouter, Depends, HTTPException

from ...core.overhead import OverheadCommand
from ..schemas import OverheadRequest


def create_router(deps, require_auth, broadcast_state) -> APIRouter:
    router = APIRouter(prefix="/api/overhead", tags=["overhead"])

    def _response(state) -> dict:
        return {
            'state': state.to_dict(),
            'effects': [{'name': n, 'label': l} for n, l in deps.overhead.effects()],
        }

    @router.get("")
    async def get_overhead():
        return _response(deps.overhead.snapshot())

    @router.post("", dependencies=[Depends(require_auth)])
    async def set_overhead(req: OverheadRequest):
        if req.rgb is not None and len(req.rgb) != 3:
            raise HTTPException(422, "rgb must have 3 components")
        cmd = OverheadCommand(
            on=req.on,
            brightness=req.brightness,
            effect=req.effect,
            kelvin=req.kelvin,
            rgb=tuple(req.rgb) if req.rgb is not None else None,
            params=req.params,
        )
        try:
            state = await deps.overhead.apply(cmd)
        except ValueError as e:
            raise HTTPException(404, str(e))
        await broadcast_state()
        return _response(state)

    return router
