"""
Binary protocol definitions for Pi <-> Teensy communication.

Packet format:
  magic (4B) | version (1B) | type (1B) | flags (2B) | frame_id (4B) |
  timestamp_us (8B) | payload_len (4B) | payload (N B) | crc32 (4B)

All multi-byte values are little-endian.
Packets are COBS-encoded with 0x00 as delimiter.
"""

import struct
import zlib
from enum import IntEnum
from dataclasses import dataclass
from typing import Optional


MAGIC = b'PILL'
PROTOCOL_VERSION = 1
HEADER_SIZE = 24  # magic(4) + ver(1) + type(1) + flags(2) + frame_id(4) + ts(8) + payload_len(4)
CRC_SIZE = 4

# Canonical payload sizes
STATS_PAYLOAD_SIZE = 28  # 7 x uint32_t
STATS_STRUCT_FMT = '<IIIIIII'
CAPS_PAYLOAD_SIZE = 56
HELLO_PAYLOAD_SIZE = 48


class PacketType(IntEnum):
  HELLO = 0x01
  CAPS = 0x02
  CONFIG = 0x03
  FRAME = 0x10
  PING = 0x20
  PONG = 0x21
  STATS = 0x30
  TEST_PATTERN = 0x40
  BLACKOUT = 0x41
  BRIGHTNESS = 0x42
  REBOOT_TO_BOOTLOADER = 0xFF


class TestPattern(IntEnum):
  ALL_BLACK = 0
  ALL_WHITE = 1
  RGB_ORDER = 2
  CHANNEL_CHASE = 3
  PIXEL_CHASE = 4
  BOTTOM_TO_TOP = 5
  CHANNEL_IDENTIFY = 6
  HEARTBEAT = 7
  STRIP_IDENTIFY = 8
  SEAM_MARKER = 9


@dataclass
class PacketHeader:
  version: int = PROTOCOL_VERSION
  packet_type: int = 0
  flags: int = 0
  frame_id: int = 0
  timestamp_us: int = 0
  payload_len: int = 0


def pack_header(header: PacketHeader) -> bytes:
  return struct.pack(
    '<4sBBHIQI',
    MAGIC,
    header.version,
    header.packet_type,
    header.flags,
    header.frame_id,
    header.timestamp_us,
    header.payload_len,
  )


def unpack_header(data: bytes) -> Optional[PacketHeader]:
  if len(data) < HEADER_SIZE:
    return None
  magic, ver, ptype, flags, fid, ts, plen = struct.unpack('<4sBBHIQI', data[:HEADER_SIZE])
  if magic != MAGIC:
    return None
  return PacketHeader(
    version=ver,
    packet_type=ptype,
    flags=flags,
    frame_id=fid,
    timestamp_us=ts,
    payload_len=plen,
  )


def build_packet(packet_type: int, payload: bytes = b'', frame_id: int = 0,
                 timestamp_us: int = 0, flags: int = 0) -> bytes:
  """Build a complete packet with header, payload, and CRC32."""
  header = PacketHeader(
    packet_type=packet_type,
    flags=flags,
    frame_id=frame_id,
    timestamp_us=timestamp_us,
    payload_len=len(payload),
  )
  raw = pack_header(header) + payload
  crc = zlib.crc32(raw) & 0xFFFFFFFF
  return raw + struct.pack('<I', crc)


def verify_packet(data: bytes) -> Optional[tuple[PacketHeader, bytes]]:
  """Verify and unpack a packet. Returns (header, payload) or None."""
  if len(data) < HEADER_SIZE + CRC_SIZE:
    return None

  header = unpack_header(data)
  if header is None:
    return None

  expected_len = HEADER_SIZE + header.payload_len + CRC_SIZE
  if len(data) < expected_len:
    return None

  raw = data[:HEADER_SIZE + header.payload_len]
  crc_bytes = data[HEADER_SIZE + header.payload_len:HEADER_SIZE + header.payload_len + CRC_SIZE]
  stored_crc = struct.unpack('<I', crc_bytes)[0]
  computed_crc = zlib.crc32(raw) & 0xFFFFFFFF

  if stored_crc != computed_crc:
    return None

  payload = data[HEADER_SIZE:HEADER_SIZE + header.payload_len]
  return (header, payload)


# --- COBS encoding/decoding ---

def cobs_encode(data: bytes) -> bytes:
  """COBS encode data. Output will not contain 0x00."""
  if len(data) == 0:
    return b'\x01'

  output = bytearray()
  idx = 0
  while idx <= len(data):
    block_start = idx
    while idx < len(data) and data[idx] != 0 and (idx - block_start) < 254:
      idx += 1

    block_len = idx - block_start
    output.append(block_len + 1)
    output.extend(data[block_start:block_start + block_len])

    if idx < len(data) and data[idx] == 0:
      idx += 1
    else:
      break
  return bytes(output)


def cobs_decode(data: bytes) -> Optional[bytes]:
  """COBS decode data. Returns None on error."""
  if len(data) == 0:
    return b''

  output = bytearray()
  idx = 0
  try:
    while idx < len(data):
      code = data[idx]
      if code == 0:
        return None
      idx += 1
      for _ in range(code - 1):
        if idx >= len(data):
          return None
        output.append(data[idx])
        idx += 1
      if code < 255 and idx < len(data):
        output.append(0)
    return bytes(output)
  except (IndexError, ValueError):
    return None


def frame_packet(data: bytes) -> bytes:
  """COBS-encode and add 0x00 delimiter."""
  return cobs_encode(data) + b'\x00'


def build_hello_payload(app_name: str = "pillar-pi", app_version: str = "1.0.0") -> bytes:
  name_bytes = app_name.encode('utf-8')[:32].ljust(32, b'\x00')
  ver_bytes = app_version.encode('utf-8')[:16].ljust(16, b'\x00')
  return name_bytes + ver_bytes


def build_frame_payload(channels: int, leds_per_channel: int, pixel_data: bytes) -> bytes:
  meta = struct.pack('<BH', channels, leds_per_channel)
  return meta + pixel_data


def build_blackout_payload(enabled: bool) -> bytes:
  """Build explicit blackout payload: 0x01=on, 0x00=off."""
  return struct.pack('<B', 0x01 if enabled else 0x00)


def parse_caps_payload(payload: bytes) -> Optional[dict]:
  if len(payload) < CAPS_PAYLOAD_SIZE:
    return None
  fw_ver = payload[:16].rstrip(b'\x00').decode('utf-8', errors='replace')
  proto_ver = payload[16]
  outputs = payload[17]
  leds_per_strip = struct.unpack('<H', payload[18:20])[0]
  color_order = payload[20:24].rstrip(b'\x00').decode('utf-8', errors='replace')
  return {
    'firmware_version': fw_ver,
    'protocol_version': proto_ver,
    'outputs': outputs,
    'leds_per_strip': leds_per_strip,
    'color_order': color_order,
  }


def parse_stats_payload(payload: bytes) -> Optional[dict]:
  """Parse STATS payload from Teensy. Exactly 28 bytes (7 x uint32)."""
  if len(payload) < STATS_PAYLOAD_SIZE:
    return None
  values = struct.unpack(STATS_STRUCT_FMT, payload[:STATS_PAYLOAD_SIZE])
  return {
    'uptime_ms': values[0],
    'frames_received': values[1],
    'frames_applied': values[2],
    'bad_crc': values[3],
    'bad_frame': values[4],
    'dropped_pending': values[5],
    'output_fps': values[6],
  }
