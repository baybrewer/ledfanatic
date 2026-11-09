// Host-side sanitizer harness for the firmware protocol layer.
// Compiles the real src/protocol.cpp + include/protocol.h (no Arduino) under ASan/UBSan.
// Run via run.sh; prints "OK <n>" on success and exits nonzero on the first failure.
//
//   --emit-vectors   print "<input-hex> <encoded-hex>" lines for pytest to decode with the
//                    Python cobs_decode (the C++ encode -> Python decode direction).

#include "protocol.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

typedef std::vector<uint8_t> Bytes;

static int g_checks = 0;

#define CHECK(cond)                                                              \
  do {                                                                           \
    if (!(cond)) {                                                               \
      std::fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);       \
      std::exit(1);                                                              \
    }                                                                            \
    g_checks++;                                                                  \
  } while (0)

// The decoder holds ~29 KB; keep it off the stack.
static COBSDecoder g_decoder;

// ---------------------------------------------------------------------------
// Helpers

static Bytes from_hex(const char* hex) {
  Bytes out;
  size_t n = std::strlen(hex);
  for (size_t i = 0; i + 1 < n; i += 2) {
    unsigned value = 0;
    std::sscanf(hex + i, "%2x", &value);
    out.push_back((uint8_t)value);
  }
  return out;
}

static std::string to_hex(const Bytes& bytes) {
  static const char* digits = "0123456789abcdef";
  std::string out;
  for (uint8_t b : bytes) {
    out.push_back(digits[b >> 4]);
    out.push_back(digits[b & 0xF]);
  }
  return out;
}

static Bytes encode(const Bytes& raw) {
  // Worst case: one code byte per 254 data bytes, plus a trailing 0x01 block.
  Bytes out(raw.size() + raw.size() / 254 + 2);
  size_t n = cobs_encode(raw.data(), raw.size(), out.data(), out.size());
  CHECK(n > 0);
  out.resize(n);
  CHECK(std::memchr(out.data(), 0x00, out.size()) == nullptr);
  return out;
}

// Outcome of one delimiter, mirroring the firmware's bounded RX loop (plan Task 1 step 5).
enum FrameOutcome { FRAME_OVERSIZE, FRAME_INCOMPLETE, FRAME_EMPTY, FRAME_PACKET };

// Feed an encoded block followed by a delimiter the way the firmware RX loop does and
// report what the loop would do. Leaves the decoded bytes readable until reset_decoder().
static FrameOutcome feed_frame(const Bytes& encoded) {
  CHECK(std::memchr(encoded.data(), 0x00, encoded.size()) == nullptr);
  for (uint8_t b : encoded) g_decoder.feed(b);
  // Delimiter arrives: the loop never feeds it, it inspects the decoder instead.
  if (g_decoder.overflowed()) return FRAME_OVERSIZE;
  if (g_decoder.remaining() > 0) return FRAME_INCOMPLETE;
  if (g_decoder.length() == 0) return FRAME_EMPTY;
  return FRAME_PACKET;
}

static Bytes decoded_bytes() {
  return Bytes(g_decoder.data(), g_decoder.data() + g_decoder.length());
}

static void reset_decoder() { g_decoder.reset(); }

static Bytes decode(const Bytes& encoded) {
  CHECK(feed_frame(encoded) == FRAME_PACKET || encoded.size() <= 1);
  Bytes out = decoded_bytes();
  reset_decoder();
  return out;
}

static void check_round_trip(const Bytes& raw) {
  Bytes enc = encode(raw);
  CHECK(feed_frame(enc) == (raw.empty() ? FRAME_EMPTY : FRAME_PACKET));
  CHECK(!g_decoder.overflowed());
  CHECK(g_decoder.remaining() == 0);
  CHECK(decoded_bytes() == raw);
  reset_decoder();
}

static Bytes build(uint8_t type, const Bytes& payload, uint32_t frame_id = 0) {
  Bytes out(HEADER_SIZE + payload.size() + CRC_SIZE);
  size_t n = build_packet(type, payload.empty() ? nullptr : payload.data(), payload.size(),
                          out.data(), out.size(), frame_id);
  CHECK(n == out.size());
  return out;
}

static void put_u32(Bytes& buf, size_t offset, uint32_t value) {
  buf[offset + 0] = (uint8_t)(value);
  buf[offset + 1] = (uint8_t)(value >> 8);
  buf[offset + 2] = (uint8_t)(value >> 16);
  buf[offset + 3] = (uint8_t)(value >> 24);
}

static Bytes run_of(uint8_t byte, size_t count) { return Bytes(count, byte); }

static Bytes concat(const Bytes& a, const Bytes& b) {
  Bytes out(a);
  out.insert(out.end(), b.begin(), b.end());
  return out;
}

// ---------------------------------------------------------------------------
// Vectors shared with pi/tests/test_protocol.py (GOLDEN_VECTORS) — the encoded side is
// what the Python encoder produces, so this is also the Python encode -> C++ decode path.

struct GoldenVector { const char* raw; const char* encoded; };

static const GoldenVector GOLDEN[] = {
  {"", "01"},
  {"00", "0101"},
  {"0000", "010101"},
  {"000000", "01010101"},
  {"11220033", "0311220233"},
  {"11223344", "0511223344"},
  {"11000000", "0211010101"},
};

// Python cobs_encode(b'A'*254 + b'\x00' + b'B') — the 254-run bug case (49d8707).
static const char* PY_A254_0_B =
  "ff41414141414141414141414141414141414141414141414141414141414141414141414141414141414141"
  "4141414141414141414141414141414141414141414141414141414141414141414141414141414141414141"
  "4141414141414141414141414141414141414141414141414141414141414141414141414141414141414141"
  "4141414141414141414141414141414141414141414141414141414141414141414141414141414141414141"
  "4141414141414141414141414141414141414141414141414141414141414141414141414141414141414141"
  "4141414141414141414141414141414141414141414141414141414141414141414141010242";

// Python frame_packet(build_packet(PacketType.PING)) — a real framed packet, delimiter included.
static const char* PY_PING_FRAMED = "0750494c4c0120010101010101010101010101010101010105c364d3cb00";

// ---------------------------------------------------------------------------
// Cases

static void test_golden_vectors() {
  for (const GoldenVector& v : GOLDEN) {
    Bytes raw = from_hex(v.raw);
    Bytes expected = from_hex(v.encoded);
    CHECK(encode(raw) == expected);
    CHECK(decode(expected) == raw);
    check_round_trip(raw);
  }
}

static void test_254_run_fix() {
  Bytes raw = concat(concat(run_of('A', 254), Bytes{0x00}), Bytes{'B'});
  Bytes expected = from_hex(PY_A254_0_B);
  CHECK(encode(raw) == expected);  // must match the fixed Python encoder byte for byte
  CHECK(decode(expected) == raw);

  const size_t lengths[] = {253, 254, 255, 508, 509};
  for (size_t n : lengths) {
    Bytes run = run_of(0xFF, n);
    check_round_trip(run);
    check_round_trip(concat(run, Bytes{0x00}));
    check_round_trip(concat(run, Bytes{0x00, 0x00}));
    check_round_trip(concat(Bytes{0x00}, run));
    check_round_trip(concat(concat(run, Bytes{0x00}), run));
  }
  // Run lengths from the Python GOLDEN_VECTORS round-trip entries.
  check_round_trip(run_of(0xFF, 300));
  check_round_trip(run_of(0xFF, 1000));
}

static void test_empty_and_consecutive_delimiters() {
  // Nothing between two delimiters: counted as an empty packet, never handled.
  CHECK(feed_frame(Bytes{}) == FRAME_EMPTY);
  reset_decoder();
  CHECK(feed_frame(Bytes{}) == FRAME_EMPTY);
  reset_decoder();
  // A lone 0x01 code is a legitimately empty packet too (encodes b'').
  CHECK(feed_frame(Bytes{0x01}) == FRAME_EMPTY);
  CHECK(g_decoder.remaining() == 0);
  reset_decoder();
  // Decoder state is clean afterwards: a real packet decodes.
  check_round_trip(from_hex("11220033"));
}

static void test_overflow_then_recovers() {
  // MAX_PACKET_BYTES + 1 nonzero bytes: the store that would exceed the cap sets the flag.
  Bytes big = run_of(0x42, MAX_PACKET_BYTES + 1);
  Bytes enc = encode(big);
  CHECK(feed_frame(enc) == FRAME_OVERSIZE);
  CHECK(g_decoder.length() == MAX_PACKET_BYTES);  // nothing stored past the cap
  reset_decoder();
  CHECK(!g_decoder.overflowed());

  // Much larger garbage — the firmware's RX loop keeps feeding until the delimiter.
  Bytes huge = run_of(0x7E, 3 * MAX_PACKET_BYTES + 17);
  CHECK(feed_frame(encode(huge)) == FRAME_OVERSIZE);
  reset_decoder();

  // Then a valid frame decodes normally.
  Bytes pkt = build(PKT_PING, Bytes{});
  CHECK(feed_frame(encode(pkt)) == FRAME_PACKET);
  PacketHeader header;
  const uint8_t* payload = nullptr;
  CHECK(verify_packet(g_decoder.data(), g_decoder.length(), &header, &payload) == VERIFY_OK);
  CHECK(header.type == PKT_PING);
  reset_decoder();
}

static void test_exact_max_frame() {
  Bytes payload(MAX_PAYLOAD_SIZE);
  for (size_t i = 0; i < payload.size(); i++) payload[i] = (uint8_t)(i * 7 + 3);  // includes zeros
  Bytes pkt = build(PKT_FRAME, payload, 77);
  CHECK(pkt.size() == MAX_PACKET_BYTES);
  Bytes enc = encode(pkt);
  CHECK(feed_frame(enc) == FRAME_PACKET);
  CHECK(!g_decoder.overflowed());
  CHECK(g_decoder.length() == MAX_PACKET_BYTES);
  PacketHeader header;
  const uint8_t* got = nullptr;
  CHECK(verify_packet(g_decoder.data(), g_decoder.length(), &header, &got) == VERIFY_OK);
  CHECK(header.payload_len == MAX_PAYLOAD_SIZE);
  CHECK(header.frame_id == 77);
  CHECK(std::memcmp(got, payload.data(), payload.size()) == 0);
  reset_decoder();

  // One byte more than the cap can never be built: build_packet refuses it.
  Bytes over(HEADER_SIZE + MAX_PAYLOAD_SIZE + 1 + CRC_SIZE);
  CHECK(build_packet(PKT_FRAME, over.data(), MAX_PAYLOAD_SIZE + 1, over.data(), over.size()) == 0);
}

static void test_verify_len_bounds() {
  // A 28-byte "packet" with a forged payload_len. Must be VERIFY_LEN with no wrap and no
  // out-of-bounds read (ASan would flag the CRC read otherwise).
  const uint32_t forged[] = {0xFFFFFFE5u, 0xFFFFFFFFu, (uint32_t)MAX_PAYLOAD_SIZE + 1};
  for (uint32_t bad_len : forged) {
    Bytes pkt = build(PKT_PING, Bytes{});
    put_u32(pkt, 20, bad_len);
    PacketHeader header;
    const uint8_t* payload = nullptr;
    CHECK(verify_packet(pkt.data(), pkt.size(), &header, &payload) == VERIFY_LEN);
  }

  // payload_len under the cap but not matching the actual length: still VERIFY_LEN
  // (the old "len < expected" check accepted trailing garbage).
  Bytes pkt = build(PKT_PING, Bytes{});
  Bytes padded = concat(pkt, Bytes{0xAA, 0xBB});
  PacketHeader header;
  const uint8_t* payload = nullptr;
  CHECK(verify_packet(padded.data(), padded.size(), &header, &payload) == VERIFY_LEN);

  Bytes with_payload = build(PKT_FRAME, run_of(0x55, 40));
  Bytes truncated_payload(with_payload.begin(), with_payload.begin() + 50);
  CHECK(verify_packet(truncated_payload.data(), truncated_payload.size(), &header, &payload) ==
        VERIFY_LEN);
}

static void test_verify_other_reasons() {
  Bytes pkt = build(PKT_FRAME, run_of(0x55, 16), 5);
  PacketHeader header;
  const uint8_t* payload = nullptr;
  CHECK(verify_packet(pkt.data(), pkt.size(), &header, &payload) == VERIFY_OK);
  CHECK(header.frame_id == 5);

  // Interrupted block: the stream died inside the header.
  Bytes cut(pkt.begin(), pkt.begin() + 10);
  CHECK(verify_packet(cut.data(), cut.size(), &header, &payload) == VERIFY_SHORT);
  CHECK(verify_packet(pkt.data(), HEADER_SIZE + CRC_SIZE - 1, &header, &payload) == VERIFY_SHORT);
  CHECK(verify_packet(pkt.data(), 0, &header, &payload) == VERIFY_SHORT);

  Bytes bad_magic(pkt);
  bad_magic[0] = 'X';
  CHECK(verify_packet(bad_magic.data(), bad_magic.size(), &header, &payload) == VERIFY_MAGIC);

  Bytes bad_version(pkt);
  bad_version[4] = PROTOCOL_VERSION + 1;
  CHECK(verify_packet(bad_version.data(), bad_version.size(), &header, &payload) ==
        VERIFY_VERSION);

  Bytes bad_crc(pkt);
  bad_crc[bad_crc.size() - 1] ^= 0xFF;
  CHECK(verify_packet(bad_crc.data(), bad_crc.size(), &header, &payload) == VERIFY_CRC);

  Bytes bad_body(pkt);
  bad_body[HEADER_SIZE + 3] ^= 0x01;
  CHECK(verify_packet(bad_body.data(), bad_body.size(), &header, &payload) == VERIFY_CRC);
}

// Index of the last COBS code byte in an encoded stream.
static size_t last_code_index(const Bytes& enc) {
  size_t idx = 0;
  size_t last = 0;
  while (idx < enc.size()) {
    last = idx;
    idx += enc[idx];
  }
  CHECK(idx == enc.size());  // well-formed input
  return last;
}

static void test_incomplete_final_block_is_rejected() {
  // A CRC-valid packet whose final code byte promises one more byte than arrives. The
  // decoded bytes ARE the valid packet, so only remaining() can catch it — and it must.
  Bytes pkt = build(PKT_STATS, run_of(0x11, 28), 9);
  Bytes enc = encode(pkt);
  size_t code_at = last_code_index(enc);
  CHECK(enc[code_at] < 0xFF);
  enc[code_at] += 1;

  CHECK(feed_frame(enc) == FRAME_INCOMPLETE);
  CHECK(g_decoder.remaining() == 1);
  CHECK(!g_decoder.overflowed());
  // Proof the framing check is load-bearing: the bytes decoded so far verify clean.
  PacketHeader header;
  const uint8_t* payload = nullptr;
  CHECK(decoded_bytes() == pkt);
  CHECK(verify_packet(g_decoder.data(), g_decoder.length(), &header, &payload) == VERIFY_OK);
  reset_decoder();

  // Same shape when the cut block is in the middle of a multi-block packet (zeros in
  // header flags/timestamp mean several blocks).
  Bytes enc2 = encode(pkt);
  enc2.resize(enc2.size() - 1);  // drop the last data byte
  CHECK(feed_frame(enc2) == FRAME_INCOMPLETE);
  reset_decoder();
}

static void test_crash_sized_round_trip() {
  Bytes payload(144);
  for (size_t i = 0; i < payload.size(); i++) payload[i] = (uint8_t)(i % 7 == 0 ? 0 : i);
  const uint32_t req_id = 0xDEADBEEFu;
  Bytes pkt = build(0x33, payload, req_id);  // PKT_CRASH (defined in a later step)
  Bytes enc = encode(pkt);
  CHECK(feed_frame(enc) == FRAME_PACKET);
  PacketHeader header;
  const uint8_t* got = nullptr;
  CHECK(verify_packet(g_decoder.data(), g_decoder.length(), &header, &got) == VERIFY_OK);
  CHECK(header.type == 0x33);
  CHECK(header.frame_id == req_id);
  CHECK(header.payload_len == 144);
  CHECK(std::memcmp(got, payload.data(), payload.size()) == 0);
  reset_decoder();

  // Default argument keeps unsolicited packets at frame_id 0.
  Bytes pots = build(PKT_POTS, run_of(0x01, 6));
  const uint8_t* ignored = nullptr;
  CHECK(verify_packet(pots.data(), pots.size(), &header, &ignored) == VERIFY_OK);
  CHECK(header.frame_id == 0);
}

static void test_python_framed_packet_decodes() {
  Bytes framed = from_hex(PY_PING_FRAMED);
  CHECK(framed.back() == 0x00);
  framed.pop_back();
  CHECK(feed_frame(framed) == FRAME_PACKET);
  PacketHeader header;
  const uint8_t* payload = nullptr;
  CHECK(verify_packet(g_decoder.data(), g_decoder.length(), &header, &payload) == VERIFY_OK);
  CHECK(header.type == PKT_PING);
  CHECK(header.payload_len == 0);
  reset_decoder();
  // And our encoder reproduces the Python framing exactly.
  Bytes raw = decode(framed);
  CHECK(encode(raw) == framed);
}

static void test_encoder_output_cap() {
  Bytes raw = from_hex("11220033");
  uint8_t small[4];
  CHECK(cobs_encode(raw.data(), raw.size(), small, sizeof(small)) == 0);  // needs 5
  CHECK(cobs_encode(raw.data(), raw.size(), small, 0) == 0);
  Bytes run = run_of(0xFF, 254);
  Bytes tight(255);
  CHECK(cobs_encode(run.data(), run.size(), tight.data(), tight.size()) == 255);
  CHECK(cobs_encode(run.data(), run.size(), tight.data(), 254) == 0);
  // build_packet refuses an output buffer that is too small.
  uint8_t tiny[HEADER_SIZE + CRC_SIZE - 1];
  CHECK(build_packet(PKT_PING, nullptr, 0, tiny, sizeof(tiny)) == 0);
}

// ---------------------------------------------------------------------------

static void emit_vectors() {
  std::vector<Bytes> inputs;
  for (const GoldenVector& v : GOLDEN) inputs.push_back(from_hex(v.raw));
  inputs.push_back(concat(concat(run_of('A', 254), Bytes{0x00}), Bytes{'B'}));
  const size_t lengths[] = {253, 254, 255, 508, 509};
  for (size_t n : lengths) {
    inputs.push_back(run_of(0xFF, n));
    inputs.push_back(concat(run_of(0xFF, n), Bytes{0x00}));
    inputs.push_back(concat(Bytes{0x00}, run_of(0xFF, n)));
  }
  inputs.push_back(build(PKT_PING, Bytes{}));
  inputs.push_back(build(PKT_STATS, run_of(0x00, 28), 42));
  Bytes crash(144);
  for (size_t i = 0; i < crash.size(); i++) crash[i] = (uint8_t)(i % 5 == 0 ? 0 : 0xFF);
  inputs.push_back(build(0x33, crash, 0xDEADBEEFu));
  for (const Bytes& raw : inputs) {
    // "-" stands for the empty input so every line has two fields.
    std::string raw_hex = raw.empty() ? "-" : to_hex(raw);
    std::printf("%s %s\n", raw_hex.c_str(), to_hex(encode(raw)).c_str());
  }
}

int main(int argc, char** argv) {
  if (argc > 1 && std::strcmp(argv[1], "--emit-vectors") == 0) {
    emit_vectors();
    return 0;
  }

  test_golden_vectors();
  test_254_run_fix();
  test_empty_and_consecutive_delimiters();
  test_overflow_then_recovers();
  test_exact_max_frame();
  test_verify_len_bounds();
  test_verify_other_reasons();
  test_incomplete_final_block_is_rejected();
  test_crash_sized_round_trip();
  test_python_framed_packet_decodes();
  test_encoder_output_cap();

  std::printf("OK %d\n", g_checks);
  return 0;
}
