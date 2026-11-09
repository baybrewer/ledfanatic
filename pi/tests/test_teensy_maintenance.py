"""TeensyMaintenance: single-flight via owned task, shielded awaiter, outcome
persisted on every exit path, maintenance flag always released."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.core import teensy_maintenance as tm
from app.core.incidents import CaptureResult
from app.core.teensy_maintenance import TeensyMaintenance, BusyError, PowerCycleUnavailable
from app.transport.link_state import LinkState


class FakeTransport:
  def __init__(self):
    self.caps = {'boot_id': 11, 'reset_reason_text': 'ipp_reset'}
    self.maintenance = False
    self.state = LinkState.READY
    self.reboot_calls = 0
    self.maintenance_at_wait = None
    self.reboot_gate = None           # asyncio.Event: reboot blocks until set
    self.wait_gate = None             # asyncio.Event: wait_for blocks until set
    self.reboot_result = True
    self.wait_result = True
    self.disconnects = []
    self.request_diag = AsyncMock(return_value={'reset_reason_text': 'software'})
    self.request_crash = AsyncMock(return_value={'valid': False})

  @property
  def boot_id(self):
    return self.caps.get('boot_id') if self.caps else None

  def disconnect(self, reason='x'):
    self.disconnects.append(reason)

  async def reboot(self):
    self.reboot_calls += 1
    if self.reboot_gate is not None:
      await self.reboot_gate.wait()
    if self.reboot_result:
      self.caps = {'boot_id': 12, 'reset_reason_text': 'software'}
    else:
      self.caps = None
    return self.reboot_result

  async def wait_for(self, state, timeout):
    self.maintenance_at_wait = self.maintenance
    if self.wait_gate is not None:
      await self.wait_gate.wait()
    return self.wait_result


class FakeIncidents:
  def __init__(self, error=None):
    self.captures = []
    self.appends = []
    self.error = error
    self.append_result = True

  async def capture(self, trigger, reason, extra=None):
    self.captures.append((trigger, reason))
    return CaptureResult(f'20260101-000000-{trigger}-{len(self.captures):06x}', None, self.error)

  async def append(self, incident_id, key, value):
    self.appends.append((incident_id, key, value))
    return self.append_result


class FakeDetector:
  def __init__(self):
    self.armed = []
    self.disarmed = 0

  def arm_expected_reboot(self, prev_boot_id, ttl):
    self.armed.append((prev_boot_id, ttl))

  def disarm_expected_reboot_if_unconsumed(self):
    self.disarmed += 1


async def settle():
  # reset() creates the owned task inside its own task: two hops before it runs
  for _ in range(3):
    await asyncio.sleep(0)


def make(uhubctl=''):
  t, inc, det = FakeTransport(), FakeIncidents(), FakeDetector()
  return TeensyMaintenance(t, inc, det, uhubctl_path=uhubctl), t, inc, det


# --- happy path ---

@pytest.mark.asyncio
async def test_reset_sequence_and_result():
  m, t, inc, det = make()
  result = await m.reset('user clicked')

  assert inc.captures == [('manual_reset', 'user clicked')]
  assert det.armed == [(11, tm.EXPECTED_REBOOT_TTL_S)] and det.disarmed == 1
  assert t.reboot_calls == 1
  assert t.maintenance is False
  assert t.maintenance_at_wait is False, "reconnect loop must run while we wait for READY"
  assert result == {
    'incident': inc.appends[0][0],
    'rebooted': True, 'reconnected': True, 'error': None,
    'new_boot_id': 12, 'reset_reason_text': 'software', 'crash': {'valid': False},
    'capture_error': None,
  }
  assert inc.appends[0][1] == 'post_reset' and inc.appends[0][2]['rebooted'] is True
  assert m.busy is False and m.current_incident is None


@pytest.mark.asyncio
async def test_reset_proceeds_when_capture_failed():
  m, t, inc, det = make()
  inc.error = 'disk full'
  result = await m.reset()
  assert t.reboot_calls == 1
  assert result['capture_error'] == 'disk full'


@pytest.mark.asyncio
async def test_reboot_false_and_no_diag():
  m, t, inc, det = make()
  t.reboot_result = False
  t.wait_result = False
  t.request_diag = AsyncMock(return_value=None)
  t.request_crash = AsyncMock(return_value=None)
  result = await m.reset()
  assert result['rebooted'] is False and result['reconnected'] is False
  assert result['reset_reason_text'] is None and result['new_boot_id'] is None
  assert result['crash'] is None and result['error'] is None
  assert t.maintenance is False and det.disarmed == 1


# --- single flight ---

@pytest.mark.asyncio
async def test_concurrent_resets_run_exactly_one_reboot():
  m, t, inc, det = make()
  t.reboot_gate = asyncio.Event()

  first = asyncio.create_task(m.reset('first'))
  await settle()                    # first reset has captured and is inside reboot()
  with pytest.raises(BusyError) as excinfo:
    await m.reset('second')
  assert excinfo.value.incident == m.current_incident and 'manual_reset' in excinfo.value.incident
  assert not first.done(), "BusyError must be immediate, not queued behind the first reset"

  t.reboot_gate.set()
  result = await first
  assert t.reboot_calls == 1
  assert inc.captures == [('manual_reset', 'first')]
  assert result['rebooted'] is True
  # Slot free again afterwards
  await m.reset('third')
  assert t.reboot_calls == 2


@pytest.mark.asyncio
async def test_busy_error_carries_current_incident_id():
  m, t, inc, det = make()
  t.reboot_gate = asyncio.Event()
  first = asyncio.create_task(m.reset())
  await settle()
  assert m.busy and m.current_incident is not None
  with pytest.raises(BusyError) as excinfo:
    await m.reset()
  assert excinfo.value.incident == m.current_incident
  t.reboot_gate.set()
  await first


# --- cancellation ---

@pytest.mark.asyncio
async def test_cancelled_awaiter_does_not_abort_operation():
  m, t, inc, det = make()
  t.reboot_gate = asyncio.Event()

  awaiter = asyncio.create_task(m.reset())
  await settle()
  awaiter.cancel()                  # HTTP client went away mid-reset
  with pytest.raises(asyncio.CancelledError):
    await awaiter

  assert m.busy, "the owned task must keep running"
  t.reboot_gate.set()
  await m._task
  assert t.reboot_calls == 1
  assert [a[1] for a in inc.appends] == ['post_reset']
  assert inc.appends[0][2]['rebooted'] is True and inc.appends[0][2]['reconnected'] is True
  assert t.maintenance is False


@pytest.mark.asyncio
async def test_cancellation_during_wait_for_clears_maintenance_and_appends():
  m, t, inc, det = make()
  t.wait_gate = asyncio.Event()
  awaiter = asyncio.create_task(m.reset())
  for _ in range(5):
    await asyncio.sleep(0)
  assert t.reboot_calls == 1 and m.busy

  m._task.cancel()                  # e.g. shutdown cancels the operation itself
  with pytest.raises(asyncio.CancelledError):
    await awaiter

  assert t.maintenance is False
  assert det.disarmed == 1
  assert len(inc.appends) == 1
  post = inc.appends[0][2]
  assert post['error'] == 'cancelled' and post['rebooted'] is True and post['reconnected'] is False


@pytest.mark.asyncio
async def test_reboot_exception_is_reported_and_maintenance_cleared():
  m, t, inc, det = make()

  async def boom():
    raise RuntimeError('loader missing')
  t.reboot = boom
  result = await m.reset()
  assert result['error'] == "RuntimeError('loader missing')"
  assert result['rebooted'] is False
  assert t.maintenance is False and det.disarmed == 1
  assert inc.appends[0][2]['error'] == result['error']


@pytest.mark.asyncio
async def test_append_failure_does_not_break_result():
  m, t, inc, det = make()
  inc.append = AsyncMock(side_effect=OSError('ro fs'))
  result = await m.reset()
  assert result['rebooted'] is True


# --- usb power cycle ---

@pytest.mark.asyncio
async def test_usb_power_cycle_unavailable_without_uhubctl():
  m, t, inc, det = make(uhubctl='')
  assert m.usb_power_cycle_available is False
  with pytest.raises(PowerCycleUnavailable):
    await m.usb_power_cycle()
  assert inc.captures == []


@pytest.mark.asyncio
async def test_usb_power_cycle_runs_sudo_uhubctl(monkeypatch):
  m, t, inc, det = make(uhubctl='/usr/sbin/uhubctl')
  assert m.usb_power_cycle_available is True
  calls = []

  class FakeProc:
    returncode = 0

    async def communicate(self):
      return b'Current status for hub 1-1\n', b''

  async def fake_exec(*args, **kwargs):
    calls.append(args)
    return FakeProc()
  monkeypatch.setattr(tm.asyncio, 'create_subprocess_exec', fake_exec)

  result = await m.usb_power_cycle('wedged')
  assert calls == [('sudo', '/usr/sbin/uhubctl', '-l', '1-1', '-a', 'cycle', '-d', '3')]
  assert inc.captures == [('usb_power_cycle', 'wedged')]
  assert t.disconnects == ['maintenance_power_cycle']
  assert t.maintenance_at_wait is False and t.maintenance is False
  assert det.armed[0][0] == 11 and det.disarmed == 1
  assert result['cycled'] is True and result['reconnected'] is True
  assert 'hub 1-1' in result['output']
  assert inc.appends[0][1] == 'post_reset'


@pytest.mark.asyncio
async def test_usb_power_cycle_is_single_flight_with_reset():
  m, t, inc, det = make(uhubctl='/usr/sbin/uhubctl')
  t.reboot_gate = asyncio.Event()
  first = asyncio.create_task(m.reset())
  await settle()
  with pytest.raises(BusyError):
    await m.usb_power_cycle()
  t.reboot_gate.set()
  await first


def test_find_uhubctl_falls_back_to_usr_sbin(monkeypatch):
  monkeypatch.setattr(tm.shutil, 'which', lambda name: None)
  monkeypatch.setattr(tm.os.path, 'exists', lambda p: p == '/usr/sbin/uhubctl')
  assert tm._find_uhubctl() == '/usr/sbin/uhubctl'
  monkeypatch.setattr(tm.os.path, 'exists', lambda p: False)
  assert tm._find_uhubctl() is None
