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
  parse_caps_payload, parse_stats_payload,
  MAGIC, PROTOCOL_VERSION, HEADER_SIZE, CRC_SIZE,
  STATS_PAYLOAD_SIZE, STATS_STRUCT_FMT,
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


class TestHardwareConstants:
  def test_python_constants_match_yaml(self):
    from app.hardware_constants import STRIPS, LEDS_PER_STRIP, CHANNELS, LEDS_PER_CHANNEL
    assert STRIPS == 10
    assert LEDS_PER_STRIP == 600
    assert CHANNELS == 5
    assert LEDS_PER_CHANNEL == 1200

  def test_teensy_constants_match(self):
    """Verify Teensy config.h values match hardware.yaml."""
    config_path = Path(__file__).parent.parent.parent / 'teensy' / 'firmware' / 'include' / 'config.h'
    if not config_path.exists():
      pytest.skip("Teensy config.h not found")
    content = config_path.read_text()

    from app.hardware_constants import LEDS_PER_CHANNEL, CHANNELS, LEDS_PER_STRIP

    # Extract values from config.h
    lps = int(re.search(r'#define\s+LEDS_PER_STRIP\s+(\d+)', content).group(1))
    active = int(re.search(r'#define\s+ACTIVE_OUTPUTS\s+(\d+)', content).group(1))
    physical = int(re.search(r'#define\s+LEDS_PER_PHYSICAL\s+(\d+)', content).group(1))

    assert lps == LEDS_PER_CHANNEL, f"config.h LEDS_PER_STRIP={lps} != hardware.yaml LEDS_PER_CHANNEL={LEDS_PER_CHANNEL}"
    assert active == CHANNELS, f"config.h ACTIVE_OUTPUTS={active} != hardware.yaml CHANNELS={CHANNELS}"
    assert physical == LEDS_PER_STRIP, f"config.h LEDS_PER_PHYSICAL={physical} != hardware.yaml LEDS_PER_STRIP={LEDS_PER_STRIP}"


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
