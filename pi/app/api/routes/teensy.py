"""Teensy link routes — pushed DIAG, crash record, incident bundles, manual recovery.

Reset and USB power-cycle are the only places the Pi ever restarts the Teensy,
and both exist solely behind an authenticated POST.
"""

import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from ...core.teensy_maintenance import BusyError, PowerCycleUnavailable
from ...models.protocol import STALL_FAULTED
from ..schemas import TeensyMaintenanceRequest


def create_router(deps, require_auth) -> APIRouter:
    router = APIRouter(prefix="/api/teensy", tags=["teensy"])

    def _maintenance():
        if deps.maintenance is None:
            raise HTTPException(503, "Teensy maintenance is not configured")
        return deps.maintenance

    def _incidents():
        if deps.incidents is None:
            raise HTTPException(503, "Incident recorder is not configured")
        return deps.incidents

    def _faulted() -> bool:
        caps = deps.transport.caps or {}
        diag = deps.transport.last_teensy_diag or {}
        return bool(caps.get('faulted')) or bool(diag.get('stall_latched', 0) & STALL_FAULTED)

    @router.get("/diag")
    async def get_diag():
        diag = deps.transport.last_teensy_diag
        diag_at = deps.transport.last_diag_at
        if diag is None or diag_at is None:
            raise HTTPException(503, "No DIAG pushed by the Teensy yet")
        return {
            'diag': diag,
            'age_s': round(time.monotonic() - diag_at, 2),
            'boot_id': deps.transport.boot_id,
            'state': deps.transport.get_status().get('state'),
        }

    @router.get("/crash")
    async def get_crash():
        if not deps.transport.connected:
            raise HTTPException(503, "Teensy not connected")
        # Firmware serves the live record while fault-idle, else the previous boot's
        record = await deps.transport.request_crash()
        if record is None:
            raise HTTPException(503, "Teensy did not answer the CRASH request")
        return {
            'crash': record,
            'source': 'live' if _faulted() else 'previous_boot',
            'boot_id': deps.transport.boot_id,
        }

    @router.get("/incidents")
    async def list_incidents():
        items = _incidents().list()
        return {'incidents': items, 'count': len(items)}

    @router.get("/incidents/{incident_id}")
    async def download_incident(incident_id: str):
        path = _incidents().path_for(incident_id)
        if path is None:
            raise HTTPException(404, "Incident not found")
        return FileResponse(path, media_type='application/json', filename=f'{incident_id}.json')

    @router.post("/reset", dependencies=[Depends(require_auth)])
    async def reset_teensy(req: TeensyMaintenanceRequest = None):
        reason = (req.reason if req and req.reason else 'manual')
        try:
            return await _maintenance().reset(reason)
        except BusyError as e:
            return JSONResponse({'busy': True, 'incident': e.incident}, status_code=409)

    @router.post("/usb-power-cycle", dependencies=[Depends(require_auth)])
    async def usb_power_cycle(req: TeensyMaintenanceRequest = None):
        maintenance = _maintenance()
        if not maintenance.usb_power_cycle_available:
            raise HTTPException(404, "uhubctl is not installed on this host")
        reason = (req.reason if req and req.reason else 'manual')
        try:
            return await maintenance.usb_power_cycle(reason)
        except PowerCycleUnavailable:
            raise HTTPException(404, "uhubctl is not installed on this host")
        except BusyError as e:
            return JSONResponse({'busy': True, 'incident': e.incident}, status_code=409)

    return router
