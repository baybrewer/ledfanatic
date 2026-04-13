"""WebSocket route and broadcast helpers."""

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


def create_router(deps) -> tuple[APIRouter, callable]:
    """Returns (router, broadcast_state_fn)."""
    ws_clients: set[WebSocket] = set()

    @router.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        await ws.accept()
        ws_clients.add(ws)
        try:
            await ws.send_json(deps.render_state.to_dict())
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
            state = deps.render_state.to_dict()
            state['brightness'] = deps.brightness_engine.get_status()
            await ws.send_json(state)

    async def broadcast_state():
        data = deps.render_state.to_dict()
        data['brightness'] = deps.brightness_engine.get_status()
        dead = set()
        for ws in ws_clients:
            try:
                await ws.send_json(data)
            except Exception:
                dead.add(ws)
        ws_clients.difference_update(dead)

    return router, broadcast_state
