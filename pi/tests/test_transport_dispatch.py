"""SerialWriter ordering/cancellation, RxDispatcher request-id routing, and the
TeensyTransport connection state machine — all against a fake serial port."""

import asyncio
import struct
import time
from unittest.mock import patch

import pytest
import serial

from app.models.protocol import (
  PacketType, build_packet, frame_packet, verify_packet, cobs_decode,
  DIAG_FIELDS, parse_caps_payload, STATS_PAYLOAD_SIZE,
)
from app.transport import usb
from app.transport.usb import SerialWriter, TeensyTransport, LinkError
from app.transport.dispatcher import RxDispatcher
from app.transport.trace import TransportTrace
from app.transport.link_state import LinkState


# --- fakes ---

class FakeSerial:
  """pyserial stand-in. write() runs in a worker thread like the real one."""

  def __init__(self, write_delay: float = 0.0, raise_on_write: Exception = None):
    self.write_delay = write_delay
    self.raise_on_write = raise_on_write
    self.written: list[bytes] = []
    self.order: list[tuple[str, int]] = []   # ('start'|'end', tag) in wall order
    self.rx = bytearray()
    self.is_open = True
    self.port = '/dev/fake'

  @property
  def in_waiting(self):
    return len(self.rx)

  def read(self, n):
    data = bytes(self.rx[:n])
    del self.rx[:n]
    return data

  def write(self, data):
    tag = _tag_of(data)
    self.order.append(('start', tag))
    if self.write_delay:
      time.sleep(self.write_delay)
    if self.raise_on_write:
      self.order.append(('end', tag))
      raise self.raise_on_write
    self.written.append(bytes(data))
    self.order.append(('end', tag))
    return len(data)

  def close(self):
    self.is_open = False

  def feed(self, packet: bytes):
    self.rx.extend(frame_packet(packet))


def _decode(framed: bytes):
  return verify_packet(cobs_decode(framed.rstrip(b'\x00')))


def _tag_of(framed: bytes) -> int:
  result = _decode(bytes(framed))
  return result[0].frame_id if result else -1


def _caps(boot_id=1, faulted=0, geometry_generation=0):
  payload = bytearray(56)
  payload[:5] = b'1.3.0'
  payload[16] = 1
  payload[17] = 5
  struct.pack_into('<H', payload, 18, 100)
  payload[20:23] = b'BGR'
  struct.pack_into('<III', payload, 24, 1000, 1, boot_id)
  struct.pack_into('<I', payload, 37, geometry_generation)
  payload[41] = faulted
  return bytes(payload)


def _stats(uptime=1):
  return struct.pack('<IIIIIII', uptime, 0, 0, 0, 0, 0, 60)


def _diag(**over):
  from app.models.protocol import DIAG_FIELD_NAMES
  values = [0] * DIAG_FIELDS
  for k, v in over.items():
    values[DIAG_FIELD_NAMES.index(k)] = v
  return struct.pack(f'<{DIAG_FIELDS}I', *values)


class FakeTeensy(FakeSerial):
  """Answers requests like firmware 1.3.0: echoes the request id."""

  def __init__(self, boot_id=1, respond_config=True, geometry_generation=1):
    super().__init__()
    self.boot_id = boot_id
    self.respond_config = respond_config
    self.geometry_generation = geometry_generation
    self.frames: list[bytes] = []
    self.configs = 0

  def write(self, data):
    n = super().write(data)
    header, payload = _decode(bytes(data))
    rid = header.frame_id
    if header.packet_type == PacketType.HELLO:
      self.feed(build_packet(PacketType.CAPS, _caps(self.boot_id, geometry_generation=self.geometry_generation), frame_id=rid))
    elif header.packet_type == PacketType.CONFIG:
      self.configs += 1
      if self.respond_config:
        self.geometry_generation += 1
        self.feed(build_packet(PacketType.CONFIG_ACK, struct.pack('<I', self.geometry_generation), frame_id=rid))
    elif header.packet_type == PacketType.PING:
      self.feed(build_packet(PacketType.STATS, _stats(), frame_id=rid))
    elif header.packet_type == PacketType.FRAME:
      self.frames.append(payload)
    return n


async def _settle(n=3):
  for _ in range(n):
    await asyncio.sleep(0.01)


def _writer(fake, **kw):
  trace = TransportTrace()
  errors = []
  w = SerialWriter(trace, on_link_error=lambda reason, exc: errors.append(reason))
  w.start(fake)
  return w, trace, errors


def _job(req_id, size=8):
  return frame_packet(build_packet(PacketType.PING, b'x' * size, frame_id=req_id))


# --- SerialWriter ---

@pytest.mark.asyncio
async def test_caller_timeout_mid_write_does_not_start_next_job_early():
  fake = FakeSerial(write_delay=0.08)
  w, trace, _ = _writer(fake)
  with pytest.raises(asyncio.TimeoutError):
    await w.write(_job(1), PacketType.PING, 1, timeout=0.01)   # write still in flight
  await w.write(_job(2), PacketType.PING, 2, timeout=1.0)
  assert fake.order == [('start', 1), ('end', 1), ('start', 2), ('end', 2)]
  assert [_tag_of(f) for f in fake.written] == [1, 2]
  w.stop()


@pytest.mark.asyncio
async def test_timed_out_in_flight_write_leaves_writer_alive_and_traced():
  fake = FakeSerial(write_delay=0.05)
  w, trace, _ = _writer(fake)
  with pytest.raises(asyncio.TimeoutError):
    await w.write(_job(1), PacketType.PING, 1, timeout=0.01)
  await asyncio.sleep(0.1)                 # the in-flight write completes; set_result must not raise
  assert w.running
  await w.write(_job(2), PacketType.PING, 2, timeout=1.0)
  tx = [e for e in trace.events if e.kind == 'tx']
  assert [e.detail['req_id'] for e in tx] == [1, 2] and all(e.detail['ok'] for e in tx)
  assert w.jobs_written == 2
  w.stop()


@pytest.mark.asyncio
async def test_job_cancelled_before_start_is_skipped():
  fake = FakeSerial(write_delay=0.05)
  w, trace, _ = _writer(fake)
  first = asyncio.ensure_future(w.write(_job(1), PacketType.PING, 1, timeout=1.0))
  await asyncio.sleep(0.005)               # job 1 is being written in the thread
  with pytest.raises(asyncio.TimeoutError):
    await w.write(_job(2), PacketType.PING, 2, timeout=0.01)   # queued behind 1, never starts
  await first
  await w.write(_job(3), PacketType.PING, 3, timeout=1.0)
  assert [_tag_of(f) for f in fake.written] == [1, 3]
  assert w.jobs_skipped == 1
  assert any(e.kind == 'write_skipped' and e.detail['req_id'] == 2 for e in trace.events)
  w.stop()


@pytest.mark.asyncio
async def test_serial_timeout_becomes_link_error():
  fake = FakeSerial(raise_on_write=serial.SerialTimeoutException('Write timeout'))
  w, trace, errors = _writer(fake)
  with pytest.raises(LinkError):
    await w.write(_job(1), PacketType.PING, 1, timeout=1.0)
  assert errors == ['write_timeout']
  assert any(e.kind == 'write_timeout' for e in trace.events)
  tx = [e for e in trace.events if e.kind == 'tx']
  assert tx and tx[0].detail['ok'] is False and tx[0].detail['error'] == 'write_timeout'
  assert w.running   # a failing job never ends the writer task
  w.stop()


@pytest.mark.asyncio
async def test_stop_fails_pending_jobs():
  fake = FakeSerial(write_delay=0.05)
  w, _, _ = _writer(fake)
  first = asyncio.ensure_future(w.write(_job(1), PacketType.PING, 1, timeout=1.0))
  second = asyncio.ensure_future(w.write(_job(2), PacketType.PING, 2, timeout=1.0))
  await asyncio.sleep(0.005)
  w.stop()
  with pytest.raises(LinkError):
    await second
  with pytest.raises(LinkError):
    await first
  with pytest.raises(LinkError):
    await w.write(_job(3), PacketType.PING, 3)


# --- RxDispatcher ---

def _dispatcher(fake):
  trace = TransportTrace()
  pots, diags = [], []
  d = RxDispatcher(trace, on_pots=lambda h, p: pots.append(p), on_diag=diags.append)
  d.attach(fake)
  w = SerialWriter(trace)
  w.start(fake)
  return d, w, trace, pots, diags


@pytest.mark.asyncio
async def test_concurrent_stats_resolve_to_their_own_callers():
  fake = FakeSerial()
  d, w, trace, _, _ = _dispatcher(fake)
  a = asyncio.ensure_future(d.request(w, PacketType.PING, timeout=1.0))
  b = asyncio.ensure_future(d.request(w, PacketType.PING, timeout=1.0))
  await _settle()
  ids = [_tag_of(f) for f in fake.written]
  assert len(set(ids)) == 2 and 0 not in ids
  # Replies arrive in reverse order; each must still reach its own caller
  fake.feed(build_packet(PacketType.STATS, _stats(uptime=ids[1]), frame_id=ids[1]))
  fake.feed(build_packet(PacketType.STATS, _stats(uptime=ids[0]), frame_id=ids[0]))
  d.drain()
  ra, rb = await a, await b
  assert ra[0].frame_id == ids[0] and struct.unpack('<I', ra[1][:4])[0] == ids[0]
  assert rb[0].frame_id == ids[1] and struct.unpack('<I', rb[1][:4])[0] == ids[1]
  assert d.waiters == {}
  w.stop()


@pytest.mark.asyncio
async def test_late_reply_after_timeout_is_traced_not_delivered():
  fake = FakeSerial()
  d, w, trace, _, _ = _dispatcher(fake)
  assert await d.request(w, PacketType.PING, timeout=0.02) is None
  rid = _tag_of(fake.written[0])
  assert d.waiters == {}
  fake.feed(build_packet(PacketType.STATS, _stats(), frame_id=rid))
  d.drain()
  kinds = [e.kind for e in trace.events]
  assert 'request_timeout' in kinds and 'late_reply' in kinds
  assert d.waiters == {}
  w.stop()


@pytest.mark.asyncio
async def test_config_nak_resolves_none():
  fake = FakeSerial()
  d, w, trace, _, _ = _dispatcher(fake)
  req = asyncio.ensure_future(d.request(w, PacketType.CONFIG, b'\x00' * 17, timeout=1.0))
  await _settle()
  rid = _tag_of(fake.written[0])
  fake.feed(build_packet(PacketType.CONFIG_NAK, frame_id=rid))
  d.drain()
  assert await req is None
  w.stop()


@pytest.mark.asyncio
async def test_pushed_diag_caches_while_solicited_diag_resolves_waiter():
  fake = FakeSerial()
  d, w, trace, _, diags = _dispatcher(fake)
  req = asyncio.ensure_future(d.request(w, PacketType.DIAG, timeout=1.0))
  await _settle()
  rid = _tag_of(fake.written[0])
  fake.feed(build_packet(PacketType.DIAG, _diag(uptime_ms=500, boot_id=9), frame_id=0))
  fake.feed(build_packet(PacketType.DIAG, _diag(uptime_ms=501, boot_id=9), frame_id=rid))
  assert d.drain() == 2
  header, payload = await req
  assert header.frame_id == rid and struct.unpack('<I', payload[:4])[0] == 501
  assert d.last_teensy_diag['uptime_ms'] == 500 and d.last_diag_at is not None
  assert diags == [d.last_teensy_diag]     # only the push hits on_diag
  w.stop()


def test_pots_routed_and_unsolicited_traced():
  fake = FakeSerial()
  trace = TransportTrace()
  pots = []
  d = RxDispatcher(trace, on_pots=lambda h, p: pots.append(p))
  d.attach(fake)
  fake.feed(build_packet(PacketType.POTS, struct.pack('<HHH', 1, 2, 3)))
  fake.feed(build_packet(PacketType.PONG))
  fake.rx.extend(b'\x01\x00')                 # empty COBS block: ignored
  fake.rx.extend(b'\x05\x01\x02\x00')         # malformed COBS: rejected
  d.drain()
  assert pots == [struct.pack('<HHH', 1, 2, 3)]
  kinds = [e.kind for e in trace.events]
  assert 'unsolicited' in kinds and 'rejected' in kinds


def test_legacy_reply_without_request_id_reaches_sole_waiter():
  """Firmware < 1.3.0 echoes frame_id 0; one matching waiter still gets it."""
  fake = FakeSerial()
  trace = TransportTrace()
  d = RxDispatcher(trace)
  d.attach(fake)
  loop = asyncio.new_event_loop()
  fut = loop.create_future()
  d.waiters[5] = fut
  d._waiter_types[5] = {PacketType.CAPS}
  fake.feed(build_packet(PacketType.CAPS, _caps(), frame_id=0))
  d.drain()
  assert fut.done() and fut.result()[0].packet_type == PacketType.CAPS
  loop.close()


def test_request_ids_are_nonzero_and_unique():
  d = RxDispatcher(TransportTrace())
  ids = [d.next_id() for _ in range(1000)]
  assert 0 not in ids and len(set(ids)) == 1000
  d._next_req_id = 0xFFFFFFFF
  assert d.next_id() == 1


# --- TeensyTransport state machine ---

def _connected_transport(teensy: FakeTeensy):
  t = TeensyTransport(reconnect_interval=0.01, handshake_timeout=0.5)
  t.find_teensy_port = lambda: '/dev/fake'
  patcher = patch.object(usb.serial, 'Serial', lambda *a, **k: teensy)
  patcher.start()
  return t, patcher


@pytest.mark.asyncio
async def test_send_frame_refused_before_ready_and_raw_format_only():
  teensy = FakeTeensy()
  t, patcher = _connected_transport(teensy)
  try:
    assert t.state == LinkState.CLOSED
    assert await t.send_frame(b'\x01' * 30) is False
    assert await t.connect() is True
    assert t.state == LinkState.HELLO_OK and t.connected and t.boot_id == 1
    assert await t.send_frame(b'\x01' * 30) is False
    assert await t.send_config([10, 0, 0, 0, 0, 0, 0, 0]) is True
    assert t.state == LinkState.CONFIGURED and t.geometry_generation == 2
    assert await t.send_frame(b'\x01' * 30) is False
    assert await t.send_brightness(1.0) and await t.send_blackout(False)
    t.mark_ready()
    assert t.state == LinkState.READY
    pixels = bytes(range(30))
    assert await t.send_frame(pixels) is True
    assert teensy.frames == [pixels]           # raw bytes, no legacy 3-byte header
    assert t.frames_sent == 1
    status = t.get_status()
    assert status['state'] == 'READY' and status['boot_id'] == 1 and status['geometry_generation'] == 2
  finally:
    t.disconnect()
    patcher.stop()


@pytest.mark.asyncio
async def test_reconnect_to_configured_teensy_sends_no_frame_until_ack():
  teensy = FakeTeensy()
  t, patcher = _connected_transport(teensy)
  try:
    await t.connect()
    await t.send_config([10, 0, 0, 0, 0, 0, 0, 0])
    t.mark_ready()
    assert await t.send_frame(b'\x02' * 30)
    t.disconnect('link_lost')
    assert t.state == LinkState.CLOSED and not t.connected

    # The Teensy still holds its geometry, but the Pi must not assume so
    teensy.respond_config = False
    assert await t.connect() is True
    assert await t.send_frame(b'\x03' * 30) is False
    assert await t.send_config([10, 0, 0, 0, 0, 0, 0, 0], timeout=0.05) is False
    assert t.state == LinkState.HELLO_OK
    t.mark_ready()                              # connect sequence finished without an ACK
    assert t.state == LinkState.HELLO_OK
    assert await t.send_frame(b'\x03' * 30) is False
    teensy.respond_config = True
    assert await t.send_config([10, 0, 0, 0, 0, 0, 0, 0]) is True
    assert t.state == LinkState.READY           # pinned already on this connection
    assert await t.send_frame(b'\x04' * 30) is True
    assert teensy.frames == [b'\x02' * 30, b'\x04' * 30]
  finally:
    t.disconnect()
    patcher.stop()


@pytest.mark.asyncio
async def test_layout_change_in_ready_gates_frames_until_ack():
  teensy = FakeTeensy()
  t, patcher = _connected_transport(teensy)
  try:
    await t.connect()
    await t.send_config([10, 0, 0, 0, 0, 0, 0, 0])
    t.mark_ready()
    teensy.respond_config = False
    pending = asyncio.ensure_future(t.send_config([12, 0, 0, 0, 0, 0, 0, 0], timeout=0.5))
    await _settle()
    assert t.state == LinkState.HELLO_OK
    assert await t.send_frame(b'\x05' * 36) is False
    rid = _tag_of(teensy.written[-1])
    teensy.feed(build_packet(PacketType.CONFIG_ACK, struct.pack('<I', 9), frame_id=rid))
    assert await pending is True
    assert t.state == LinkState.READY and t.geometry_generation == 9
    assert await t.send_frame(b'\x05' * 36) is True
  finally:
    t.disconnect()
    patcher.stop()


@pytest.mark.asyncio
async def test_request_stats_and_health_without_recovery_code():
  teensy = FakeTeensy()
  t, patcher = _connected_transport(teensy)
  try:
    await t.connect()
    stats = await t.request_stats()
    assert stats and stats['output_fps'] == 60 and t.last_teensy_stats == stats
    with patch.object(usb, 'reboot_teensy') as reboot:
      await t._check_health()
      reboot.assert_not_called()
    assert not hasattr(t, '_recover')
    assert not hasattr(usb, 'MAX_RECOVERY_ATTEMPTS')
  finally:
    t.disconnect()
    patcher.stop()


@pytest.mark.asyncio
async def test_drain_incoming_returns_only_pots():
  teensy = FakeTeensy()
  t, patcher = _connected_transport(teensy)
  try:
    await t.connect()
    teensy.feed(build_packet(PacketType.POTS, struct.pack('<HHH', 10, 20, 30)))
    teensy.feed(build_packet(PacketType.DIAG, _diag(uptime_ms=5, boot_id=1)))
    await _settle()
    packets = await t.drain_incoming()
    assert [h.packet_type for h, _ in packets] == [PacketType.POTS]
    assert t.last_teensy_diag['uptime_ms'] == 5
    assert await t.drain_incoming() == []
  finally:
    t.disconnect()
    patcher.stop()


@pytest.mark.asyncio
async def test_maintenance_pauses_reconnect_loop():
  t = TeensyTransport(reconnect_interval=0.005)
  calls = []
  t.find_teensy_port = lambda: calls.append(1) or None
  t.maintenance = True
  task = asyncio.ensure_future(t.reconnect_loop())
  await asyncio.sleep(0.03)
  assert calls == []
  t.maintenance = False
  await asyncio.sleep(0.03)
  assert calls
  task.cancel()
  try:
    await task
  except asyncio.CancelledError:
    pass
