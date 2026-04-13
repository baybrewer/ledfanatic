/*
 * Pillar Controller — Teensy 4.1 Firmware
 *
 * Transport + output engine for OctoWS2811-driven LED pillar.
 * Receives framed pixel data over USB Serial from Raspberry Pi.
 */

#include <Arduino.h>
#include <OctoWS2811.h>
#include "config.h"
#include "protocol.h"

// --- OctoWS2811 setup ---
DMAMEM int displayMemory[LEDS_PER_STRIP * 6];
int drawingMemory[LEDS_PER_STRIP * 6];
const int octoConfig = WS2811_BGR | WS2811_800kHz;
OctoWS2811 leds(LEDS_PER_STRIP, displayMemory, drawingMemory, octoConfig);

// --- State ---
static COBSDecoder decoder;
static uint8_t pendingFrame[ACTIVE_OUTPUTS * LEDS_PER_STRIP * 3];
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
void sendPacket(uint8_t type, const uint8_t* payload, size_t len);
void applyPendingFrame();
void runTestPattern();
void runWatchdog();
void setPixel(int strip, int pixel, uint8_t r, uint8_t g, uint8_t b);
void clearAll();

// -------------------------------------------------------------------
void setup() {
  Serial.begin(115200);  // Baud ignored on Teensy USB
  leds.begin();
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
  // Frame payload: channels(1) + leds_per_ch(2) + pixel data
  if (len < 3) {
    stats.badFrame++;
    return;
  }

  uint8_t channels = payload[0];
  uint16_t ledsPerCh = payload[1] | (payload[2] << 8);

  size_t expectedPixels = (size_t)channels * ledsPerCh * 3;
  if (len - 3 < expectedPixels) {
    stats.badFrame++;
    return;
  }

  if (channels > ACTIVE_OUTPUTS || ledsPerCh > LEDS_PER_STRIP) {
    stats.badFrame++;
    return;
  }

  // Zero out then copy pixel data to pending frame
  memset(pendingFrame, 0, sizeof(pendingFrame));
  memcpy(pendingFrame, payload + 3, expectedPixels);

  if (pendingFrameReady) {
    stats.droppedPending++;
  }
  pendingFrameReady = true;
  stats.framesReceived++;
  activeTestPattern = -1;
}

void handleConfig(const uint8_t* payload, size_t len) {
  // Color order is compile-time (WS2811_BGR in OctoWS2811 config) — no-op
  (void)payload;
  (void)len;
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

  // Active outputs
  payload[17] = ACTIVE_OUTPUTS;

  // LEDs per strip
  payload[18] = LEDS_PER_STRIP & 0xFF;
  payload[19] = (LEDS_PER_STRIP >> 8) & 0xFF;

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
  // Copy pending frame data to OctoWS2811 drawing buffer
  for (int ch = 0; ch < ACTIVE_OUTPUTS; ch++) {
    int baseOffset = ch * LEDS_PER_STRIP * 3;
    for (int px = 0; px < LEDS_PER_STRIP; px++) {
      int idx = baseOffset + px * 3;
      uint8_t r = pendingFrame[idx];
      uint8_t g = pendingFrame[idx + 1];
      uint8_t b = pendingFrame[idx + 2];

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

      int stripPixel = ch * LEDS_PER_STRIP + px;
      leds.setPixel(stripPixel, r, g, b);
    }
  }

  // Clear unused channels
  for (int ch = ACTIVE_OUTPUTS; ch < TOTAL_OUTPUTS; ch++) {
    for (int px = 0; px < LEDS_PER_STRIP; px++) {
      leds.setPixel(ch * LEDS_PER_STRIP + px, 0);
    }
  }
}

// -------------------------------------------------------------------
void clearAll() {
  for (int i = 0; i < TOTAL_OUTPUTS * LEDS_PER_STRIP; i++) {
    leds.setPixel(i, 0);
  }
}

void setPixel(int strip, int pixel, uint8_t r, uint8_t g, uint8_t b) {
  if (strip < 0 || strip >= TOTAL_OUTPUTS) return;
  if (pixel < 0 || pixel >= LEDS_PER_STRIP) return;
  int idx = strip * LEDS_PER_STRIP + pixel;
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
      for (int ch = 0; ch < ACTIVE_OUTPUTS; ch++) {
        for (int px = 0; px < LEDS_PER_STRIP; px++) {
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
      for (int ch = 0; ch < ACTIVE_OUTPUTS; ch++) {
        for (int px = 0; px < LEDS_PER_STRIP; px++) {
          setPixel(ch, px, r, g, b);
        }
      }
      break;
    }

    case TEST_CHANNEL_CHASE: {
      // One pixel chase per channel
      int pos = (t / 10) % LEDS_PER_STRIP;
      for (int ch = 0; ch < ACTIVE_OUTPUTS; ch++) {
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
      int totalPixels = ACTIVE_OUTPUTS * LEDS_PER_STRIP;
      int pos = (t / 5) % totalPixels;
      int ch = pos / LEDS_PER_STRIP;
      int px = pos % LEDS_PER_STRIP;
      setPixel(ch, px, 255, 255, 255);
      break;
    }

    case TEST_BOTTOM_TO_TOP: {
      // White sweep from bottom to top on each channel
      int pos = (t / 15) % LEDS_PER_STRIP;
      for (int ch = 0; ch < ACTIVE_OUTPUTS; ch++) {
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
      int activeChannel = (t / 2000) % ACTIVE_OUTPUTS;
      for (int px = 0; px < LEDS_PER_STRIP; px++) {
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
      for (int ch = 0; ch < ACTIVE_OUTPUTS; ch++) {
        for (int px = 0; px < LEDS_PER_STRIP; px++) {
          setPixel(ch, px, 0, v, v / 2);
        }
      }
      break;
    }

    case TEST_STRIP_IDENTIFY: {
      // Each physical strip pair gets a distinct color
      // Even strip (first 172) = bright, odd strip (next 172) = dimmer version
      uint8_t colors[][3] = {
        {255, 0, 0}, {0, 255, 0}, {0, 0, 255}, {255, 255, 0}, {255, 0, 255}
      };
      for (int ch = 0; ch < ACTIVE_OUTPUTS; ch++) {
        // First half = "even" strip
        for (int px = 0; px < LEDS_PER_PHYSICAL; px++) {
          setPixel(ch, px, colors[ch][0], colors[ch][1], colors[ch][2]);
        }
        // Second half = "odd" strip (dimmer)
        for (int px = LEDS_PER_PHYSICAL; px < LEDS_PER_STRIP; px++) {
          setPixel(ch, px, colors[ch][0] / 4, colors[ch][1] / 4, colors[ch][2] / 4);
        }
      }
      break;
    }

    case TEST_SEAM_MARKER: {
      // Mark the seam channels (CH0 strip 0 and CH4 strip 9)
      float pulse = sin(t / 300.0) * 0.5 + 0.5;
      uint8_t v = (uint8_t)(pulse * 200);

      // CH0, first physical strip (S0) = red
      for (int px = 0; px < LEDS_PER_PHYSICAL; px++) {
        setPixel(0, px, v, 0, 0);
      }

      // CH4, second physical strip (S9) = blue
      for (int px = LEDS_PER_PHYSICAL; px < LEDS_PER_STRIP; px++) {
        setPixel(4, px, 0, 0, v);
      }
      break;
    }
  }
}
