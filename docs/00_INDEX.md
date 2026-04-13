# LED Pillar Planning Packet

**Note:** The numbered section files below are the authoring source for the planning packet. `MASTER_SPEC.md` is a consolidated copy and should not be edited independently — any changes should be made in the numbered files. The section docs describe the original design intent; where they conflict with shipped code, the shipped code and `docs/current-contracts.md` are authoritative.

This packet is designed to be handed directly to Claude Code / Opus as the implementation specification for a Raspberry Pi + Teensy 4.1 + OctoWS2811 LED pillar controller.

## One-line conclusion

Yes: **Raspberry Pi → USB → Teensy 4.1 → OctoWS2811 → WS2812 strips** is a sound architecture for this project, and with the current physical plan of **10 strips × 172 LEDs**, paired into **5 serpentine channels of 344 LEDs each**, the hard WS2812 wire-time ceiling is about **96.4 FPS** using PJRC's `30 µs per LED + 50 µs reset` timing, or about **94.3 FPS** using a more conservative `280 µs` reset assumption.[1][4]

That means:

- **30 FPS** is trivial.
- **60 FPS** is the correct default product target.
- **90-ish FPS** is physically possible if the Pi pipeline and USB framing stay lean.
- The Teensy is not the bottleneck here; **the longest WS2812 channel is**.

## Files in this packet

- `MASTER_SPEC.md` — consolidated version if you want to upload only one file
- `01_EXECUTIVE_SUMMARY.md`
- `02_SYSTEM_ARCHITECTURE.md`
- `03_PERFORMANCE_BUDGET.md`
- `04_WIRING_AND_MAPPING.md`
- `05_PI_SOFTWARE_SPEC.md`
- `06_TEENSY_FIRMWARE_SPEC.md`
- `07_IMPLEMENTATION_PLAN.md`
- `08_TEST_AND_ACCEPTANCE_PLAN.md`
- `09_CLAUDE_HANDOFF_PROMPT.md`
- `10_SOURCES.md`
- `assets/cylinder_mapping.svg`
- `assets/system_architecture.svg`

## Recommended implementation stance

- Use **one Teensy 4.1 + OctoWS2811 adaptor**
- Wire **5 active outputs**, one per pair of adjacent strips
- Let the **Pi own all UX, media, audio-reactive logic, scene selection, uploads, playlists, and control**
- Let the **Teensy own only packet ingestion, mapping to Octo buffers, and DMA-driven LED output**
- Keep the render model **logical 10 × 172 cylindrical canvas**, regardless of the serpentine wiring

## Important caveat

PJRC's stock `VideoDisplay` example proves that Raspberry Pi-like computers can stream video to Teensy boards, but that example has its own layout assumptions, including `LED_HEIGHT` being a multiple of 8. Your pillar's logical height is **172**, so the example is a **reference implementation only**, not a drop-in solution.[1]

## Footnotes

[1] PJRC OctoWS2811 library — https://www.pjrc.com/teensy/td_libs_OctoWS2811.html  
[4] WS2812B datasheet — https://cdn.sparkfun.com/assets/e/6/1/f/4/WS2812B-LED-datasheet.pdf
