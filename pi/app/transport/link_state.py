"""Connection state of the Teensy link (shared by usb.py and detection.py)."""

from enum import Enum


class LinkState(str, Enum):
  CLOSED = 'CLOSED'           # no port
  OPEN = 'OPEN'               # port open, dispatcher reading, no handshake yet
  HELLO_OK = 'HELLO_OK'       # CAPS received (also: CONFIG in flight after a layout change)
  CONFIGURED = 'CONFIGURED'   # CONFIG_ACK received for the current geometry
  READY = 'READY'             # brightness pinned + blackout restored: frames may flow

  @property
  def rank(self) -> int:
    return _ORDER.index(self)

  def at_least(self, other: 'LinkState') -> bool:
    return self.rank >= other.rank


_ORDER = [LinkState.CLOSED, LinkState.OPEN, LinkState.HELLO_OK, LinkState.CONFIGURED,
          LinkState.READY]
