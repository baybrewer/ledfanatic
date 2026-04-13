# 06. Teensy Firmware Specification

## 6.1 Firmware role

The Teensy firmware is a **transport + output engine**, not a scene engine.

It should:
- enumerate over USB
- report capabilities
- receive framed pixel packets
- validate them
- copy them into LED drawing buffers
- trigger OctoWS2811 output
- expose diagnostics
- fail safely

It should **not**:
- decode video
- host UI logic
- own scene storage beyond a small current-state cache

## 6.2 Hardware baseline

- Teensy 4.1
- OctoWS2811 adaptor board
- WS2812/WS2812B-class strips
- 5 active outputs
- 3 spare outputs
- USB data link from Pi

PJRC documents that OctoWS2811 uses DMA to update up to 8 strips simultaneously with minimal CPU impact.[1]

## 6.3 Output topology

### Octo configuration
- `ledsPerStrip = 344`
- `activeOutputs = 5`
- `unusedOutputs = 3`
- color order configurable, default likely `GRB`

### Why 344
Because each electrical channel represents two 172-LED strips chained in serpentine fashion.

## 6.4 Buffer strategy

Use:
- `displayMemory`
- `drawingMemory`

PJRC documents these as the normal OctoWS2811 double-buffering path.[1]

### Requirement
The firmware must never start an LED transfer from a partially written frame buffer.

### Strategy
- host packet arrives into a USB RX buffer
- packet is validated
- packet payload is copied into an application-side frame buffer
- that frame buffer is converted into the Octo drawing buffer
- when Octo is not busy, call `show()`

## 6.5 Protocol design

### Recommendation
Use a binary protocol over **USB Serial**.

PJRC documents that Teensy USB Serial is transferred at maximum USB speed and baud settings are ignored.[3]

### Why USB Serial for v1
- simple on Pi with `pyserial`
- simple on Teensy with `Serial`
- easy debugging
- plenty fast for this payload

### Packet framing
Use **COBS** or an equivalent robust packet-framing scheme with:
- explicit delimiter
- explicit payload length
- CRC32

Do not depend on raw serial read boundaries.

## 6.6 Packet types

### Required packet types
- `HELLO`
- `CAPS`
- `CONFIG`
- `FRAME`
- `PING`
- `PONG`
- `STATS`
- `TEST_PATTERN`
- `BLACKOUT`
- `BRIGHTNESS`
- `REBOOT_TO_BOOTLOADER`

### HELLO / CAPS exchange
At connection startup:

Pi sends:
- protocol version
- app name/version

Teensy replies:
- firmware version
- protocol version
- supported outputs
- `ledsPerStrip`
- color order
- active mapping mode
- stats counters

> **Current behavior:** `PING` returns `STATS` directly; `PONG` is defined but unused. Color order is compile-time GRB. `CONFIG` is defined as a packet type but `handleConfig()` in firmware is currently a no-op — runtime color-order configuration is a backlog item.

## 6.7 Packet schema (shipped)

```text
HEADER (24 bytes):
  magic           4 bytes   "PILL"
  version         1 byte
  type            1 byte
  flags           2 bytes   little-endian uint16
  frame_id        4 bytes   little-endian uint32
  timestamp_us    8 bytes   little-endian uint64
  payload_len     4 bytes   little-endian uint32

PAYLOAD (for FRAME type):
  channels        1 byte
  leds_per_ch     2 bytes   little-endian uint16
  pixel_data      N bytes   channel-major RGB triplets

CRC32             4 bytes   over header + payload

Packets are COBS-encoded with 0x00 as the delimiter.
```

### Payload order
Recommended v1 payload:
- channel-major
- RGB triplets
- contiguous
- `channel 0 pixel 0..343`, then channel 1, etc.

This keeps the Pi-side serializer and Teensy-side parser obvious.

### Why not send Octo-native packed memory?
Because that leaks Octo internals into the Pi app.
Only do that if profiling proves it is necessary.

## 6.8 Mapping inside firmware

### Decision
The Pi owns the cylinder mapping from logical `10 × 172` to `5 × 344`.

The Teensy should assume it receives already-mapped channel data.

### Why
- keeps firmware simpler
- keeps mapping edits on the Pi side
- avoids firmware reflash for layout corrections

### Exception
The Teensy should still contain a **minimal test mapping layer** for local diagnostics.

## 6.9 Frame scheduling

### Rule
The Teensy should always output the most recent **complete** valid frame.

### Behavior
- if a new frame arrives while Octo is busy, store it as pending
- when `leds.busy()` becomes false, swap/copy and `show()`
- if multiple new frames arrive before the current transfer ends, the firmware may drop older pending frames and keep only the newest complete frame

This is the correct behavior for a real-time video/effects device.

## 6.10 Local diagnostics

The Teensy firmware must implement local test patterns independent of the Pi media stack:

- all black
- all white at safe brightness
- RGB order test
- per-channel chase
- per-pixel chase
- bottom-to-top sweep
- channel identify
- heartbeat/status indicator

These should be triggerable by control packets.

## 6.11 Watchdog / stale frame behavior

If no valid frame arrives for a configurable timeout:
- either hold last frame
- or fade to black
- or switch to a configured fallback pattern

### Recommendation
Default to:
- hold last frame for 500 ms
- then fade to black over 250 ms
- unless a diagnostics mode is active

## 6.12 Brightness control

### Principle
Global brightness limiting should happen on the Pi.

### Teensy-side role
The Teensy may also expose:
- emergency master brightness scalar
- blackout command
- startup safe brightness

This is an operational safety feature, not the primary artistic control.

## 6.13 Error handling

The firmware must:
- reject bad CRC packets
- reject unsupported protocol versions
- reject bad channel counts / payload lengths
- count malformed packets
- continue running after malformed input
- expose counters via `STATS`

## 6.14 Logging / stats exposed to host

Return:
- firmware version
- uptime
- last frame ID applied
- valid frame count
- malformed packet count
- dropped-pending-frame count
- current output FPS
- current color order
- current active output count

## 6.15 Power and wiring cautions

PJRC states:
- Octo adaptor includes **74HCT245** buffering and **100-ohm** matching resistors[2]
- signal ground and LED power ground should meet near strip inputs[2]
- if Teensy is externally powered while USB is connected, the **VIN-VUSB** link must be managed correctly to avoid back-feeding the computer[2][3]

The firmware spec should assume the hardware plan has already handled this.

## 6.16 Color order / strip type

Because WS2812-family strips vary, firmware must expose configurable color order:
- `RGB`
- `GRB`
- `BRG`
- etc.

Default to `GRB`.
Confirm with the RGB order diagnostic at bring-up.

## 6.17 Pseudocode sketch

```cpp
loop() {
    read_usb_bytes();
    while (packet_available()) {
        Packet p = decode_packet();
        if (!p.valid_crc) {
            stats.bad_crc++;
            continue;
        }

        switch (p.type) {
            case CONFIG:
                apply_config(p);
                break;

            case FRAME:
                if (validate_frame(p)) {
                    copy_payload_to_pending_frame(p);
                    stats.frames_received++;
                } else {
                    stats.bad_frame++;
                }
                break;

            case TEST_PATTERN:
                activate_test_pattern(p);
                break;

            case BLACKOUT:
                blackout();
                break;
        }
    }

    if (!leds.busy() && pending_frame_ready) {
        map_pending_to_octo_draw_buffer();
        leds.show();
        stats.frames_applied++;
        pending_frame_ready = false;
    }

    run_watchdog_and_timeouts();
}
```

## 6.18 Non-negotiable firmware rules

1. Never output from a half-written frame.
2. Never block the main loop on serial reads.
3. Keep exactly one newest pending frame if needed.
4. Diagnostics must not require recompilation.
5. Mapping assumptions must be surfaced back to the host.

## References

[1] PJRC OctoWS2811 library  
[2] PJRC OctoWS2811 adaptor board  
[3] PJRC Teensy 4.1
