"""
Transport trace: the last N link events plus a byte-budgeted ring of raw packets.

Evidence for incident bundles. Events are cheap dicts; raw chunks share one
ring so a burst of large frames evicts old bytes instead of growing memory.
Each event remembers where its bytes sit in the ring (absolute offset) so a
frozen trace can say "bytes already evicted" instead of silently losing them.
"""

import copy
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TraceEvent:
  t: float                     # time.time(), wall clock for correlation with journals
  kind: str                    # tx | rx | rejected | <free-form event kind>
  detail: dict = field(default_factory=dict)
  raw_offset: Optional[int] = None   # absolute offset of the chunk in the raw stream
  raw_len: int = 0                   # bytes kept (after truncation)
  raw_total: int = 0                 # bytes the packet actually had
  truncated: bool = False


class TransportTrace:
  def __init__(self, byte_budget: int = 512 * 1024, event_maxlen: int = 4000,
               raw_chunk_max: int = 4096):
    self.byte_budget = byte_budget
    self.raw_chunk_max = raw_chunk_max
    self.events: deque[TraceEvent] = deque(maxlen=event_maxlen)
    # (absolute_offset, bytes) oldest first; absolute offsets never reset so an
    # event's raw_offset stays meaningful after eviction
    self._chunks: deque[tuple[int, bytes]] = deque()
    self._raw_bytes = 0
    self._next_offset = 0

  # --- raw ring ---

  def _store_raw(self, raw: Optional[bytes]) -> tuple[Optional[int], int, int, bool]:
    if raw is None:
      return None, 0, 0, False
    total = len(raw)
    keep = min(total, self.raw_chunk_max, self.byte_budget)
    chunk = bytes(raw[:keep])
    offset = self._next_offset
    self._next_offset += keep
    if keep:
      self._chunks.append((offset, chunk))
      self._raw_bytes += keep
      while self._raw_bytes > self.byte_budget and self._chunks:
        _, evicted = self._chunks.popleft()
        self._raw_bytes -= len(evicted)
    return offset, keep, total, keep < total

  @property
  def raw_bytes_retained(self) -> int:
    return self._raw_bytes

  @property
  def raw_base_offset(self) -> int:
    """Absolute offset of the oldest retained byte (everything before is evicted)."""
    return self._chunks[0][0] if self._chunks else self._next_offset

  def raw_for(self, event: TraceEvent) -> Optional[bytes]:
    """The event's bytes if still in the ring, else None."""
    if event.raw_offset is None:
      return None
    for offset, chunk in self._chunks:
      if offset == event.raw_offset:
        return chunk
    return None

  # --- recording ---

  def _add(self, kind: str, detail: dict, raw: Optional[bytes] = None) -> TraceEvent:
    offset, kept, total, truncated = self._store_raw(raw)
    ev = TraceEvent(time.time(), kind, detail, offset, kept, total, truncated)
    self.events.append(ev)
    return ev

  def tx(self, ptype: int, req_id: int, nbytes_requested: int, nbytes_written: int,
         write_ms: float, ok: bool, raw: Optional[bytes] = None, error: str = None) -> TraceEvent:
    detail = {
      'ptype': int(ptype), 'req_id': req_id, 'requested': nbytes_requested,
      'written': nbytes_written, 'write_ms': round(write_ms, 3), 'ok': ok,
    }
    if error:
      detail['error'] = error
    return self._add('tx', detail, raw)

  def rx_raw(self, raw: bytes) -> TraceEvent:
    """One serial-read chunk exactly as it came off the wire (delimiters,
    partial tails and all). Packet/rejection events point back at these via
    `wire` so framing failures can be reconstructed from the real bytes."""
    return self._add('rx_raw', {'nbytes': len(raw)}, raw)

  def rx(self, ptype: int, req_id: int, raw: Optional[bytes] = None, **detail) -> TraceEvent:
    return self._add('rx', {'ptype': int(ptype), 'req_id': req_id, **detail}, raw)

  def rejected(self, reason: str, raw: Optional[bytes] = None, **detail) -> TraceEvent:
    return self._add('rejected', {'reason': reason, **detail}, raw)

  def event(self, kind: str, **detail) -> TraceEvent:
    return self._add(kind, detail)

  # --- export ---

  def freeze(self) -> dict:
    """Deep-copied snapshot. Call BEFORE any live probe so the probe's own
    traffic cannot overwrite the evidence it is meant to explain."""
    events = [
      {
        't': ev.t, 'kind': ev.kind, 'detail': copy.deepcopy(ev.detail),
        'raw_offset': ev.raw_offset, 'raw_len': ev.raw_len, 'raw_total': ev.raw_total,
        'truncated': ev.truncated,
        'raw_evicted': ev.raw_offset is not None and ev.raw_offset < self.raw_base_offset,
      }
      for ev in self.events
    ]
    return {
      'frozen_at': time.time(),
      'byte_budget': self.byte_budget,
      'raw_base_offset': self.raw_base_offset,
      'raw_bytes': self._raw_bytes,
      'raw_chunks': [{'offset': off, 'hex': chunk.hex()} for off, chunk in self._chunks],
      'events': events,
    }
