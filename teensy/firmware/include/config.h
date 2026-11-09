#pragma once

// LED geometry — defaults match legacy 5x344 layout.
// Dynamic geometry is received via CONFIG packet from Pi at startup.

// --- LED Configuration (defaults) ---
#define DEFAULT_LEDS_PER_STRIP  166  // 2 × 83 LEDs per serpentine pair
#define DEFAULT_ACTIVE_OUTPUTS  5    // 5 serpentine pairs
#define TOTAL_OUTPUTS           8    // OctoWS2811 always addresses 8
#define MAX_LEDS_PER_OUTPUT     1200 // max LEDs on any single output
#define MAX_TOTAL_LEDS          (TOTAL_OUTPUTS * MAX_LEDS_PER_OUTPUT)
#define LEDS_PER_PHYSICAL       83   // LEDs per physical strip (for test patterns)
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
#define MAX_PACKET_BYTES  (HEADER_SIZE + MAX_PAYLOAD_SIZE + CRC_SIZE)        // decoded packet cap

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
#define PKT_POTS               0x31
#define PKT_DIAG               0x32  // request -> response; also pushed every 1000 ms (frame_id 0)
#define PKT_CRASH              0x33  // request -> 96-byte crash record response
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
#define TEST_PATTERN_FAULT_INJECT 0xFE  // only acted on when built with -DFAULT_INJECT*

// --- Timing ---
// Bytes drained per loop iteration. Bounded so one iteration can never spin on a
// host that streams faster than we parse; leftover bytes wait in the USB RX FIFO.
// Responses wait this long for a free USB TX buffer before being dropped.
// The core's availableForWrite() ignores the buffer being filled and counts
// only idle transfers, so it reads 0 for ~0.1-1 ms around every host IN poll.
#define TX_WAIT_US             3000
#define USB_READ_CHUNK         8192
#define STATS_INTERVAL_MS      1000

// --- Potentiometer inputs ---
#define POT_PIN_BRIGHTNESS  24   // A10
#define POT_PIN_MENU        25   // A11
#define POT_PIN_PATTERN     26   // A12
#define POTS_INTERVAL_MS    50

// --- Color order ---
#define COLOR_ORDER_RGB  0
#define COLOR_ORDER_GRB  1
#define COLOR_ORDER_BRG  2
#define COLOR_ORDER_BGR  5

// Default color order — determined by physical strip testing
#define DEFAULT_COLOR_ORDER  COLOR_ORDER_BGR

// --- Firmware info ---
#define FIRMWARE_VERSION "1.3.1"
#define FIRMWARE_NAME    "pillar-teensy"
