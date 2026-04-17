/*
 * Pillar Controller — Teensy 4.1 Firmware
 *
 * Transport + output engine for OctoWS2811-driven LED pillar.
 * Receives framed pixel data over USB Serial from Raspberry Pi.
 *
 * Supports dynamic geometry via CONFIG packets. Falls back to
 * legacy 5x344 defaults (or EEPROM-saved config) if no CONFIG
 * is received.
 */

#include <Arduino.h>
#include <OctoWS2811.h>
#include <EEPROM.h>
#include "config.h"
#include "protocol.h"

// --- EEPROM layout ---
#define EEPROM_CONFIG_ADDR 0
#define EEPROM_MAGIC 0x504D  // "PM" for pixel map

// --- OctoWS2811 setup (max-sized DMA buffers) ---
// OctoWS2811 uses int arrays where each int holds 4 bytes (one RGB component
// per output pin interleaved). Size = ledsPerStrip * 6 ints = ledsPerStrip * 24 bytes.
// We allocate for worst case: MAX_LEDS_PER_OUTPUT per strip.
DMAMEM int displayMemory[MAX_LEDS_PER_OUTPUT * 6];
int drawingMemory[MAX_LEDS_PER_OUTPUT * 6];
const int octoConfig = WS2811_BGR | WS2811_800kHz;
OctoWS2811 leds(DEFAULT_LEDS_PER_STRIP, displayMemory, drawingMemory, octoConfig);

// --- Runtime geometry (updated by CONFIG or EEPROM) ---
static bool configReceived = false;
static uint8_t activeOutputs = DEFAULT_ACTIVE_OUTPUTS;
static uint16_t ledsPerOutput[TOTAL_OUTPUTS] = {
  DEFAULT_LEDS_PER_STRIP, DEFAULT_LEDS_PER_STRIP,
  DEFAULT_LEDS_PER_STRIP, DEFAULT_LEDS_PER_STRIP,
  DEFAULT_LEDS_PER_STRIP, 0, 0, 0
};
static uint16_t maxLedsPerStrip = DEFAULT_LEDS_PER_STRIP;  // max across all outputs
static uint32_t totalLeds = DEFAULT_ACTIVE_OUTPUTS * DEFAULT_LEDS_PER_STRIP;
static uint32_t frameSize = DEFAULT_ACTIVE_OUTPUTS * DEFAULT_LEDS_PER_STRIP * 3;

// --- Pending frame buffer (max-sized) ---
static uint8_t pendingFrame[MAX_LEDS_PER_OUTPUT * TOTAL_OUTPUTS * 3];

// --- State ---
static COBSDecoder decoder;
static bool pendingFrameReady = false;
static uint8_t masterBrightness = 255;
static bool blackout = false;
static int activeTestPattern = -1;  // -1 = none

// --- Watchdog state ---
static uint32_t lastFrameTime = 0;
static bool watchdogFading = false;
static uint8_t fadeLevel = 255;

// --- Stats ---
struct Stats {
  uint32_t uptimeMs;
  uint32_t framesReceived;
  uint32_t framesApplied;
  uint32_t badCrc;
  uint32_t badFrame;
  uint32_t droppedPending;
  uint32_t outputFps;
} stats;

static uint32_t fpsCounter = 0;
static uint32_t lastFpsTime = 0;

// --- Forward declarations ---
void handlePacket(const uint8_t* data, size_t len);
void handleHello(const uint8_t* payload, size_t len);
void handleFrame(const uint8_t* payload, size_t len);
void handleConfig(const uint8_t* payload, size_t len);
void handleTestPattern(const uint8_t* payload, size_t len);
void handleBrightness(const uint8_t* payload, size_t len);
void sendCaps();
void sendStats();
void sendAck();
void sendNak();
void sendPacket(uint8_t type, const uint8_t* payload, size_t len);
void applyPendingFrame();
void recalcGeometry();
void reconfigureOcto();
void saveConfigToEEPROM();
bool loadConfigFromEEPROM();
void runTestPattern();
void runWatchdog();
void setPixel(int strip, int pixel, uint8_t r, uint8_t g, uint8_t b);
void clearAll();

// -------------------------------------------------------------------
// Recalculate derived geometry values from ledsPerOutput[]
void recalcGeometry() {
  maxLedsPerStrip = 0;
  totalLeds = 0;
  activeOutputs = 0;
  for (int i = 0; i < TOTAL_OUTPUTS; i++) {
    if (ledsPerOutput[i] > 0) {
      activeOutputs++;
    }
    totalLeds += ledsPerOutput[i];
    if (ledsPerOutput[i] > maxLedsPerStrip) {
      maxLedsPerStrip = ledsPerOutput[i];
    }
  }
  // frameSize = sum of all per-output LED counts * 3 bytes
  frameSize = totalLeds * 3;
}

// Reinitialize OctoWS2811 with current maxLedsPerStrip
void reconfigureOcto() {
  leds.begin(maxLedsPerStrip, displayMemory, drawingMemory, octoConfig);
  clearAll();
  leds.show();
}

// -------------------------------------------------------------------
// EEPROM persistence
void saveConfigToEEPROM() {
  uint16_t addr = EEPROM_CONFIG_ADDR;

  // Magic (2 bytes)
  uint16_t magic = EEPROM_MAGIC;
  EEPROM.put(addr, magic);
  addr += sizeof(magic);

  // activeOutputs (1 byte)
  EEPROM.put(addr, activeOutputs);
  addr += sizeof(activeOutputs);

  // ledsPerOutput (16 bytes: 8 x uint16_t)
  for (int i = 0; i < TOTAL_OUTPUTS; i++) {
    EEPROM.put(addr, ledsPerOutput[i]);
    addr += sizeof(uint16_t);
  }
}

bool loadConfigFromEEPROM() {
  uint16_t addr = EEPROM_CONFIG_ADDR;

  // Check magic
  uint16_t magic;
  EEPROM.get(addr, magic);
  if (magic != EEPROM_MAGIC) return false;
  addr += sizeof(magic);

  // Read activeOutputs
  uint8_t storedActive;
  EEPROM.get(addr, storedActive);
  addr += sizeof(storedActive);

  // Read ledsPerOutput
  uint16_t storedLeds[TOTAL_OUTPUTS];
  for (int i = 0; i < TOTAL_OUTPUTS; i++) {
    EEPROM.get(addr, storedLeds[i]);
    addr += sizeof(uint16_t);
  }

  // Validate
  if (storedActive == 0 || storedActive > TOTAL_OUTPUTS) return false;
  for (int i = 0; i < TOTAL_OUTPUTS; i++) {
    if (storedLeds[i] > MAX_LEDS_PER_OUTPUT) return false;
  }

  // Apply
  for (int i = 0; i < TOTAL_OUTPUTS; i++) {
    ledsPerOutput[i] = storedLeds[i];
  }
  recalcGeometry();
  configReceived = true;
  return true;
}

// -------------------------------------------------------------------
void setup() {
  Serial.begin(115200);  // Baud ignored on Teensy USB

  // Try loading saved config from EEPROM before first leds.begin()
  if (loadConfigFromEEPROM()) {
    reconfigureOcto();
  } else {
    leds.begin();
  }
  leds.show();

  lastFrameTime = millis();
  lastFpsTime = millis();

  // Brief startup flash
  for (int i = 0; i < leds.numPixels(); i++) {
    leds.setPixel(i, 0x001000);  // dim green
  }
  leds.show();
  delay(200);
  clearAll();
  leds.show();
}

// -------------------------------------------------------------------
void loop() {
  // --- Read USB bytes ---
  int avail = Serial.available();
  if (avail > 0) {
    uint8_t buf[USB_READ_CHUNK];
    int toRead = min(avail, (int)sizeof(buf));
    int bytesRead = Serial.readBytes(buf, toRead);

    for (int i = 0; i < bytesRead; i++) {
      if (decoder.feed(buf[i])) {
        handlePacket(decoder.data(), decoder.length());
        decoder.reset();
      }
    }
  }

  // --- Apply pending frame when Octo is ready ---
  if (!leds.busy()) {
    if (blackout) {
      clearAll();
      leds.show();
    } else if (activeTestPattern >= 0) {
      runTestPattern();
      leds.show();
      fpsCounter++;
    } else if (pendingFrameReady) {
      applyPendingFrame();
      leds.show();
      pendingFrameReady = false;
      stats.framesApplied++;
      fpsCounter++;
      lastFrameTime = millis();
      watchdogFading = false;
      fadeLevel = 255;
    }
  }

  // --- Watchdog ---
  runWatchdog();

  // --- FPS counter ---
  uint32_t now = millis();
  if (now - lastFpsTime >= STATS_INTERVAL_MS) {
    stats.outputFps = fpsCounter * 1000 / (now - lastFpsTime);
    fpsCounter = 0;
    lastFpsTime = now;
  }

  stats.uptimeMs = now;
}

// -------------------------------------------------------------------
void handlePacket(const uint8_t* data, size_t len) {
  PacketHeader header;
  const uint8_t* payload;

  if (!verify_packet(data, len, &header, &payload)) {
    stats.badCrc++;
    return;
  }

  switch (header.type) {
    case PKT_HELLO:
      handleHello(payload, header.payload_len);
      break;

    case PKT_FRAME:
      handleFrame(payload, header.payload_len);
      break;

    case PKT_CONFIG:
      handleConfig(payload, header.payload_len);
      break;

    case PKT_PING:
      sendStats();
      break;

    case PKT_TEST_PATTERN:
      handleTestPattern(payload, header.payload_len);
      break;

    case PKT_BLACKOUT:
      // Explicit blackout: payload byte 0x01=on, 0x00=off
      // Empty payload treated as on for backward safety
      if (header.payload_len >= 1) {
        blackout = (payload[0] != 0);
      } else {
        blackout = true;
      }
      activeTestPattern = -1;
      break;

    case PKT_BRIGHTNESS:
      handleBrightness(payload, header.payload_len);
      break;

    case PKT_REBOOT_BOOTLOADER:
      _reboot_Teensyduino_();
      break;
  }
}

// -------------------------------------------------------------------
void handleHello(const uint8_t* payload, size_t len) {
  // Pi announced itself — respond with capabilities
  sendCaps();
  activeTestPattern = -1;
  blackout = false;
}

void handleFrame(const uint8_t* payload, size_t len) {
  const uint8_t* pixelData;
  size_t pixelLen;

  if (configReceived) {
    // Post-CONFIG: raw pixel data, no header
    pixelData = payload;
    pixelLen = len;
  } else {
    // Legacy: channels(1) + leds_per_ch(2) + pixel data
    if (len < 3) {
      stats.badFrame++;
      return;
    }
    pixelData = payload + 3;
    pixelLen = len - 3;
  }

  if (pixelLen != frameSize) {
    stats.badFrame++;
    return;
  }

  memcpy(pendingFrame, pixelData, pixelLen);

  if (pendingFrameReady) {
    stats.droppedPending++;
  }
  pendingFrameReady = true;
  stats.framesReceived++;
  activeTestPattern = -1;
}

void handleConfig(const uint8_t* payload, size_t len) {
  // CONFIG payload: active_outputs(u8) + leds_per_output(u16 x 8) = 17 bytes
  const size_t CONFIG_SIZE = 1 + TOTAL_OUTPUTS * 2;
  if (len < CONFIG_SIZE) {
    sendNak();
    return;
  }

  uint8_t newActive = payload[0];
  uint16_t newLeds[TOTAL_OUTPUTS];
  for (int i = 0; i < TOTAL_OUTPUTS; i++) {
    newLeds[i] = payload[1 + i * 2] | (payload[2 + i * 2] << 8);
  }

  // Validate
  if (newActive == 0 || newActive > TOTAL_OUTPUTS) {
    sendNak();
    return;
  }
  for (int i = 0; i < TOTAL_OUTPUTS; i++) {
    if (newLeds[i] > MAX_LEDS_PER_OUTPUT) {
      sendNak();
      return;
    }
  }

  // Apply new config
  for (int i = 0; i < TOTAL_OUTPUTS; i++) {
    ledsPerOutput[i] = newLeds[i];
  }
  recalcGeometry();
  configReceived = true;

  // Reconfigure OctoWS2811 with new strip length
  reconfigureOcto();

  // Persist to EEPROM
  saveConfigToEEPROM();

  // Clear any pending state
  pendingFrameReady = false;
  activeTestPattern = -1;

  sendAck();
}

void handleTestPattern(const uint8_t* payload, size_t len) {
  if (len >= 1) {
    if (payload[0] == TEST_PATTERN_NONE) {
      activeTestPattern = -1;  // clear test mode
    } else {
      activeTestPattern = payload[0];
    }
    pendingFrameReady = false;
  }
}

void handleBrightness(const uint8_t* payload, size_t len) {
  if (len >= 1) {
    masterBrightness = payload[0];
  }
}

// -------------------------------------------------------------------
void sendCaps() {
  uint8_t payload[56];
  memset(payload, 0, sizeof(payload));

  // Firmware version string (16 bytes)
  strncpy((char*)payload, FIRMWARE_VERSION, 16);

  // Protocol version
  payload[16] = PROTOCOL_VERSION;

  // Active outputs (report current runtime value)
  payload[17] = activeOutputs;

  // LEDs per strip (report maxLedsPerStrip for OctoWS2811 context)
  payload[18] = maxLedsPerStrip & 0xFF;
  payload[19] = (maxLedsPerStrip >> 8) & 0xFF;

  // Color order (compile-time: WS2811_BGR)
  strncpy((char*)payload + 20, "BGR", 4);

  sendPacket(PKT_CAPS, payload, sizeof(payload));
}

void sendStats() {
  uint8_t payload[28];
  memcpy(payload + 0, &stats.uptimeMs, 4);
  memcpy(payload + 4, &stats.framesReceived, 4);
  memcpy(payload + 8, &stats.framesApplied, 4);
  memcpy(payload + 12, &stats.badCrc, 4);
  memcpy(payload + 16, &stats.badFrame, 4);
  memcpy(payload + 20, &stats.droppedPending, 4);
  memcpy(payload + 24, &stats.outputFps, 4);
  sendPacket(PKT_STATS, payload, sizeof(payload));
}

void sendAck() {
  sendPacket(PKT_CONFIG_ACK, nullptr, 0);
}

void sendNak() {
  sendPacket(PKT_CONFIG_NAK, nullptr, 0);
}

void sendPacket(uint8_t type, const uint8_t* payload, size_t len) {
  uint8_t raw[HEADER_SIZE + 128 + CRC_SIZE];
  size_t rawLen = build_packet(type, payload, len, raw, sizeof(raw));
  if (rawLen == 0) return;

  uint8_t encoded[sizeof(raw) * 2];
  size_t encLen = cobs_encode(raw, rawLen, encoded, sizeof(encoded));
  if (encLen == 0) return;

  Serial.write(encoded, encLen);
  Serial.write((uint8_t)0x00);  // delimiter
}

// -------------------------------------------------------------------
void applyPendingFrame() {
  // Copy pending frame data to OctoWS2811 drawing buffer.
  // pendingFrame is packed as contiguous blocks: for each output pin,
  // ledsPerOutput[pin] * 3 bytes of RGB data.
  size_t srcOffset = 0;
  for (int ch = 0; ch < TOTAL_OUTPUTS; ch++) {
    uint16_t count = ledsPerOutput[ch];
    for (uint16_t px = 0; px < count; px++) {
      uint8_t r = pendingFrame[srcOffset];
      uint8_t g = pendingFrame[srcOffset + 1];
      uint8_t b = pendingFrame[srcOffset + 2];
      srcOffset += 3;

      // Apply master brightness
      if (masterBrightness < 255) {
        r = (uint16_t)r * masterBrightness / 255;
        g = (uint16_t)g * masterBrightness / 255;
        b = (uint16_t)b * masterBrightness / 255;
      }

      // Apply watchdog fade
      if (fadeLevel < 255) {
        r = (uint16_t)r * fadeLevel / 255;
        g = (uint16_t)g * fadeLevel / 255;
        b = (uint16_t)b * fadeLevel / 255;
      }

      int stripPixel = ch * maxLedsPerStrip + px;
      leds.setPixel(stripPixel, r, g, b);
    }
    // Clear remaining pixels on this output (beyond count, up to maxLedsPerStrip)
    for (uint16_t px = count; px < maxLedsPerStrip; px++) {
      leds.setPixel(ch * maxLedsPerStrip + px, 0);
    }
  }
}

// -------------------------------------------------------------------
void clearAll() {
  for (int i = 0; i < TOTAL_OUTPUTS * (int)maxLedsPerStrip; i++) {
    leds.setPixel(i, 0);
  }
}

void setPixel(int strip, int pixel, uint8_t r, uint8_t g, uint8_t b) {
  if (strip < 0 || strip >= TOTAL_OUTPUTS) return;
  if (pixel < 0 || pixel >= (int)ledsPerOutput[strip]) return;
  int idx = strip * (int)maxLedsPerStrip + pixel;
  leds.setPixel(idx, r, g, b);
}

// -------------------------------------------------------------------
void runWatchdog() {
  if (activeTestPattern >= 0 || blackout) return;

  uint32_t elapsed = millis() - lastFrameTime;

  if (elapsed > WATCHDOG_TIMEOUT_MS && !watchdogFading) {
    watchdogFading = true;
    fadeLevel = 255;
  }

  if (watchdogFading) {
    uint32_t fadeElapsed = elapsed - WATCHDOG_TIMEOUT_MS;
    if (fadeElapsed >= FADE_DURATION_MS) {
      fadeLevel = 0;
    } else {
      fadeLevel = 255 - (uint8_t)(255UL * fadeElapsed / FADE_DURATION_MS);
    }

    // Re-apply last frame with fade
    if (!leds.busy() && stats.framesApplied > 0) {
      applyPendingFrame();
      leds.show();
    }
  }
}

// -------------------------------------------------------------------
void runTestPattern() {
  uint32_t t = millis();

  clearAll();

  switch (activeTestPattern) {
    case TEST_ALL_BLACK:
      // Already cleared
      break;

    case TEST_ALL_WHITE: {
      uint8_t v = masterBrightness / 4;  // Safe brightness
      for (int ch = 0; ch < (int)activeOutputs; ch++) {
        for (int px = 0; px < (int)ledsPerOutput[ch]; px++) {
          setPixel(ch, px, v, v, v);
        }
      }
      break;
    }

    case TEST_RGB_ORDER: {
      // Cycle through R, G, B every 2 seconds
      int phase = (t / 2000) % 3;
      uint8_t r = (phase == 0) ? 128 : 0;
      uint8_t g = (phase == 1) ? 128 : 0;
      uint8_t b = (phase == 2) ? 128 : 0;
      for (int ch = 0; ch < (int)activeOutputs; ch++) {
        for (int px = 0; px < (int)ledsPerOutput[ch]; px++) {
          setPixel(ch, px, r, g, b);
        }
      }
      break;
    }

    case TEST_CHANNEL_CHASE: {
      // One pixel chase per channel
      for (int ch = 0; ch < (int)activeOutputs; ch++) {
        int pos = (t / 10) % (int)ledsPerOutput[ch];
        uint8_t hue = (ch * 51) % 256;  // Different color per channel
        // Simple hue to RGB
        uint8_t r, g, b;
        if (hue < 85) { r = hue * 3; g = 255 - hue * 3; b = 0; }
        else if (hue < 170) { hue -= 85; r = 255 - hue * 3; g = 0; b = hue * 3; }
        else { hue -= 170; r = 0; g = hue * 3; b = 255 - hue * 3; }
        setPixel(ch, pos, r, g, b);
      }
      break;
    }

    case TEST_PIXEL_CHASE: {
      // Single pixel chasing across all channels
      int totalPixels = (int)totalLeds;
      int pos = (t / 5) % totalPixels;
      // Find which channel and pixel offset
      int remaining = pos;
      for (int ch = 0; ch < (int)activeOutputs; ch++) {
        if (remaining < (int)ledsPerOutput[ch]) {
          setPixel(ch, remaining, 255, 255, 255);
          break;
        }
        remaining -= (int)ledsPerOutput[ch];
      }
      break;
    }

    case TEST_BOTTOM_TO_TOP: {
      // White sweep from bottom to top on each channel
      for (int ch = 0; ch < (int)activeOutputs; ch++) {
        int pos = (t / 15) % (int)ledsPerOutput[ch];
        setPixel(ch, pos, 255, 255, 255);
        // Trail
        for (int trail = 1; trail <= 5; trail++) {
          int tp = pos - trail;
          if (tp >= 0) {
            uint8_t v = 255 - trail * 45;
            setPixel(ch, tp, v, v, v);
          }
        }
      }
      break;
    }

    case TEST_CHANNEL_IDENTIFY: {
      // Light each channel one at a time, cycling
      int activeChannel = (t / 2000) % (int)activeOutputs;
      for (int px = 0; px < (int)ledsPerOutput[activeChannel]; px++) {
        setPixel(activeChannel, px, 0, 128, 0);
      }
      // Show channel number with colored pixels at bottom
      for (int i = 0; i <= activeChannel; i++) {
        setPixel(activeChannel, i, 255, 0, 0);
      }
      break;
    }

    case TEST_HEARTBEAT: {
      // Gentle breathing pulse
      float phase = sin(t / 500.0) * 0.5 + 0.5;
      uint8_t v = (uint8_t)(phase * 64);
      for (int ch = 0; ch < (int)activeOutputs; ch++) {
        for (int px = 0; px < (int)ledsPerOutput[ch]; px++) {
          setPixel(ch, px, 0, v, v / 2);
        }
      }
      break;
    }

    case TEST_STRIP_IDENTIFY: {
      // Each physical strip pair gets a distinct color
      // Even strip (first half) = bright, odd strip (second half) = dimmer
      uint8_t colors[][3] = {
        {255, 0, 0}, {0, 255, 0}, {0, 0, 255}, {255, 255, 0}, {255, 0, 255}
      };
      for (int ch = 0; ch < (int)activeOutputs && ch < 5; ch++) {
        uint16_t half = ledsPerOutput[ch] / 2;
        // First half = "even" strip
        for (int px = 0; px < (int)half; px++) {
          setPixel(ch, px, colors[ch][0], colors[ch][1], colors[ch][2]);
        }
        // Second half = "odd" strip (dimmer)
        for (int px = (int)half; px < (int)ledsPerOutput[ch]; px++) {
          setPixel(ch, px, colors[ch][0] / 4, colors[ch][1] / 4, colors[ch][2] / 4);
        }
      }
      break;
    }

    case TEST_SEAM_MARKER: {
      // Mark the seam channels (first output first half, last output second half)
      float pulse = sin(t / 300.0) * 0.5 + 0.5;
      uint8_t v = (uint8_t)(pulse * 200);

      int lastCh = (int)activeOutputs - 1;
      uint16_t halfFirst = ledsPerOutput[0] / 2;
      uint16_t halfLast = ledsPerOutput[lastCh] / 2;

      // CH0, first half = red
      for (int px = 0; px < (int)halfFirst; px++) {
        setPixel(0, px, v, 0, 0);
      }

      // Last channel, second half = blue
      for (int px = (int)halfLast; px < (int)ledsPerOutput[lastCh]; px++) {
        setPixel(lastCh, px, 0, 0, v);
      }
      break;
    }
  }
}
