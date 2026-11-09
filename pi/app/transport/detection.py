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
    self._now = now
    self.open_episodes: dict[str, Episode] = {}
    self.emitted: deque = deque(maxlen=200)          # (kind, reason, at) for status/tests
    self.expected_reboot: Optional[ExpectedReboot] = None
    self._ping_misses = 0
    self._current_boot_id: Optional[int] = None
    self._prev_boot_id: Optional[int] = None
    self._prev_snapshot: Optional[dict] = None       # frozen evidence of the previous connection
    self._prev_diag: Optional[dict] = None           # last pushed DIAG of this connection
    self._diag_seen_this_connection = False
    self._fault_boot_ids: set[int] = set()
    self._window: Optional[dict] = None              # output_stall sampling window

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
    if boot_id and boot_id != self._current_boot_id:
      # A new boot ends a fault-idle episode from the old one
      self._close('fault')
    self._current_boot_id = boot_id
    self._prev_diag = None
    self._diag_seen_this_connection = False
    self._window = None
    if caps.get('faulted'):
      self._fault('CAPS reports fault-idle mode', boot_id, caps=caps)

  def on_disconnected(self, reason: str):
    """Called before the transport forgets caps/diag so the evidence is frozen."""
    t = self.transport
    trace = getattr(t, 'trace', None)
    self._prev_snapshot = {
      'at': time.time(), 'reason': reason, 'boot_id': self._current_boot_id,
      'caps': getattr(t, 'caps', None), 'last_diag': getattr(t, 'last_teensy_diag', None),
      'last_stats': getattr(t, 'last_teensy_stats', None),
      'trace': trace.freeze() if trace is not None else None,
    }
    if self._current_boot_id:
      self._prev_boot_id = self._current_boot_id
    self._current_boot_id = None
    self._prev_diag = None
    self._diag_seen_this_connection = False
    self._window = None
    self._ping_misses = 0
    if reason == 'link_lost' and not getattr(t, 'maintenance', False):
      self._open('link_lost', 'serial port disappeared')

  def on_handshake_failed(self, consecutive: int):
    if consecutive >= HANDSHAKE_FAILS_TO_STALL:
      self._open('stall', f'{consecutive} failed handshakes with the port present')

  def on_ping_result(self, ok: bool):
    if ok:
      self._ping_misses = 0
      self._close('stall')
      return
    self._ping_misses += 1
    if self._ping_misses >= PING_MISSES_TO_STALL:
      self._open('stall', f'{self._ping_misses} unanswered PINGs')

  def on_config_result(self, ok: bool, timed_out: bool = False):
    if ok:
      self._close('stall')
    elif timed_out:
      self._open('stall', f'CONFIG not committed within {CONFIG_TIMEOUT_S:.0f} s')

  def on_write_timeout(self, detail: Optional[dict] = None):
    self._open('write_timeout', 'serial write timed out', detail or {})

  def on_state(self, state: LinkState):
    if state == LinkState.READY:
      for kind in ('stall', 'write_timeout', 'link_lost'):
        self._close(kind)

  def on_diag(self, diag: dict):
    """Pushed DIAG (req_id 0). Solicited DIAG replies never come here."""
    boot_id = diag.get('boot_id') or self._current_boot_id or 0
    prev = self._prev_diag if self._prev_diag and self._prev_diag.get('boot_id') == boot_id else None
    now = self._now()
    self._diag_seen_this_connection = True

    if prev is not None and diag['uptime_ms'] < prev['uptime_ms']:
      # Same boot_id but time went backwards: the Teensy rebooted under us
      self._reboot_observed(boot_id, boot_id, None, live=True)
      prev = None
      self._window = None

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
                       live: bool = False):
    if self._consume_expected(prev_boot_id):
      self._trace('expected_reboot_consumed', prev_boot_id=prev_boot_id, new_boot_id=new_boot_id)
      return
    t = self.transport
    trace = getattr(t, 'trace', None)
    previous = self._prev_snapshot
    if live and trace is not None:
      # No disconnect happened: the evidence is whatever the ring holds right now
      previous = {'at': time.time(), 'reason': 'uptime_rollback', 'boot_id': prev_boot_id,
                  'caps': getattr(t, 'caps', None), 'last_diag': self._prev_diag,
                  'trace': trace.freeze()}
    detail = {
      'prev_boot_id': prev_boot_id, 'new_boot_id': new_boot_id,
      'reset_reason_text': (caps or {}).get('reset_reason_text'),
      'caps': caps, 'previous': previous,
    }
    # Point event: the link is healthy again by the time we see the new boot
    self._emit(None, 'unexpected_reboot', 'Teensy rebooted on its own', detail,
               enrich=self._fetch_crash)

  def _fault(self, reason: str, boot_id: int, **detail):
    if boot_id in self._fault_boot_ids:
      return
    self._fault_boot_ids.add(boot_id)
    self._open('fault', reason, {'boot_id': boot_id, **detail}, enrich=self._fetch_crash)

  def _eval_output_stall(self, now: float):
    t = self.transport
    diag = getattr(t, 'last_teensy_diag', None)
    eligible = (
      getattr(t, 'state', None) == LinkState.READY
      and not getattr(t, 'blackout', False)
      and getattr(t, 'test_pattern_active', None) is None
      and diag is not None and self._diag_seen_this_connection
    )
    if not eligible:
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

  def _open(self, kind: str, reason: str, detail: Optional[dict] = None, enrich=None) -> Episode:
    ep = self.open_episodes.get(kind)
    if ep is not None:
      ep.last_asserted = self._now()
      return ep
    ep = Episode(kind, reason, time.time(), dict(detail or {}), last_asserted=self._now())
    self.open_episodes[kind] = ep
    logger.warning(f"Teensy link episode opened: {kind} — {reason}")
    self._refresh_stalled()
    self._emit(ep, kind, reason, ep.detail, enrich)
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

  def _emit(self, episode: Optional[Episode], kind: str, reason: str, detail: dict, enrich=None):
    self.emitted.append((kind, reason, time.time()))
    self._trace('incident', episode=kind, reason=reason)
    if self.on_incident is None:
      return
    try:
      loop = asyncio.get_running_loop()
    except RuntimeError:
      logger.warning(f"incident {kind} not recorded: no running event loop")
      return
    payload = {'reason': reason, 'since': episode.since if episode else time.time(), **detail}
    loop.create_task(self._isolated(episode, kind, payload, enrich))

  async def _isolated(self, episode: Optional[Episode], kind: str, detail: dict, enrich):
    try:
      if enrich is not None:
        try:
          extra = await enrich()
          if extra:
            detail = {**detail, **extra}
        except Exception:
          logger.exception(f"incident {kind}: evidence enrichment failed")
      result = self.on_incident(kind, detail)
      if inspect.isawaitable(result):
        result = await result
      incident_id = _incident_id_of(result)
      if episode is not None and incident_id:
        episode.incident_id = incident_id
        self._refresh_stalled()
    except Exception:
      logger.exception(f"on_incident callback failed for {kind}")

  async def _fetch_crash(self) -> dict:
    request_crash = getattr(self.transport, 'request_crash', None)
    if request_crash is None:
      return {}
    return {'crash': await request_crash()}


def _incident_id_of(result) -> Optional[str]:
  if result is None:
    return None
  if isinstance(result, str):
    return result
  if isinstance(result, dict):
    return result.get('id')
  return getattr(result, 'id', None)
