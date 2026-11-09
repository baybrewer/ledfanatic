"""
USB Serial transport to Teensy.

Device discovery, connection state machine, serialized writes, id-routed
reads, health pings and link-episode detection. Nothing in this module ever
resets the Teensy on its own: `reboot()` exists for the maintenance module,
which only runs it on a user's request.

State machine: CLOSED → OPEN (port open, reader running) → HELLO_OK (CAPS)
→ CONFIGURED (CONFIG_ACK) → READY (brightness pinned, blackout restored).
Frames go out only in READY, and only as raw pixel bytes.
"""

import asyncio
import logging
import os
import struct
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Optional, TYPE_CHECKING

import serial
import serial.tools.list_ports

from .teensy_reset import reboot_teensy
from .trace import TransportTrace
from .dispatcher import RxDispatcher
from .detection import LinkDetector
from .link_state import LinkState
from ..models.protocol import (
  PacketType, build_packet, frame_packet,
  build_hello_payload, build_blackout_payload,
  build_config_payload, output_config_to_list,
  parse_caps_payload, parse_stats_payload, parse_diag_payload, parse_crash_payload,
  parse_config_ack_payload,
)

if TYPE_CHECKING:
  from ..layout import CompiledLayout

logger = logging.getLogger(__name__)

TEENSY_VID = 0x16C0
TEENSY_PID = 0x0483

HEALTH_INTERVAL_S = 5.0
RX_POLL_S = 0.01            # reader cadence; bounds request latency and DIAG freshness
FRAME_WRITE_TIMEOUT_S = 1.0
CONFIG_TIMEOUT_S = 3.0
CONFIG_ATTEMPTS = 3          # a lost ACK is retried, never answered with a reset
CONFIG_RETRY_DELAY_S = 1.0
BRING_UP_RETRY_S = 5.0       # connected but not READY for this long → re-run CONFIG + restoration
WRITER_STOP_TIMEOUT_S = 5.0  # > pyserial write_timeout, so a stuck write is still recorded


class LinkError(Exception):
  """A serial write failed or timed out; the connection is being torn down."""


@dataclass
class WriteJob:
  framed: bytes
  ptype: int
  req_id: int
  future: asyncio.Future
  deadline: float = 0.0
  enqueued_at: float = field(default_factory=time.monotonic)


_STOP = object()   # queue sentinel: wakes an idle writer task so it can exit


def _close_quietly(port):
  try:
    if port is not None and port.is_open:
      port.close()
  except Exception:
    pass


class SerialWriter:
  """The only code that calls serial.write.

  One persistent task drains a queue of WriteJobs so writes are strictly
  ordered and a caller's timeout/cancellation can never interleave with, or
  abandon, an in-flight write. Futures are resolved only when not already done
  (a timed-out caller's future is cancelled) — an unguarded set_result would
  raise InvalidStateError and kill the task. A job that raises never ends the
  task; it logs and moves on.

  Shutdown is graceful: stop() fails the jobs that have not started, then lets
  the task finish the write already handed to the thread (bounded by pyserial's
  write_timeout), trace its outcome and only then exit and close the port it
  was asked to. start() awaits the previous task, so two writers never overlap
  a port lifetime.
  """

  def __init__(self, trace: TransportTrace, on_link_error=None, on_terminal_write=None):
    self.trace = trace
    self.on_link_error = on_link_error     # (reason: str, exc) -> None, sync
    # (outcome: dict) -> None, sync: the write that was in flight when stop()
    # was called, once it has returned. A teardown incident is emitted before
    # that write finishes, so this is how its outcome still reaches the bundle.
    self.on_terminal_write = on_terminal_write
    self.serial: Optional[serial.Serial] = None
    self._queue: asyncio.Queue = asyncio.Queue()
    self._task: Optional[asyncio.Task] = None
    self._stopping = False
    self._close_port: Optional[serial.Serial] = None   # closed by the exiting task
    self.jobs_written = 0
    self.jobs_skipped = 0

  @property
  def running(self) -> bool:
    return self._task is not None and not self._task.done() and not self._stopping

  async def start(self, port: serial.Serial):
    await self.wait_stopped()
    self.serial = port
    self._stopping = False
    self._close_port = None
    # A fresh queue per task: a stop sentinel left behind must not stop this one
    self._queue = asyncio.Queue()
    self._task = asyncio.get_running_loop().create_task(self._run(self._queue), name='serial-writer')

  def stop(self, reason: str = 'writer stopped', close_port: bool = False) -> Optional[asyncio.Task]:
    """Stop accepting jobs. Returns the task to await (see wait_stopped)."""
    port = self.serial
    self.serial = None
    self._stopping = True
    self._fail_pending(reason)
    task = self._task
    if task is not None and not task.done():
      if close_port:
        self._close_port = port   # the port stays open until the in-flight write returns
      self._queue.put_nowait(_STOP)
      return task
    if close_port:
      _close_quietly(port)
    return None

  async def wait_stopped(self, timeout: float = WRITER_STOP_TIMEOUT_S):
    """Await the current task's exit. Cancels it only past the timeout, which is
    longer than any serial write can take, so cancellation is a true last resort."""
    task = self._task
    if task is None or task.done():
      return
    done, _ = await asyncio.wait({task}, timeout=timeout)
    if not done:
      logger.error(f"serial writer did not stop within {timeout:.1f}s; cancelling")
      task.cancel()
      await asyncio.wait({task})

  def _fail_pending(self, reason: str):
    while not self._queue.empty():
      job = self._queue.get_nowait()
      if job is _STOP:
        continue
      if not job.future.done():
        job.future.set_exception(LinkError(reason))

  async def write(self, framed: bytes, ptype: int, req_id: int = 0, timeout: float = 1.0) -> int:
    """Queue a write and wait for it. Returns bytes written.

    Raises LinkError when the write failed, asyncio.TimeoutError when the
    caller's deadline passed first (the write itself still completes or is
    skipped — see _run).
    """
    if self.serial is None or not self.running:
      raise LinkError('port not open')
    loop = asyncio.get_running_loop()
    job = WriteJob(framed, ptype, req_id, loop.create_future(), loop.time() + timeout)
    self._queue.put_nowait(job)
    return await asyncio.wait_for(job.future, timeout)

  async def _run(self, queue: asyncio.Queue):
    try:
      while True:
        job = await queue.get()
        if job is _STOP:
          return
        if job.future.done():
          # Caller gave up before we started: skip, never write stale data
          self.jobs_skipped += 1
          self.trace.event('write_skipped', ptype=int(job.ptype), req_id=job.req_id)
          continue
        port = self.serial
        if port is None:
          if not job.future.done():
            job.future.set_exception(LinkError('port not open'))
          continue
        await self._write_job(port, job)
    finally:
      # Runs after the in-flight write returned (or on last-resort cancel), so
      # the port is never closed under a write the trace has not seen
      self.trace.event('writer_stopped', jobs_written=self.jobs_written)
      _close_quietly(self._close_port)
      self._close_port = None

  async def _write_job(self, port, job: WriteJob):
    t0 = time.perf_counter()
    outcome = None
    try:
      written = await asyncio.to_thread(port.write, job.framed)
      write_ms = (time.perf_counter() - t0) * 1000
      self.trace.tx(job.ptype, job.req_id, len(job.framed), written or 0, write_ms, True,
                    job.framed)
      self.jobs_written += 1
      outcome = self._outcome(job, written or 0, write_ms, None)
      if not job.future.done():
        job.future.set_result(written)
    except serial.SerialTimeoutException as e:
      write_ms = (time.perf_counter() - t0) * 1000
      self.trace.tx(job.ptype, job.req_id, len(job.framed), 0, write_ms, False, job.framed,
                    error='write_timeout')
      self.trace.event('write_timeout', ptype=int(job.ptype), write_ms=round(write_ms, 1))
      outcome = self._outcome(job, 0, write_ms, 'write_timeout')
      self._link_error('write_timeout', e)
      if not job.future.done():
        job.future.set_exception(LinkError('serial write timed out'))
    except asyncio.CancelledError:
      write_ms = (time.perf_counter() - t0) * 1000
      self.trace.tx(job.ptype, job.req_id, len(job.framed), 0, write_ms, False, job.framed,
                    error='cancelled_in_flight')
      if not job.future.done():
        job.future.set_exception(LinkError('writer stopped'))
      self._terminal(self._outcome(job, 0, write_ms, 'cancelled_in_flight'))
      raise
    except Exception as e:
      write_ms = (time.perf_counter() - t0) * 1000
      self.trace.tx(job.ptype, job.req_id, len(job.framed), 0, write_ms, False, job.framed,
                    error=repr(e))
      outcome = self._outcome(job, 0, write_ms, repr(e))
      if isinstance(e, (serial.SerialException, OSError)):
        self._link_error('write_error', e)
      else:
        logger.exception("serial writer: unexpected error in job")
      if not job.future.done():
        job.future.set_exception(LinkError(f'serial write failed: {e}'))
    self._terminal(outcome)

  @staticmethod
  def _outcome(job: WriteJob, written: int, write_ms: float, error: Optional[str]) -> dict:
    return {'at': time.time(), 'ok': error is None, 'error': error, 'ptype': int(job.ptype),
            'frame_id': job.req_id, 'bytes': len(job.framed), 'written': written,
            'write_ms': round(write_ms, 1),
            # The frozen snapshots predate this write; without the bytes the
            # teardown's last frame could never be reconstructed from the bundle.
            'framed_hex': job.framed.hex()}

  def _terminal(self, outcome: Optional[dict]):
    """stop() was called while this write was in flight: the write is the last
    one this port will ever carry, and the teardown's incident wants to know how
    it ended. Not a terminal write when the writer is still running."""
    if outcome is None or not self._stopping or self.on_terminal_write is None:
      return
    try:
      self.on_terminal_write(outcome)
    except Exception:
      logger.exception("on_terminal_write handler failed")

  def _link_error(self, reason: str, exc: Exception):
    if self.on_link_error is None:
      return
    try:
      self.on_link_error(reason, exc)
    except Exception:
      logger.exception("on_link_error handler failed")


class TeensyTransport:
  def __init__(self, reconnect_interval: float = 1.0, handshake_timeout: float = 3.0,
               on_incident=None):
    self.reconnect_interval = reconnect_interval
    self.handshake_timeout = handshake_timeout
    self.serial: Optional[serial.Serial] = None
    self.state = LinkState.CLOSED
    self.caps: Optional[dict] = None
    self.geometry_generation: Optional[int] = None
    self.frame_id = 0
    self._on_connect_callback = None   # async callable, set by main.py
    # Connection generation: bumped on every port open and every live reboot.
    # Restoration flags and CONFIG transactions carry the generation they were
    # issued under and are ignored when it moved, so a write that completes
    # while the port is draining can never pin the *next* connection, and a
    # CONFIG ACK from a previous boot can never promote the new one.
    self._conn_gen = 0
    self._brightness_pinned = False
    self._blackout_restored = False
    # One CONFIG transaction at a time: READY can then only ever describe the
    # latest committed geometry
    self._config_lock = asyncio.Lock()
    self._bring_up_task: Optional[asyncio.Task] = None
    self._last_bring_up = 0.0

    self.trace = TransportTrace()
    self._pots_queue: deque = deque(maxlen=64)
    self.dispatcher = RxDispatcher(self.trace, on_pots=self._on_pots, on_diag=self._on_pushed_diag)
    self.writer = SerialWriter(self.trace, on_link_error=self._on_link_error,
                               on_terminal_write=self._on_terminal_write)
    self.detector = LinkDetector(self, on_incident)
    self.detector.on_live_reboot = self._on_live_reboot
    self._rx_task: Optional[asyncio.Task] = None

    # Pi-owned output state the detector needs (blackout/test suppress output_stall)
    self.blackout = False
    self.test_pattern_active: Optional[int] = None

    # Maintenance (manual reset) pauses the reconnect loop; set by TeensyMaintenance
    self.maintenance = False
    self.stalled: Optional[dict] = None   # open episode summary, written by the detector

    # Stats
    self.frames_sent = 0
    self.send_errors = 0
    self.reconnect_count = 0
    self.last_teensy_stats: Optional[dict] = None
    self._handshake_fails = 0

  # --- properties ---

  @property
  def connected(self) -> bool:
    return self.state.at_least(LinkState.HELLO_OK)

  @property
  def _ready_pinned(self) -> bool:
    return self._brightness_pinned and self._blackout_restored

  @property
  def boot_id(self) -> Optional[int]:
    return self.caps.get('boot_id') if self.caps else None

  @property
  def last_teensy_diag(self) -> Optional[dict]:
    return self.dispatcher.last_teensy_diag

  @property
  def last_diag_at(self) -> Optional[float]:
    return self.dispatcher.last_diag_at

  # --- discovery / connection ---

  def find_teensy_port(self) -> Optional[str]:
    for port in serial.tools.list_ports.comports():
      if port.vid == TEENSY_VID and port.pid == TEENSY_PID:
        return port.device
      if port.manufacturer and 'teensy' in port.manufacturer.lower():
        return port.device
    return None

  def _set_state(self, state: LinkState):
    if state == self.state:
      return
    logger.info(f"Teensy link {self.state.value} → {state.value}")
    self.trace.event('state', frm=self.state.value, to=state.value)
    self.state = state
    self._detect(self.detector.on_state, state)

  def _detect(self, hook, *args):
    """Detection must never take the link down with it."""
    try:
      hook(*args)
    except Exception:
      logger.exception(f"link detector hook {getattr(hook, '__name__', hook)} failed")

  async def connect(self) -> bool:
    # The previous writer may still be finishing its last write on the old
    # port: a new port is never opened while that write can still complete
    await self.writer.wait_stopped()
    port = self.find_teensy_port()
    if not port:
      logger.debug("Teensy not found")
      return False
    try:
      self.serial = serial.Serial(port, baudrate=115200, timeout=0.1, write_timeout=1.0)
    except (serial.SerialException, OSError) as e:
      logger.error(f"Connection failed: {e}")
      self.serial = None
      return False

    logger.info(f"Connected to Teensy on {port}")
    self._conn_gen += 1
    self._reset_connection_state()
    self.dispatcher.attach(self.serial)
    await self.writer.start(self.serial)
    if self.serial is None:
      return False   # disconnected while the previous writer was draining
    self._rx_task = asyncio.get_running_loop().create_task(self._rx_loop(), name='serial-reader')
    self._set_state(LinkState.OPEN)

    caps = await self._handshake()
    if caps is None:
      logger.warning("Handshake failed")
      self.disconnect('handshake_failed')
      return False
    self.caps = caps
    logger.info(f"Teensy caps: {caps}")
    self._set_state(LinkState.HELLO_OK)
    self._detect(self.detector.on_connected, caps)
    return True

  def _reset_connection_state(self):
    """Everything the firmware forgets on a new boot, forgotten here too."""
    self._brightness_pinned = False
    self._blackout_restored = False
    self.test_pattern_active = None
    self.geometry_generation = None

  def disconnect(self, reason: str = 'disconnect'):
    if self.state != LinkState.CLOSED or self.serial is not None:
      self._detect(self.detector.on_disconnected, reason)
    if self._rx_task is not None:
      self._rx_task.cancel()
      self._rx_task = None
    if self._bring_up_task is not None and not self._bring_up_task.done():
      self._bring_up_task.cancel()
    self._bring_up_task = None
    port = self.serial
    self.serial = None
    # The writer closes the port itself once its in-flight write has returned;
    # a port it never took is closed here
    writer_owns_port = port is not None and self.writer.serial is port
    self.writer.stop(reason, close_port=writer_owns_port)
    self.dispatcher.detach(reason)
    if not writer_owns_port:
      _close_quietly(port)
    self.caps = None
    self._reset_connection_state()
    self._set_state(LinkState.CLOSED)

  async def disconnect_and_drain(self, reason: str = 'disconnect'):
    """disconnect(), then wait until the writer has finished its in-flight
    write and closed the port. Anything that will touch the device next (the
    134-baud reboot request, a USB power-cycle, process exit) must call this
    rather than disconnect(): two port lifetimes must never overlap, and the
    write being preserved must not be interrupted by them."""
    rx_task = self._rx_task
    self.disconnect(reason)
    await self.writer.wait_stopped()
    if rx_task is not None:
      await asyncio.gather(rx_task, return_exceptions=True)

  async def _handshake(self) -> Optional[dict]:
    payload = build_hello_payload("ledfanatic-pi", "1.0.0")
    try:
      reply = await self.request(PacketType.HELLO, payload, timeout=self.handshake_timeout)
    except LinkError as e:
      logger.error(f"Failed to send HELLO: {e}")
      return None
    if reply is None:
      return None
    header, caps_payload = reply
    if header.packet_type != PacketType.CAPS:
      return None
    return parse_caps_payload(caps_payload)

  async def _rx_loop(self):
    """Single reader: drains the port and lets the dispatcher route packets."""
    while True:
      try:
        self.dispatcher.drain()
      except asyncio.CancelledError:
        raise
      except (serial.SerialException, OSError) as e:
        logger.error(f"Serial read failed: {e}")
        self._on_link_error('read_error', e)
        return
      except Exception:
        logger.exception("rx loop: unexpected error")
      await asyncio.sleep(RX_POLL_S)

  def _on_link_error(self, reason: str, exc: Exception):
    """Writer/reader reported the port is gone or wedged: tear down, detect."""
    if reason == 'write_timeout':
      self._detect(self.detector.on_write_timeout, {'error': repr(exc)})
      self.disconnect('write_timeout')
    else:
      self.disconnect('link_lost')

  def _on_terminal_write(self, outcome: dict):
    self._detect(self.detector.on_terminal_write, outcome)

  def _on_pots(self, header, payload):
    self._pots_queue.append((header, payload))

  def _on_pushed_diag(self, diag: dict):
    self._detect(self.detector.on_diag, diag)

  # --- requests ---

  async def request(self, ptype: int, payload: bytes = b'', timeout: float = 0.5,
                    raise_on_timeout: bool = False):
    """Id-correlated request/response. (header, payload) | None. Raises LinkError."""
    if not self.state.at_least(LinkState.OPEN):
      return None
    return await self.dispatcher.request(self.writer, ptype, payload, timeout,
                                         raise_on_timeout=raise_on_timeout)

  async def request_stats(self) -> Optional[dict]:
    """PING → STATS. Keeps last_teensy_stats for status/incident bundles."""
    if not self.connected:
      return None
    try:
      reply = await self.request(PacketType.PING, timeout=0.5)
    except LinkError as e:
      logger.error(f"Stats request failed: {e}")
      return None
    if reply is None:
      return None
    parsed = parse_stats_payload(reply[1])
    if parsed is None:
      return {'error': 'malformed_stats', 'payload_len': len(reply[1])}
    self.last_teensy_stats = parsed
    return parsed

  async def request_diag(self, timeout: float = 0.5) -> Optional[dict]:
    if not self.connected:
      return None
    try:
      reply = await self.request(PacketType.DIAG, timeout=timeout)
    except LinkError:
      return None
    return parse_diag_payload(reply[1]) if reply else None

  async def request_crash(self, timeout: float = 0.5) -> Optional[dict]:
    if not self.connected:
      return None
    try:
      reply = await self.request(PacketType.CRASH, timeout=timeout)
    except LinkError:
      return None
    return parse_crash_payload(reply[1]) if reply else None

  # --- commands ---

  async def send_frame(self, pixel_data: bytes) -> bool:
    """Send a FRAME (raw packed pixel bytes from pack_frame). Only in READY."""
    if self.state != LinkState.READY:
      return False
    gen = self._conn_gen
    self.frame_id += 1
    timestamp_us = int(time.monotonic() * 1_000_000) & 0xFFFFFFFFFFFFFFFF
    framed = frame_packet(build_packet(
      PacketType.FRAME, pixel_data, frame_id=self.frame_id, timestamp_us=timestamp_us,
    ))
    try:
      await self.writer.write(framed, PacketType.FRAME, self.frame_id, FRAME_WRITE_TIMEOUT_S)
    except LinkError as e:
      self.send_errors += 1
      logger.error(f"Frame send failed: {e}")
      return False
    except asyncio.TimeoutError:
      self.send_errors += 1
      self.trace.event('frame_write_wait_timeout', frame_id=self.frame_id)
      return False
    self.frames_sent += 1
    if self._same_connection(gen):
      self.test_pattern_active = None    # firmware drops its test pattern on an accepted FRAME
    return True

  async def send_command(self, packet_type: int, payload: bytes = b'') -> bool:
    """Fire-and-forget command on the current connection. True when the write
    completed *and* the connection it was issued on is still the live one; a
    write that lands while the port is draining is true on the wire but must
    not count as state restored on whatever connection comes next."""
    if not self.connected:
      return False
    gen = self._conn_gen
    framed = frame_packet(build_packet(packet_type, payload))
    try:
      await self.writer.write(framed, packet_type, 0, timeout=1.0)
    except (LinkError, asyncio.TimeoutError) as e:
      logger.error(f"Command send failed: {e}")
      return False
    return self._same_connection(gen)

  async def send_blackout(self, enabled: bool) -> bool:
    """Send explicit blackout command: True=on, False=off."""
    self.blackout = bool(enabled)
    ok = await self.send_command(PacketType.BLACKOUT, build_blackout_payload(enabled))
    if ok:
      self._blackout_restored = True
      self.test_pattern_active = None  # firmware drops its test pattern on BLACKOUT (either value)
    return ok

  async def send_brightness(self, brightness: float) -> bool:
    val = max(0, min(255, int(brightness * 255)))
    ok = await self.send_command(PacketType.BRIGHTNESS, struct.pack('<B', val))
    if ok:
      self._brightness_pinned = True
    return ok

  async def send_test_pattern(self, pattern_id: int) -> bool:
    ok = await self.send_command(PacketType.TEST_PATTERN, struct.pack('<B', pattern_id))
    if ok:
      self.test_pattern_active = None if pattern_id == 0xFF else pattern_id
    return ok

  async def apply_geometry(self, layout_provider: Callable[[], Optional[tuple['CompiledLayout', list[int]]]],
                           commit: Callable[['CompiledLayout'], None], *,
                           commit_offline: bool = False,
                           timeout: float = CONFIG_TIMEOUT_S,
                           attempts: Optional[int] = None,
                           retry_delay: Optional[float] = None) -> bool:
    """The one geometry transaction: select layout → CONFIG → commit → promote.

    Everything happens under the CONFIG lock, and the layout is read via
    `layout_provider()` only *after* the lock is held, so a bring-up queued
    behind a layout change sends the geometry that change committed, never one
    it captured earlier. `commit(compiled)` runs on ACK, before any state
    promotion: the renderer and the firmware therefore agree on the frame size
    before a single frame is allowed out. A commit that raises leaves the link
    in HELLO_OK (no frames) and the reconnect loop re-runs this transaction.

    Returns True only on CONFIG ACK. With `commit_offline`, a disconnected
    link still runs commit (there is no firmware geometry to disagree with and
    the next bring-up CONFIGs whatever is current) but still returns False.
    """
    async with self._config_lock:
      selected = layout_provider()
      if selected is None:
        logger.warning("No compiled layout available for CONFIG")
        return False
      compiled, config_list = selected
      if not self.connected:
        if commit_offline:
          commit(compiled)
        return False
      return await self._config_transaction(list(config_list), timeout, attempts, retry_delay,
                                            commit=lambda: commit(compiled))

  async def send_config(self, output_config, timeout: float = CONFIG_TIMEOUT_S,
                        attempts: Optional[int] = None,
                        retry_delay: Optional[float] = None) -> bool:
    """Low-level CONFIG primitive: send and wait for ACK/NAK, True on ACK.
    Production code goes through apply_geometry(), which also commits the
    renderer under the same lock; this exists for tests and tooling that only
    need the firmware side."""
    if not self.connected:
      return False
    config_list = output_config if isinstance(output_config, list) \
      else output_config_to_list(output_config)
    async with self._config_lock:
      return await self._config_transaction(config_list, timeout, attempts, retry_delay)

  async def _config_transaction(self, config_list: list[int], timeout: float,
                                attempts: Optional[int], retry_delay: Optional[float],
                                commit: Optional[Callable[[], None]] = None) -> bool:
    """CONFIG → ACK/NAK under the already-held lock.

    While the ACK is pending the link drops to HELLO_OK so no frame can reach a
    geometry the firmware has not committed. A lost ACK is retried (bounded)
    and the CONFIG stall episode stays open until READY; nothing here ever
    resets the Teensy. The transaction is bound to the connection generation it
    started under: a reply arriving after a reconnect or live reboot is
    discarded. ACK restores READY if brightness and blackout were already
    restored on this connection (layout change), else CONFIGURED (the bring-up
    finishes with mark_ready()).
    """
    if not self.connected:
      return False
    attempts = attempts or CONFIG_ATTEMPTS
    retry_delay = CONFIG_RETRY_DELAY_S if retry_delay is None else retry_delay
    gen = self._conn_gen
    was_ready = self.state == LinkState.READY
    self._set_state(LinkState.HELLO_OK)
    payload = build_config_payload(config_list)
    reply = None
    for attempt in range(1, attempts + 1):
      try:
        reply = await self.request(PacketType.CONFIG, payload, timeout, raise_on_timeout=True)
        break
      except LinkError as e:
        logger.error(f"Failed to send CONFIG: {e}")
        self._detect(self.detector.on_config_result, False, False)
        return False
      except asyncio.TimeoutError:
        logger.warning(f"CONFIG: no response (attempt {attempt}/{attempts})")
        if not self._same_connection(gen):
          return False
        self._detect(self.detector.on_config_result, False, True)
        if attempt == attempts:
          return False   # episode stays open; reconnect_loop re-runs the bring-up later
        await asyncio.sleep(retry_delay)
        if not self._same_connection(gen):
          return False
    if not self._same_connection(gen):
      return False   # link dropped or the Teensy rebooted while waiting
    if reply is None:
      logger.warning("CONFIG rejected (NAK)")
      if was_ready:
        self._set_state(LinkState.READY)   # old geometry is still committed and pinned
      self._detect(self.detector.on_config_result, False, False)
      return False
    geometry_generation = parse_config_ack_payload(reply[1])
    if commit is not None:
      try:
        commit()
      except Exception:
        # Firmware holds the new geometry but the renderer may not: stay in
        # HELLO_OK (no frames) so the loop re-runs the transaction
        logger.exception("layout commit failed after CONFIG ACK; link held below READY")
        return False
    self.geometry_generation = geometry_generation
    logger.info(f"CONFIG acknowledged (active pins: {config_list}, "
                f"geometry_generation: {self.geometry_generation})")
    # The stall episode closes on READY (detector.on_state), not on the ACK:
    # a committed geometry with unrestored brightness/blackout is not healthy yet
    self._set_state(LinkState.READY if self._ready_pinned else LinkState.CONFIGURED)
    return True

  def _same_connection(self, gen: int) -> bool:
    return self.connected and self._conn_gen == gen

  def _same_session(self, gen: int) -> bool:
    """Same port and boot, handshake not necessarily done (OPEN counts)."""
    return self.state.at_least(LinkState.OPEN) and self._conn_gen == gen

  def mark_ready(self) -> bool:
    """Connect sequence finished. READY only when this connection holds a
    committed CONFIG and both restoration writes succeeded on it; pins nothing
    itself, so a call outliving its connection cannot leak into the next one."""
    if self.state == LinkState.CONFIGURED and self._ready_pinned:
      self._set_state(LinkState.READY)
    return self.state == LinkState.READY

  async def wait_for(self, state: LinkState, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
      if self.state.at_least(state):
        return True
      await asyncio.sleep(0.05)
    return self.state.at_least(state)

  async def drain_incoming(self) -> list:
    """POTS packets received since the last call (the reader task owns the port;
    this only hands over what the dispatcher queued)."""
    packets = list(self._pots_queue)
    self._pots_queue.clear()
    return packets

  # --- maintenance (user-initiated only; see core/teensy_maintenance.py) ---

  async def reboot(self) -> bool:
    """Reboot the Teensy via the 134-baud bootloader request. Never called by
    detection; only TeensyMaintenance on an explicit user action."""
    port = (self.serial.port if self.serial else None) or self.find_teensy_port()
    # The bootloader request opens the same device at 134 baud: our port must
    # be fully closed (in-flight write returned) before that happens
    await self.disconnect_and_drain('maintenance_reboot')
    if not port:
      logger.error("Teensy reboot: no serial port")
      return False
    return await reboot_teensy(port)

  # --- background loop ---

  def _on_live_reboot(self, prev_boot_id: int, new_boot_id: int):
    """A pushed DIAG showed a new boot without a USB disconnect: the Teensy has
    forgotten CONFIG/brightness/blackout and our CAPS (boot_id, firmware,
    fault flag) describe a boot that no longer exists. Bump the generation so
    every in-flight CONFIG/restoration from the old boot is discarded, drop to
    OPEN (no commands) and bring the link up again from HELLO. Detection owns
    the incident; this only restores the link."""
    logger.warning(f"Teensy rebooted in place (boot {prev_boot_id} -> {new_boot_id}); "
                   f"renegotiating from HELLO")
    self._conn_gen += 1
    self._reset_connection_state()
    if self.state.at_least(LinkState.OPEN):
      self._set_state(LinkState.OPEN)
    if self._bring_up_task is not None and not self._bring_up_task.done():
      self._bring_up_task.cancel()
    self._bring_up_task = None
    self._start_bring_up(renegotiate=True)

  def _start_bring_up(self, renegotiate: bool = False):
    """Run the connect sequence (CONFIG → brightness → blackout → mark_ready)
    as an owned task so the loop keeps ticking the detector meanwhile. With
    `renegotiate` the sequence starts at HELLO (live reboot)."""
    if self._on_connect_callback is None and not renegotiate:
      return
    if self._bring_up_task is not None and not self._bring_up_task.done():
      return
    self._last_bring_up = time.monotonic()
    self._bring_up_task = asyncio.get_running_loop().create_task(
      self._run_bring_up(renegotiate), name='teensy-bring-up')

  async def _run_bring_up(self, renegotiate: bool = False):
    try:
      if renegotiate and not await self._renegotiate():
        return
      if self._on_connect_callback is not None:
        await self._on_connect_callback()
    except asyncio.CancelledError:
      raise
    except Exception as e:
      logger.error(f"on_connect callback failed: {e}", exc_info=True)

  async def _renegotiate(self) -> bool:
    """HELLO → CAPS on the open port after a live reboot. Refreshes caps,
    boot_id and firmware facts and tells the detector, so status, incident
    metadata and the next manual reset describe the boot that is actually
    running. Failure tears the connection down; the loop reconnects normally."""
    gen = self._conn_gen
    caps = await self._handshake()
    if not self._same_session(gen):
      return False   # port dropped or another reboot happened meanwhile
    if caps is None:
      logger.warning("Handshake after live reboot failed")
      self.disconnect('handshake_failed')
      return False
    self.caps = caps
    logger.info(f"Teensy caps after live reboot: {caps}")
    self._set_state(LinkState.HELLO_OK)
    self._detect(self.detector.on_rehandshake, caps)
    return True

  def _bring_up_due(self, now: float) -> bool:
    """Connected but below READY (lost CONFIG ACK, failed restoration): retry the
    whole sequence instead of sitting in HELLO_OK forever. Skipped while a
    CONFIG transaction is in flight (layout change) or a bring-up is running."""
    return (
      self.connected and self.state != LinkState.READY
      and not self._config_lock.locked()
      and (self._bring_up_task is None or self._bring_up_task.done())
      and now - self._last_bring_up >= BRING_UP_RETRY_S
    )

  async def reconnect_loop(self):
    """Keep the link up; health-ping; tick the detector. Never resets the Teensy."""
    last_health = time.monotonic()
    while True:
      try:
        if self.maintenance:
          await asyncio.sleep(self.reconnect_interval)
          continue
        if self.state == LinkState.CLOSED:
          if await self.connect():
            self._handshake_fails = 0
            self.reconnect_count += 1
            logger.info(f"Reconnected (attempt #{self.reconnect_count})")
            self._start_bring_up()
            last_health = time.monotonic()
          elif self.find_teensy_port():
            self._handshake_fails += 1
            self._detect(self.detector.on_handshake_failed, self._handshake_fails)
        elif self.serial is not None and not os.path.exists(self.serial.port):
          logger.warning(f"Serial port {self.serial.port} disappeared")
          self.disconnect('link_lost')
        else:
          now = time.monotonic()
          if self._bring_up_due(now):
            logger.warning(f"Teensy link stuck in {self.state.value}; re-running CONFIG + restoration")
            self._start_bring_up()
          if now - last_health >= HEALTH_INTERVAL_S:
            last_health = now
            await self._check_health()
        self._detect(self.detector.tick)
        await asyncio.sleep(self.reconnect_interval)
      except asyncio.CancelledError:
        break
      except Exception:
        logger.exception("reconnect_loop: unexpected error")
        await asyncio.sleep(self.reconnect_interval)

  async def _check_health(self):
    stats = await self.request_stats()
    ok = bool(stats) and 'error' not in stats
    self._detect(self.detector.on_ping_result, ok)

  def get_status(self) -> dict:
    diag_at = self.last_diag_at
    return {
      'connected': self.connected,
      'state': self.state.value,
      'port': self.serial.port if self.serial else None,
      'caps': self.caps,
      'firmware_version': self.caps.get('firmware_version') if self.caps else None,
      'boot_id': self.boot_id,
      'geometry_generation': self.geometry_generation,
      'frames_sent': self.frames_sent,
      'send_errors': self.send_errors,
      'reconnect_count': self.reconnect_count,
      'stalled': self.stalled,
      'maintenance': self.maintenance,
      'last_diag_age_s': round(time.monotonic() - diag_at, 1) if diag_at is not None else None,
    }
