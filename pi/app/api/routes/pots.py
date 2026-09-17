"""Physical pot control routes — status and per-knob enable flags."""

from fastapi import APIRouter, Depends

from ..schemas import PotsConfigRequest


def create_router(deps, require_auth) -> APIRouter:
    router = APIRouter(prefix="/api/pots", tags=["pots"])

    @router.get("")
    async def get_pots():
        if deps.pot_controller is not None:
            status = deps.pot_controller.get_status()
        else:
            status = {'values': None, 'category': None,
                      'enabled': deps.state_manager.pots_enabled}
        return status

    @router.post("/config", dependencies=[Depends(require_auth)])
    async def update_pots_config(req: PotsConfigRequest):
        update = {k: v for k, v in
                  (('brightness', req.brightness), ('menu', req.menu),
                   ('pattern', req.pattern)) if v is not None}
        if update:
            deps.state_manager.pots_enabled = update
        return {'enabled': deps.state_manager.pots_enabled}

    return router
