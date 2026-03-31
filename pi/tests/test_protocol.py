"""Tests for the binary protocol."""

import struct
import zlib
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
