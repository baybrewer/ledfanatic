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


class TestHardwareConstants:
  def test_python_constants_match_yaml(self):
    from app.hardware_constants import STRIPS, LEDS_PER_STRIP, CHANNELS, LEDS_PER_CHANNEL
    assert STRIPS == 10
    assert LEDS_PER_STRIP == 172
    assert CHANNELS == 5
    assert LEDS_PER_CHANNEL == 344

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
