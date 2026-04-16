"""
USB Serial transport to Teensy.

Handles device discovery, connection, reconnection, handshake,
frame sending, and stats querying.
"""

import asyncio
import logging
import time
import struct
from typing import Optional

import serial
import serial.tools.list_ports

from ..models.protocol import (
  PacketType, build_packet, verify_packet, frame_packet,
  build_hello_payload, build_frame_payload, build_blackout_payload,
  parse_caps_payload, parse_stats_payload,
  cobs_encode, cobs_decode, PROTOCOL_VERSION,
)
from ..hardware_constants import LEDS_PER_CHANNEL

logger = logging.getLogger(__name__)

TEENSY_VID = 0x16C0
TEENSY_PID = 0x0483


class TeensyTransport:
  def __init__(self, reconnect_interval: float = 1.0, handshake_timeout: float = 3.0):
    self.reconnect_interval = reconnect_interval
    self.handshake_timeout = handshake_timeout
    self.serial: Optional[serial.Serial] = None
    self.connected = False
    self.caps: Optional[dict] = None
    self.frame_id = 0
    self._rx_buffer = bytearray()
    self._lock = asyncio.Lock()

    # Stats
    self.frames_sent = 0
    self.send_errors = 0
    self.reconnect_count = 0

  def find_teensy_port(self) -> Optional[str]:
    for port in serial.tools.list_ports.comports():
      if port.vid == TEENSY_VID and port.pid == TEENSY_PID:
        return port.device
      if port.manufacturer and 'teensy' in port.manufacturer.lower():
        return port.device
    return None

  async def connect(self) -> bool:
    port = self.find_teensy_port()
    if not port:
      logger.debug("Teensy not found")
      return False

    try:
      self.serial = serial.Serial(
        port,
        baudrate=115200,
        timeout=0.1,
        write_timeout=1.0,
      )
      self.connected = True
      self._rx_buffer.clear()
      logger.info(f"Connected to Teensy on {port}")

      success = await self._handshake()
      if not success:
        logger.warning("Handshake failed")
        self.disconnect()
        return False

      return True
    except (serial.SerialException, OSError) as e:
      logger.error(f"Connection failed: {e}")
      self.connected = False
      return False

  def disconnect(self):
    if self.serial and self.serial.is_open:
      try:
        self.serial.close()
      except Exception:
        pass
    self.serial = None
    self.connected = False
    self.caps = None

  async def _handshake(self) -> bool:
    hello_payload = build_hello_payload("pillar-pi", "1.0.0")
    packet = build_packet(PacketType.HELLO, hello_payload)
    framed = frame_packet(packet)

    try:
      async with self._lock:
        self.serial.write(framed)
        self.serial.flush()
    except (serial.SerialException, OSError) as e:
      logger.error(f"Failed to send HELLO: {e}")
      return False

    start = time.monotonic()
    while time.monotonic() - start < self.handshake_timeout:
      result = self._read_packet()
      if result:
        header, payload = result
        if header.packet_type == PacketType.CAPS:
          self.caps = parse_caps_payload(payload)
          if self.caps:
            logger.info(f"Teensy caps: {self.caps}")
            return True
      await asyncio.sleep(0.01)

    return False

  def _read_packet(self) -> Optional[tuple]:
    # No lock needed: called only from the same async context (request_stats,
    # _handshake) which already holds or doesn't contend with the write lock.
    if not self.serial or not self.serial.is_open:
      return None

    try:
      available = self.serial.in_waiting
      if available:
        self._rx_buffer.extend(self.serial.read(available))
    except (serial.SerialException, OSError):
      return None

    while b'\x00' in self._rx_buffer:
      idx = self._rx_buffer.index(b'\x00')
      if idx == 0:
        self._rx_buffer.pop(0)
        continue

      encoded = bytes(self._rx_buffer[:idx])
      self._rx_buffer = self._rx_buffer[idx + 1:]

      decoded = cobs_decode(encoded)
      if decoded is None:
        continue

      result = verify_packet(decoded)
      if result:
        return result

    return None

  async def send_frame(self, channel_data: bytes, channels: int = 5,
                       leds_per_channel: int = LEDS_PER_CHANNEL) -> bool:
    if not self.connected or not self.serial:
      return False

    self.frame_id += 1
    timestamp_us = int(time.monotonic() * 1_000_000) & 0xFFFFFFFFFFFFFFFF

    payload = build_frame_payload(channels, leds_per_channel, channel_data)
    packet = build_packet(
      PacketType.FRAME,
      payload,
      frame_id=self.frame_id,
      timestamp_us=timestamp_us,
    )
    framed = frame_packet(packet)

    async with self._lock:
      try:
        await asyncio.to_thread(self.serial.write, framed)
        self.frames_sent += 1
        return True
      except (serial.SerialException, OSError) as e:
        self.send_errors += 1
        logger.error(f"Frame send failed: {e}")
        self.connected = False
        return False

  async def send_command(self, packet_type: int, payload: bytes = b'') -> bool:
    if not self.connected or not self.serial:
      return False

    packet = build_packet(packet_type, payload)
    framed = frame_packet(packet)

    async with self._lock:
      try:
        self.serial.write(framed)
        return True
      except (serial.SerialException, OSError) as e:
        logger.error(f"Command send failed: {e}")
        self.connected = False
        return False

  async def send_blackout(self, enabled: bool) -> bool:
    """Send explicit blackout command: True=on, False=off."""
    return await self.send_command(
      PacketType.BLACKOUT,
      build_blackout_payload(enabled),
    )

  async def send_brightness(self, brightness: float) -> bool:
    val = max(0, min(255, int(brightness * 255)))
    return await self.send_command(PacketType.BRIGHTNESS, struct.pack('<B', val))

  async def send_test_pattern(self, pattern_id: int) -> bool:
    return await self.send_command(PacketType.TEST_PATTERN, struct.pack('<B', pattern_id))

  async def request_stats(self) -> Optional[dict]:
    if not await self.send_command(PacketType.PING):
      return None

    start = time.monotonic()
    while time.monotonic() - start < 0.5:
      result = self._read_packet()
      if result:
        header, payload = result
        if header.packet_type == PacketType.STATS:
          parsed = parse_stats_payload(payload)
          if parsed:
            return parsed
          return {'error': 'malformed_stats', 'payload_len': len(payload)}
      await asyncio.sleep(0.005)
    return None

  async def reconnect_loop(self):
    """Background task: keep trying to connect."""
    while True:
      try:
        if not self.connected:
          success = await self.connect()
          if success:
            self.reconnect_count += 1
            logger.info(f"Reconnected (attempt #{self.reconnect_count})")
        await asyncio.sleep(self.reconnect_interval)
      except asyncio.CancelledError:
        break

  def get_status(self) -> dict:
    return {
      'connected': self.connected,
      'port': self.serial.port if self.serial else None,
      'caps': self.caps,
      'frames_sent': self.frames_sent,
      'send_errors': self.send_errors,
      'reconnect_count': self.reconnect_count,
    }
