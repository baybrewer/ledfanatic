"""TransportTrace: byte-budget ring, eviction, truncation, frozen snapshots."""

from app.transport.trace import TransportTrace


def test_budget_evicts_oldest_chunks():
  trace = TransportTrace(byte_budget=1000, raw_chunk_max=4096)
  events = [trace.rx(0x30, i, bytes([i]) * 300) for i in range(5)]
  assert trace.raw_bytes_retained <= 1000
  # 5 x 300 = 1500 > 1000: the two oldest chunks are gone, three remain
  assert trace.raw_bytes_retained == 900
  assert trace.raw_for(events[0]) is None and trace.raw_for(events[1]) is None
  assert trace.raw_for(events[2]) == b'\x02' * 300
  assert trace.raw_for(events[4]) == b'\x04' * 300
  # Absolute offsets survive eviction so a frozen trace can say what is missing
  assert events[3].raw_offset == 900
  assert trace.raw_base_offset == 600


def test_oversized_chunk_truncated_and_marked():
  trace = TransportTrace(byte_budget=10_000, raw_chunk_max=100)
  ev = trace.tx(0x10, 7, 300, 300, 1.2, True, b'\xAA' * 300)
  assert ev.truncated is True
  assert ev.raw_len == 100 and ev.raw_total == 300
  assert trace.raw_for(ev) == b'\xAA' * 100
  assert ev.detail['requested'] == 300 and ev.detail['ok'] is True


def test_chunk_never_exceeds_budget_itself():
  trace = TransportTrace(byte_budget=50, raw_chunk_max=4096)
  ev = trace.rejected('cobs', b'\x01' * 200)
  assert ev.raw_len == 50 and ev.truncated
  assert trace.raw_bytes_retained == 50


def test_event_ring_bounded():
  trace = TransportTrace(event_maxlen=10)
  for i in range(25):
    trace.event('tick', n=i)
  assert len(trace.events) == 10
  assert trace.events[0].detail['n'] == 15


def test_events_without_raw():
  trace = TransportTrace()
  ev = trace.event('write_timeout', ptype=0x10)
  assert ev.raw_offset is None and ev.raw_len == 0 and not ev.truncated
  assert trace.raw_for(ev) is None


def test_freeze_is_a_deep_snapshot():
  trace = TransportTrace(byte_budget=1000)
  trace.rx(0x02, 1, b'caps')
  trace.event('state', frm='OPEN', to='HELLO_OK')
  snap = trace.freeze()
  # Later traffic (e.g. the incident probe itself) must not leak into the snapshot
  trace.tx(0x20, 2, 10, 10, 0.5, True, b'ping-probe')
  trace.events[-1].detail['mutated'] = True
  assert len(snap['events']) == 2
  assert snap['events'][0]['kind'] == 'rx' and snap['events'][1]['detail']['to'] == 'HELLO_OK'
  assert snap['raw_chunks'] == [{'offset': 0, 'hex': b'caps'.hex()}]
  assert all('mutated' not in e['detail'] for e in snap['events'])
  assert snap['byte_budget'] == 1000 and 'frozen_at' in snap


def test_freeze_marks_evicted_raw():
  trace = TransportTrace(byte_budget=100)
  trace.rx(0x10, 1, b'a' * 80)
  trace.rx(0x10, 2, b'b' * 80)
  snap = trace.freeze()
  assert snap['events'][0]['raw_evicted'] is True
  assert snap['events'][1]['raw_evicted'] is False
  assert snap['raw_base_offset'] == 80
