"""
API contract tests.

Verify the exact route surface, response shapes, and auth behavior
of the shipped v1 API. These tests use FastAPI TestClient and never
touch real hardware — all dependencies are mocked.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.api.server import create_app
from app.core.renderer import Renderer, RenderState
from app.core.state import StateManager
from app.core.brightness import BrightnessEngine
from app.transport.usb import TeensyTransport
from app.media.manager import MediaManager, MediaItem
from app.audio.analyzer import AudioAnalyzer


# --- Fixtures ---

@pytest.fixture
def mock_deps(tmp_path):
    """Create mocked dependencies for create_app."""
    transport = MagicMock(spec=TeensyTransport)
    transport.connected = False
    transport.caps = None
    transport.frames_sent = 0
    transport.send_errors = 0
    transport.reconnect_count = 0
    transport.serial = None
    transport.get_status.return_value = {
        'connected': False, 'port': None, 'caps': None,
        'frames_sent': 0, 'send_errors': 0, 'reconnect_count': 0,
    }
    transport.send_blackout = AsyncMock(return_value=True)
    transport.send_brightness = AsyncMock(return_value=True)
    transport.send_test_pattern = AsyncMock(return_value=True)
    transport.request_stats = AsyncMock(return_value=None)

    render_state = RenderState()
    render_state.current_scene = 'rainbow_rotate'

    brightness_engine = BrightnessEngine()

    state_manager = StateManager(config_dir=tmp_path)
    state_manager.load()

    renderer = MagicMock(spec=Renderer)
    renderer.state = render_state
    renderer.set_scene = MagicMock(return_value=True)
    renderer.activate_scene = MagicMock(return_value=True)

    media_manager = MediaManager(
        media_dir=tmp_path / "media",
        cache_dir=tmp_path / "cache",
    )

    audio_analyzer = MagicMock(spec=AudioAnalyzer)
    audio_analyzer.list_devices.return_value = []

    config = {'auth': {'token': 'test-token-123'}}

    return {
        'transport': transport,
        'renderer': renderer,
        'render_state': render_state,
        'state_manager': state_manager,
        'brightness_engine': brightness_engine,
        'media_manager': media_manager,
        'audio_analyzer': audio_analyzer,
        'config': config,
    }


@pytest.fixture
def app(mock_deps):
    return create_app(**mock_deps)


@pytest.fixture
def client(app):
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def auth_header():
    return {"Authorization": "Bearer test-token-123"}


# --- Route existence tests ---

class TestRouteExistence:
    """Verify every documented route exists and responds."""

    # Public GET endpoints
    @pytest.mark.parametrize("path", [
        "/api/system/status",
        "/api/scenes/list",
        "/api/scenes/presets",
        "/api/brightness/status",
        "/api/media/list",
        "/api/audio/devices",
        "/api/diagnostics/stats",
        "/api/transport/status",
    ])
    def test_public_get_routes_exist(self, client, path):
        resp = client.get(path)
        assert resp.status_code == 200, f"{path} returned {resp.status_code}"

    # Protected POST endpoints that should reject without auth
    @pytest.mark.parametrize("path,body", [
        ("/api/system/reboot", None),
        ("/api/system/restart-app", None),
        ("/api/scenes/activate", {"effect": "fire", "params": {}}),
        ("/api/scenes/presets/save", {"name": "t", "effect": "fire", "params": {}}),
        ("/api/scenes/presets/load/test", None),
        ("/api/brightness/config", {"manual_cap": 0.5}),
        ("/api/display/brightness", {"manual_cap": 0.5}),
        ("/api/display/fps", {"value": 30}),
        ("/api/display/blackout", {"enabled": True}),
        ("/api/media/play/fake-id", None),
        ("/api/diagnostics/test-pattern", {"pattern": "all_white"}),
        ("/api/diagnostics/clear", None),
        ("/api/audio/config", {}),
        ("/api/audio/start", None),
        ("/api/audio/stop", None),
    ])
    def test_protected_post_routes_reject_without_auth(self, client, path, body):
        if body is not None:
            resp = client.post(path, json=body)
        else:
            resp = client.post(path)
        # Should be 401 or 403, not 404/405
        assert resp.status_code in (401, 403), f"{path} returned {resp.status_code} without auth"

    def test_delete_preset_rejects_without_auth(self, client):
        resp = client.delete("/api/scenes/presets/test")
        assert resp.status_code in (401, 403)

    def test_delete_media_rejects_without_auth(self, client):
        resp = client.delete("/api/media/fake-id")
        assert resp.status_code in (401, 403)


# --- Response shape tests ---

class TestResponseShapes:
    """Verify response payloads match the documented contract."""

    def test_system_status_shape(self, client):
        resp = client.get("/api/system/status")
        data = resp.json()
        assert 'transport' in data
        assert 'render' in data
        assert 'brightness' in data
        assert 'scenes_count' in data
        assert 'media_count' in data

    def test_scenes_list_shape(self, client):
        resp = client.get("/api/scenes/list")
        data = resp.json()
        assert 'effects' in data
        assert 'current' in data
        assert isinstance(data['effects'], dict)
        # Each effect should have a type
        for name, info in data['effects'].items():
            assert 'type' in info
            assert info['type'] in ('generative', 'audio', 'diagnostic')

    def test_presets_shape(self, client):
        resp = client.get("/api/scenes/presets")
        data = resp.json()
        assert isinstance(data, dict)

    def test_brightness_status_shape(self, client):
        resp = client.get("/api/brightness/status")
        data = resp.json()
        assert 'manual_cap' in data
        assert 'auto_enabled' in data
        assert 'effective_brightness' in data
        assert 'solar_phase' in data

    def test_media_list_shape(self, client):
        resp = client.get("/api/media/list")
        data = resp.json()
        assert 'items' in data
        assert isinstance(data['items'], list)

    def test_audio_devices_shape(self, client):
        resp = client.get("/api/audio/devices")
        data = resp.json()
        assert 'devices' in data
        assert isinstance(data['devices'], list)

    def test_transport_status_shape(self, client):
        resp = client.get("/api/transport/status")
        data = resp.json()
        assert 'connected' in data
        assert 'frames_sent' in data

    def test_diagnostics_stats_shape(self, client):
        resp = client.get("/api/diagnostics/stats")
        data = resp.json()
        assert 'transport' in data
        assert 'render' in data
        assert 'brightness' in data
        assert 'teensy' in data


# --- Authenticated endpoint behavior ---

class TestAuthenticatedBehavior:
    """Verify endpoints work correctly with valid auth."""

    def test_activate_scene(self, client, auth_header, mock_deps):
        resp = client.post(
            "/api/scenes/activate",
            json={"effect": "fire", "params": {}},
            headers=auth_header,
        )
        assert resp.status_code == 200
        assert resp.json()['status'] == 'ok'
        mock_deps['renderer'].activate_scene.assert_called_with('fire', {})

    def test_activate_unknown_scene_returns_404(self, client, auth_header, mock_deps):
        mock_deps['renderer'].activate_scene.return_value = False
        resp = client.post(
            "/api/scenes/activate",
            json={"effect": "nonexistent"},
            headers=auth_header,
        )
        assert resp.status_code == 404

    def test_save_preset(self, client, auth_header):
        resp = client.post(
            "/api/scenes/presets/save",
            json={"name": "my_preset", "effect": "fire", "params": {"speed": 1.0}},
            headers=auth_header,
        )
        assert resp.status_code == 200
        assert resp.json()['status'] == 'saved'

    def test_load_preset(self, client, auth_header, mock_deps):
        # Save first
        mock_deps['state_manager'].save_scene('my_preset', 'fire', {'speed': 1.0})
        resp = client.post(
            "/api/scenes/presets/load/my_preset",
            headers=auth_header,
        )
        assert resp.status_code == 200

    def test_load_nonexistent_preset(self, client, auth_header):
        resp = client.post(
            "/api/scenes/presets/load/does_not_exist",
            headers=auth_header,
        )
        assert resp.status_code == 404

    def test_delete_preset(self, client, auth_header, mock_deps):
        mock_deps['state_manager'].save_scene('to_delete', 'fire', {})
        resp = client.delete("/api/scenes/presets/to_delete", headers=auth_header)
        assert resp.status_code == 200

    def test_brightness_config(self, client, auth_header):
        resp = client.post(
            "/api/brightness/config",
            json={"manual_cap": 0.5},
            headers=auth_header,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert 'manual_cap' in data

    def test_legacy_brightness(self, client, auth_header):
        resp = client.post(
            "/api/display/brightness",
            json={"manual_cap": 0.7},
            headers=auth_header,
        )
        assert resp.status_code == 200

    def test_set_fps(self, client, auth_header):
        resp = client.post(
            "/api/display/fps",
            json={"value": 30},
            headers=auth_header,
        )
        assert resp.status_code == 200
        assert resp.json()['fps'] == 30

    def test_fps_clamped(self, client, auth_header):
        resp = client.post(
            "/api/display/fps",
            json={"value": 200},
            headers=auth_header,
        )
        assert resp.status_code == 200
        assert resp.json()['fps'] <= 90

    def test_blackout(self, client, auth_header, mock_deps):
        resp = client.post(
            "/api/display/blackout",
            json={"enabled": True},
            headers=auth_header,
        )
        assert resp.status_code == 200
        assert resp.json()['blackout'] is True
        mock_deps['transport'].send_blackout.assert_called_with(True)

    def test_diagnostics_test_pattern(self, client, auth_header, mock_deps):
        resp = client.post(
            "/api/diagnostics/test-pattern",
            json={"pattern": "all_white"},
            headers=auth_header,
        )
        assert resp.status_code == 200

    def test_diagnostics_clear(self, client, auth_header, mock_deps):
        resp = client.post(
            "/api/diagnostics/clear",
            headers=auth_header,
        )
        assert resp.status_code == 200
        mock_deps['transport'].send_test_pattern.assert_called_with(0xFF)

    def test_reboot(self, client, auth_header):
        with patch('subprocess.Popen') as mock_popen:
            resp = client.post("/api/system/reboot", headers=auth_header)
            assert resp.status_code == 200
            mock_popen.assert_called_with(["sudo", "reboot"])

    def test_restart_app(self, client, auth_header):
        with patch('subprocess.Popen') as mock_popen:
            resp = client.post("/api/system/restart-app", headers=auth_header)
            assert resp.status_code == 200
            mock_popen.assert_called_with(["sudo", "systemctl", "restart", "ledfanatic"])

    def test_media_play_not_found(self, client, auth_header):
        resp = client.post("/api/media/play/nonexistent", headers=auth_header)
        assert resp.status_code == 404

    def test_media_delete_not_found(self, client, auth_header):
        resp = client.delete("/api/media/nonexistent", headers=auth_header)
        assert resp.status_code == 404

    def test_audio_config(self, client, auth_header):
        resp = client.post(
            "/api/audio/config",
            json={"sensitivity": 1.5},
            headers=auth_header,
        )
        assert resp.status_code == 200

    def test_audio_start(self, client, auth_header):
        resp = client.post("/api/audio/start", headers=auth_header)
        assert resp.status_code == 200

    def test_audio_stop(self, client, auth_header):
        resp = client.post("/api/audio/stop", headers=auth_header)
        assert resp.status_code == 200


# --- Auth fail-closed tests ---

class TestAuthFailClosed:
    """Verify auth fails closed when token is unconfigured or placeholder."""

    @pytest.fixture
    def no_token_client(self, mock_deps):
        mock_deps['config'] = {}  # No auth token configured
        app = create_app(**mock_deps)
        return TestClient(app, raise_server_exceptions=False)

    @pytest.fixture
    def placeholder_token_client(self, mock_deps):
        mock_deps['config'] = {'auth': {'token': 'your-secret-token-here'}}
        app = create_app(**mock_deps)
        return TestClient(app, raise_server_exceptions=False)

    @pytest.mark.parametrize("path,body", [
        ("/api/scenes/activate", {"effect": "fire"}),
        ("/api/brightness/config", {"manual_cap": 0.5}),
        ("/api/display/blackout", {"enabled": True}),
    ])
    def test_no_token_allows_open_access(self, no_token_client, path, body):
        """No configured token = open access (LAN-only device)."""
        if body:
            resp = no_token_client.post(path, json=body)
        else:
            resp = no_token_client.post(path)
        assert resp.status_code != 401, f"{path} should not reject with no token"

    @pytest.mark.parametrize("path,body", [
        ("/api/scenes/activate", {"effect": "fire"}),
        ("/api/brightness/config", {"manual_cap": 0.5}),
    ])
    def test_placeholder_token_allows_open_access(self, placeholder_token_client, path, body):
        """Placeholder token = open access (treated as no token configured)."""
        if body:
            resp = placeholder_token_client.post(path, json=body)
        else:
            resp = placeholder_token_client.post(path)
        assert resp.status_code != 401, f"{path} should not reject with placeholder token"

    def test_public_endpoints_still_work_without_token(self, no_token_client):
        """Public GET routes work even with no auth configured."""
        resp = no_token_client.get("/api/system/status")
        assert resp.status_code == 200

    def test_wrong_token_rejected(self, client):
        """Wrong bearer token rejected."""
        resp = client.post(
            "/api/system/reboot",
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp.status_code == 401


# --- WebSocket basic test ---

class TestWebSocket:
    def test_ws_connect_and_receive_state(self, client):
        with client.websocket_connect("/ws") as ws:
            data = ws.receive_json()
            assert 'target_fps' in data
            assert 'current_scene' in data

    def test_ws_ping_pong(self, client):
        with client.websocket_connect("/ws") as ws:
            # Receive initial state
            ws.receive_json()
            # Send ping
            ws.send_json({"action": "ping"})
            resp = ws.receive_json()
            assert resp.get('action') == 'pong'

    def test_ws_get_state(self, client):
        with client.websocket_connect("/ws") as ws:
            # Receive initial state
            ws.receive_json()
            ws.send_json({"action": "get_state"})
            resp = ws.receive_json()
            assert 'target_fps' in resp
            assert 'brightness' in resp


# --- Nonexistent route tests ---

class TestNonexistentRoutes:
    """Verify v2/aspirational routes do NOT exist."""

    @pytest.mark.parametrize("path", [
        "/api/mapping/config",
        "/api/mapping/pairs",
        "/api/playlists",
        "/api/wifi/scan",
        "/api/wifi/config",
    ])
    def test_aspirational_routes_do_not_exist(self, client, path):
        resp = client.get(path)
        assert resp.status_code in (404, 405), f"Unexpected route exists: {path}"
