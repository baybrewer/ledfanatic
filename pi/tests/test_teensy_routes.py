"""/api/teensy routes and the WebSocket `transport` state block."""

import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api.server import create_app
from app.api.routes.ws import build_state_payload
from app.core.renderer import Renderer, RenderState
from app.core.state import StateManager
from app.core.brightness import BrightnessEngine
from app.core.incidents import IncidentRecorder
from app.core.teensy_maintenance import TeensyMaintenance, BusyError
from app.media.manager import MediaManager
from app.audio.analyzer import AudioAnalyzer
from app.models.protocol import STALL_FAULTED
from app.transport.link_state import LinkState
from app.transport.trace import TransportTrace


class FakeTransport:
  def __init__(self):
    self.state = LinkState.READY
    self.caps = {'boot_id': 5, 'firmware_version': '1.3.0', 'faulted': 0}
    self.stalled = None
    self.maintenance = False
    self.trace = TransportTrace()
    self.last_teensy_stats = None
    self.last_teensy_diag = None
    self.last_diag_at = None
    self.request_diag = AsyncMock(return_value=None)
    self.request_crash = AsyncMock(return_value={'valid': True, 'text': 'HardFault'})
    self.send_blackout = AsyncMock(return_value=True)
    self.send_brightness = AsyncMock(return_value=True)
    self.send_test_pattern = AsyncMock(return_value=True)
    self.request_stats = AsyncMock(return_value=None)
    self.reboot = AsyncMock(return_value=True)
    self.frames_sent = 0
    self.send_errors = 0
    self.reconnect_count = 0
    self.serial = None

  @property
  def connected(self):
    return self.state.at_least(LinkState.HELLO_OK)

  @property
  def boot_id(self):
    return self.caps.get('boot_id') if self.caps else None

  async def wait_for(self, state, timeout):
    return True

  def get_status(self):
    return {
      'connected': self.connected, 'state': self.state.value, 'port': None, 'caps': self.caps,
      'firmware_version': self.caps.get('firmware_version') if self.caps else None,
      'boot_id': self.boot_id, 'geometry_generation': 2, 'frames_sent': 0, 'send_errors': 0,
      'reconnect_count': 0, 'stalled': self.stalled, 'maintenance': self.maintenance,
      'last_diag_age_s': None,
    }


class FakeDetector:
  def arm_expected_reboot(self, prev_boot_id, ttl):
    pass

  def disarm_expected_reboot_if_unconsumed(self):
    pass


@pytest.fixture
def env(tmp_path, monkeypatch):
  from app.core import incidents as incidents_mod
  monkeypatch.setattr(incidents_mod, '_run_journal', lambda cmd: {'rc': 0, 'text': ''})

  transport = FakeTransport()
  render_state = RenderState()
  state_manager = StateManager(config_dir=tmp_path)
  state_manager.load()
  brightness_engine = BrightnessEngine()
  renderer = MagicMock(spec=Renderer)
  renderer.state = render_state
  incidents = IncidentRecorder(transport, render_state, state_manager, brightness_engine,
                               logs_dir=tmp_path / 'logs', fallback_dir=tmp_path / 'fb',
                               retry_delay_s=0)
  maintenance = TeensyMaintenance(transport, incidents, FakeDetector(), uhubctl_path='')
  audio = MagicMock(spec=AudioAnalyzer)
  audio.list_devices.return_value = []
  app = create_app(
    transport=transport, renderer=renderer, render_state=render_state,
    state_manager=state_manager, brightness_engine=brightness_engine,
    media_manager=MediaManager(media_dir=tmp_path / 'media', cache_dir=tmp_path / 'cache'),
    audio_analyzer=audio, config={'auth': {'token': 'tok'}},
    incidents=incidents, maintenance=maintenance,
  )
  return {
    'client': TestClient(app, raise_server_exceptions=False), 'app': app,
    'transport': transport, 'incidents': incidents, 'maintenance': maintenance,
  }


AUTH = {'Authorization': 'Bearer tok'}


# --- diag / crash ---

def test_diag_503_until_pushed(env):
  assert env['client'].get('/api/teensy/diag').status_code == 503
  env['transport'].last_teensy_diag = {'boot_id': 5, 'uptime_ms': 1000, 'stall_latched': 0}
  env['transport'].last_diag_at = time.monotonic() - 1.0
  resp = env['client'].get('/api/teensy/diag')
  assert resp.status_code == 200
  body = resp.json()
  assert body['diag']['boot_id'] == 5
  assert 0.9 <= body['age_s'] <= 5.0
  assert body['boot_id'] == 5 and body['state'] == 'READY'


def test_crash_previous_boot_vs_live(env):
  resp = env['client'].get('/api/teensy/crash')
  assert resp.status_code == 200
  assert resp.json() == {'crash': {'valid': True, 'text': 'HardFault'}, 'source': 'previous_boot',
                         'boot_id': 5}
  env['transport'].last_teensy_diag = {'boot_id': 5, 'stall_latched': STALL_FAULTED}
  assert env['client'].get('/api/teensy/crash').json()['source'] == 'live'


def test_crash_503_when_disconnected_or_silent(env):
  env['transport'].request_crash = AsyncMock(return_value=None)
  assert env['client'].get('/api/teensy/crash').status_code == 503
  env['transport'].state = LinkState.CLOSED
  assert env['client'].get('/api/teensy/crash').status_code == 503


# --- incidents ---

def test_incident_list_and_download(env):
  client = env['client']
  assert client.get('/api/teensy/incidents').json() == {'incidents': [], 'count': 0}

  resp = client.post('/api/teensy/reset', json={'reason': 'test'}, headers=AUTH)
  assert resp.status_code == 200
  incident_id = resp.json()['incident']

  listed = client.get('/api/teensy/incidents').json()
  assert listed['count'] == 1
  item = listed['incidents'][0]
  assert item['id'] == incident_id and item['trigger'] == 'manual_reset'
  assert item['fallback'] is False and item['size_bytes'] > 0 and item['at']

  dl = client.get(f'/api/teensy/incidents/{incident_id}')
  assert dl.status_code == 200
  assert dl.headers['content-type'].startswith('application/json')
  assert f'{incident_id}.json' in dl.headers['content-disposition']
  bundle = dl.json()
  assert bundle['trigger'] == 'manual_reset' and bundle['post_reset']['rebooted'] is True


def test_incident_download_404_for_unknown_or_traversal(env):
  client = env['client']
  assert client.get('/api/teensy/incidents/20260101-010101-stall-abcdef').status_code == 404
  assert client.get('/api/teensy/incidents/..%2F..%2Fetc%2Fpasswd').status_code == 404
  assert client.get('/api/teensy/incidents/not-an-id').status_code == 404


# --- reset ---

def test_reset_requires_auth(env):
  assert env['client'].post('/api/teensy/reset').status_code == 401
  assert env['transport'].reboot.await_count == 0


def test_reset_200_shape(env):
  resp = env['client'].post('/api/teensy/reset', headers=AUTH)
  assert resp.status_code == 200
  body = resp.json()
  assert set(body) == {'incident', 'rebooted', 'reconnected', 'error', 'new_boot_id',
                       'reset_reason_text', 'crash', 'capture_error'}
  assert body['rebooted'] is True and body['reconnected'] is True and body['error'] is None
  assert body['new_boot_id'] == 5 and body['capture_error'] is None
  assert env['incidents'].path_for(body['incident']) is not None


def test_reset_409_when_busy(env):
  env['maintenance'].reset = AsyncMock(side_effect=BusyError('20260101-010101-manual_reset-abcdef'))
  resp = env['client'].post('/api/teensy/reset', headers=AUTH)
  assert resp.status_code == 409
  assert resp.json() == {'busy': True, 'incident': '20260101-010101-manual_reset-abcdef'}


# --- usb power cycle ---

def test_usb_power_cycle_404_when_unavailable(env):
  assert env['client'].post('/api/teensy/usb-power-cycle').status_code == 401
  resp = env['client'].post('/api/teensy/usb-power-cycle', headers=AUTH)
  assert resp.status_code == 404


def test_usb_power_cycle_200_when_available(env):
  env['maintenance']._uhubctl = '/usr/sbin/uhubctl'
  env['maintenance'].usb_power_cycle = AsyncMock(return_value={
    'incident': 'x', 'cycled': True, 'reconnected': True, 'error': None, 'output': '',
    'new_boot_id': 6, 'reset_reason_text': 'power_on', 'crash': None, 'capture_error': None,
  })
  resp = env['client'].post('/api/teensy/usb-power-cycle', json={'reason': 'why'}, headers=AUTH)
  assert resp.status_code == 200 and resp.json()['cycled'] is True
  env['maintenance'].usb_power_cycle.assert_awaited_once_with('why')


def test_usb_power_cycle_409_when_busy(env):
  env['maintenance']._uhubctl = '/usr/sbin/uhubctl'
  env['maintenance'].usb_power_cycle = AsyncMock(side_effect=BusyError('inc'))
  resp = env['client'].post('/api/teensy/usb-power-cycle', headers=AUTH)
  assert resp.status_code == 409 and resp.json() == {'busy': True, 'incident': 'inc'}


# --- state payload ---

def test_state_payload_transport_block(env):
  deps = env['app'].state.deps
  env['transport'].stalled = {'kind': 'stall', 'reason': '3 unanswered PINGs', 'since': 1.0,
                              'incident': 'abc'}
  env['transport'].maintenance = True
  block = build_state_payload(deps)['transport']
  assert block == {
    'state': 'READY', 'firmware_version': '1.3.0', 'boot_id': 5,
    'stalled': {'kind': 'stall', 'reason': '3 unanswered PINGs', 'since': 1.0, 'incident': 'abc'},
    'maintenance': True, 'usb_power_cycle_available': False,
  }
  env['transport'].stalled = None
  env['maintenance']._uhubctl = '/usr/sbin/uhubctl'
  block = build_state_payload(deps)['transport']
  assert block['stalled'] is None and block['usb_power_cycle_available'] is True


def test_state_payload_without_maintenance_dep(env):
  deps = env['app'].state.deps
  deps.maintenance = None
  assert build_state_payload(deps)['transport']['usb_power_cycle_available'] is False


def test_routes_503_without_deps(env):
  deps = env['app'].state.deps
  deps.maintenance = None
  deps.incidents = None
  assert env['client'].post('/api/teensy/reset', headers=AUTH).status_code == 503
  assert env['client'].get('/api/teensy/incidents').status_code == 503
