#pragma once

// LED geometry — defaults match legacy 5x344 layout.
// Dynamic geometry is received via CONFIG packet from Pi at startup.

// --- LED Configuration (defaults) ---
#define DEFAULT_LEDS_PER_STRIP  344  // 2 × 172 LEDs per serpentine pair
#define DEFAULT_ACTIVE_OUTPUTS  5    // 5 serpentine pairs
#define TOTAL_OUTPUTS           8    // OctoWS2811 always addresses 8
#define MAX_LEDS_PER_OUTPUT     1200 // max LEDs on any single output
#define MAX_TOTAL_LEDS          (TOTAL_OUTPUTS * MAX_LEDS_PER_OUTPUT)
#define LEDS_PER_PHYSICAL       172  // LEDs per physical strip (for test patterns)
#define PHYSICAL_STRIPS         10   // Total physical strips (for test patterns)

// --- Protocol ---
#define PROTOCOL_VERSION  1
#define MAGIC_0  'P'
#define MAGIC_1  'I'
#define MAGIC_2  'L'
#define MAGIC_3  'L'
#define HEADER_SIZE       24
#define CRC_SIZE          4
#define MAX_PAYLOAD_SIZE  (TOTAL_OUTPUTS * MAX_LEDS_PER_OUTPUT * 3 + 8)  // worst-case frame

// --- Packet types ---
#define PKT_HELLO              0x01
#define PKT_CAPS               0x02
#define PKT_CONFIG             0x03
#define PKT_CONFIG_ACK         0x04
#define PKT_CONFIG_NAK         0x05
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
#define FIRMWARE_VERSION "1.1.0"
#define FIRMWARE_NAME    "pillar-teensy"
