#pragma once

// LED geometry values must match pi/config/hardware.yaml.
// To regenerate after changing hardware.yaml:
//   python3 pi/scripts/generate_teensy_config.py
// Cross-language validation: pi/tests/test_protocol.py::TestHardwareConstants

// --- LED Configuration ---
#define LEDS_PER_STRIP    1200   // 2 × 600 LEDs per serpentine pair
#define ACTIVE_OUTPUTS    5     // 5 serpentine pairs
#define TOTAL_OUTPUTS     8     // OctoWS2811 always addresses 8
#define LEDS_PER_PHYSICAL 600   // LEDs per physical strip
#define PHYSICAL_STRIPS   10    // Total physical strips

// --- Protocol ---
#define PROTOCOL_VERSION  1
#define MAGIC_0  'P'
#define MAGIC_1  'I'
#define MAGIC_2  'L'
#define MAGIC_3  'L'
#define HEADER_SIZE       24
#define CRC_SIZE          4
#define MAX_PAYLOAD_SIZE  (ACTIVE_OUTPUTS * LEDS_PER_STRIP * 3 + 8)  // channels meta + pixel data

// --- Packet types ---
#define PKT_HELLO              0x01
#define PKT_CAPS               0x02
#define PKT_CONFIG             0x03
#define PKT_FRAME              0x10
#define PKT_PING               0x20
#define PKT_PONG               0x21
#define PKT_STATS              0x30
#define PKT_TEST_PATTERN       0x40
#define PKT_BLACKOUT           0x41
#define PKT_BRIGHTNESS         0x42
#define PKT_REBOOT_BOOTLOADER  0xFF

// --- Test patterns ---
#define TEST_ALL_BLACK         0
#define TEST_ALL_WHITE         1
#define TEST_RGB_ORDER         2
#define TEST_CHANNEL_CHASE     3
#define TEST_PIXEL_CHASE       4
#define TEST_BOTTOM_TO_TOP     5
#define TEST_CHANNEL_IDENTIFY  6
#define TEST_HEARTBEAT         7
#define TEST_STRIP_IDENTIFY    8
#define TEST_SEAM_MARKER       9
#define TEST_PATTERN_NONE      0xFF

// --- Timing ---
#define WATCHDOG_TIMEOUT_MS    500
#define FADE_DURATION_MS       250
#define USB_READ_CHUNK         4096
#define STATS_INTERVAL_MS      1000

// --- Color order ---
#define COLOR_ORDER_RGB  0
#define COLOR_ORDER_GRB  1
#define COLOR_ORDER_BRG  2
#define COLOR_ORDER_BGR  5

// Default color order — determined by physical strip testing
#define DEFAULT_COLOR_ORDER  COLOR_ORDER_BGR

// --- Firmware info ---
#define FIRMWARE_VERSION "1.0.0"
#define FIRMWARE_NAME    "pillar-teensy"
