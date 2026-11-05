"""Stall detection → remote reboot, in TeensyTransport (serial I/O faked)."""

import asyncio
from unittest.mock import AsyncMock, patch

from app.transport import usb
from app.transport.usb import TeensyTransport, HEALTH_MISSES_TO_STALL


def _run(coro):
  return asyncio.new_event_loop().run_until_complete(coro)


def _transport():
  t = TeensyTransport()
  t.connected = True
  t.find_teensy_port = lambda: '/dev/ttyACM0'
  return t


def test_unanswered_pings_trigger_one_reboot():
  t = _transport()
  t.request_stats = AsyncMock(return_value=None)
  with patch.object(usb, 'reboot_teensy', AsyncMock(return_value=True)) as reboot, \
       patch.object(usb.asyncio, 'sleep', AsyncMock()):
    for _ in range(HEALTH_MISSES_TO_STALL - 1):
      _run(t._check_health())
    reboot.assert_not_awaited()
    _run(t._check_health())
    reboot.assert_awaited_once_with('/dev/ttyACM0')
  assert not t.connected
  assert t.recoveries == 1 and t.last_recovery['ok']


def test_answered_ping_resets_misses_and_keeps_stats():
  t = _transport()
  t.request_stats = AsyncMock(side_effect=[None, None, {'uptime_ms': 5}, None, None])
  with patch.object(usb, 'reboot_teensy', AsyncMock(return_value=True)) as reboot:
    for _ in range(5):
      _run(t._check_health())
  reboot.assert_not_awaited()
  assert t.last_teensy_stats == {'uptime_ms': 5}


def test_recovery_backoff():
  t = _transport()
  with patch.object(usb, 'reboot_teensy', AsyncMock(return_value=False)) as reboot:
    _run(t._recover('first'))
    _run(t._recover('second'))
  assert reboot.await_count == 1
  assert t.last_recovery['reason'] == 'first' and not t.last_recovery['ok']


def test_attempts_capped_until_healthy_ping():
  t = _transport()
  t.request_stats = AsyncMock(return_value={'uptime_ms': 1})
  with patch.object(usb, 'reboot_teensy', AsyncMock(return_value=True)) as reboot, \
       patch.object(usb.asyncio, 'sleep', AsyncMock()):
    for _ in range(usb.MAX_RECOVERY_ATTEMPTS + 2):
      t._last_recovery_at = 0.0   # skip backoff
      _run(t._recover('stall'))
    assert reboot.await_count == usb.MAX_RECOVERY_ATTEMPTS
    _run(t._check_health())       # healthy PING rearms
    t._last_recovery_at = 0.0
    _run(t._recover('stall'))
  assert reboot.await_count == usb.MAX_RECOVERY_ATTEMPTS + 1


def test_failed_loader_retries_boot_only():
  t = _transport()
  with patch.object(usb, 'reboot_teensy', AsyncMock(return_value=False)), \
       patch.object(usb, 'boot_from_bootloader', AsyncMock(return_value=True)) as boot:
    _run(t._recover('stall'))
    assert not t._stuck_in_bootloader   # serial port still there: not in HalfKay
    t.find_teensy_port = lambda: None   # Teensy now only visible as HalfKay
    t._stuck_in_bootloader = True
    assert t._stuck_in_bootloader
    t._last_recovery_at = 0.0
    _run(t._recover('Teensy left in bootloader'))
  boot.assert_awaited_once()
  assert not t._stuck_in_bootloader


def _one_reconnect_pass(t, config_ack, timed_out):
  t.connected = False
  t.connect = AsyncMock(return_value=True)

  async def on_connect():
    t._last_config_ack = config_ack
    t._config_timed_out = timed_out
  t._on_connect_callback = on_connect
  with patch.object(usb, 'reboot_teensy', AsyncMock(return_value=True)) as reboot, \
       patch.object(usb.asyncio, 'sleep', AsyncMock(side_effect=asyncio.CancelledError)):
    _run(t.reconnect_loop())
  return reboot


def test_config_nak_does_not_reboot():
  reboot = _one_reconnect_pass(_transport(), config_ack=False, timed_out=False)
  reboot.assert_not_awaited()


def test_config_timeout_reboots():
  reboot = _one_reconnect_pass(_transport(), config_ack=False, timed_out=True)
  reboot.assert_awaited_once()
