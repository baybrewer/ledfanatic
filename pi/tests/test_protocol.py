"""Tests for the binary protocol."""

import re
import struct
import zlib
from pathlib import Path
import pytest
from app.models.protocol import (
  build_packet, verify_packet, PacketType,
  cobs_encode, cobs_decode, frame_packet,
  build_hello_payload, build_frame_payload, build_blackout_payload,
  parse_caps_payload, parse_stats_payload, parse_pots_payload,
  MAGIC, PROTOCOL_VERSION, HEADER_SIZE, CRC_SIZE,
  STATS_PAYLOAD_SIZE, STATS_STRUCT_FMT, POTS_PAYLOAD_SIZE,
)


class TestPacketBuildVerify:
  def test_round_trip_empty(self):
    pkt = build_packet(PacketType.PING)
    result = verify_packet(pkt)
    assert result is not None
    header, payload = result
    assert header.packet_type == PacketType.PING
    assert len(payload) == 0

  def test_round_trip_with_payload(self):
    data = b'\x01\x02\x03\x04'
    pkt = build_packet(PacketType.CONFIG, data)
    result = verify_packet(pkt)
    assert result is not None
    header, payload = result
    assert header.packet_type == PacketType.CONFIG
    assert payload == data

  def test_frame_id_preserved(self):
    pkt = build_packet(PacketType.FRAME, b'test', frame_id=12345)
    result = verify_packet(pkt)
    assert result is not None
    assert result[0].frame_id == 12345

  def test_bad_crc_rejected(self):
    pkt = bytearray(build_packet(PacketType.PING))
    pkt[-1] ^= 0xFF  # corrupt CRC
    assert verify_packet(bytes(pkt)) is None

  def test_bad_magic_rejected(self):
    pkt = bytearray(build_packet(PacketType.PING))
    pkt[0] = ord('X')
    assert verify_packet(bytes(pkt)) is None

  def test_truncated_rejected(self):
    pkt = build_packet(PacketType.PING)
    assert verify_packet(pkt[:10]) is None

  def test_large_payload(self):
    data = bytes(range(256)) * 20  # 5120 bytes
    pkt = build_packet(PacketType.FRAME, data)
    result = verify_packet(pkt)
    assert result is not None
    assert result[1] == data


class TestCOBS:
  def test_round_trip_simple(self):
    data = b'hello'
    encoded = cobs_encode(data)
    decoded = cobs_decode(encoded)
    assert decoded == data

  def test_round_trip_with_zeros(self):
    data = b'\x00\x01\x00\x02\x00'
    encoded = cobs_encode(data)
    assert b'\x00' not in encoded
    decoded = cobs_decode(encoded)
    assert decoded == data

  def test_no_zeros_in_encoded(self):
    data = b'test\x00data\x00end'
    encoded = cobs_encode(data)
    assert b'\x00' not in encoded

  def test_empty_data(self):
    encoded = cobs_encode(b'')
    decoded = cobs_decode(encoded)
    # Empty input produces empty output
    assert decoded is not None

  def test_all_zeros(self):
    data = b'\x00' * 10
    encoded = cobs_encode(data)
    assert b'\x00' not in encoded
    decoded = cobs_decode(encoded)
    assert decoded == data


class TestFramePacket:
  def test_has_delimiter(self):
    pkt = build_packet(PacketType.PING)
    framed = frame_packet(pkt)
    assert framed[-1:] == b'\x00'

  def test_no_internal_zeros(self):
    pkt = build_packet(PacketType.PING)
    framed = frame_packet(pkt)
    # No zeros except the final delimiter
    assert b'\x00' not in framed[:-1]


class TestHelloPayload:
  def test_correct_size(self):
    payload = build_hello_payload()
    assert len(payload) == 48  # 32 + 16

  def test_custom_name(self):
    payload = build_hello_payload("test-app", "2.0.0")
    assert b'test-app' in payload


class TestFramePayload:
  def test_format(self):
    pixels = bytes(5 * 344 * 3)
    payload = build_frame_payload(5, 344, pixels)
    assert payload[0] == 5  # channels
    assert struct.unpack('<H', payload[1:3])[0] == 344  # leds per channel
    assert len(payload) == 3 + len(pixels)


class TestCapsPayload:
  def test_parse_valid(self):
    payload = bytearray(56)
    payload[:7] = b'1.0.0\x00\x00'
    payload[16] = 1  # proto version
    payload[17] = 5  # outputs
    struct.pack_into('<H', payload, 18, 344)
    payload[20:23] = b'GRB'

    result = parse_caps_payload(bytes(payload))
    assert result is not None
    assert result['firmware_version'] == '1.0.0'
    assert result['protocol_version'] == 1
    assert result['outputs'] == 5
    assert result['leds_per_strip'] == 344
    assert result['color_order'] == 'GRB'

  def test_too_short_rejected(self):
    assert parse_caps_payload(b'\x00' * 10) is None


class TestBlackoutPayload:
  def test_blackout_on(self):
    payload = build_blackout_payload(True)
    assert payload == b'\x01'

  def test_blackout_off(self):
    payload = build_blackout_payload(False)
    assert payload == b'\x00'

  def test_blackout_packet_round_trip(self):
    pkt = build_packet(PacketType.BLACKOUT, build_blackout_payload(True))
    result = verify_packet(pkt)
    assert result is not None
    header, payload = result
    assert header.packet_type == PacketType.BLACKOUT
    assert payload == b'\x01'


class TestStatsPayload:
  def test_parse_valid_28_bytes(self):
    values = (100000, 5000, 4990, 2, 1, 8, 60)
    payload = struct.pack(STATS_STRUCT_FMT, *values)
    assert len(payload) == STATS_PAYLOAD_SIZE

    result = parse_stats_payload(payload)
    assert result is not None
    assert result['uptime_ms'] == 100000
    assert result['frames_received'] == 5000
    assert result['frames_applied'] == 4990
    assert result['bad_crc'] == 2
    assert result['bad_frame'] == 1
    assert result['dropped_pending'] == 8
    assert result['output_fps'] == 60

  def test_parse_too_short(self):
    assert parse_stats_payload(b'\x00' * 20) is None

  def test_parse_extra_bytes_ok(self):
    """Extra bytes beyond 28 are ignored (forward compatibility)."""
    values = (1, 2, 3, 4, 5, 6, 7)
    payload = struct.pack(STATS_STRUCT_FMT, *values) + b'\xff' * 10
    result = parse_stats_payload(payload)
    assert result is not None
    assert result['uptime_ms'] == 1


# --- COBS golden vectors from the COBS specification ---
GOLDEN_VECTORS = [
  # (unencoded, encoded)
  (b'', b'\x01'),
  (b'\x00', b'\x01\x01'),
  (b'\x00\x00', b'\x01\x01\x01'),
  (b'\x00\x00\x00', b'\x01\x01\x01\x01'),
  (b'\x11\x22\x00\x33', b'\x03\x11\x22\x02\x33'),
  (b'\x11\x22\x33\x44', b'\x05\x11\x22\x33\x44'),
  (b'\x11\x00\x00\x00', b'\x02\x11\x01\x01\x01'),
  # Packet with flags=0x0000 (real protocol scenario)
  (b'PILL\x01\x10\x00\x00\x01\x00\x00\x00',
   None),  # just verify round-trip, don't hardcode encoding
  # Long non-zero runs (COBS 0xFF continuation blocks)
  (b'\xFF' * 254, None),  # exactly 254 — single 0xFF block
  (b'\xFF' * 255, None),  # 255 — needs 0xFF + 0x02 blocks
  (b'\xFF' * 300, None),  # 300 — needs 0xFF + continuation
  (b'\xFF' * 508, None),  # 2 x 254 — two 0xFF blocks
  (b'\xFF' * 1000, None), # stress test
]


class TestCOBSGoldenVectors:
  @pytest.mark.parametrize("raw,expected_encoded", [v for v in GOLDEN_VECTORS if v[1] is not None])
  def test_encode_matches_spec(self, raw, expected_encoded):
    assert cobs_encode(raw) == expected_encoded

  @pytest.mark.parametrize("raw,expected_encoded", [v for v in GOLDEN_VECTORS if v[1] is not None])
  def test_decode_matches_spec(self, raw, expected_encoded):
    assert cobs_decode(expected_encoded) == raw

  @pytest.mark.parametrize("raw,_", GOLDEN_VECTORS)
  def test_round_trip(self, raw, _):
    encoded = cobs_encode(raw)
    assert b'\x00' not in encoded
    decoded = cobs_decode(encoded)
    assert decoded == raw

  def test_packet_with_zero_flags(self):
    """Real protocol packet: flags=0x0000 creates consecutive zeros."""
    # Build a PING packet (has flags=0x0000)
    pkt = build_packet(PacketType.PING)
    # Verify it round-trips through COBS
    framed = frame_packet(pkt)
    # Remove delimiter
    encoded = framed[:-1]
    decoded = cobs_decode(encoded)
    assert decoded is not None
    result = verify_packet(decoded)
    assert result is not None
    assert result[0].packet_type == PacketType.PING


class TestProtocolContracts:
  """Lock the exact protocol contracts that must not change without
  coordinated updates across Pi code, Teensy code, tests, and docs."""

  def test_header_size_is_24_bytes(self):
    assert HEADER_SIZE == 24

  def test_crc_size_is_4_bytes(self):
    assert CRC_SIZE == 4

  def test_magic_is_pill(self):
    assert MAGIC == b'PILL'

  def test_protocol_version_is_1(self):
    from app.models.protocol import PROTOCOL_VERSION
    assert PROTOCOL_VERSION == 1

  def test_header_struct_format(self):
    """Header must pack as: magic(4) + ver(1) + type(1) + flags(2) +
    frame_id(4) + timestamp_us(8) + payload_len(4) = 24 bytes."""
    from app.models.protocol import pack_header, PacketHeader
    header = PacketHeader(
      version=1, packet_type=0x10, flags=0,
      frame_id=1, timestamp_us=0, payload_len=0,
    )
    packed = pack_header(header)
    assert len(packed) == 24
    # Verify magic at start
    assert packed[:4] == b'PILL'
    # Verify version byte
    assert packed[4] == 1
    # Verify type byte
    assert packed[5] == 0x10

  def test_frame_payload_has_3_byte_meta(self):
    """FRAME payload starts with channels(1) + leds_per_channel(2)."""
    pixels = b'\x00' * 30
    payload = build_frame_payload(5, 344, pixels)
    # First byte: channels
    assert payload[0] == 5
    # Next 2 bytes: leds_per_channel (little-endian uint16)
    lpc = struct.unpack('<H', payload[1:3])[0]
    assert lpc == 344
    # Rest is pixel data
    assert payload[3:] == pixels

  def test_hello_yields_caps_packet_types(self):
    """HELLO type is 0x01, CAPS type is 0x02."""
    assert PacketType.HELLO == 0x01
    assert PacketType.CAPS == 0x02

  def test_ping_yields_stats_packet_types(self):
    """PING type is 0x20, STATS type is 0x30. PONG (0x21) exists but is unused."""
    assert PacketType.PING == 0x20
    assert PacketType.STATS == 0x30
    assert PacketType.PONG == 0x21  # reserved/unused

  def test_caps_payload_size_is_56(self):
    from app.models.protocol import CAPS_PAYLOAD_SIZE
    assert CAPS_PAYLOAD_SIZE == 56

  def test_stats_payload_size_is_28(self):
    assert STATS_PAYLOAD_SIZE == 28

  def test_stats_struct_is_7_uint32(self):
    """STATS payload: 7 x uint32 little-endian."""
    assert STATS_STRUCT_FMT == '<IIIIIII'
    assert struct.calcsize(STATS_STRUCT_FMT) == 28

  def test_blackout_semantics_explicit(self):
    """Blackout is explicit: 0x01=on, 0x00=off, never toggle."""
    on = build_blackout_payload(True)
    off = build_blackout_payload(False)
    assert on == b'\x01'
    assert off == b'\x00'

  def test_test_pattern_clear_is_0xff(self):
    from app.models.protocol import TestPattern
    assert TestPattern.CLEAR == 0xFF

  def test_hello_payload_size_is_48(self):
    from app.models.protocol import HELLO_PAYLOAD_SIZE
    assert HELLO_PAYLOAD_SIZE == 48
    payload = build_hello_payload()
    assert len(payload) == HELLO_PAYLOAD_SIZE

  def test_all_packet_types_defined(self):
    """All shipped packet types exist with correct values."""
    assert PacketType.HELLO == 0x01
    assert PacketType.CAPS == 0x02
    assert PacketType.CONFIG == 0x03
    assert PacketType.FRAME == 0x10
    assert PacketType.PING == 0x20
    assert PacketType.PONG == 0x21
    assert PacketType.STATS == 0x30
    assert PacketType.TEST_PATTERN == 0x40
    assert PacketType.BLACKOUT == 0x41
    assert PacketType.BRIGHTNESS == 0x42
    assert PacketType.REBOOT_TO_BOOTLOADER == 0xFF

  def test_caps_payload_field_offsets(self):
    """Verify CAPS payload field positions match firmware."""
    payload = bytearray(56)
    payload[:7] = b'1.0.0\x00\x00'  # firmware_version at offset 0..15
    payload[16] = 1                  # protocol_version at offset 16
    payload[17] = 5                  # outputs at offset 17
    struct.pack_into('<H', payload, 18, 344)  # leds_per_strip at offset 18..19
    payload[20:23] = b'GRB'          # color_order at offset 20..23

    caps = parse_caps_payload(bytes(payload))
    assert caps['firmware_version'] == '1.0.0'
    assert caps['protocol_version'] == 1
    assert caps['outputs'] == 5
    assert caps['leds_per_strip'] == 344
    assert caps['color_order'] == 'GRB'

  def test_stats_payload_field_order(self):
    """STATS fields: uptime_ms, frames_received, frames_applied,
    bad_crc, bad_frame, dropped_pending, output_fps."""
    values = (99, 88, 77, 66, 55, 44, 33)
    payload = struct.pack(STATS_STRUCT_FMT, *values)
    result = parse_stats_payload(payload)
    assert result['uptime_ms'] == 99
    assert result['frames_received'] == 88
    assert result['frames_applied'] == 77
    assert result['bad_crc'] == 66
    assert result['bad_frame'] == 55
    assert result['dropped_pending'] == 44
    assert result['output_fps'] == 33


class TestCOBSLongRuns:
  """Test COBS with long non-zero runs (>254 bytes)."""

  def test_254_non_zero_bytes(self):
    data = b'\xFF' * 254
    encoded = cobs_encode(data)
    assert b'\x00' not in encoded
    # Should be 0xFF code + 254 data bytes = 255 bytes
    assert len(encoded) == 255
    assert encoded[0] == 0xFF
    decoded = cobs_decode(encoded)
    assert decoded == data

  def test_255_non_zero_bytes(self):
    data = b'\xFF' * 255
    encoded = cobs_encode(data)
    assert b'\x00' not in encoded
    decoded = cobs_decode(encoded)
    assert decoded == data

  def test_300_non_zero_bytes(self):
    data = b'\xFF' * 300
    encoded = cobs_encode(data)
    assert b'\x00' not in encoded
    decoded = cobs_decode(encoded)
    assert decoded == data
    assert len(decoded) == 300

  def test_1000_non_zero_bytes(self):
    data = b'\xFF' * 1000
    encoded = cobs_encode(data)
    assert b'\x00' not in encoded
    decoded = cobs_decode(encoded)
    assert decoded == data
    assert len(decoded) == 1000

  def test_full_frame_packet(self):
    """Full 5x344 frame of bright pixels must survive COBS round-trip."""
    pixels = b'\xFF' * (5 * 344 * 3)
    pkt = build_packet(PacketType.FRAME, struct.pack('<BH', 5, 344) + pixels)
    framed = frame_packet(pkt)
    encoded = framed[:-1]  # remove delimiter
    decoded = cobs_decode(encoded)
    assert decoded is not None
    assert len(decoded) == len(pkt)
    result = verify_packet(decoded)
    assert result is not None
    header, payload = result
    assert header.packet_type == PacketType.FRAME

  def test_mixed_zeros_and_long_runs(self):
    """Data with zeros interspersed in long non-zero regions."""
    data = b'\xFF' * 300 + b'\x00' + b'\xAA' * 200 + b'\x00' + b'\x55' * 100
    encoded = cobs_encode(data)
    assert b'\x00' not in encoded
    decoded = cobs_decode(encoded)
    assert decoded == data

  def test_random_payloads(self):
    """Random payloads of various sizes round-trip correctly."""
    import random
    rng = random.Random(42)
    for size in [1, 10, 100, 253, 254, 255, 256, 500, 1000, 5000]:
      data = bytes(rng.randint(0, 255) for _ in range(size))
      encoded = cobs_encode(data)
      assert b'\x00' not in encoded, f"Zero in encoded for size {size}"
      decoded = cobs_decode(encoded)
      assert decoded == data, f"Round-trip failed for size {size}"


class TestCRC32CrossLanguage:
  """Verify Python CRC matches what Teensy should compute."""

  def test_ping_packet_crc(self):
    """PING packet CRC must be deterministic and known."""
    pkt = build_packet(PacketType.PING)
    # Extract stored CRC
    stored_crc = struct.unpack('<I', pkt[-4:])[0]
    # Verify it matches zlib
    computed = zlib.crc32(pkt[:-4]) & 0xFFFFFFFF
    assert stored_crc == computed

  def test_hello_packet_crc(self):
    pkt = build_packet(PacketType.HELLO, build_hello_payload())
    stored_crc = struct.unpack('<I', pkt[-4:])[0]
    computed = zlib.crc32(pkt[:-4]) & 0xFFFFFFFF
    assert stored_crc == computed

  def test_frame_packet_crc(self):
    pixels = b'\x80' * (5 * 344 * 3)
    pkt = build_packet(PacketType.FRAME, struct.pack('<BH', 5, 344) + pixels)
    stored_crc = struct.unpack('<I', pkt[-4:])[0]
    computed = zlib.crc32(pkt[:-4]) & 0xFFFFFFFF
    assert stored_crc == computed

  def test_blackout_packet_crc(self):
    pkt = build_packet(PacketType.BLACKOUT, build_blackout_payload(True))
    stored_crc = struct.unpack('<I', pkt[-4:])[0]
    computed = zlib.crc32(pkt[:-4]) & 0xFFFFFFFF
    assert stored_crc == computed


class TestPotsPayload:
  def test_parse_valid(self):
    payload = struct.pack('<HHH', 0, 512, 1023)
    assert parse_pots_payload(payload) == (0, 512, 1023)

  def test_too_short_rejected(self):
    assert parse_pots_payload(b'\x00' * 5) is None

  def test_extra_bytes_ok(self):
    payload = struct.pack('<HHH', 1, 2, 3) + b'\xAA\xBB'
    assert parse_pots_payload(payload) == (1, 2, 3)

  def test_packet_round_trip(self):
    pkt = build_packet(PacketType.POTS, struct.pack('<HHH', 10, 20, 30))
    framed = frame_packet(pkt)
    assert framed.endswith(b'\x00')
    decoded = cobs_decode(framed[:-1])
    header, payload = verify_packet(decoded)
    assert header.packet_type == PacketType.POTS
    assert len(payload) == POTS_PAYLOAD_SIZE
    assert parse_pots_payload(payload) == (10, 20, 30)


def _firmware_cobs_decode(encoded: bytes) -> bytes:
  """Literal port of COBSDecoder.feed in teensy/firmware/include/protocol.h."""
  out = bytearray()
  remaining = last_code = 0
  had_block = False
  for b in encoded:
    if remaining == 0:
      if had_block and last_code < 255:
        out.append(0)
      last_code, remaining, had_block = b, b - 1, True
    else:
      out.append(b)
      remaining -= 1
  return bytes(out)


def test_cobs_full_block_followed_by_zero_keeps_the_zero():
  # Regression: a 254-byte non-zero run then 0x00 lost the zero (frames failed CRC)
  for run in (253, 254, 255, 508, 509):
    for tail in (b'', b'\x00', b'\x00\x05', b'\x00\x00'):
      data = b'\x07' * run + tail
      encoded = cobs_encode(data)
      assert 0 not in encoded
      assert cobs_decode(encoded) == data, (run, tail)
      assert _firmware_cobs_decode(encoded) == data, (run, tail)


def test_cobs_random_frames_roundtrip_through_firmware_decoder():
  import random
  rng = random.Random(0)
  for _ in range(300):
    data = bytes(rng.choice((0, rng.randrange(1, 256))) if rng.random() < 0.01
                 else rng.randrange(1, 256) for _ in range(4980))
    assert _firmware_cobs_decode(cobs_encode(data)) == data


# --- Firmware 1.3.0 additions: reset reason, CAPS extension, CONFIG_ACK, DIAG, CRASH ---

from app.models.protocol import (  # noqa: E402
  DIAG_FIELDS, DIAG_FIELD_NAMES, DIAG_PAYLOAD_SIZE, CRASH_PAYLOAD_SIZE, CRASH_CORE_FMT,
  CRASH_EXT_FMT, SRC_SRSR_BITS, STALL_FAULTED,
  decode_reset_reason, parse_config_ack_payload, parse_diag_payload, parse_crash_payload,
  core_fault_record_crc,
)


class TestResetReason:
  @pytest.mark.parametrize('bit,name', list(SRC_SRSR_BITS.items()))
  def test_every_bit_named(self, bit, name):
    assert decode_reset_reason(1 << bit) == name

  def test_all_nine_bits_present(self):
    assert set(SRC_SRSR_BITS.values()) == {
      'IPP_RESET_B', 'LOCKUP_SYSRESETREQ', 'CSU_RESET_B', 'IPP_USER_RESET_B', 'WDOG_RST_B',
      'JTAG_RST_B', 'JTAG_SW_RST', 'WDOG3_RST_B', 'TEMPSENSE_RST_B',
    }

  def test_combination_joined_in_bit_order(self):
    assert decode_reset_reason(0x3) == 'IPP_RESET_B+LOCKUP_SYSRESETREQ'
    assert decode_reset_reason((1 << 4) | (1 << 8)) == 'WDOG_RST_B+TEMPSENSE_RST_B'

  def test_unknown_bits_reported(self):
    assert decode_reset_reason(1 << 12) == 'unknown(0x1000)'
    assert decode_reset_reason((1 << 1) | (1 << 20)) == 'LOCKUP_SYSRESETREQ+unknown(0x100000)'

  def test_zero(self):
    assert decode_reset_reason(0) == 'none'

  def test_packet_type_values(self):
    assert PacketType.DIAG == 0x32 and PacketType.CRASH == 0x33


class TestCapsExtension:
  def _caps(self, **ext):
    payload = bytearray(56)
    payload[:5] = b'1.3.0'
    payload[16] = 1
    payload[17] = 5
    struct.pack_into('<H', payload, 18, 344)
    payload[20:23] = b'BGR'
    struct.pack_into('<III', payload, 24, ext.get('uptime_ms', 0), ext.get('reset_reason', 0),
                     ext.get('boot_id', 0))
    payload[36] = ext.get('crash_present', 0)
    struct.pack_into('<I', payload, 37, ext.get('geometry_generation', 0))
    payload[41] = ext.get('faulted', 0)
    return bytes(payload)

  def test_extension_fields(self):
    caps = parse_caps_payload(self._caps(uptime_ms=12345, reset_reason=0x2, boot_id=0xABCDE,
                                         crash_present=1, geometry_generation=7, faulted=1))
    assert caps['uptime_ms'] == 12345
    assert caps['reset_reason'] == 0x2
    assert caps['reset_reason_text'] == 'LOCKUP_SYSRESETREQ'
    assert caps['boot_id'] == 0xABCDE
    assert caps['crash_present'] is True
    assert caps['geometry_generation'] == 7
    assert caps['faulted'] is True
    assert caps['firmware_version'] == '1.3.0' and caps['color_order'] == 'BGR'

  def test_old_firmware_zero_filled(self):
    caps = parse_caps_payload(self._caps())
    assert caps['boot_id'] == 0 and caps['uptime_ms'] == 0
    assert caps['crash_present'] is False and caps['faulted'] is False
    assert caps['geometry_generation'] == 0


class TestConfigAck:
  def test_generation(self):
    assert parse_config_ack_payload(struct.pack('<I', 42)) == 42

  def test_empty_legacy_ack(self):
    assert parse_config_ack_payload(b'') is None

  def test_zero_is_not_none(self):
    assert parse_config_ack_payload(struct.pack('<I', 0)) == 0


class TestDiagPayload:
  def test_layout_contract(self):
    assert DIAG_FIELDS == 45 and DIAG_PAYLOAD_SIZE == 180
    assert DIAG_FIELD_NAMES[0] == 'uptime_ms'
    assert DIAG_FIELD_NAMES[25] == 'stall_active' and DIAG_FIELD_NAMES[26] == 'stall_latched'
    assert DIAG_FIELD_NAMES[35] == 'last_op'
    assert DIAG_FIELD_NAMES[43] == 'fault_count'

  def test_parse_all_fields_in_order(self):
    values = list(range(100, 100 + DIAG_FIELDS))
    diag = parse_diag_payload(struct.pack(f'<{DIAG_FIELDS}I', *values))
    for i, name in enumerate(DIAG_FIELD_NAMES):
      assert diag[name] == 100 + i

  def test_decoded_helpers(self):
    values = [0] * DIAG_FIELDS
    values[1] = 1 << 4                     # reset_reason WDOG
    values[25] = 0b101                     # stall_active RX_NO_PACKET + TX_DROPPED
    values[26] = STALL_FAULTED | 0b1       # stall_latched
    values[35] = 5                         # last_op show
    diag = parse_diag_payload(struct.pack(f'<{DIAG_FIELDS}I', *values))
    assert diag['stall_active_names'] == ['RX_NO_PACKET', 'TX_DROPPED']
    assert diag['stall_latched_names'] == ['RX_NO_PACKET', 'FAULTED']
    assert diag['last_op_name'] == 'show'
    assert diag['reset_reason_text'] == 'WDOG_RST_B'

  def test_short_rejected(self):
    # Anything below the oldest pushing firmware's 44 fields is garbage; a
    # trailing partial word on a longer payload is ignored, not fatal.
    assert parse_diag_payload(b'\x00' * (44 * 4 - 1)) is None
    assert parse_diag_payload(b'\x00' * (DIAG_PAYLOAD_SIZE - 1)) is not None


def build_crash_fixture(valid=1, source=2, src_srsr=0x2, ipsr=3, cfsr=(1 << 9) | (1 << 15),
                        hfsr=1 << 30, bfar=0xFFFFFFF0, temp=41.5, time_=1700000000,
                        pc=0x60001234, lr=0x60000ABD, exc_return=0xFFFFFFF9, corrupt_crc=False):
  """CRASH payload laid out exactly like the firmware: 16 B prefix, the core's
  44-byte arm_fault_info_struct (len, ipsr, cfsr, hfsr, mmfar, bfar, ret, xpsr,
  float temp, time, crc) with the core's own CRC, then the 36-byte extension."""
  core_no_crc = struct.pack('<IIIIIIIIfI', 11, ipsr, cfsr, hfsr, 0, bfar, 0x60001000,
                            0x21000000, temp, time_)
  crc = core_fault_record_crc(core_no_crc)
  if corrupt_crc:
    crc ^= 1
  core = core_no_crc + struct.pack('<I', crc)
  assert len(core) == 44 and struct.calcsize(CRASH_CORE_FMT) == 44
  ext = struct.pack(CRASH_EXT_FMT, 1, 2, 3, 4, 12, lr, pc, 0x21000000, exc_return)
  assert len(ext) == 36
  payload = struct.pack('<IIII', valid, source, 0, src_srsr) + core + ext
  assert len(payload) == CRASH_PAYLOAD_SIZE
  return payload


class TestCrashPayload:
  def test_core_crc_matches_teensy_algorithm(self):
    # Reference: the core xors whole words into a reflected CRC32 with no final
    # inversion, which equals zlib.crc32 over the same bytes inverted
    data = struct.pack('<10I', *range(1, 11))
    assert core_fault_record_crc(data) == (zlib.crc32(data) ^ 0xFFFFFFFF)

  def test_decode_valid_record(self):
    crash = parse_crash_payload(build_crash_fixture())
    assert crash['valid'] is True and crash['crc_ok'] is True
    assert crash['source'] == 2 and crash['source_name'] == 'previous_boot'
    assert crash['exception'] == 'HardFault'
    assert crash['cfsr_names'] == ['PRECISERR', 'BFARVALID']
    assert crash['hfsr_names'] == ['FORCED']
    assert crash['core']['bfar'] == 0xFFFFFFF0
    assert crash['core']['temp'] == pytest.approx(41.5)
    assert crash['core']['time'] == 1700000000
    assert crash['ext']['pc'] == 0x60001234 and crash['ext']['lr'] == 0x60000ABD
    assert crash['ext']['exc_return'] == 0xFFFFFFF9
    assert crash['faulted_in_thread_mode'] is True
    assert crash['reset_reason_text'] == 'LOCKUP_SYSRESETREQ'
    assert 'HardFault' in crash['text'] and 'pc=0x60001234' in crash['text']
    assert 'BFAR=0xfffffff0' in crash['text'] and 'PRECISERR' in crash['text']

  def test_handler_mode_from_exc_return(self):
    crash = parse_crash_payload(build_crash_fixture(exc_return=0xFFFFFFF1))
    assert crash['faulted_in_thread_mode'] is False
    assert 'handler mode' in crash['text']

  def test_bad_crc_flagged_not_rejected(self):
    crash = parse_crash_payload(build_crash_fixture(corrupt_crc=True))
    assert crash['valid'] is True and crash['crc_ok'] is False
    assert 'crc=BAD' in crash['text']

  def test_no_record(self):
    crash = parse_crash_payload(b'\x00' * CRASH_PAYLOAD_SIZE)
    assert crash['valid'] is False and crash['text'] == 'no crash record'
    assert crash['faulted_in_thread_mode'] is None

  def test_irq_name_and_short_payload(self):
    crash = parse_crash_payload(build_crash_fixture(ipsr=16 + 20))
    assert crash['exception'] == 'IRQ20'
    assert parse_crash_payload(b'\x00' * 95) is None


def test_parse_diag_accepts_44_field_payload_from_firmware_1_3_0():
  values = list(range(1, 45))
  diag = parse_diag_payload(struct.pack('<44I', *values))
  assert diag is not None
  assert diag['fault_count'] == 44 and diag['tx_dropped_push'] == 0
  assert parse_diag_payload(struct.pack('<43I', *values[:43])) is None
