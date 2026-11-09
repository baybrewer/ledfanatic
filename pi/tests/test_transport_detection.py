"""LinkDetector: one incident per episode, clearing conditions, blackout rules,
expected vs unexpected reboots — and never a reset of any kind."""

import asyncio
import inspect
from unittest.mock import AsyncMock, patch

import pytest

from app.models.protocol import DIAG_FIELD_NAMES, STALL_FAULTED
from app.transport import detection
from app.transport.detection import LinkDetector, FLAG_QUIET_S, OUTPUT_WINDOW_S, DIAG_STALE_S
from app.transport.link_state import LinkState
from app.transport.trace import TransportTrace
from app.transport import usb


class FakeTransport:
  def __init__(self):
    self.state = LinkState.READY
    self.blackout = False
    self.test_pattern_active = None
    self.frames_sent = 0
    self.last_teensy_diag = None
    self.last_diag_at = None
    self.last_teensy_stats = None
    self.caps = None
    self.maintenance = False
    self.stalled = None
    self.trace = TransportTrace()
    self.request_crash = AsyncMock(return_value={'valid': True, 'text': 'HardFault'})


def diag(**over):
  d = {name: 0 for name in DIAG_FIELD_NAMES}
  d['boot_id'] = 1
  d.update(over)
  return d


class Harness:
  def __init__(self):
    self.clock = 100.0
    self.transport = FakeTransport()
    self.incidents: list[tuple[str, dict]] = []
    self.detector = LinkDetector(self.transport, self.record, now=lambda: self.clock)

  async def record(self, kind, detail):
    self.incidents.append((kind, detail))
    return {'id': f'inc-{len(self.incidents)}'}

  def kinds(self):
    return [k for k, _ in self.incidents]

  def push_diag(self, **over):
    # uptime follows the harness clock unless a test pins it (rollback cases)
    over.setdefault('uptime_ms', int(self.clock * 1000))
    d = diag(**over)
    self.transport.last_teensy_diag = d
    self.transport.last_diag_at = self.clock
    self.detector.on_diag(d)
    return d

  def advance(self, seconds):
    self.clock += seconds

  async def settle(self):
    for _ in range(3):
      await asyncio.sleep(0)


@pytest.fixture
def h():
  return Harness()


# --- stall / write_timeout / link_lost ---

@pytest.mark.asyncio
async def test_ping_misses_open_one_stall_episode_then_new_one_after_clear(h):
  for _ in range(2):
    h.detector.on_ping_result(False)
  await h.settle()
  assert h.incidents == []
  h.detector.on_ping_result(False)
  h.detector.on_ping_result(False)          # re-assert inside the episode
  await h.settle()
  assert h.kinds() == ['stall']
  assert h.transport.stalled['kind'] == 'stall' and h.transport.stalled['incident'] == 'inc-1'
  h.detector.on_ping_result(True)
  assert h.transport.stalled is None
  for _ in range(3):
    h.detector.on_ping_result(False)
  await h.settle()
  assert h.kinds() == ['stall', 'stall']


@pytest.mark.asyncio
async def test_handshake_failures_and_config_timeout_are_stalls(h):
  h.detector.on_handshake_failed(1)
  await h.settle()
  assert h.incidents == []
  h.detector.on_handshake_failed(2)
  await h.settle()
  assert h.kinds() == ['stall']
  h.detector.on_state(LinkState.READY)
  assert h.detector.open_episodes == {}
  h.detector.on_config_result(False, timed_out=True)
  await h.settle()
  assert h.kinds() == ['stall', 'stall']
  assert 'CONFIG' in h.incidents[-1][1]['reason']
  h.detector.on_config_result(False, timed_out=False)     # NAK is an answer, not a stall
  h.detector.on_config_result(True)                       # ACK alone is not READY: episode stays open
  assert 'stall' in h.detector.open_episodes
  h.detector.on_state(LinkState.READY)
  assert h.detector.open_episodes == {}


@pytest.mark.asyncio
async def test_write_timeout_episode(h):
  h.detector.on_write_timeout({'error': 'x'})
  h.detector.on_write_timeout({'error': 'y'})
  await h.settle()
  assert h.kinds() == ['write_timeout']
  h.detector.on_state(LinkState.READY)
  h.detector.on_write_timeout()
  await h.settle()
  assert h.kinds() == ['write_timeout', 'write_timeout']


@pytest.mark.asyncio
async def test_link_lost_only_when_not_in_maintenance(h):
  h.detector.on_disconnected('link_lost')
  await h.settle()
  assert h.kinds() == ['link_lost']
  h.detector.on_state(LinkState.READY)
  h.transport.maintenance = True
  h.detector.on_disconnected('link_lost')
  h.detector.on_disconnected('disconnect')
  await h.settle()
  assert h.kinds() == ['link_lost']


# --- output_stall ---

def _run_window(h, pi_frames, rx_delta, applied_delta):
  """One full sampling window: start sample, then end sample with the deltas."""
  d = h.transport.last_teensy_diag or diag()
  h.detector.tick()                                   # opens/continues the window
  h.advance(OUTPUT_WINDOW_S)
  h.transport.frames_sent += pi_frames
  h.push_diag(frames_received=d['frames_received'] + rx_delta,
              frames_applied=d['frames_applied'] + applied_delta,
              uptime_ms=d['uptime_ms'] + 10_000)
  h.detector.tick()


@pytest.mark.asyncio
async def test_output_stall_received_growing_applied_flat_then_clears(h):
  h.push_diag(frames_received=10, frames_applied=10)
  _run_window(h, pi_frames=200, rx_delta=200, applied_delta=0)
  await h.settle()
  assert h.kinds() == ['output_stall']
  _run_window(h, pi_frames=200, rx_delta=200, applied_delta=0)   # still stalled: same episode
  await h.settle()
  assert h.kinds() == ['output_stall']
  _run_window(h, pi_frames=200, rx_delta=200, applied_delta=200)  # both progress: clears
  assert h.detector.open_episodes == {}
  _run_window(h, pi_frames=200, rx_delta=0, applied_delta=0)      # Teensy receives nothing
  await h.settle()
  assert h.kinds() == ['output_stall', 'output_stall']


@pytest.mark.asyncio
async def test_output_stall_needs_enough_pi_frames_and_ready(h):
  h.push_diag(frames_received=10, frames_applied=10)
  _run_window(h, pi_frames=50, rx_delta=0, applied_delta=0)       # Pi barely rendered
  await h.settle()
  assert h.incidents == []
  h.transport.state = LinkState.CONFIGURED
  _run_window(h, pi_frames=500, rx_delta=0, applied_delta=0)
  await h.settle()
  assert h.incidents == []


@pytest.mark.asyncio
async def test_stale_pushed_diag_while_ready_is_output_stall(h):
  h.push_diag()
  h.detector.tick()
  h.advance(DIAG_STALE_S + 1)
  h.detector.tick()
  await h.settle()
  assert h.kinds() == ['output_stall'] and 'DIAG' in h.incidents[0][1]['reason']


@pytest.mark.asyncio
async def test_ready_without_first_diag_is_output_stall_on_diag_firmware(h):
  h.detector.on_connected({'boot_id': 1, 'firmware_version': '1.3.0'})
  h.detector.on_state(LinkState.READY)
  h.detector.tick()
  h.advance(DIAG_STALE_S)                          # exactly at the limit: still waiting
  h.detector.tick()
  await h.settle()
  assert h.incidents == []
  h.advance(1)
  h.detector.tick()
  await h.settle()
  assert h.kinds() == ['output_stall'] and 'READY' in h.incidents[0][1]['reason']
  h.detector.tick()                                # re-assert: same episode
  await h.settle()
  assert h.kinds() == ['output_stall']


@pytest.mark.asyncio
async def test_legacy_firmware_never_pushes_diag_and_is_exempt(h):
  h.detector.on_connected({'boot_id': 0, 'firmware_version': '1.2.0'})
  h.detector.on_state(LinkState.READY)
  for _ in range(100):
    h.advance(1)
    h.detector.tick()
  await h.settle()
  assert h.incidents == []
  # The deadline is per READY entry: leaving READY resets it
  h.detector.on_disconnected('disconnect')
  h.detector.on_connected({'boot_id': 3, 'firmware_version': '1.3.0'})
  h.detector.on_state(LinkState.CONFIGURED)
  h.advance(DIAG_STALE_S + 10)
  h.detector.tick()
  await h.settle()
  assert h.incidents == []


@pytest.mark.asyncio
async def test_blackout_suppresses_output_stall_but_not_fault(h):
  h.transport.blackout = True
  h.push_diag(frames_received=10, frames_applied=10)
  _run_window(h, pi_frames=500, rx_delta=0, applied_delta=0)
  await h.settle()
  assert h.incidents == []
  h.push_diag(stall_latched=STALL_FAULTED, fault_count=1)
  h.push_diag(stall_latched=STALL_FAULTED, fault_count=1)      # same boot: one incident
  await h.settle()
  assert h.kinds() == ['fault']
  # CRASH is the recorder's probe (after its own freeze), never the detector's
  assert 'crash' not in h.incidents[0][1]
  h.transport.request_crash.assert_not_awaited()
  assert h.transport.stalled['kind'] == 'fault'


@pytest.mark.asyncio
async def test_fault_evidence_is_frozen_at_emission_not_at_capture(h):
  h.transport.trace.event('failure_bytes')
  h.push_diag(stall_latched=STALL_FAULTED, fault_count=1)
  # Traffic between the hook and the recorder task must not reach the evidence
  h.transport.trace.event('later_traffic')
  h.transport.last_teensy_diag = {'boot_id': 1, 'uptime_ms': 999_999}
  await h.settle()
  evidence = h.incidents[0][1]['evidence']
  kinds = [e['kind'] for e in evidence['trace']['events']]
  assert 'failure_bytes' in kinds and 'later_traffic' not in kinds
  assert evidence['last_diag']['stall_latched'] == STALL_FAULTED


@pytest.mark.asyncio
async def test_fault_episode_survives_reconnect_to_same_faulted_boot(h):
  h.detector.on_connected({'boot_id': 7, 'faulted': True})
  await h.settle()
  assert h.kinds() == ['fault']
  fault = h.detector.open_episodes['fault']
  h.detector.on_disconnected('link_lost')
  h.detector.on_state(LinkState.READY)
  h.detector.on_connected({'boot_id': 7, 'faulted': True})      # same boot, still faulted
  await h.settle()
  assert h.detector.open_episodes['fault'] is fault             # same episode, same incident
  assert h.kinds().count('fault') == 1
  h.push_diag(boot_id=7, stall_latched=STALL_FAULTED, fault_count=1)
  assert 'fault' in h.detector.open_episodes
  # Only a different boot id ends it (and a pushed DIAG can be the first to show it)
  h.push_diag(boot_id=8)
  assert 'fault' not in h.detector.open_episodes


@pytest.mark.asyncio
async def test_fault_from_caps_and_fault_count_growth_once_per_boot(h):
  h.detector.on_connected({'boot_id': 7, 'faulted': True})
  await h.settle()
  assert h.kinds() == ['fault']
  h.push_diag(boot_id=7, fault_count=1)
  h.push_diag(boot_id=7, fault_count=2)
  await h.settle()
  assert h.kinds() == ['fault']
  # A new boot clears the fault episode; its own fault counts again
  h.detector.on_disconnected('disconnect')
  h.detector.arm_expected_reboot(7)
  h.detector.on_connected({'boot_id': 8})
  assert 'fault' not in h.detector.open_episodes
  h.push_diag(boot_id=8, fault_count=0)
  h.push_diag(boot_id=8, fault_count=1)
  await h.settle()
  assert h.kinds() == ['fault', 'fault']


# --- teensy_flag ---

@pytest.mark.asyncio
async def test_teensy_flag_counters_and_bits(h):
  h.transport.state = LinkState.CONFIGURED      # keep output_stall out of this test
  h.push_diag(bad_crc=0)
  h.push_diag(bad_crc=1)
  h.push_diag(bad_crc=2)
  await h.settle()
  assert h.kinds() == ['teensy_flag'] and 'bad_crc+' in h.incidents[0][1]['reason']
  h.advance(FLAG_QUIET_S)
  h.detector.tick()
  assert h.detector.open_episodes == {}
  h.push_diag(bad_crc=2, stall_active=0b10)                        # DMA_INCOMPLETE gained
  await h.settle()
  assert h.kinds() == ['teensy_flag', 'teensy_flag']
  assert 'DMA_INCOMPLETE' in h.incidents[1][1]['reason']
  h.advance(FLAG_QUIET_S)
  h.push_diag(bad_crc=2, stall_active=0b10)                        # bit still set: stays open
  assert 'teensy_flag' in h.detector.open_episodes
  h.advance(FLAG_QUIET_S)
  h.push_diag(bad_crc=2, stall_active=0)
  assert h.detector.open_episodes == {}


# --- unexpected_reboot ---

@pytest.mark.asyncio
async def test_boot_id_change_without_expected_reboot_carries_previous_trace(h):
  h.transport.trace.event('marker', n=1)
  h.detector.on_connected({'boot_id': 1})
  h.push_diag(boot_id=1, uptime_ms=5000)
  h.detector.on_disconnected('link_lost')
  h.detector.on_state(LinkState.READY)
  h.detector.on_connected({'boot_id': 2, 'reset_reason_text': 'IPP_RESET_B'})
  await h.settle()
  kinds = h.kinds()
  assert kinds.count('unexpected_reboot') == 1
  detail = dict(h.incidents)['unexpected_reboot']
  assert detail['prev_boot_id'] == 1 and detail['new_boot_id'] == 2
  assert detail['previous']['last_diag']['uptime_ms'] == 5000
  assert any(e['kind'] == 'marker' for e in detail['previous']['trace']['events'])
  assert 'crash' not in detail                       # recorder probes CRASH after its freeze
  h.transport.request_crash.assert_not_awaited()
  assert 'unexpected_reboot' not in h.detector.open_episodes   # point event


@pytest.mark.asyncio
async def test_failed_handshake_does_not_erase_reboot_baseline(h):
  h.transport.trace.event('healthy_marker')
  h.detector.on_connected({'boot_id': 1})
  h.push_diag(boot_id=1, uptime_ms=5000)
  h.transport.caps = {'boot_id': 1}
  h.detector.on_disconnected('link_lost')
  # dispatcher.attach() has already cleared DIAG and the port has no CAPS yet
  h.transport.caps = None
  h.transport.last_teensy_diag = None
  h.transport.trace.event('handshake_noise')
  h.detector.on_disconnected('handshake_failed')
  h.detector.on_state(LinkState.READY)
  h.detector.on_connected({'boot_id': 2})
  await h.settle()
  detail = dict(h.incidents)['unexpected_reboot']
  assert detail['prev_boot_id'] == 1 and detail['new_boot_id'] == 2
  previous = detail['previous']
  assert previous['boot_id'] == 1 and previous['caps'] == {'boot_id': 1}
  assert previous['last_diag']['uptime_ms'] == 5000
  kinds = [e['kind'] for e in previous['trace']['events']]
  assert 'healthy_marker' in kinds and 'handshake_noise' not in kinds
  assert [a['reason'] for a in detail['failed_attempts']] == ['handshake_failed']


@pytest.mark.asyncio
async def test_boot_id_change_in_pushed_diag_is_a_live_reboot(h):
  hook_calls = []
  h.detector.on_live_reboot = lambda prev, new: hook_calls.append((prev, new))
  h.detector.on_connected({'boot_id': 1})
  h.push_diag(boot_id=1, uptime_ms=50_000, frames_received=500)
  h.push_diag(boot_id=2, uptime_ms=500)
  await h.settle()
  assert h.kinds() == ['unexpected_reboot']
  detail = h.incidents[0][1]
  assert detail['prev_boot_id'] == 1 and detail['new_boot_id'] == 2
  assert detail['previous']['reason'] == 'boot_id_changed'
  assert detail['previous']['last_diag']['frames_received'] == 500   # old DIAG kept as evidence
  assert hook_calls == [(1, 2)]
  assert h.detector._current_boot_id == 2
  # The new boot's counters are compared against themselves, not the old boot's
  h.push_diag(boot_id=2, uptime_ms=1500)
  await h.settle()
  assert h.kinds() == ['unexpected_reboot']


@pytest.mark.asyncio
async def test_armed_expected_reboot_suppresses_and_is_consumed_once(h):
  h.detector.on_connected({'boot_id': 1})
  h.detector.on_disconnected('maintenance_reboot')
  h.detector.arm_expected_reboot(1, ttl=20)
  h.detector.on_connected({'boot_id': 2})
  await h.settle()
  assert h.incidents == []
  assert h.detector.expected_reboot is None
  h.detector.on_disconnected('disconnect')
  h.detector.on_connected({'boot_id': 3})          # second reboot is not covered
  await h.settle()
  assert h.kinds() == ['unexpected_reboot']


@pytest.mark.asyncio
async def test_expired_expected_reboot_yields_incident(h):
  h.detector.on_connected({'boot_id': 1})
  h.detector.on_disconnected('maintenance_reboot')
  h.detector.arm_expected_reboot(1, ttl=20)
  h.advance(21)
  h.detector.tick()
  assert h.detector.expected_reboot is None
  h.detector.on_connected({'boot_id': 2})
  await h.settle()
  assert h.kinds() == ['unexpected_reboot']


@pytest.mark.asyncio
async def test_disarm_unconsumed_and_wrong_boot_id(h):
  h.detector.arm_expected_reboot(1)
  h.detector.disarm_expected_reboot_if_unconsumed()
  assert h.detector.expected_reboot is None
  h.detector.on_connected({'boot_id': 1})
  h.detector.on_disconnected('x')
  h.detector.arm_expected_reboot(99)              # armed for a different previous boot
  h.detector.on_connected({'boot_id': 2})
  await h.settle()
  assert h.kinds() == ['unexpected_reboot']


@pytest.mark.asyncio
async def test_uptime_rollback_same_boot_id_is_a_reboot(h):
  h.detector.on_connected({'boot_id': 1})
  h.push_diag(boot_id=1, uptime_ms=90_000)
  h.push_diag(boot_id=1, uptime_ms=1_000)
  await h.settle()
  assert h.kinds() == ['unexpected_reboot']
  assert h.incidents[0][1]['previous']['reason'] == 'uptime_rollback'


@pytest.mark.asyncio
async def test_first_connection_and_old_firmware_do_not_report_reboot(h):
  h.detector.on_connected({'boot_id': 0})
  h.detector.on_disconnected('x')
  h.detector.on_connected({'boot_id': 0})
  h.detector.on_disconnected('x')
  h.detector.on_connected({'boot_id': 5})
  await h.settle()
  assert h.incidents == []


# --- isolation / no reset ---

@pytest.mark.asyncio
async def test_raising_callback_leaves_detector_and_episodes_alive(h):
  async def boom(kind, detail):
    raise RuntimeError('recorder down')
  h.detector.on_incident = boom
  for _ in range(3):
    h.detector.on_ping_result(False)
  await h.settle()
  assert 'stall' in h.detector.open_episodes
  assert h.transport.stalled['incident'] is None
  h.detector.on_ping_result(True)                 # the detector keeps working afterwards
  assert h.detector.open_episodes == {}
  h.detector.on_incident = lambda kind, detail: 'sync-id'   # sync callbacks are fine too
  h.detector.on_write_timeout()
  await h.settle()
  assert h.transport.stalled['incident'] == 'sync-id'


@pytest.mark.asyncio
async def test_detection_never_reboots_the_teensy():
  transport = TeensyTransport_with_fake_port()
  det = transport.detector
  with patch.object(usb, 'reboot_teensy', AsyncMock(return_value=True)) as reboot:
    for _ in range(5):
      det.on_ping_result(False)
    det.on_handshake_failed(5)
    det.on_config_result(False, timed_out=True)
    det.on_write_timeout()
    det.on_disconnected('link_lost')
    det.on_connected({'boot_id': 1, 'faulted': True})
    det.on_diag(diag(stall_latched=STALL_FAULTED, stall_active=0b111, bad_crc=5))
    det.on_disconnected('link_lost')
    det.on_connected({'boot_id': 2})
    for _ in range(3):
      det.tick()
    await asyncio.sleep(0.01)
    reboot.assert_not_awaited()
  source = inspect.getsource(detection)
  assert 'reboot_teensy' not in source and '.reboot(' not in source
  assert len(det.emitted) >= 5


def TeensyTransport_with_fake_port():
  t = usb.TeensyTransport()
  t.find_teensy_port = lambda: '/dev/fake'
  t.request_crash = AsyncMock(return_value=None)
  return t



def test_ping_success_does_not_close_config_stall(h):
  h.detector.on_config_result(False, timed_out=True)
  assert 'stall' in h.detector.open_episodes
  h.detector.on_ping_result(True)
  assert 'stall' in h.detector.open_episodes, "CONFIG stall must clear on READY, not on a PING answer"
  h.detector.on_state(LinkState.READY)
  assert 'stall' not in h.detector.open_episodes


def test_ping_success_closes_ping_stall(h):
  for _ in range(detection.PING_MISSES_TO_STALL):
    h.detector.on_ping_result(False)
  assert h.detector.open_episodes['stall'].detail['origin'] == 'ping'
  h.detector.on_ping_result(True)
  assert 'stall' not in h.detector.open_episodes


# --- Codex round 2: terminal write evidence, CAPS refresh after live reboot ---

def _appender(h):
  appends = []

  async def append(incident_id, key, value):
    appends.append((incident_id, key, value))
    return True
  h.detector.on_append = append
  return appends


@pytest.mark.asyncio
async def test_terminal_write_appends_to_link_lost_incident_once_id_is_known(h):
  appends = _appender(h)
  outcome = {'ok': True, 'ptype': 2, 'frame_id': 7, 'bytes': 40, 'written': 40}
  h.detector.on_disconnected('link_lost')
  h.detector.on_terminal_write(outcome)       # the recorder has not returned an id yet
  await h.settle()
  assert h.kinds() == ['link_lost']
  assert appends == [('inc-1', 'terminal_write', outcome)]
  # A graceful or maintenance close owes the bundle nothing
  h.detector.on_state(LinkState.READY)
  h.detector.on_disconnected('maintenance_reboot')
  h.detector.on_terminal_write({'ok': True})
  await h.settle()
  assert len(appends) == 1


@pytest.mark.asyncio
async def test_terminal_write_reaches_write_timeout_incident_in_either_order(h):
  appends = _appender(h)
  first = {'ok': False, 'error': 'write_timeout', 'ptype': 2, 'frame_id': 3, 'bytes': 40}
  # write_timeout fires inside the write: outcome arrives before the incident id
  h.detector.on_write_timeout({'error': 'x'})
  h.detector.on_disconnected('write_timeout')
  h.detector.on_terminal_write(first)
  await h.settle()
  assert appends == [('inc-1', 'terminal_write', first)]
  # ...and the other way round
  h.detector.on_state(LinkState.READY)
  second = {'ok': False, 'error': 'write_timeout', 'ptype': 2, 'frame_id': 9, 'bytes': 40}
  h.detector.on_write_timeout()
  h.detector.on_disconnected('write_timeout')
  await h.settle()
  h.detector.on_terminal_write(second)
  await h.settle()
  assert appends[-1] == ('inc-2', 'terminal_write', second)
  kinds = [e.kind for e in h.transport.trace.events]
  assert kinds.count('terminal_write') == 2


@pytest.mark.asyncio
async def test_rehandshake_after_live_reboot_moves_boot_without_second_incident(h):
  h.detector.on_connected({'boot_id': 2, 'firmware_version': '1.3.0'})
  h.push_diag(boot_id=2, uptime_ms=50_000)
  h.push_diag(boot_id=3, uptime_ms=500)
  h.detector.on_rehandshake({'boot_id': 3, 'firmware_version': '1.3.0', 'faulted': 0})
  await h.settle()
  assert h.kinds() == ['unexpected_reboot']
  assert h.detector._current_boot_id == 3 and h.detector._established
  h.push_diag(boot_id=3, uptime_ms=1500)
  await h.settle()
  assert h.kinds() == ['unexpected_reboot']        # boot 3 is now the baseline
  # A faulted CAPS on the refreshed boot still opens the fault episode
  h.detector.on_rehandshake({'boot_id': 3, 'firmware_version': '1.3.0', 'faulted': 1})
  await h.settle()
  assert h.kinds() == ['unexpected_reboot', 'fault']
