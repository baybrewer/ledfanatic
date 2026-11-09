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
from typing import Optional

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

logger = logging.getLogger(__name__)

TEENSY_VID = 0x16C0
TEENSY_PID = 0x0483

HEALTH_INTERVAL_S = 5.0
RX_POLL_S = 0.01            # reader cadence; bounds request latency and DIAG freshness
FRAME_WRITE_TIMEOUT_S = 1.0
CONFIG_TIMEOUT_S = 3.0


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


class SerialWriter:
  """The only code that calls serial.write.

  One persistent task drains a queue of WriteJobs so writes are strictly
  ordered and a caller's timeout/cancellation can never interleave with, or
  abandon, an in-flight write. Futures are resolved only when not already done
  (a timed-out caller's future is cancelled) — an unguarded set_result would
  raise InvalidStateError and kill the task. A job that raises never ends the
  task; it logs and moves on.
  """

  def __init__(self, trace: TransportTrace, on_link_error=None):
    self.trace = trace
    self.on_link_error = on_link_error     # (reason: str, exc) -> None, sync
    self.serial: Optional[serial.Serial] = None
    self._queue: asyncio.Queue[WriteJob] = asyncio.Queue()
    self._task: Optional[asyncio.Task] = None
    self.jobs_written = 0
    self.jobs_skipped = 0

  @property
  def running(self) -> bool:
    return self._task is not None and not self._task.done()

  def start(self, port: serial.Serial):
    self.serial = port
    if not self.running:
      self._task = asyncio.get_running_loop().create_task(self._run(), name='serial-writer')

  def stop(self, reason: str = 'writer stopped'):
    self.serial = None
    if self._task is not None:
      self._task.cancel()
      self._task = None
    self._fail_pending(reason)

  def _fail_pending(self, reason: str):
    while not self._queue.empty():
      job = self._queue.get_nowait()
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

  async def _run(self):
    while True:
      job = await self._queue.get()
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
      t0 = time.perf_counter()
      try:
        written = await asyncio.to_thread(port.write, job.framed)
        write_ms = (time.perf_counter() - t0) * 1000
        self.trace.tx(job.ptype, job.req_id, len(job.framed), written or 0, write_ms, True,
                      job.framed)
        self.jobs_written += 1
        if not job.future.done():
          job.future.set_result(written)
      except serial.SerialTimeoutException as e:
        write_ms = (time.perf_counter() - t0) * 1000
        self.trace.tx(job.ptype, job.req_id, len(job.framed), 0, write_ms, False, job.framed,
                      error='write_timeout')
        self.trace.event('write_timeout', ptype=int(job.ptype), write_ms=round(write_ms, 1))
        self._link_error('write_timeout', e)
        if not job.future.done():
          job.future.set_exception(LinkError('serial write timed out'))
      except asyncio.CancelledError:
        if not job.future.done():
          job.future.set_exception(LinkError('writer stopped'))
        raise
      except Exception as e:
        write_ms = (time.perf_counter() - t0) * 1000
        self.trace.tx(job.ptype, job.req_id, len(job.framed), 0, write_ms, False, job.framed,
                      error=repr(e))
        if isinstance(e, (serial.SerialException, OSError)):
          self._link_error('write_error', e)
        else:
          logger.exception("serial writer: unexpected error in job")
        if not job.future.done():
          job.future.set_exception(LinkError(f'serial write failed: {e}'))

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
    self._ready_pinned = False         # brightness/blackout applied on this connection

    self.trace = TransportTrace()
    self._pots_queue: deque = deque(maxlen=64)
    self.dispatcher = RxDispatcher(self.trace, on_pots=self._on_pots, on_diag=self._on_pushed_diag)
    self.writer = SerialWriter(self.trace, on_link_error=self._on_link_error)
    self.detector = LinkDetector(self, on_incident)
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
    self.dispatcher.attach(self.serial)
    self.writer.start(self.serial)
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

  def disconnect(self, reason: str = 'disconnect'):
    if self.state != LinkState.CLOSED or self.serial is not None:
      self._detect(self.detector.on_disconnected, reason)
    if self._rx_task is not None:
      self._rx_task.cancel()
      self._rx_task = None
    self.writer.stop(reason)
    self.dispatcher.detach(reason)
    if self.serial is not None:
      try:
        if self.serial.is_open:
          self.serial.close()
      except Exception:
        pass
    self.serial = None
    self.caps = None
    self.geometry_generation = None
    self._ready_pinned = False
    self._set_state(LinkState.CLOSED)

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
    return True

  async def send_command(self, packet_type: int, payload: bytes = b'') -> bool:
    if not self.connected:
      return False
    framed = frame_packet(build_packet(packet_type, payload))
    try:
      await self.writer.write(framed, packet_type, 0, timeout=1.0)
      return True
    except (LinkError, asyncio.TimeoutError) as e:
      logger.error(f"Command send failed: {e}")
      return False

  async def send_blackout(self, enabled: bool) -> bool:
    """Send explicit blackout command: True=on, False=off."""
    self.blackout = bool(enabled)
    return await self.send_command(PacketType.BLACKOUT, build_blackout_payload(enabled))

  async def send_brightness(self, brightness: float) -> bool:
    val = max(0, min(255, int(brightness * 255)))
    return await self.send_command(PacketType.BRIGHTNESS, struct.pack('<B', val))

  async def send_test_pattern(self, pattern_id: int) -> bool:
    self.test_pattern_active = None if pattern_id == 0xFF else pattern_id
    return await self.send_command(PacketType.TEST_PATTERN, struct.pack('<B', pattern_id))

  async def send_config(self, output_config, timeout: float = CONFIG_TIMEOUT_S) -> bool:
    """Send CONFIG and wait for ACK/NAK. True on ACK.

    While the ACK is pending the link drops to HELLO_OK so no frame can reach a
    geometry the firmware has not committed; ACK restores READY if brightness
    and blackout were already pinned on this connection (layout change), else
    CONFIGURED (fresh connection, caller finishes with mark_ready()).
    """
    if not self.connected:
      return False
    config_list = output_config if isinstance(output_config, list) \
      else output_config_to_list(output_config)
    was_ready = self.state == LinkState.READY
    self._set_state(LinkState.HELLO_OK)
    try:
      reply = await self.request(PacketType.CONFIG, build_config_payload(config_list), timeout,
                                 raise_on_timeout=True)
    except LinkError as e:
      logger.error(f"Failed to send CONFIG: {e}")
      self._detect(self.detector.on_config_result, False, False)
      return False
    except asyncio.TimeoutError:
      logger.warning("CONFIG: no response (timeout)")
      if self.connected:
        self._detect(self.detector.on_config_result, False, True)
      return False
    if not self.connected:
      return False   # link dropped while waiting
    if reply is None:
      logger.warning("CONFIG rejected (NAK)")
      if was_ready:
        self._set_state(LinkState.READY)   # old geometry is still committed and pinned
      self._detect(self.detector.on_config_result, False, False)
      return False
    self.geometry_generation = parse_config_ack_payload(reply[1])
    logger.info(f"CONFIG acknowledged (active pins: {config_list}, "
                f"geometry_generation: {self.geometry_generation})")
    self._set_state(LinkState.READY if (was_ready or self._ready_pinned) else LinkState.CONFIGURED)
    self._detect(self.detector.on_config_result, True, False)
    return True

  def mark_ready(self):
    """Connect sequence finished (CONFIG ACK, brightness pinned, blackout restored)."""
    self._ready_pinned = True
    if self.state == LinkState.CONFIGURED:
      self._set_state(LinkState.READY)

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
    self.disconnect('maintenance_reboot')
    if not port:
      logger.error("Teensy reboot: no serial port")
      return False
    return await reboot_teensy(port)

  # --- background loop ---

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
            if self._on_connect_callback:
              try:
                await self._on_connect_callback()
              except Exception as e:
                logger.error(f"on_connect callback failed: {e}", exc_info=True)
            last_health = time.monotonic()
          elif self.find_teensy_port():
            self._handshake_fails += 1
            self._detect(self.detector.on_handshake_failed, self._handshake_fails)
        elif self.serial is not None and not os.path.exists(self.serial.port):
          logger.warning(f"Serial port {self.serial.port} disappeared")
          self.disconnect('link_lost')
        elif time.monotonic() - last_health >= HEALTH_INTERVAL_S:
          last_health = time.monotonic()
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
