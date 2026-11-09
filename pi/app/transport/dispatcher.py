"""
RX dispatcher: the only reader of the Teensy serial port once it is open.

Every inbound packet is routed by header.frame_id (the request id the Pi put
on the matching request): a nonzero id resolves the waiting Future; id 0 is
unsolicited (POTS, pushed DIAG). Concurrent requests therefore never steal
each other's replies, and a reply that arrives after its caller gave up is
traced instead of being delivered to the wrong caller.
"""

import asyncio
import logging
import time
from typing import Callable, Optional

import serial

from ..models.protocol import (
  PacketType, PacketHeader, build_packet, frame_packet, verify_packet, cobs_decode,
  parse_diag_payload,
)

logger = logging.getLogger(__name__)

# Responses old firmware (< 1.3.0) sends with frame_id 0; see _route_legacy_reply
_RESPONSE_TYPES = {
  PacketType.CAPS, PacketType.CONFIG_ACK, PacketType.CONFIG_NAK,
  PacketType.STATS, PacketType.DIAG, PacketType.CRASH,
}
_REQUEST_RESPONSES = {
  PacketType.HELLO: {PacketType.CAPS},
  PacketType.CONFIG: {PacketType.CONFIG_ACK, PacketType.CONFIG_NAK},
  PacketType.PING: {PacketType.STATS},
  PacketType.DIAG: {PacketType.DIAG},
  PacketType.CRASH: {PacketType.CRASH},
}
MAX_RX_BUFFER = 64 * 1024   # a stream with no delimiter must not grow forever


class RxDispatcher:
  def __init__(self, trace, on_pots: Optional[Callable] = None,
               on_diag: Optional[Callable] = None):
    self.trace = trace
    self.on_pots = on_pots          # (header, payload) -> None
    self.on_diag = on_diag          # (diag_dict) -> None, pushed DIAG only
    self.serial: Optional[serial.Serial] = None
    self.waiters: dict[int, asyncio.Future] = {}
    self._waiter_types: dict[int, set] = {}
    self._rx_buffer = bytearray()
    self._next_req_id = 0
    self.last_teensy_diag: Optional[dict] = None
    self.last_diag_at: Optional[float] = None    # time.monotonic()
    self.packets_in = 0

  # --- lifecycle ---

  def attach(self, port: serial.Serial):
    self.serial = port
    self._rx_buffer.clear()
    self.last_teensy_diag = None
    self.last_diag_at = None

  def detach(self, reason: str = 'detached'):
    """Fail every pending waiter so no request hangs across a reconnect."""
    self.serial = None
    self._rx_buffer.clear()
    for req_id, fut in list(self.waiters.items()):
      if not fut.done():
        fut.set_exception(ConnectionError(reason))
    self.waiters.clear()
    self._waiter_types.clear()

  def next_id(self) -> int:
    # Nonzero and monotonic; wraps inside 32 bits because the header field is uint32
    self._next_req_id = (self._next_req_id % 0xFFFFFFFF) + 1
    return self._next_req_id

  # --- reading ---

  def drain(self) -> int:
    """Read everything buffered on the port and route it. Returns packets routed.

    Raises serial.SerialException / OSError when the port is gone so the
    owner can tear the connection down; never raises on bad packets.
    """
    if self.serial is None:
      return 0
    available = self.serial.in_waiting
    if available:
      self._rx_buffer.extend(self.serial.read(available))
    if len(self._rx_buffer) > MAX_RX_BUFFER:
      self.trace.rejected('rx_overflow', bytes(self._rx_buffer[:64]))
      self._rx_buffer.clear()
      return 0

    routed = 0
    while True:
      idx = self._rx_buffer.find(b'\x00')
      if idx < 0:
        break
      encoded = bytes(self._rx_buffer[:idx])
      del self._rx_buffer[:idx + 1]
      if not encoded:
        continue
      decoded = cobs_decode(encoded)
      if decoded is None:
        self.trace.rejected('cobs', encoded)
        continue
      result = verify_packet(decoded)
      if result is None:
        self.trace.rejected('verify', decoded)
        continue
      self._route(result[0], result[1], decoded)
      routed += 1
    return routed

  def _route(self, header: PacketHeader, payload: bytes, raw: bytes):
    self.packets_in += 1
    ptype = header.packet_type
    req_id = header.frame_id
    self.trace.rx(ptype, req_id, raw)

    if ptype == PacketType.POTS:
      if self.on_pots:
        self.on_pots(header, payload)
      return

    if req_id != 0:
      fut = self.waiters.pop(req_id, None)
      self._waiter_types.pop(req_id, None)
      if fut is None or fut.done():
        # Caller already timed out: evidence, not delivery
        self.trace.event('late_reply', ptype=int(ptype), req_id=req_id)
        return
      fut.set_result(None if ptype == PacketType.CONFIG_NAK else (header, payload))
      return

    if ptype == PacketType.DIAG:
      parsed = parse_diag_payload(payload)
      if parsed is None:
        self.trace.rejected('diag_short', raw)
        return
      self.last_teensy_diag = parsed
      self.last_diag_at = time.monotonic()
      if self.on_diag:
        self.on_diag(parsed)
      return

    if ptype in _RESPONSE_TYPES and self._route_legacy_reply(header, payload):
      return
    self.trace.event('unsolicited', ptype=int(ptype))

  def _route_legacy_reply(self, header: PacketHeader, payload: bytes) -> bool:
    """Firmware before 1.3.0 echoes no request id. If exactly one waiter expects
    this response type, hand it over so the link still comes up on a Pi deployed
    ahead of the firmware flash. Inert once the firmware echoes ids."""
    candidates = [
      rid for rid, types in self._waiter_types.items()
      if header.packet_type in types and not self.waiters[rid].done()
    ]
    if len(candidates) != 1:
      return False
    req_id = candidates[0]
    fut = self.waiters.pop(req_id)
    self._waiter_types.pop(req_id, None)
    self.trace.event('legacy_reply', ptype=int(header.packet_type), req_id=req_id)
    fut.set_result(None if header.packet_type == PacketType.CONFIG_NAK else (header, payload))
    return True

  # --- requests ---

  async def request(self, writer, ptype: int, payload: bytes = b'', timeout: float = 0.5,
                    write_timeout: float = 1.0,
                    raise_on_timeout: bool = False) -> Optional[tuple[PacketHeader, bytes]]:
    """Send a request and await its id-matched reply. None on timeout or NAK.

    raise_on_timeout=True raises asyncio.TimeoutError instead, for callers that
    must tell "no answer" from "NAK" (CONFIG). Raises LinkError (from the
    writer) if the write itself failed.
    """
    req_id = self.next_id()
    fut = asyncio.get_running_loop().create_future()
    self.waiters[req_id] = fut
    self._waiter_types[req_id] = _REQUEST_RESPONSES.get(ptype, set())
    framed = frame_packet(build_packet(ptype, payload, frame_id=req_id))
    try:
      await writer.write(framed, ptype, req_id, timeout=write_timeout)
      return await asyncio.wait_for(fut, timeout)
    except asyncio.TimeoutError:
      self.trace.event('request_timeout', ptype=int(ptype), req_id=req_id)
      if raise_on_timeout:
        raise
      return None
    except ConnectionError:
      return None
    finally:
      # Only remove our own future: a late reply may already have popped it,
      # and the id is never reused, so there is nothing else to collide with
      if self.waiters.get(req_id) is fut:
        self.waiters.pop(req_id, None)
        self._waiter_types.pop(req_id, None)
