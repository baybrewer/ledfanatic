"""
User-initiated Teensy recovery: manual reset and optional USB power-cycle.

Nothing here runs on its own — every entry point is an explicit API call.
Single-flight is enforced with an owned task rather than a lock: a lock would
*queue* a second request and run two resets back to back, while a running
task lets the second caller fail fast with BusyError. The HTTP awaiter is
shielded so a client disconnect never abandons a half-done reset; the
terminal outcome is appended to the incident bundle on every exit path.
"""

import asyncio
import logging
import os
import shutil
from typing import Optional

from ..transport.link_state import LinkState
from ..transport.teensy_reset import kill_and_reap

logger = logging.getLogger(__name__)

RESET_RECONNECT_TIMEOUT_S = 10.0
EXPECTED_REBOOT_TTL_S = 20.0
POWER_CYCLE_RECONNECT_TIMEOUT_S = 15.0
POWER_CYCLE_CMD_TIMEOUT_S = 30.0
# Pi 4: port 1-1 is the whole downstream hub — every USB device loses power
UHUBCTL_ARGS = ['-l', '1-1', '-a', 'cycle', '-d', '3']


class BusyError(Exception):
  """A maintenance operation is already running."""

  def __init__(self, incident: Optional[str]):
    super().__init__(f'maintenance already running (incident {incident})')
    self.incident = incident


class PowerCycleUnavailable(Exception):
  """uhubctl is not installed on this host."""


class TeensyMaintenance:
  def __init__(self, transport, incidents, detector, uhubctl_path: Optional[str] = None):
    self.transport = transport
    self.incidents = incidents
    self.detector = detector
    self._uhubctl = uhubctl_path if uhubctl_path is not None else _find_uhubctl()
    self._task: Optional[asyncio.Task] = None
    self._current_incident: Optional[str] = None

  @property
  def busy(self) -> bool:
    return self._task is not None and not self._task.done()

  @property
  def current_incident(self) -> Optional[str]:
    return self._current_incident if self.busy else None

  @property
  def usb_power_cycle_available(self) -> bool:
    return bool(self._uhubctl)

  # --- entry points ---

  async def reset(self, reason: str = 'manual') -> dict:
    return await self._start(self._run_reset(reason))

  async def usb_power_cycle(self, reason: str = 'manual') -> dict:
    if not self.usb_power_cycle_available:
      raise PowerCycleUnavailable('uhubctl not installed')
    return await self._start(self._run_power_cycle(reason))

  async def _start(self, coro) -> dict:
    if self.busy:
      coro.close()
      raise BusyError(self._current_incident)
    self._current_incident = None
    self._task = asyncio.get_running_loop().create_task(coro, name='teensy-maintenance')
    # A cancelled HTTP awaiter cancels only this await, never the operation
    return await asyncio.shield(self._task)

  # --- operations ---

  async def _run_reset(self, reason: str) -> dict:
    t = self.transport
    prev_boot = t.boot_id
    # Proceeds even if the capture failed: the user asked for recovery, the
    # error is reported in the result instead
    cap = await self.incidents.capture('manual_reset', reason)
    self._current_incident = cap.id
    self.detector.arm_expected_reboot(prev_boot, ttl=EXPECTED_REBOOT_TTL_S)
    t.maintenance = True
    post = {'rebooted': False, 'reconnected': False, 'error': None}
    try:
      post['rebooted'] = bool(await t.reboot())
      # The reconnect loop sleeps while maintenance is set; release it before
      # waiting or READY could never arrive
      t.maintenance = False
      post['reconnected'] = await t.wait_for(LinkState.READY, timeout=RESET_RECONNECT_TIMEOUT_S)
    except asyncio.CancelledError:
      post['error'] = 'cancelled'
      raise
    except Exception as e:
      post['error'] = repr(e)
    finally:
      await self._finish(cap, post)
    return {'incident': cap.id, **post}

  async def _run_power_cycle(self, reason: str) -> dict:
    t = self.transport
    prev_boot = t.boot_id
    cap = await self.incidents.capture('usb_power_cycle', reason)
    self._current_incident = cap.id
    # Power-off + enumeration takes longer than a bootloader reboot
    self.detector.arm_expected_reboot(prev_boot, ttl=POWER_CYCLE_RECONNECT_TIMEOUT_S + 5)
    t.maintenance = True
    post = {'cycled': False, 'reconnected': False, 'error': None, 'output': None}
    try:
      t.disconnect('maintenance_power_cycle')
      rc, output = await _run_uhubctl(self._uhubctl)
      post['output'] = output
      post['cycled'] = rc == 0
      t.maintenance = False
      post['reconnected'] = await t.wait_for(LinkState.READY,
                                             timeout=POWER_CYCLE_RECONNECT_TIMEOUT_S)
    except asyncio.CancelledError:
      post['error'] = 'cancelled'
      raise
    except Exception as e:
      post['error'] = repr(e)
    finally:
      await self._finish(cap, post)
    return {'incident': cap.id, **post}

  async def _finish(self, cap, post: dict):
    """Every exit path: release the link, record the outcome. Never raises."""
    t = self.transport
    t.maintenance = False
    try:
      self.detector.disarm_expected_reboot_if_unconsumed()
    except Exception:
      logger.exception("maintenance: disarm_expected_reboot failed")
    try:
      post.update(await self._post_state())
    except Exception as e:
      logger.exception("maintenance: post-reset probe failed")
      post.setdefault('new_boot_id', getattr(t, 'boot_id', None))
      post.setdefault('reset_reason_text', None)
      post.setdefault('crash', {'error': repr(e)})
    post['capture_error'] = cap.error
    try:
      appended = await self.incidents.append(cap.id, 'post_reset', post)
      if not appended:
        logger.error(f"maintenance: post_reset not persisted for incident {cap.id}")
    except Exception:
      logger.exception(f"maintenance: append post_reset failed for incident {cap.id}")

  async def _post_state(self) -> dict:
    t = self.transport
    diag = await t.request_diag() if getattr(t, 'request_diag', None) else None
    caps = getattr(t, 'caps', None) or {}
    reset_reason_text = (diag or {}).get('reset_reason_text') or caps.get('reset_reason_text')
    crash = await t.request_crash() if getattr(t, 'request_crash', None) else None
    return {
      'new_boot_id': t.boot_id,
      'reset_reason_text': reset_reason_text,
      'crash': crash,
    }


def _find_uhubctl() -> Optional[str]:
  # The service user's PATH may omit /usr/sbin, where Debian installs it (and
  # where the sudoers line points)
  found = shutil.which('uhubctl')
  if found:
    return found
  debian_path = '/usr/sbin/uhubctl'
  return debian_path if os.path.exists(debian_path) else None


async def _run_uhubctl(uhubctl_path: str) -> tuple[Optional[int], str]:
  """`sudo uhubctl ...` — sudoers grants exactly this binary (setup.sh)."""
  proc = await asyncio.create_subprocess_exec(
    'sudo', uhubctl_path, *UHUBCTL_ARGS,
    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
  )
  try:
    output, _ = await asyncio.wait_for(proc.communicate(), POWER_CYCLE_CMD_TIMEOUT_S)
  except asyncio.TimeoutError:
    await kill_and_reap(proc)
    return None, 'uhubctl timed out'
  except BaseException:
    # A cancelled operation must not leave uhubctl cutting hub power on its own
    await kill_and_reap(proc)
    raise
  return proc.returncode, output.decode(errors='replace')[-2000:]
