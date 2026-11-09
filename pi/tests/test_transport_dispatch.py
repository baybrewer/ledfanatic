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

  def __init__(self, boot_id=1, respond_config=True, geometry_generation=1, ignore_configs=0):
    super().__init__()
    self.boot_id = boot_id
    self.respond_config = respond_config
    self.geometry_generation = geometry_generation
    self.ignore_configs = ignore_configs   # swallow the first N CONFIGs (lost ACK)
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
      if self.ignore_configs > 0:
        self.ignore_configs -= 1
      elif self.respond_config:
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


async def _writer(fake, **kw):
  trace = TransportTrace()
  errors = []
  w = SerialWriter(trace, on_link_error=lambda reason, exc: errors.append(reason))
  await w.start(fake)
  return w, trace, errors


def _job(req_id, size=8):
  return frame_packet(build_packet(PacketType.PING, b'x' * size, frame_id=req_id))


# --- SerialWriter ---

@pytest.mark.asyncio
async def test_caller_timeout_mid_write_does_not_start_next_job_early():
  fake = FakeSerial(write_delay=0.08)
  w, trace, _ = await _writer(fake)
  with pytest.raises(asyncio.TimeoutError):
    await w.write(_job(1), PacketType.PING, 1, timeout=0.01)   # write still in flight
  await w.write(_job(2), PacketType.PING, 2, timeout=1.0)
  assert fake.order == [('start', 1), ('end', 1), ('start', 2), ('end', 2)]
  assert [_tag_of(f) for f in fake.written] == [1, 2]
  w.stop()


@pytest.mark.asyncio
async def test_timed_out_in_flight_write_leaves_writer_alive_and_traced():
  fake = FakeSerial(write_delay=0.05)
  w, trace, _ = await _writer(fake)
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
  w, trace, _ = await _writer(fake)
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
  w, trace, errors = await _writer(fake)
  with pytest.raises(LinkError):
    await w.write(_job(1), PacketType.PING, 1, timeout=1.0)
  assert errors == ['write_timeout']
  assert any(e.kind == 'write_timeout' for e in trace.events)
  tx = [e for e in trace.events if e.kind == 'tx']
  assert tx and tx[0].detail['ok'] is False and tx[0].detail['error'] == 'write_timeout'
  assert w.running   # a failing job never ends the writer task
  w.stop()


@pytest.mark.asyncio
async def test_stop_fails_queued_jobs_but_completes_the_in_flight_write():
  fake = FakeSerial(write_delay=0.05)
  w, trace, _ = await _writer(fake)
  first = asyncio.ensure_future(w.write(_job(1), PacketType.PING, 1, timeout=1.0))
  second = asyncio.ensure_future(w.write(_job(2), PacketType.PING, 2, timeout=1.0))
  await asyncio.sleep(0.005)
  task = w.stop(close_port=True)
  assert not w.running and fake.is_open          # port stays open under the in-flight write
  with pytest.raises(LinkError):
    await second
  with pytest.raises(LinkError):
    await w.write(_job(3), PacketType.PING, 3)
  assert await first == len(_job(1))            # the started write ran to completion
  await task
  assert fake.is_open is False
  assert [_tag_of(f) for f in fake.written] == [1]
  tx = [e for e in trace.events if e.kind == 'tx']
  assert len(tx) == 1 and tx[0].detail['req_id'] == 1 and tx[0].detail['ok']
  assert trace.events[-1].kind == 'writer_stopped'


@pytest.mark.asyncio
async def test_start_after_stop_waits_for_the_old_writer():
  """A new port lifetime never overlaps a write still in flight on the old one."""
  old, new = FakeSerial(write_delay=0.05), FakeSerial()
  w, trace, _ = await _writer(old)
  first = asyncio.ensure_future(w.write(_job(1), PacketType.PING, 1, timeout=1.0))
  await asyncio.sleep(0.005)
  w.stop(close_port=True)
  await w.start(new)
  assert old.is_open is False and old.order == [('start', 1), ('end', 1)]
  assert await first == len(_job(1))
  await w.write(_job(2), PacketType.PING, 2, timeout=1.0)
  assert [_tag_of(f) for f in new.written] == [2]
  tx = [e.detail['req_id'] for e in trace.events if e.kind == 'tx']
  assert tx == [1, 2]
  w.stop()


# --- RxDispatcher ---

async def _dispatcher(fake):
  trace = TransportTrace()
  pots, diags = [], []
  d = RxDispatcher(trace, on_pots=lambda h, p: pots.append(p), on_diag=diags.append)
  d.attach(fake)
  w = SerialWriter(trace)
  await w.start(fake)
  return d, w, trace, pots, diags


@pytest.mark.asyncio
async def test_concurrent_stats_resolve_to_their_own_callers():
  fake = FakeSerial()
  d, w, trace, _, _ = await _dispatcher(fake)
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
  d, w, trace, _, _ = await _dispatcher(fake)
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
  d, w, trace, _, _ = await _dispatcher(fake)
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
  d, w, trace, _, diags = await _dispatcher(fake)
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


def test_incomplete_tail_is_traced_at_read_time_and_on_detach():
  fake = FakeSerial()
  trace = TransportTrace()
  d = RxDispatcher(trace)
  d.attach(fake)
  fake.rx.extend(b'\x03\x01\x02')          # a packet whose delimiter never arrives
  assert d.drain() == 0
  raw = [e for e in trace.events if e.kind == 'rx_raw']
  assert len(raw) == 1 and trace.raw_for(raw[0]) == b'\x03\x01\x02'
  assert trace.raw_bytes_retained == 3
  d.detach('link_lost')
  tail = [e for e in trace.events if e.kind == 'rx_tail_discarded']
  assert len(tail) == 1
  assert tail[0].detail['reason'] == 'link_lost'
  assert tail[0].detail['wire'] == {'offset': 0, 'len': 3, 'chunks': [raw[0].raw_offset]}


def test_packet_and_rejection_events_reference_wire_bytes():
  """Packets are referenced by the chunks they were cut from, not re-copied decoded."""
  fake = FakeSerial()
  trace = TransportTrace()
  d = RxDispatcher(trace)
  d.attach(fake)
  framed = frame_packet(build_packet(PacketType.STATS, _stats(), frame_id=0))
  fake.rx.extend(framed[:5])                  # split across two serial reads
  d.drain()
  fake.rx.extend(framed[5:])
  assert d.drain() == 1
  raw = [e for e in trace.events if e.kind == 'rx_raw']
  assert b''.join(trace.raw_for(r) for r in raw) == framed
  rx = next(e for e in trace.events if e.kind == 'rx')
  assert rx.raw_offset is None                # no second copy in the ring
  assert rx.detail['wire'] == {'offset': 0, 'len': len(framed),
                               'chunks': [raw[0].raw_offset, raw[1].raw_offset]}
  bad = bytearray(framed)
  bad[-3] ^= 0xFF                             # corrupt the CRC, keep COBS framing valid
  fake.rx.extend(bad)
  d.drain()
  rej = next(e for e in trace.events if e.kind == 'rejected')
  assert rej.detail['reason'] in ('verify', 'cobs')
  assert rej.detail['wire']['offset'] == len(framed) and rej.detail['wire']['len'] == len(framed)
  wire_chunk = next(e for e in trace.events if e.kind == 'rx_raw' and e.raw_offset in rej.detail['wire']['chunks'])
  assert trace.raw_for(wire_chunk) == bytes(bad)


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


async def _bring_up(t: TeensyTransport, config=(10, 0, 0, 0, 0, 0, 0, 0)):
  """The connect sequence main.py runs: CONFIG → brightness → blackout → mark_ready."""
  assert await t.connect() is True
  assert await t.send_config(list(config)) is True
  assert await t.send_brightness(1.0) and await t.send_blackout(False)
  assert t.mark_ready() is True
  assert t.state == LinkState.READY


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
    assert t.mark_ready() is True
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
    await _bring_up(t)
    assert await t.send_frame(b'\x02' * 30)
    t.disconnect('link_lost')
    assert t.state == LinkState.CLOSED and not t.connected

    # The Teensy still holds its geometry, but the Pi must not assume so
    teensy.respond_config = False
    assert await t.connect() is True
    assert await t.send_frame(b'\x03' * 30) is False
    assert await t.send_config([10, 0, 0, 0, 0, 0, 0, 0], timeout=0.05, attempts=1) is False
    assert t.state == LinkState.HELLO_OK
    assert t.mark_ready() is False              # connect sequence finished without an ACK
    assert t.state == LinkState.HELLO_OK
    assert await t.send_frame(b'\x03' * 30) is False
    teensy.respond_config = True
    assert await t.send_config([10, 0, 0, 0, 0, 0, 0, 0]) is True
    # Brightness/blackout from the previous connection do not carry over
    assert t.state == LinkState.CONFIGURED
    assert await t.send_frame(b'\x04' * 30) is False
    assert await t.send_brightness(1.0) and await t.send_blackout(False)
    assert t.mark_ready() is True
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
    await _bring_up(t)
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
async def test_stale_mark_ready_cannot_promote_the_next_connection():
  """Review repro: link drops mid-restoration, the connect sequence still calls
  mark_ready(), and after reconnect CONFIG alone must not reach READY."""
  teensy = FakeTeensy()
  t, patcher = _connected_transport(teensy)
  try:
    await t.connect()
    assert await t.send_config([10, 0, 0, 0, 0, 0, 0, 0]) is True
    assert await t.send_brightness(1.0)
    t.disconnect('link_lost')                   # blackout restore never happened
    assert t.mark_ready() is False and t.state == LinkState.CLOSED
    await t.connect()
    assert await t.send_config([10, 0, 0, 0, 0, 0, 0, 0]) is True
    assert t.state == LinkState.CONFIGURED
    assert await t.send_frame(b'\x01' * 30) is False
    assert await t.send_brightness(1.0)
    assert t.mark_ready() is False              # blackout still unrestored on this connection
    assert await t.send_blackout(False)
    assert t.mark_ready() is True and t.state == LinkState.READY
  finally:
    t.disconnect()
    patcher.stop()


@pytest.mark.asyncio
async def test_lost_config_ack_is_retried_and_stall_clears_only_on_ready():
  teensy = FakeTeensy(ignore_configs=1)
  t, patcher = _connected_transport(teensy)
  try:
    await t.connect()
    with patch.object(usb, 'reboot_teensy') as reboot:
      ok = await t.send_config([10, 0, 0, 0, 0, 0, 0, 0], timeout=0.05, attempts=3, retry_delay=0.01)
      reboot.assert_not_called()
    assert ok is True and teensy.configs == 2
    assert t.state == LinkState.CONFIGURED
    assert 'stall' in t.detector.open_episodes  # the ACK alone does not end the episode
    assert await t.send_brightness(1.0) and await t.send_blackout(False)
    assert t.mark_ready() is True
    assert 'stall' not in t.detector.open_episodes
    assert len([e for e in t.detector.emitted if e[0] == 'stall']) == 1
  finally:
    t.disconnect()
    patcher.stop()


@pytest.mark.asyncio
async def test_config_exhausting_attempts_keeps_episode_open_without_reset():
  teensy = FakeTeensy(respond_config=False)
  t, patcher = _connected_transport(teensy)
  try:
    await t.connect()
    with patch.object(usb, 'reboot_teensy') as reboot:
      ok = await t.send_config([10, 0, 0, 0, 0, 0, 0, 0], timeout=0.02, attempts=3, retry_delay=0.01)
      reboot.assert_not_called()
    assert ok is False and teensy.configs == 3
    assert t.state == LinkState.HELLO_OK and t.connected
    assert 'stall' in t.detector.open_episodes
    assert t.mark_ready() is False
  finally:
    t.disconnect()
    patcher.stop()


@pytest.mark.asyncio
async def test_reconnect_loop_reruns_bring_up_when_stuck_below_ready():
  """A connect sequence whose CONFIG ACK was lost is re-run by the loop, never
  answered with a reboot; the second run reaches READY."""
  teensy = FakeTeensy(ignore_configs=1)
  t, patcher = _connected_transport(teensy)
  runs = []

  async def connect_sequence():
    runs.append(1)
    await t.send_config([10, 0, 0, 0, 0, 0, 0, 0], timeout=0.03, attempts=1)
    await t.send_brightness(1.0)
    await t.send_blackout(False)
    t.mark_ready()

  t._on_connect_callback = connect_sequence
  # The fake port has no /dev node; keep the loop's port-presence check honest otherwise
  with patch.object(usb, 'BRING_UP_RETRY_S', 0.05), patch.object(usb, 'reboot_teensy') as reboot, \
       patch.object(usb.os.path, 'exists', lambda path: True):
    loop_task = asyncio.ensure_future(t.reconnect_loop())
    try:
      assert await t.wait_for(LinkState.READY, timeout=2.0)
      assert runs == [1, 1] and teensy.configs == 2
      reboot.assert_not_called()
      assert 'stall' not in t.detector.open_episodes
      assert t.get_status()['stalled'] is None
    finally:
      loop_task.cancel()
      try:
        await loop_task
      except asyncio.CancelledError:
        pass
      t.disconnect()
      patcher.stop()


@pytest.mark.asyncio
async def test_overlapping_config_requests_are_serialized():
  teensy = FakeTeensy()
  t, patcher = _connected_transport(teensy)
  try:
    await _bring_up(t)
    base = teensy.configs
    teensy.respond_config = False
    a = asyncio.ensure_future(t.send_config([10, 0, 0, 0, 0, 0, 0, 0], timeout=1.0, attempts=1))
    b = asyncio.ensure_future(t.send_config([12, 0, 0, 0, 0, 0, 0, 0], timeout=1.0, attempts=1))
    await _settle()
    assert teensy.configs == base + 1 and t.state == LinkState.HELLO_OK   # B waits for A's outcome
    rid_a = _tag_of(teensy.written[-1])
    teensy.feed(build_packet(PacketType.CONFIG_ACK, struct.pack('<I', 5), frame_id=rid_a))
    assert await a is True
    await _settle()
    # B is now the pending (latest) geometry: no READY, no frames, until its own ACK
    assert teensy.configs == base + 2 and t.state == LinkState.HELLO_OK
    assert await t.send_frame(b'\x01' * 36) is False
    rid_b = _tag_of(teensy.written[-1])
    assert rid_b != rid_a
    teensy.feed(build_packet(PacketType.CONFIG_ACK, struct.pack('<I', 6), frame_id=rid_b))
    assert await b is True
    assert t.state == LinkState.READY and t.geometry_generation == 6
    assert await t.send_frame(b'\x01' * 36) is True
  finally:
    t.disconnect()
    patcher.stop()


@pytest.mark.asyncio
async def test_disconnect_mid_frame_write_records_tx_before_port_close():
  teensy = FakeTeensy()
  t, patcher = _connected_transport(teensy)
  try:
    await _bring_up(t)
    teensy.write_delay = 0.05
    sending = asyncio.ensure_future(t.send_frame(b'\x07' * 30))
    await asyncio.sleep(0.01)
    t.disconnect('link_lost')
    assert t.state == LinkState.CLOSED and teensy.is_open   # close waits for the write
    await t.writer.wait_stopped()
    assert teensy.is_open is False
    assert await sending is True
    frame_tx = [e for e in t.trace.events if e.kind == 'tx' and e.detail['ptype'] == int(PacketType.FRAME)]
    assert len(frame_tx) == 1 and frame_tx[0].detail['ok']
    assert teensy.frames == [b'\x07' * 30]
  finally:
    t.disconnect()
    patcher.stop()


@pytest.mark.asyncio
async def test_test_pattern_flag_cleared_by_frame_blackout_and_reconnect():
  teensy = FakeTeensy()
  t, patcher = _connected_transport(teensy)
  try:
    await _bring_up(t)
    assert await t.send_test_pattern(1) and t.test_pattern_active == 1
    assert await t.send_frame(b'\x01' * 30) and t.test_pattern_active is None
    assert await t.send_test_pattern(2) and t.test_pattern_active == 2
    assert await t.send_blackout(True) and t.test_pattern_active is None
    assert await t.send_test_pattern(3) and t.test_pattern_active == 3
    assert await t.send_blackout(False) and t.test_pattern_active is None
    assert await t.send_test_pattern(4) and t.test_pattern_active == 4
    assert await t.send_test_pattern(0xFF) and t.test_pattern_active is None
    assert await t.send_test_pattern(5) and t.test_pattern_active == 5
    t.disconnect('link_lost')
    assert t.test_pattern_active is None        # a new boot/connection starts clean
    await t.connect()
    assert t.test_pattern_active is None
    # A failed send leaves the flag alone: the firmware never saw the command
    t.disconnect()
    assert await t.send_test_pattern(6) is False and t.test_pattern_active is None
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
