"""
Link detection: turns transport events and pushed DIAG into *episodes*.

Detection and evidence only — nothing here ever resets the Teensy. An episode
opens on the transition into a bad state (one incident), stays open while the
condition re-asserts (no new incident), closes when its clearing condition
holds, and a later re-assertion opens a fresh episode (new incident).

The on_incident callback (Task 3's IncidentRecorder.capture_event) runs in an
isolated task: a raising or slow recorder can never take the reconnect loop or
the reader down with it.
"""

import asyncio
import inspect
import logging
import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from ..models.protocol import STALL_FAULTED, decode_stall_bits
from .link_state import LinkState

logger = logging.getLogger(__name__)

PING_MISSES_TO_STALL = 3        # PINGs are every 5 s
HANDSHAKE_FAILS_TO_STALL = 2
CONFIG_TIMEOUT_S = 3.0
OUTPUT_WINDOW_S = 10.0
OUTPUT_MIN_PI_FRAMES = 100
DIAG_STALE_S = 3.0
FLAG_QUIET_S = 60.0
EXPECTED_REBOOT_TTL_S = 20.0

# DIAG counters whose growth means the link is corrupting or dropping data
FLAG_COUNTERS = ('oversize', 'bad_short', 'bad_magic', 'bad_version', 'bad_len', 'bad_crc',
                 'bad_framing', 'tx_dropped')


@dataclass
class Episode:
  kind: str
  reason: str
  since: float                       # time.time()
  detail: dict = field(default_factory=dict)
  incident_id: Optional[str] = None
  last_asserted: float = 0.0         # detector clock
  flag_bits: int = 0                 # teensy_flag: union of stall_active bits seen
  terminal_write: Optional[dict] = None   # outcome waiting for incident_id before it is appended

  def to_dict(self) -> dict:
    return {'kind': self.kind, 'reason': self.reason, 'since': self.since,
            'incident': self.incident_id}


@dataclass
class ExpectedReboot:
  prev_boot_id: int
  expires_at: float                  # detector clock
  consumed: bool = False


class LinkDetector:
  def __init__(self, transport, on_incident: Optional[Callable[..., Awaitable]] = None,
               now: Callable[[], float] = time.monotonic):
    self.transport = transport
    self.on_incident = on_incident
    # (incident_id, key, value) -> Awaitable[bool]: IncidentRecorder.append.
    # Evidence that only exists after the incident was emitted (the terminal
    # outcome of the write in flight at teardown) is added through this.
    self.on_append: Optional[Callable[..., Awaitable]] = None
    self._now = now
    self.open_episodes: dict[str, Episode] = {}
    self.emitted: deque = deque(maxlen=200)          # (kind, reason, at) for status/tests
    self.expected_reboot: Optional[ExpectedReboot] = None
    self._ping_misses = 0
    self._current_boot_id: Optional[int] = None
    self._prev_boot_id: Optional[int] = None
    self._prev_snapshot: Optional[dict] = None       # frozen evidence of the last *established* connection
    self._failed_attempts: deque = deque(maxlen=5)   # handshake failures since then, kept apart
    self._established = False                        # CAPS seen on the current port session
    self._prev_diag: Optional[dict] = None           # last pushed DIAG of this connection
    self._diag_seen_this_connection = False
    self._pushes_diag = False                        # firmware >= 1.3.0 pushes DIAG every second
    self._ready_since: Optional[float] = None        # detector clock; None when not READY
    self._fault_boot_ids: set[int] = set()
    self._window: Optional[dict] = None              # output_stall sampling window
    # The teardown episode whose incident still owes the in-flight write's outcome
    self._teardown_episode: Optional[Episode] = None
    # Optional transport hook: a boot change seen in pushed DIAG (no disconnect
    # happened) means the Teensy lost its CONFIG and needs renegotiation
    self.on_live_reboot: Optional[Callable[[int, int], None]] = None

  # --- transport hooks ---

  def on_connected(self, caps: dict):
    """CAPS received on a (re)connection: reboot detection + fault flag."""
    boot_id = caps.get('boot_id') or 0
    prev_uptime = ((self._prev_snapshot or {}).get('last_diag') or {}).get('uptime_ms')
    rebooted = False
    if self._prev_boot_id:
      if boot_id and boot_id != self._prev_boot_id:
        rebooted = True
      elif prev_uptime is not None and caps.get('uptime_ms', 0) < prev_uptime and boot_id == self._prev_boot_id:
        rebooted = True   # boot_id generator failed to change; uptime rollback is the fallback
    if rebooted:
      self._reboot_observed(self._prev_boot_id, boot_id, caps)
    self._close_fault_if_new_boot(boot_id)
    self._current_boot_id = boot_id
    self._established = True
    self._failed_attempts.clear()
    self._prev_diag = None
    self._diag_seen_this_connection = False
    self._pushes_diag = _pushes_diag(caps)
    self._window = None
    if caps.get('faulted'):
      self._fault('CAPS reports fault-idle mode', boot_id, caps=caps)

  def on_rehandshake(self, caps: dict):
    """CAPS refreshed on the open port after a live reboot. The reboot itself
    was already observed (and reported) from pushed DIAG, so this only moves
    the boot baseline and firmware facts; running reboot detection again here
    would emit the same incident twice."""
    boot_id = caps.get('boot_id') or 0
    self._close_fault_if_new_boot(boot_id)
    if boot_id:
      self._current_boot_id = boot_id
    self._established = True
    self._pushes_diag = _pushes_diag(caps)
    self._window = None
    if caps.get('faulted'):
      self._fault('CAPS reports fault-idle mode', boot_id, caps=caps)

  def on_disconnected(self, reason: str):
    """Called before the transport forgets caps/diag so the evidence is frozen."""
    t = self.transport
    trace = getattr(t, 'trace', None)
    snapshot = {
      'at': time.time(), 'reason': reason, 'boot_id': self._current_boot_id,
      'caps': getattr(t, 'caps', None), 'last_diag': getattr(t, 'last_teensy_diag', None),
      'last_stats': getattr(t, 'last_teensy_stats', None),
      'trace': trace.freeze() if trace is not None else None,
    }
    if self._established:
      self._prev_snapshot = snapshot
    else:
      # A failed handshake has no CAPS/DIAG: it must not erase the reboot
      # baseline from the last connection that actually worked
      self._failed_attempts.append(snapshot)
    if self._current_boot_id:
      self._prev_boot_id = self._current_boot_id
    self._established = False
    self._current_boot_id = None
    self._prev_diag = None
    self._diag_seen_this_connection = False
    self._pushes_diag = False
    self._ready_since = None
    self._window = None
    self._ping_misses = 0
    if reason == 'link_lost' and not getattr(t, 'maintenance', False):
      self._teardown_episode = self._open('link_lost', 'serial port disappeared')
    elif reason != 'write_timeout':
      # on_write_timeout already claimed this teardown; any other reason is a
      # graceful or maintenance close whose last write is not incident evidence
      self._teardown_episode = None

  def on_handshake_failed(self, consecutive: int):
    if consecutive >= HANDSHAKE_FAILS_TO_STALL:
      self._open('stall', f'{consecutive} failed handshakes with the port present',
                 {'origin': 'handshake'})

  def on_ping_result(self, ok: bool):
    if ok:
      self._ping_misses = 0
      # A PING answer only proves the control path; a CONFIG/handshake stall is
      # cleared by reaching READY (on_state), not by this.
      ep = self.open_episodes.get('stall')
      if ep is not None and ep.detail.get('origin') == 'ping':
        self._close('stall')
      return
    self._ping_misses += 1
    if self._ping_misses >= PING_MISSES_TO_STALL:
      self._open('stall', f'{self._ping_misses} unanswered PINGs', {'origin': 'ping'})

  def on_config_result(self, ok: bool, timed_out: bool = False):
    # Success is not a close: the episode ends when the link reaches READY.
    if not ok and timed_out:
      self._open('stall', f'CONFIG not committed within {CONFIG_TIMEOUT_S:.0f} s',
                 {'origin': 'config'})

  def on_write_timeout(self, detail: Optional[dict] = None):
    self._teardown_episode = self._open('write_timeout', 'serial write timed out', detail or {})

  def on_terminal_write(self, outcome: dict):
    """The writer finished the write that was in flight when the link went
    down. The incident for that teardown was emitted (and its evidence frozen)
    before this write returned, so its outcome is appended to the same bundle
    rather than lost; the immediate failure snapshot is left untouched."""
    self._trace('terminal_write', **outcome)
    ep, self._teardown_episode = self._teardown_episode, None
    if ep is None:
      return
    ep.terminal_write = outcome
    self._flush_terminal_write(ep)

  def _flush_terminal_write(self, ep: Episode):
    # Either side may arrive first: the outcome (write_timeout fires inside the
    # write itself) or the incident id (persisted in a thread). Append once both exist.
    if ep.terminal_write is None or not ep.incident_id or self.on_append is None:
      return
    outcome, ep.terminal_write = ep.terminal_write, None
    try:
      loop = asyncio.get_running_loop()
    except RuntimeError:
      logger.warning(f"terminal write for incident {ep.incident_id} not recorded: no running event loop")
      return
    loop.create_task(self._isolated_append(ep.incident_id, 'terminal_write', outcome))

  def on_state(self, state: LinkState):
    if state == LinkState.READY:
      if self._ready_since is None:
        self._ready_since = self._now()
      for kind in ('stall', 'write_timeout', 'link_lost'):
        self._close(kind)
    else:
      self._ready_since = None

  def on_diag(self, diag: dict):
    """Pushed DIAG (req_id 0). Solicited DIAG replies never come here."""
    boot_id = diag.get('boot_id') or self._current_boot_id or 0
    prev = self._prev_diag
    now = self._now()
    self._diag_seen_this_connection = True

    old_boot = (prev or {}).get('boot_id') or self._current_boot_id or 0
    if boot_id and old_boot and boot_id != old_boot:
      # The boot id moved without a disconnect: a reboot happened under us.
      # Decide this BEFORE dropping prev, or the old DIAG is lost as evidence
      self._reboot_observed(old_boot, boot_id, None, live=True, reason='boot_id_changed')
      self._close_fault_if_new_boot(boot_id)
      self._current_boot_id = boot_id
      prev = None
      self._window = None
      self._notify_live_reboot(old_boot, boot_id)
    elif prev is not None and diag['uptime_ms'] < prev['uptime_ms']:
      # Same boot_id but time went backwards: the Teensy rebooted under us
      self._reboot_observed(boot_id, boot_id, None, live=True)
      prev = None
      self._window = None
      self._notify_live_reboot(boot_id, boot_id)

    # teensy_flag: active bits gained or corruption counters grown
    prev_active = prev['stall_active'] if prev else 0
    gained = diag['stall_active'] & ~prev_active & ~STALL_FAULTED
    grew = [k for k in FLAG_COUNTERS if prev is not None and diag.get(k, 0) > prev.get(k, 0)]
    if gained or grew:
      reason = ' '.join(decode_stall_bits(gained) + [f'{k}+' for k in grew])
      ep = self._open('teensy_flag', f'Teensy flags: {reason}', {'diag': diag})
      ep.flag_bits |= gained
      ep.last_asserted = now
    ep = self.open_episodes.get('teensy_flag')
    if ep and (diag['stall_active'] & ep.flag_bits) == 0 and now - ep.last_asserted >= FLAG_QUIET_S:
      self._close('teensy_flag')

    # fault: independent of blackout/test mode (fault-idle keeps PING/DIAG healthy)
    if diag['stall_latched'] & STALL_FAULTED:
      self._fault('DIAG stall_latched FAULTED', boot_id, diag=diag)
    elif prev is not None and diag.get('fault_count', 0) > prev.get('fault_count', 0):
      self._fault('DIAG fault_count grew', boot_id, diag=diag)

    self._prev_diag = diag

  def tick(self):
    """Periodic evaluation (every ~1 s from the reconnect loop)."""
    now = self._now()
    er = self.expected_reboot
    if er and not er.consumed and now > er.expires_at:
      self.expected_reboot = None
      self._trace('expected_reboot_expired', prev_boot_id=er.prev_boot_id)
    self._eval_output_stall(now)
    ep = self.open_episodes.get('teensy_flag')
    if ep and now - ep.last_asserted >= FLAG_QUIET_S:
      last = self._prev_diag or {}
      if (last.get('stall_active', 0) & ep.flag_bits) == 0:
        self._close('teensy_flag')

  # --- expected reboot (armed by TeensyMaintenance only) ---

  def arm_expected_reboot(self, prev_boot_id: Optional[int], ttl: float = EXPECTED_REBOOT_TTL_S):
    self.expected_reboot = ExpectedReboot(prev_boot_id or 0, self._now() + ttl)

  def disarm_expected_reboot_if_unconsumed(self):
    if self.expected_reboot and not self.expected_reboot.consumed:
      self.expected_reboot = None

  def _consume_expected(self, prev_boot_id: int) -> bool:
    er = self.expected_reboot
    if er is None or er.consumed or er.prev_boot_id != prev_boot_id:
      return False
    if self._now() > er.expires_at:
      self.expected_reboot = None
      return False
    er.consumed = True
    self.expected_reboot = None
    return True

  # --- detectors ---

  def _reboot_observed(self, prev_boot_id: int, new_boot_id: int, caps: Optional[dict],
                       live: bool = False, reason: str = 'uptime_rollback'):
    if self._consume_expected(prev_boot_id):
      self._trace('expected_reboot_consumed', prev_boot_id=prev_boot_id, new_boot_id=new_boot_id)
      return
    t = self.transport
    trace = getattr(t, 'trace', None)
    previous = self._prev_snapshot
    if live:
      # No disconnect happened: the evidence is whatever the ring holds right now
      previous = {'at': time.time(), 'reason': reason, 'boot_id': prev_boot_id,
                  'caps': getattr(t, 'caps', None), 'last_diag': self._prev_diag,
                  'trace': trace.freeze() if trace is not None else None}
    detail = {
      'prev_boot_id': prev_boot_id, 'new_boot_id': new_boot_id,
      'reset_reason_text': (caps or {}).get('reset_reason_text'),
      'caps': caps, 'previous': previous,
      'failed_attempts': list(self._failed_attempts),
    }
    # Point event: the link is healthy again by the time we see the new boot
    self._emit(None, 'unexpected_reboot', 'Teensy rebooted on its own', detail)

  def _notify_live_reboot(self, prev_boot_id: int, new_boot_id: int):
    self._trace('live_reboot', prev_boot_id=prev_boot_id, new_boot_id=new_boot_id)
    if self.on_live_reboot is None:
      return
    try:
      self.on_live_reboot(prev_boot_id, new_boot_id)
    except Exception:
      logger.exception("on_live_reboot hook failed")

  def _fault(self, reason: str, boot_id: int, **detail):
    if boot_id in self._fault_boot_ids:
      return
    self._fault_boot_ids.add(boot_id)
    self._open('fault', reason, {'boot_id': boot_id, **detail})

  def _close_fault_if_new_boot(self, boot_id: int):
    """Fault-idle persists until the Teensy reboots, so only a *different* boot
    id ends the episode — a reconnect to the same faulted boot keeps it open."""
    ep = self.open_episodes.get('fault')
    if ep is not None and boot_id and boot_id != ep.detail.get('boot_id'):
      self._close('fault')

  def _eval_output_stall(self, now: float):
    t = self.transport
    diag = getattr(t, 'last_teensy_diag', None)
    eligible = (
      getattr(t, 'state', None) == LinkState.READY
      and not getattr(t, 'blackout', False)
      and getattr(t, 'test_pattern_active', None) is None
    )
    if not eligible:
      self._window = None
      return
    if not self._diag_seen_this_connection:
      # 1.3.0+ pushes DIAG within a second of READY; silence past the stale
      # limit is a stall even while PINGs still answer. 1.2.0 never pushes
      # DIAG (_pushes_diag False), so it is exempt here by design.
      ready_for = now - self._ready_since if self._ready_since is not None else 0.0
      if self._pushes_diag and ready_for > DIAG_STALE_S:
        self._open('output_stall', f'no pushed DIAG within {ready_for:.0f} s of READY')
      self._window = None
      return
    if diag is None:
      self._window = None
      return
    last_at = getattr(t, 'last_diag_at', None)
    if last_at is not None and now - last_at > DIAG_STALE_S:
      self._open('output_stall', f'no pushed DIAG for {now - last_at:.0f} s while READY')
      self._window = None
      return
    boot_id = diag.get('boot_id', 0)
    w = self._window
    sample = {'start': now, 'boot_id': boot_id, 'pi': getattr(t, 'frames_sent', 0),
              'rx': diag['frames_received'], 'applied': diag['frames_applied']}
    if w is None or w['boot_id'] != boot_id:
      self._window = sample
      return
    if now - w['start'] < OUTPUT_WINDOW_S:
      return
    pi_delta = sample['pi'] - w['pi']
    rx_delta = sample['rx'] - w['rx']
    applied_delta = sample['applied'] - w['applied']
    if pi_delta >= OUTPUT_MIN_PI_FRAMES and (rx_delta == 0 or applied_delta == 0):
      self._open('output_stall',
                 f'{pi_delta} frames written, Teensy received +{rx_delta} applied +{applied_delta}',
                 {'pi_delta': pi_delta, 'rx_delta': rx_delta, 'applied_delta': applied_delta})
    elif rx_delta > 0 and applied_delta > 0:
      self._close('output_stall')
    self._window = sample

  # --- episodes ---

  def _open(self, kind: str, reason: str, detail: Optional[dict] = None) -> Episode:
    ep = self.open_episodes.get(kind)
    if ep is not None:
      ep.last_asserted = self._now()
      return ep
    ep = Episode(kind, reason, time.time(), dict(detail or {}), last_asserted=self._now())
    self.open_episodes[kind] = ep
    logger.warning(f"Teensy link episode opened: {kind} — {reason}")
    self._refresh_stalled()
    self._emit(ep, kind, reason, ep.detail)
    return ep

  def _close(self, kind: str):
    ep = self.open_episodes.pop(kind, None)
    if ep is None:
      return
    logger.info(f"Teensy link healthy again: {kind} cleared (incident {ep.incident_id})")
    self._trace('episode_closed', episode=kind, incident=ep.incident_id)
    self._refresh_stalled()

  def _refresh_stalled(self):
    oldest = min(self.open_episodes.values(), key=lambda e: e.since, default=None)
    try:
      self.transport.stalled = oldest.to_dict() if oldest else None
    except AttributeError:
      pass

  def _trace(self, event_kind: str, **detail):
    trace = getattr(self.transport, 'trace', None)
    if trace is not None:
      trace.event(event_kind, **detail)

  # --- incident emission (isolated) ---

  def _emit(self, episode: Optional[Episode], kind: str, reason: str, detail: dict):
    self.emitted.append((kind, reason, time.time()))
    # Freeze NOW, in the hook that saw the failure: the recorder runs a task
    # later and by then rx traffic (or its own probes) may have evicted the
    # bytes that explain this incident. CRASH is probed by the recorder after
    # its own freeze, never here.
    evidence = self._freeze_evidence()
    self._trace('incident', episode=kind, reason=reason)
    if self.on_incident is None:
      return
    try:
      loop = asyncio.get_running_loop()
    except RuntimeError:
      logger.warning(f"incident {kind} not recorded: no running event loop")
      return
    payload = {'reason': reason, 'since': episode.since if episode else time.time(),
               **detail, 'evidence': evidence}
    loop.create_task(self._isolated(episode, kind, payload))

  def _freeze_evidence(self) -> dict:
    t = self.transport
    trace = getattr(t, 'trace', None)
    get_status = getattr(t, 'get_status', None)
    evidence = {'at': time.time(), 'trace': None, 'status': None,
                'last_diag': getattr(t, 'last_teensy_diag', None),
                'last_stats': getattr(t, 'last_teensy_stats', None)}
    try:
      evidence['trace'] = trace.freeze() if trace is not None else None
      evidence['status'] = get_status() if callable(get_status) else None
    except Exception as e:
      evidence['error'] = repr(e)
    return evidence

  async def _isolated(self, episode: Optional[Episode], kind: str, detail: dict):
    try:
      result = self.on_incident(kind, detail)
      if inspect.isawaitable(result):
        result = await result
      incident_id = _incident_id_of(result)
      if episode is not None and incident_id:
        episode.incident_id = incident_id
        self._refresh_stalled()
        self._flush_terminal_write(episode)
    except Exception:
      logger.exception(f"on_incident callback failed for {kind}")

  async def _isolated_append(self, incident_id: str, key: str, value: dict):
    try:
      result = self.on_append(incident_id, key, value)
      if inspect.isawaitable(result):
        result = await result
      if result is False:
        logger.error(f"incident {incident_id}: {key} not persisted")
    except Exception:
      logger.exception(f"on_append failed for incident {incident_id} ({key})")


def _pushes_diag(caps: dict) -> bool:
  """Firmware 1.3.0 introduced the pushed DIAG stream; 1.2.0 (and anything
  unparsable) is treated as silent so it is never blamed for not pushing."""
  return _parse_version(caps.get('firmware_version')) >= (1, 3, 0)


def _parse_version(text) -> tuple:
  # Tolerates "v1.3.0" / "1.3.0-rc1"; anything without digits compares as 0
  numbers = re.findall(r'\d+', str(text or ''))[:3]
  return tuple(int(n) for n in numbers) if numbers else (0,)


def _incident_id_of(result) -> Optional[str]:
  if result is None:
    return None
  if isinstance(result, str):
    return result
  if isinstance(result, dict):
    return result.get('id')
  return getattr(result, 'id', None)
