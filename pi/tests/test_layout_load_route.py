"""POST /api/layout/configs/load: layout.yaml is replaced and the renderer switched
only when the geometry transaction actually committed (Codex impl review round 3)."""

import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api.server import create_app
from app.core.brightness import BrightnessEngine
from app.core.renderer import RenderState
from app.core.state import StateManager
from app.layout import load_layout, compile_layout
from app.transport.usb import TeensyTransport

AUTH = {"Authorization": "Bearer t"}


@pytest.fixture
def env(tmp_path):
  config_dir = tmp_path / "config"
  config_dir.mkdir()
  saves = config_dir / "layout_saves"
  saves.mkdir()
  # Current layout = the repo one; the save to load is a different geometry
  shutil.copy(Path("config/layout.yaml"), config_dir / "layout.yaml")
  (saves / "two.yaml").write_text(
    "version: 1\nmatrix: {width: 2, height: 10}\noutputs:\n"
    "  - id: octo_ch0\n    channel: 0\n    chipset: WS2812\n    color_order: BGR\n    segments:\n"
    "      - id: a\n        type: linear\n        start: {x: 0, y: 0}\n        direction: \"+y\"\n        length: 10\n        physical_offset: 0\n"
    "      - id: b\n        type: linear\n        start: {x: 1, y: 9}\n        direction: \"-y\"\n        length: 10\n        physical_offset: 10\n")
  transport = MagicMock(spec=TeensyTransport)
  transport.connected = True
  transport.caps = None
  transport.frames_sent = transport.send_errors = transport.reconnect_count = 0
  transport.get_status.return_value = {'connected': True}
  renderer = MagicMock()
  state_manager = StateManager(config_dir=tmp_path)
  state_manager.load()
  layout_config = load_layout(config_dir)
  app = create_app(transport=transport, renderer=renderer, render_state=RenderState(),
                   state_manager=state_manager, brightness_engine=BrightnessEngine({}),
                   media_manager=MagicMock(), audio_analyzer=None,
                   config={'auth': {'token': 't'}}, layout_config=layout_config,
                   compiled_layout=compile_layout(layout_config))
  app.state.deps.config_dir = config_dir
  return TestClient(app, raise_server_exceptions=False), transport, renderer, config_dir


def _original(config_dir):
  return (config_dir / "layout.yaml").read_text()


def test_rejected_or_dropped_config_leaves_layout_yaml_and_renderer_alone(env):
  client, transport, renderer, config_dir = env
  before = _original(config_dir)
  # apply_geometry runs but the commit callback never fires (NAK / timeout / link dropped)
  transport.apply_geometry = AsyncMock(return_value=False)
  r = client.post("/api/layout/configs/load", json={"filename": "two.yaml"}, headers=AUTH)
  assert r.status_code == 502
  assert _original(config_dir) == before
  renderer.apply_layout.assert_not_called()


def test_committed_config_persists_layout_yaml_and_switches_renderer(env):
  client, transport, renderer, config_dir = env

  async def apply_geometry(provider, commit, **kw):
    compiled, _ = provider()
    commit(compiled)
    return True
  transport.apply_geometry = apply_geometry
  r = client.post("/api/layout/configs/load", json={"filename": "two.yaml"}, headers=AUTH)
  assert r.status_code == 200 and r.json()["status"] == "ok"
  assert "width: 2" in _original(config_dir)
  renderer.apply_layout.assert_called_once()
