"""WebSocket route and broadcast helpers."""

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


def build_state_payload(deps) -> dict:
    """Single definition of the state pushed to UI clients."""
    data = deps.render_state.to_dict()
    data['brightness'] = deps.brightness_engine.get_status()
    data['current_params'] = deps.state_manager.current_params
    if deps.overhead is not None:
        data['overhead'] = deps.overhead.snapshot().to_dict()
    # Link health for the stall banner / Reset Teensy button
    link = deps.transport.get_status()
    maintenance = deps.maintenance
    data['transport'] = {
        'state': link.get('state'),
        'firmware_version': link.get('firmware_version'),
        'boot_id': link.get('boot_id'),
        'stalled': link.get('stalled'),
        'maintenance': bool(link.get('maintenance', False)),
        'usb_power_cycle_available': bool(
            maintenance is not None and maintenance.usb_power_cycle_available),
    }
    return data


def create_router(deps) -> tuple[APIRouter, callable]:
    """Returns (router, broadcast_state_fn)."""
    ws_clients: set[WebSocket] = set()

    @router.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        await ws.accept()
        ws_clients.add(ws)
        try:
            await ws.send_json(build_state_payload(deps))
            while True:
                data = await ws.receive_text()
                try:
                    msg = json.loads(data)
                    await _handle_ws_message(msg, ws)
                except json.JSONDecodeError:
                    pass
        except WebSocketDisconnect:
            pass
        finally:
            ws_clients.discard(ws)

    async def _handle_ws_message(msg: dict, ws: WebSocket):
        action = msg.get('action')
        if action == 'ping':
            await ws.send_json({'action': 'pong'})
        elif action == 'get_state':
            await ws.send_json(build_state_payload(deps))

    async def broadcast_state():
        data = build_state_payload(deps)
        dead = set()
        for ws in ws_clients:
            try:
                await ws.send_json(data)
            except Exception:
                dead.add(ws)
        ws_clients.difference_update(dead)

    return router, broadcast_state
