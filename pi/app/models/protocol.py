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
POTS_PAYLOAD_SIZE = 6  # 3 x uint16 LE (brightness, menu, pattern), ADC range 0-1023
POTS_STRUCT_FMT = '<HHH'

# DIAG: 44 x uint32 LE, pushed by firmware 1.3.0 every second and on request.
# Order is the wire contract — never reorder, only append (and bump DIAG_FIELDS).
DIAG_FIELD_NAMES = (
  'uptime_ms', 'reset_reason', 'boot_id', 'rx_bytes', 'pkt_ok',
  'bad_short', 'bad_magic', 'bad_version', 'bad_len', 'bad_crc',
  'oversize', 'empty_packets', 'last_pkt_type', 'ms_since_rx_byte', 'ms_since_good_pkt',
  'live_decoder_out_len', 'live_decoder_remaining', 'last_reject_reason', 'last_reject_len',
  'last_reject_ms_ago', 'loops_per_sec', 'max_loop_us', 'max_tx_us', 'tx_dropped',
  'ms_since_show_start', 'stall_active', 'stall_latched', 'stack_free', 'frames_received',
  'frames_applied', 'frames_pending_dropped', 'octo_begins', 'rx_backlog', 'usb_sts',
  'usb_irq', 'last_op', 'dma3_csr_es', 'tmr4_enbl_sctrl0', 'crash_present',
  'geometry_generation', 'bad_framing', 'frames_pending', 'ignored_in_fault_mode', 'fault_count',
)
DIAG_FIELDS = 44
assert len(DIAG_FIELD_NAMES) == DIAG_FIELDS
DIAG_PAYLOAD_SIZE = DIAG_FIELDS * 4
DIAG_STRUCT_FMT = f'<{DIAG_FIELDS}I'

# stall_active / stall_latched bit names (bit 3 is latched-only)
STALL_BIT_NAMES = {0: 'RX_NO_PACKET', 1: 'DMA_INCOMPLETE', 2: 'TX_DROPPED', 3: 'FAULTED'}
STALL_FAULTED = 1 << 3

LAST_OP_NAMES = {
  0: 'none', 1: 'rx', 2: 'handle_frame', 3: 'handle_config', 4: 'geometry_commit',
  5: 'show', 6: 'send', 7: 'pots', 8: 'octo_begin',
}

# CRASH: 4 x uint32 prefix + 44 B core arm_fault_info_struct + 36 B extension
CRASH_PAYLOAD_SIZE = 96
CRASH_CORE_SIZE = 44
CRASH_CORE_FMT = '<IIIIIIIIfII'   # len, ipsr, cfsr, hfsr, mmfar, bfar, ret, xpsr, temp, time, crc
CRASH_EXT_FMT = '<9I'             # r0, r1, r2, r3, r12, lr, pc, xpsr, exc_return
CRASH_SOURCE_NAMES = {0: 'none', 1: 'live', 2: 'previous_boot'}

# i.MX RT1062 SRC_SRSR reset-status bits (imxrt.h names)
SRC_SRSR_BITS = {
  0: 'IPP_RESET_B',          # power-on reset
  1: 'LOCKUP_SYSRESETREQ',   # CPU lockup or software SYSRESETREQ (e.g. bootloader request)
  2: 'CSU_RESET_B',
  3: 'IPP_USER_RESET_B',     # external reset pin
  4: 'WDOG_RST_B',
  5: 'JTAG_RST_B',
  6: 'JTAG_SW_RST',
  7: 'WDOG3_RST_B',
  8: 'TEMPSENSE_RST_B',      # thermal shutdown
}


class PacketType(IntEnum):
  HELLO = 0x01
  CAPS = 0x02
  CONFIG = 0x03
  CONFIG_ACK = 0x04
  CONFIG_NAK = 0x05
  FRAME = 0x10
  PING = 0x20
  PONG = 0x21
  STATS = 0x30
  POTS = 0x31
  DIAG = 0x32
  CRASH = 0x33
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
  CLEAR = 0xFF


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
  trailing_zero = False

  while idx < len(data):
    # Find next zero byte or end of data, capped at 254 bytes
    block_start = idx
    while idx < len(data) and data[idx] != 0 and (idx - block_start) < 254:
      idx += 1

    block_len = idx - block_start

    if block_len == 254:
      # Full block: 0xFF means "no implied zero", so a zero right after the
      # run must NOT be consumed here — it becomes the next (0x01) block.
      # Consuming it silently dropped the zero (~5% of real frames failed CRC).
      output.append(0xFF)
      output.extend(data[block_start:block_start + block_len])
      trailing_zero = False
      # Do NOT consume a zero byte — continue scanning
    else:
      # Normal block: ended by zero byte or end of data
      output.append(block_len + 1)
      output.extend(data[block_start:block_start + block_len])
      # Consume the zero byte if present
      if idx < len(data) and data[idx] == 0:
        idx += 1
        trailing_zero = True
      else:
        trailing_zero = False

  # If data ended with a zero, emit trailing \x01 block
  if trailing_zero:
    output.append(0x01)

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
        return None  # unexpected zero in encoded data
      idx += 1
      for _ in range(code - 1):
        if idx >= len(data):
          return None
        output.append(data[idx])
        idx += 1
      # If code < 0xFF and there are more bytes, insert implicit zero
      # 0xFF means the block was a 254-byte non-zero run (no trailing zero)
      if code < 0xFF and idx < len(data):
        output.append(0)
    return bytes(output)
  except (IndexError, ValueError):
    return None


def frame_packet(data: bytes) -> bytes:
  """COBS-encode and add 0x00 delimiter."""
  return cobs_encode(data) + b'\x00'


def build_hello_payload(app_name: str = "ledfanatic-pi", app_version: str = "1.0.0") -> bytes:
  name_bytes = app_name.encode('utf-8')[:32].ljust(32, b'\x00')
  ver_bytes = app_version.encode('utf-8')[:16].ljust(16, b'\x00')
  return name_bytes + ver_bytes


def build_frame_payload(channels: int, leds_per_channel: int, pixel_data: bytes) -> bytes:
  meta = struct.pack('<BH', channels, leds_per_channel)
  return meta + pixel_data


def build_blackout_payload(enabled: bool) -> bytes:
  """Build explicit blackout payload: 0x01=on, 0x00=off."""
  return struct.pack('<B', 0x01 if enabled else 0x00)


# --- CONFIG payload ---

CONFIG_PAYLOAD_FORMAT = '<B8H'
CONFIG_PAYLOAD_SIZE = struct.calcsize(CONFIG_PAYLOAD_FORMAT)  # 17 bytes


def output_config_to_list(output_config: dict) -> list[int]:
  """Convert output_config dict to flat [leds_per_pin x 8] list.

  output_config is {pin: [(strip_id, offset, count), ...], ...}.
  Returns a list of 8 ints where each entry is the max (offset + count)
  for that pin — i.e. total LEDs needed on that OctoWS2811 output.
  """
  result = [0] * 8
  for pin, entries in output_config.items():
    for strip_id, offset, count in entries:
      needed = offset + count
      if needed > result[pin]:
        result[pin] = needed
  return result


def build_config_payload(output_config: list[int]) -> bytes:
  """Build CONFIG packet payload. output_config: 8 entries, LEDs per output pin."""
  assert len(output_config) == 8
  active = sum(1 for n in output_config if n > 0)
  return struct.pack(CONFIG_PAYLOAD_FORMAT, active, *output_config)


def parse_caps_payload(payload: bytes) -> Optional[dict]:
  if len(payload) < CAPS_PAYLOAD_SIZE:
    return None
  fw_ver = payload[:16].rstrip(b'\x00').decode('utf-8', errors='replace')
  proto_ver = payload[16]
  outputs = payload[17]
  leds_per_strip = struct.unpack('<H', payload[18:20])[0]
  color_order = payload[20:24].rstrip(b'\x00').decode('utf-8', errors='replace')
  # Bytes 24-41 were zero-filled before firmware 1.3.0, so old firmware decodes
  # as uptime 0 / boot_id 0 / not faulted rather than failing.
  uptime_ms, reset_reason, boot_id = struct.unpack('<III', payload[24:36])
  crash_present = payload[36]
  geometry_generation = struct.unpack('<I', payload[37:41])[0]
  faulted = payload[41]
  return {
    'firmware_version': fw_ver,
    'protocol_version': proto_ver,
    'outputs': outputs,
    'leds_per_strip': leds_per_strip,
    'color_order': color_order,
    'uptime_ms': uptime_ms,
    'reset_reason': reset_reason,
    'reset_reason_text': decode_reset_reason(reset_reason),
    'boot_id': boot_id,
    'crash_present': bool(crash_present),
    'geometry_generation': geometry_generation,
    'faulted': bool(faulted),
  }


def parse_config_ack_payload(payload: bytes) -> Optional[int]:
  """CONFIG_ACK payload = uint32 geometry_generation of the committed geometry.

  Pre-1.3.0 firmware sent an empty ACK; None keeps that distinguishable from 0.
  """
  if len(payload) < 4:
    return None
  return struct.unpack('<I', payload[:4])[0]


def decode_reset_reason(raw: int) -> str:
  """Name every set SRC_SRSR bit, '+'-joined; undefined bits become unknown(0x..)."""
  names = [name for bit, name in SRC_SRSR_BITS.items() if raw & (1 << bit)]
  known_mask = sum(1 << bit for bit in SRC_SRSR_BITS)
  unknown = raw & ~known_mask & 0xFFFFFFFF
  if unknown:
    names.append(f'unknown(0x{unknown:x})')
  return '+'.join(names) if names else 'none'


def decode_stall_bits(raw: int) -> list[str]:
  names = [name for bit, name in STALL_BIT_NAMES.items() if raw & (1 << bit)]
  unknown = raw & ~sum(1 << bit for bit in STALL_BIT_NAMES) & 0xFFFFFFFF
  if unknown:
    names.append(f'unknown(0x{unknown:x})')
  return names


def parse_diag_payload(payload: bytes) -> Optional[dict]:
  """Parse a DIAG payload (DIAG_FIELDS x uint32 LE) into a named dict.

  Adds decoded helpers (`*_names`, `last_op_name`, `reset_reason_text`) so
  consumers never re-implement the bit tables.
  """
  if len(payload) < DIAG_PAYLOAD_SIZE:
    return None
  values = struct.unpack(DIAG_STRUCT_FMT, payload[:DIAG_PAYLOAD_SIZE])
  diag = dict(zip(DIAG_FIELD_NAMES, values))
  diag['stall_active_names'] = decode_stall_bits(diag['stall_active'])
  diag['stall_latched_names'] = decode_stall_bits(diag['stall_latched'])
  diag['last_op_name'] = LAST_OP_NAMES.get(diag['last_op'], f"unknown({diag['last_op']})")
  diag['reset_reason_text'] = decode_reset_reason(diag['reset_reason'])
  return diag


# --- CRASH record (core arm_fault_info_struct + extension) ---

CFSR_BITS = {
  # MemManage (MMFSR)
  0: 'IACCVIOL', 1: 'DACCVIOL', 3: 'MUNSTKERR', 4: 'MSTKERR', 5: 'MLSPERR', 7: 'MMARVALID',
  # BusFault (BFSR)
  8: 'IBUSERR', 9: 'PRECISERR', 10: 'IMPRECISERR', 11: 'UNSTKERR', 12: 'STKERR',
  13: 'LSPERR', 15: 'BFARVALID',
  # UsageFault (UFSR)
  16: 'UNDEFINSTR', 17: 'INVSTATE', 18: 'INVPC', 19: 'NOCP', 24: 'UNALIGNED', 25: 'DIVBYZERO',
}
HFSR_BITS = {1: 'VECTTBL', 30: 'FORCED', 31: 'DEBUGEVT'}
IPSR_NAMES = {
  0: 'Thread', 2: 'NMI', 3: 'HardFault', 4: 'MemManage', 5: 'BusFault', 6: 'UsageFault',
  11: 'SVCall', 12: 'DebugMonitor', 14: 'PendSV', 15: 'SysTick',
}


def _named_bits(raw: int, table: dict) -> list[str]:
  return [name for bit, name in table.items() if raw & (1 << bit)]


def core_fault_record_crc(record: bytes) -> int:
  """CRC the Teensy core computes over the first 10 words of arm_fault_info_struct.

  Mirrors startup.c / CrashReport.cpp::isvalid: init 0xFFFFFFFF, each word xor'd
  in whole, 32 reflected shifts, no final inversion (so it is NOT zlib.crc32).
  """
  crc = 0xFFFFFFFF
  for (word,) in struct.iter_unpack('<I', record[:CRASH_CORE_SIZE - 4]):
    crc ^= word
    for _ in range(32):
      crc = (crc >> 1) ^ (0xEDB88320 if crc & 1 else 0)
  return crc


def _ipsr_name(ipsr: int) -> str:
  exc = ipsr & 0x1FF
  if exc in IPSR_NAMES:
    return IPSR_NAMES[exc]
  if exc >= 16:
    return f'IRQ{exc - 16}'
  return f'exception({exc})'


def parse_crash_payload(payload: bytes) -> Optional[dict]:
  """Parse a 96-byte CRASH payload into decoded fields plus a human `text`.

  valid=0 means the firmware had no CRC-valid record; the rest is zeros.
  """
  if len(payload) < CRASH_PAYLOAD_SIZE:
    return None
  valid, source, src_gpr5, src_srsr = struct.unpack('<IIII', payload[:16])
  core_raw = payload[16:16 + CRASH_CORE_SIZE]
  core_vals = struct.unpack(CRASH_CORE_FMT, core_raw)
  core = dict(zip(
    ('len', 'ipsr', 'cfsr', 'hfsr', 'mmfar', 'bfar', 'ret', 'xpsr', 'temp', 'time', 'crc'),
    core_vals,
  ))
  ext_vals = struct.unpack(CRASH_EXT_FMT, payload[16 + CRASH_CORE_SIZE:CRASH_PAYLOAD_SIZE])
  ext = dict(zip(('r0', 'r1', 'r2', 'r3', 'r12', 'lr', 'pc', 'xpsr', 'exc_return'), ext_vals))

  result = {
    'valid': bool(valid),
    'source': source,
    'source_name': CRASH_SOURCE_NAMES.get(source, f'unknown({source})'),
    'src_gpr5': src_gpr5,
    'src_srsr': src_srsr,
    'reset_reason_text': decode_reset_reason(src_srsr),
    'core': core,
    'ext': ext,
    'crc_ok': bool(valid) and core_fault_record_crc(core_raw) == core['crc'],
    'exception': _ipsr_name(core['ipsr']),
    'cfsr_names': _named_bits(core['cfsr'], CFSR_BITS),
    'hfsr_names': _named_bits(core['hfsr'], HFSR_BITS),
    # EXC_RETURN bit 3: 1 = thread mode (main loop), 0 = handler mode (inside an ISR)
    'faulted_in_thread_mode': bool(ext['exc_return'] & 0x8) if valid else None,
  }
  if not valid:
    result['text'] = 'no crash record'
    return result
  lines = [
    f"{result['exception']} ({result['source_name']}) at pc=0x{ext['pc']:08x} "
    f"lr=0x{ext['lr']:08x} xpsr=0x{ext['xpsr']:08x} "
    f"{'thread' if result['faulted_in_thread_mode'] else 'handler'} mode",
    f"CFSR=0x{core['cfsr']:08x} [{' '.join(result['cfsr_names']) or '-'}] "
    f"HFSR=0x{core['hfsr']:08x} [{' '.join(result['hfsr_names']) or '-'}]",
  ]
  if core['cfsr'] & (1 << 7):
    lines.append(f"MMFAR=0x{core['mmfar']:08x}")
  if core['cfsr'] & (1 << 15):
    lines.append(f"BFAR=0x{core['bfar']:08x}")
  lines.append(
    f"r0-r3={ext['r0']:08x} {ext['r1']:08x} {ext['r2']:08x} {ext['r3']:08x} "
    f"r12={ext['r12']:08x} exc_return=0x{ext['exc_return']:08x}",
  )
  lines.append(
    f"temp={core['temp']:.1f}C time={core['time']} reset={result['reset_reason_text']} "
    f"crc={'ok' if result['crc_ok'] else 'BAD'}",
  )
  result['text'] = '\n'.join(lines)
  return result


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


def parse_pots_payload(payload: bytes) -> Optional[tuple[int, int, int]]:
  """Parse POTS payload from Teensy: (brightness, menu, pattern) raw ADC values."""
  if len(payload) < POTS_PAYLOAD_SIZE:
    return None
  return struct.unpack(POTS_STRUCT_FMT, payload[:POTS_PAYLOAD_SIZE])
