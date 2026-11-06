#pragma once

// Wire protocol shared with pi/app/models/protocol.py. This header has no Arduino
// dependency so the host-side sanitizer harness (test_native/, -DNATIVE_TEST) can
// compile protocol.cpp unchanged.
#include <stdint.h>
#include <stddef.h>
#include "config.h"

// --- Packet header (24 bytes, little-endian) ---
struct __attribute__((packed)) PacketHeader {
  uint8_t  magic[4];
  uint8_t  version;
  uint8_t  type;
  uint16_t flags;
  uint32_t frame_id;
  uint64_t timestamp_us;
  uint32_t payload_len;
};

// --- COBS decoder ---
// Byte-at-a-time decoder. The RX loop owns delimiter handling: on 0x00 it inspects
// overflowed() / remaining() / length(), acts, and ALWAYS calls reset().
class COBSDecoder {
public:
  COBSDecoder() { reset(); }

  void reset() {
    _out_len = 0;
    _remaining = 0;
    _last_code = 0;
    _had_block = false;
    _overflow = false;
  }

  // Feed one byte. Returns true when a delimiter (0x00) arrives with data decoded —
  // kept for the legacy RX loop; the bounded RX loop handles 0x00 itself and ignores
  // the return value.
  bool feed(uint8_t byte) {
    if (byte == 0x00) {
      return _out_len > 0;
    }

    if (_remaining == 0) {
      // Code byte. A previous block shorter than 0xFF implies a zero before this one.
      if (_had_block && _last_code < 0xFF) {
        store(0x00);
      }
      _last_code = byte;
      _remaining = byte - 1;
      _had_block = true;
    } else {
      store(byte);
      _remaining--;
    }
    return false;
  }

  const uint8_t* data() const { return _output; }
  size_t length() const { return _out_len; }

  // A store would have exceeded MAX_PACKET_BYTES. Nothing past the cap is kept; the
  // packet must be dropped as oversize even if the rest of it looks fine.
  bool overflowed() const { return _overflow; }

  // Bytes the last code byte promised but that have not arrived yet. Nonzero at a
  // delimiter means the final block was cut short — reject the packet even though the
  // bytes decoded so far may form a CRC-valid packet.
  uint8_t remaining() const { return _remaining; }

private:
  void store(uint8_t byte) {
    if (_out_len >= MAX_PACKET_BYTES) {
      _overflow = true;
      return;
    }
    _output[_out_len++] = byte;
  }

  // One spare byte so an off-by-one in a caller's cap check can never write past the end.
  uint8_t _output[MAX_PACKET_BYTES + 1];
  size_t _out_len;
  uint8_t _remaining;
  uint8_t _last_code;
  bool _had_block;
  bool _overflow;
};

// --- COBS encoder ---
// Returns the encoded length, or 0 if it would not fit in max_out. The output never
// contains 0x00; the caller appends the delimiter.
size_t cobs_encode(const uint8_t* input, size_t len, uint8_t* output, size_t max_out);

// --- CRC32 ---
uint32_t crc32(const uint8_t* data, size_t len);

// --- Packet verification ---
// Index order matters: the firmware's DIAG counts rejects in a verifyFail[6] array.
enum VerifyResult : uint8_t {
  VERIFY_OK = 0,
  VERIFY_SHORT,    // fewer bytes than header + CRC
  VERIFY_MAGIC,
  VERIFY_VERSION,
  VERIFY_LEN,      // payload_len over cap, or total length != header + payload_len + CRC
  VERIFY_CRC,
};

VerifyResult verify_packet(const uint8_t* data, size_t len, PacketHeader* header,
                           const uint8_t** payload);

// --- Packet building ---
// frame_id echoes the request id on responses (0 for unsolicited packets).
size_t build_packet(uint8_t type, const uint8_t* payload, size_t payload_len,
                    uint8_t* output, size_t max_out, uint32_t frame_id = 0);
