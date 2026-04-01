#include "protocol.h"
#include <string.h>

// --- CRC32 (same polynomial as zlib) ---
static const uint32_t crc32_table[256] = {
  0x00000000, 0x77073096, 0xEE0E612C, 0x990951BA, 0x076DC419, 0x706AF48F,
  0xE963A535, 0x9E6495A3, 0x0EDB8832, 0x79DCB8A4, 0xE0D5E91B, 0x97D2D988,
  0x09B64C2B, 0x7EB17CBB, 0xE7B82D09, 0x90BF1D91, 0x1DB71064, 0x6AB020F2,
  0xF3B97148, 0x84BE41DE, 0x1ADAD47D, 0x6DDDE4EB, 0xF4D4B551, 0x83D385C7,
  0x136C9856, 0x646BA8C0, 0xFD62F97A, 0x8A65C9EC, 0x14015C4F, 0x63066CD9,
  0xFA0F3D63, 0x8D080DF5, 0x3B6E20C8, 0x4C69105E, 0xD56041E4, 0xA2677172,
  0x3C03E4D1, 0x4B04D447, 0xD20D85FD, 0xA50AB56B, 0x35B5A8FA, 0x42B2986C,
  0xDBBBC9D6, 0xACBCF940, 0x32D86CE3, 0x45DF5C75, 0xDCD60DCF, 0xABD13D59,
  0x26D930AC, 0x51DE003A, 0xC8D75180, 0xBFD06116, 0x21B4F0B5, 0x56B3C423,
  0xCFBA9599, 0xB8BDA50F, 0x2802B89E, 0x5F058808, 0xC60CD9B2, 0xB10BE924,
  0x2F6F7C87, 0x58684C11, 0xC1611DAB, 0xB6662D3D, 0x76DC4190, 0x01DB7106,
  0x98D220BC, 0xEFD5102A, 0x71B18589, 0x06B6B51F, 0x9FBFE4A5, 0xE8B8D433,
  0x7807C9A2, 0x0F00F934, 0x9609A88E, 0xE10E9818, 0x7F6A0DBB, 0x086D3D2D,
  0x91646C97, 0xE6635C01, 0x6B6B51F4, 0x1C6C6162, 0x856530D8, 0xF262004E,
  0x6C0695ED, 0x1B01A57B, 0x8208F4C1, 0xF50FC457, 0x65B0D9C6, 0x12B7E950,
  0x8BBEB8EA, 0xFCB9887C, 0x62DD1DDF, 0x15DA2D49, 0x8CD37CF3, 0xFBD44C65,
  0x4DB26158, 0x3AB551CE, 0xA3BC0074, 0xD4BB30E2, 0x4ADFA541, 0x3DD895D7,
  0xA4D1C46D, 0xD3D6F4FB, 0x4369E96A, 0x346ED9FC, 0xAD678846, 0xDA60B8D0,
  0x44042D73, 0x33031DE5, 0xAA0A4C5F, 0xDD0D7822, 0x3B6E20C8, 0x4C69105E,
  0xD56041E4, 0xA2677172, 0x3C03E4D1, 0x4B04D447, 0xD20D85FD, 0xA50AB56B,
  0x35B5A8FA, 0x42B2986C, 0xDBBBC9D6, 0xACBCF940, 0x32D86CE3, 0x45DF5C75,
  0xDCD60DCF, 0xABD13D59, 0x26D930AC, 0x51DE003A, 0xC8D75180, 0xBFD06116,
  0x21B4F0B5, 0x56B3C423, 0xCFBA9599, 0xB8BDA50F, 0x2802B89E, 0x5F058808,
  0xC60CD9B2, 0xB10BE924, 0x2F6F7C87, 0x58684C11, 0xC1611DAB, 0xB6662D3D,
  0x76DC4190, 0x01DB7106, 0x98D220BC, 0xEFD5102A, 0x71B18589, 0x06B6B51F,
  0x9FBFE4A5, 0xE8B8D433, 0x7807C9A2, 0x0F00F934, 0x9609A88E, 0xE10E9818,
  0x7F6A0DBB, 0x086D3D2D, 0x91646C97, 0xE6635C01, 0x6B6B51F4, 0x1C6C6162,
  0x856530D8, 0xF262004E, 0x6C0695ED, 0x1B01A57B, 0x8208F4C1, 0xF50FC457,
  0x65B0D9C6, 0x12B7E950, 0x8BBEB8EA, 0xFCB9887C, 0x62DD1DDF, 0x15DA2D49,
  0x8CD37CF3, 0xFBD44C65, 0x4DB26158, 0x3AB551CE, 0xA3BC0074, 0xD4BB30E2,
  0x4ADFA541, 0x3DD895D7, 0xA4D1C46D, 0xD3D6F4FB, 0x4369E96A, 0x346ED9FC,
  0xAD678846, 0xDA60B8D0, 0x44042D73, 0x33031DE5, 0xAA0A4C5F, 0xDD0D7822,
  0x9B64C2B0, 0xEC63F226, 0x756AA39C, 0x026D930A, 0x9C0906A9, 0xEB0E363F,
  0x72076785, 0x05005713, 0x95BF4A82, 0xE2B87A14, 0x7BB12BAE, 0x0CB61B38,
  0x92D28E9B, 0xE5D5BE0D, 0x7CDCEFB7, 0x0BDBDF21, 0x86D3D2D4, 0xF1D4E242,
  0x68DDB3F6, 0x1FDA836E, 0x81BE16CD, 0xF6B9265B, 0x6FB077E1, 0x18B74777,
  0x88085AE6, 0xFF0F6B70, 0x66063BCA, 0x11010B5C, 0x8F659EFF, 0xF862AE69,
  0x616BFFD3, 0x166CCF45, 0xA00AE278, 0xD70DD2EE, 0x4E048354, 0x3903B3C2,
  0xA7672661, 0xD06016F7, 0x4969474D, 0x3E6E77DB, 0xAED16A4A, 0xD9D65ADC,
  0x40DF0B66, 0x37D83BF0, 0xA9BCAE53, 0xDEBB9EC5, 0x47B2CF7F, 0x30B5FFE9,
  0xBDBDF21C, 0xCABAC28A, 0x53B39330, 0x24B4A3A6, 0xBAD03605, 0xCDD706FF,
  0x54DE5729, 0x23D967BF, 0xB3667A2E, 0xC4614AB8, 0x5D681B02, 0x2A6F2B94,
  0xB40BBE37, 0xC30C8EA1, 0x5A05DF1B, 0x2D02EF8D,
};

uint32_t crc32(const uint8_t* data, size_t len) {
  uint32_t crc = 0xFFFFFFFF;
  for (size_t i = 0; i < len; i++) {
    crc = (crc >> 8) ^ crc32_table[(crc ^ data[i]) & 0xFF];
  }
  return crc ^ 0xFFFFFFFF;
}

// --- COBS encoder ---
size_t cobs_encode(const uint8_t* input, size_t len, uint8_t* output, size_t max_out) {
    size_t out_idx = 0;
    size_t read_idx = 0;

    while (read_idx < len) {
        size_t block_start = read_idx;
        // Scan for next zero or 254-byte limit
        while (read_idx < len && input[read_idx] != 0 && (read_idx - block_start) < 254) {
            read_idx++;
        }

        size_t block_len = read_idx - block_start;

        if (block_len == 254 && (read_idx >= len || input[read_idx] != 0)) {
            // Non-zero run hit 254 limit — emit 0xFF continuation
            if (out_idx >= max_out) return 0;
            output[out_idx++] = 0xFF;
            for (size_t i = 0; i < block_len; i++) {
                if (out_idx >= max_out) return 0;
                output[out_idx++] = input[block_start + i];
            }
            // Do not consume a zero — continue scanning
        } else {
            // Normal block ended by zero or end of input
            if (out_idx >= max_out) return 0;
            output[out_idx++] = (uint8_t)(block_len + 1);
            for (size_t i = 0; i < block_len; i++) {
                if (out_idx >= max_out) return 0;
                output[out_idx++] = input[block_start + i];
            }
            // Consume zero if present
            if (read_idx < len && input[read_idx] == 0) {
                read_idx++;
            } else {
                break; // End of input
            }
        }
    }

    // Handle empty input or input that ends at a block boundary
    if (len == 0) {
        if (out_idx >= max_out) return 0;
        output[out_idx++] = 0x01;
    }

    return out_idx;
}

// --- Packet verification ---
bool verify_packet(const uint8_t* data, size_t len, PacketHeader* header, const uint8_t** payload) {
  if (len < HEADER_SIZE + CRC_SIZE) return false;

  memcpy(header, data, sizeof(PacketHeader));

  // Check magic
  if (header->magic[0] != MAGIC_0 || header->magic[1] != MAGIC_1 ||
      header->magic[2] != MAGIC_2 || header->magic[3] != MAGIC_3) {
    return false;
  }

  // Check version
  if (header->version != PROTOCOL_VERSION) return false;

  // Check total length
  size_t expected = HEADER_SIZE + header->payload_len + CRC_SIZE;
  if (len < expected) return false;

  // Verify CRC
  uint32_t computed = crc32(data, HEADER_SIZE + header->payload_len);
  uint32_t stored;
  memcpy(&stored, data + HEADER_SIZE + header->payload_len, sizeof(uint32_t));
  if (computed != stored) return false;

  *payload = data + HEADER_SIZE;
  return true;
}

// --- Packet building ---
size_t build_packet(uint8_t type, const uint8_t* payload, size_t payload_len,
                    uint8_t* output, size_t max_out) {
  if (HEADER_SIZE + payload_len + CRC_SIZE > max_out) return 0;

  PacketHeader hdr;
  hdr.magic[0] = MAGIC_0;
  hdr.magic[1] = MAGIC_1;
  hdr.magic[2] = MAGIC_2;
  hdr.magic[3] = MAGIC_3;
  hdr.version = PROTOCOL_VERSION;
  hdr.type = type;
  hdr.flags = 0;
  hdr.frame_id = 0;
  hdr.timestamp_us = 0;
  hdr.payload_len = payload_len;

  memcpy(output, &hdr, sizeof(PacketHeader));
  if (payload_len > 0 && payload) {
    memcpy(output + HEADER_SIZE, payload, payload_len);
  }

  uint32_t crc_val = crc32(output, HEADER_SIZE + payload_len);
  memcpy(output + HEADER_SIZE + payload_len, &crc_val, sizeof(uint32_t));

  return HEADER_SIZE + payload_len + CRC_SIZE;
}
