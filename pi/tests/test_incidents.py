"""IncidentRecorder: bundle shape, collision-proof ids, atomic writes with
retry → fallback root, containment-checked lookups, lossless append failures."""

import asyncio
import json
import logging
from unittest.mock import AsyncMock

import pytest

from app.core import incidents as incidents_mod
from app.core.incidents import IncidentRecorder, CaptureResult
from app.transport.trace import TransportTrace

REAL_RUN_JOURNAL = incidents_mod._run_journal   # captured before the autouse patch


class FakeTransport:
  def __init__(self):
    self.trace = TransportTrace()
    self.stalled = {'kind': 'stall', 'reason': '3 unanswered PINGs', 'since': 1.0, 'incident': None}
    self.last_teensy_stats = {'frames_received': 10}
    self.last_teensy_diag = {'boot_id': 7, 'uptime_ms': 1000, 'stall_latched': 0}
    self.last_diag_at = None
    self.request_crash = AsyncMock(return_value={'valid': False})

    async def request_diag(timeout=0.5):
      # The probe's own traffic must not land in the frozen trace
      self.trace.event('probe_traffic')
      return {'boot_id': 7, 'uptime_ms': 1500}
    self.request_diag = request_diag

  def get_status(self):
    return {'state': 'READY', 'boot_id': 7, 'geometry_generation': 3}


class FakeRenderState:
  current_scene = 'rainbow'
  target_fps = 60
  actual_fps = 59.4
  blackout = False


class FakeStateManager:
  current_params = {'speed': 1.0}


class FakeBrightness:
  def get_status(self):
    return {'effective_brightness': 0.42}


def make_recorder(tmp_path, transport=None, **over):
  kwargs = dict(logs_dir=tmp_path / 'logs', fallback_dir=tmp_path / 'fallback', retry_delay_s=0)
  kwargs.update(over)
  return IncidentRecorder(transport or FakeTransport(), FakeRenderState(), FakeStateManager(),
                          FakeBrightness(), **kwargs)


def read_bundle(path):
  with open(path) as f:
    return json.load(f)


@pytest.fixture(autouse=True)
def no_journalctl(monkeypatch):
  monkeypatch.setattr(incidents_mod, '_run_journal', lambda cmd: {'rc': 0, 'text': ' '.join(cmd)})


# --- bundle shape ---

@pytest.mark.asyncio
async def test_bundle_shape_and_trace_frozen_before_probe(tmp_path):
  transport = FakeTransport()
  transport.trace.event('before_capture')
  rec = make_recorder(tmp_path, transport)

  cap = await rec.capture('stall', '3 unanswered PINGs', extra={'since': 1.0})

  assert cap.error is None and cap.path is not None
  assert incidents_mod.INCIDENT_ID_RE.match(cap.id), cap.id
  assert cap.id.split('-')[2] == 'stall'
  b = read_bundle(cap.path)
  assert b['schema_version'] == 2
  assert b['trigger'] == 'stall' and b['reason'] == '3 unanswered PINGs'
  assert b['transport'] == {'state': 'READY', 'boot_id': 7, 'geometry_generation': 3}
  assert b['stalled']['kind'] == 'stall'
  assert b['last_stats'] == {'frames_received': 10}
  assert b['last_diag']['boot_id'] == 7 and 'age_s' in b['last_diag']
  frozen_kinds = [ev['kind'] for ev in b['trace']['events']]
  assert 'before_capture' in frozen_kinds
  assert 'probe_traffic' not in frozen_kinds, "trace must be frozen before the live DIAG probe"
  assert b['live_diag'] == {'boot_id': 7, 'uptime_ms': 1500}
  assert b['crash'] == {'valid': False}
  assert b['render'] == {'scene': 'rainbow', 'params': {'speed': 1.0}, 'target_fps': 60,
                         'actual_fps': 59.4, 'blackout': False, 'brightness_effective': 0.42}
  assert set(b['system']) == {'uptime_s', 'loadavg', 'mem_available_kb'}
  assert b['journal_service']['rc'] == 0 and '-u ledfanatic' in b['journal_service']['text']
  assert b['journal_kernel']['rc'] == 0 and '-k' in b['journal_kernel']['text']
  assert b['extra'] == {'since': 1.0}


@pytest.mark.asyncio
async def test_live_diag_timeout_marker_when_no_reply(tmp_path):
  transport = FakeTransport()
  transport.request_diag = AsyncMock(return_value=None)
  rec = make_recorder(tmp_path, transport)
  cap = await rec.capture('stall', 'x')
  assert read_bundle(cap.path)['live_diag'] == {'error': 'timeout'}


@pytest.mark.asyncio
async def test_live_diag_timeout_marker_when_probe_hangs(tmp_path):
  transport = FakeTransport()

  async def hang(timeout=0.5):
    await asyncio.sleep(10)
  transport.request_diag = hang
  rec = make_recorder(tmp_path, transport, live_diag_timeout_s=0.02)
  cap = await rec.capture('stall', 'x')
  assert read_bundle(cap.path)['live_diag'] == {'error': 'timeout'}


@pytest.mark.asyncio
async def test_journal_error_shape(tmp_path, monkeypatch):
  monkeypatch.setattr(incidents_mod, '_run_journal', REAL_RUN_JOURNAL)
  monkeypatch.setattr(incidents_mod, 'JOURNAL_SERVICE_CMD', ['/nonexistent/journalctl'])
  rec = make_recorder(tmp_path)
  cap = await rec.capture('stall', 'x')
  js = read_bundle(cap.path)['journal_service']
  assert js['rc'] is None and 'error' in js


@pytest.mark.asyncio
async def test_capture_survives_broken_transport(tmp_path):
  transport = FakeTransport()
  transport.get_status = lambda: 1 / 0
  transport.request_crash = AsyncMock(side_effect=RuntimeError('boom'))
  rec = make_recorder(tmp_path, transport)
  cap = await rec.capture('fault', 'x')
  b = read_bundle(cap.path)
  assert 'error' in b['transport'] and 'error' in b['crash']


@pytest.mark.asyncio
async def test_capture_event_uses_detector_payload(tmp_path):
  rec = make_recorder(tmp_path)
  result = await rec.capture_event('unexpected_reboot',
                                   {'reason': 'Teensy rebooted on its own', 'since': 5.0,
                                    'prev_boot_id': 1, 'crash': {'valid': True}})
  assert isinstance(result, CaptureResult) and result.id.split('-')[2] == 'unexpected_reboot'
  b = read_bundle(result.path)
  assert b['reason'] == 'Teensy rebooted on its own'
  assert b['extra'] == {'since': 5.0, 'prev_boot_id': 1, 'crash': {'valid': True}}


# --- ids / files ---

@pytest.mark.asyncio
async def test_two_captures_in_one_second_make_two_files(tmp_path):
  rec = make_recorder(tmp_path)
  a, b = await asyncio.gather(rec.capture('stall', 'a'), rec.capture('stall', 'b'))
  assert a.id != b.id
  assert a.path.exists() and b.path.exists()
  assert len([p for p in (tmp_path / 'logs' / 'incidents').iterdir() if p.suffix == '.json']) == 2
  assert not list((tmp_path / 'logs' / 'incidents').glob('*.tmp'))


@pytest.mark.asyncio
async def test_same_random_suffix_rerolls(tmp_path, monkeypatch):
  tokens = iter(['aaaaaa', 'aaaaaa', 'bbbbbb'])
  monkeypatch.setattr(incidents_mod.secrets, 'token_hex', lambda n: next(tokens))
  rec = make_recorder(tmp_path)
  a = await rec.capture('stall', 'a')
  b = await rec.capture('stall', 'b')
  assert a.id.endswith('aaaaaa') and b.id.endswith('bbbbbb')


@pytest.mark.asyncio
async def test_trigger_is_sanitized(tmp_path):
  rec = make_recorder(tmp_path)
  cap = await rec.capture('Weird Kind!', 'x')
  assert cap.id.split('-')[2] == 'weird_kind'
  assert rec.path_for(cap.id) == cap.path


# --- path_for / list / append across roots ---

@pytest.mark.asyncio
async def test_path_for_rejects_traversal_and_bad_ids(tmp_path):
  rec = make_recorder(tmp_path)
  cap = await rec.capture('stall', 'x')
  (tmp_path / 'outside.json').write_text('{}')
  for bad in ['../outside', '../../outside', 'foo/bar', cap.id + '/../outside',
              cap.id.upper(), cap.id + '.json', '', None, 'x' * 40]:
    assert rec.path_for(bad) is None, bad
  assert rec.path_for(cap.id) == cap.path


@pytest.mark.asyncio
async def test_lookup_resolves_in_both_roots(tmp_path):
  rec = make_recorder(tmp_path)
  primary = await rec.capture('stall', 'x')
  fallback_id = '20260101-010101-stall-abcdef'
  (tmp_path / 'fallback').mkdir()
  (tmp_path / 'fallback' / f'{fallback_id}.json').write_text(json.dumps({'trigger': 'stall'}))
  (tmp_path / 'fallback' / 'not-an-incident.json').write_text('{}')

  assert rec.path_for(fallback_id) == (tmp_path / 'fallback' / f'{fallback_id}.json').resolve()
  listed = rec.list()
  assert [item['id'] for item in listed] == [primary.id, fallback_id]   # newest first
  assert listed[0]['fallback'] is False and listed[1]['fallback'] is True
  assert listed[1]['trigger'] == 'stall' and listed[1]['at'] == '2026-01-01T01:01:01'
  assert await rec.append(fallback_id, 'post_reset', {'rebooted': True})
  assert read_bundle(rec.path_for(fallback_id))['post_reset'] == {'rebooted': True}


@pytest.mark.asyncio
async def test_oserror_retries_then_fallback(tmp_path, caplog):
  # A regular file where the logs dir should be: every mkdir raises OSError
  (tmp_path / 'logs').write_text('not a directory')
  sleeps = []
  rec = make_recorder(tmp_path, retry_delay_s=0.001)
  original_sleep = incidents_mod.time.sleep
  incidents_mod.time.sleep = lambda s: sleeps.append(s)
  try:
    with caplog.at_level(logging.WARNING):
      cap = await rec.capture('link_lost', 'port vanished')
  finally:
    incidents_mod.time.sleep = original_sleep

  assert cap.error is None
  assert cap.path.parent == (tmp_path / 'fallback')
  assert len(sleeps) == incidents_mod.WRITE_ATTEMPTS - 1
  assert sum('attempt' in r.message for r in caplog.records) == incidents_mod.WRITE_ATTEMPTS
  # Everything downstream still works off the fallback copy
  assert rec.path_for(cap.id) == cap.path.resolve()
  assert rec.list()[0] == {**rec.list()[0], 'id': cap.id, 'fallback': True}
  assert await rec.append(cap.id, 'post_reset', {'ok': 1})
  assert read_bundle(cap.path)['post_reset'] == {'ok': 1}


@pytest.mark.asyncio
async def test_failed_write_leaves_no_listed_or_empty_bundle(tmp_path, monkeypatch):
  def broken_write(final, text):
    (final.with_name(final.name + '.tmp')).write_text('partial')
    raise OSError('disk full')
  monkeypatch.setattr(incidents_mod, '_replace_contents', broken_write)
  rec = make_recorder(tmp_path)
  cap = await rec.capture('stall', 'x')
  assert cap.path is None and 'disk full' in cap.error
  assert rec.list() == []
  assert rec.path_for(cap.id) is None
  for root in rec.roots:
    assert not root.is_dir() or list(root.iterdir()) == []    # no .json, .tmp or .lock debris


@pytest.mark.asyncio
async def test_reservation_is_invisible_until_published(tmp_path):
  rec = make_recorder(tmp_path)
  seen_during_write = {}

  def spy_write(final, text):
    seen_during_write['list'] = rec.list()
    seen_during_write['path_for'] = rec.path_for(final.stem)
    seen_during_write['locks'] = sorted(p.name for p in final.parent.iterdir())
    REAL_REPLACE(final, text)
  REAL_REPLACE = incidents_mod._replace_contents
  incidents_mod._replace_contents = spy_write
  try:
    cap = await rec.capture('stall', 'x')
  finally:
    incidents_mod._replace_contents = REAL_REPLACE
  assert seen_during_write['list'] == [] and seen_during_write['path_for'] is None
  assert seen_during_write['locks'] == [f'{cap.id}{incidents_mod.RESERVE_SUFFIX}']
  assert [item['id'] for item in rec.list()] == [cap.id]
  assert sorted(p.name for p in cap.path.parent.iterdir()) == [f'{cap.id}.json']


@pytest.mark.asyncio
async def test_both_roots_unwritable_returns_error_not_exception(tmp_path):
  (tmp_path / 'logs').write_text('x')
  (tmp_path / 'fallback').write_text('x')
  rec = make_recorder(tmp_path)
  cap = await rec.capture('stall', 'x')
  assert cap.path is None and cap.error
  assert incidents_mod.INCIDENT_ID_RE.match(cap.id)


@pytest.mark.asyncio
async def test_append_failure_returns_false_and_logs_value(tmp_path, caplog):
  rec = make_recorder(tmp_path)
  with caplog.at_level(logging.ERROR):
    ok = await rec.append('20260101-010101-stall-abcdef', 'post_reset', {'rebooted': True, 'marker': 'KEEPME'})
  assert ok is False
  assert any('KEEPME' in r.message for r in caplog.records)

  cap = await rec.capture('stall', 'x')
  cap.path.write_text('{not json')
  with caplog.at_level(logging.ERROR):
    ok = await rec.append(cap.id, 'post_reset', {'marker': 'KEEPME2'})
  assert ok is False
  assert any('KEEPME2' in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_append_is_atomic_rewrite(tmp_path):
  rec = make_recorder(tmp_path)
  cap = await rec.capture('stall', 'x')
  assert await rec.append(cap.id, 'post_reset', {'rebooted': False})
  b = read_bundle(cap.path)
  assert b['post_reset'] == {'rebooted': False} and b['trigger'] == 'stall'
  assert not list(cap.path.parent.glob('*.tmp'))
