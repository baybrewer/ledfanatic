<!-- THIS FILE IS A CONSOLIDATED COPY. DO NOT EDIT INDEPENDENTLY. -->
<!-- The numbered section files (01–10) in this directory are the authoring source. -->
<!-- For current shipped behavior, see docs/current-contracts.md -->

# MASTER SPEC

This is the consolidated version of the LED pillar planning packet.


---

# LED Pillar Planning Packet

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


---

# 01. Executive Summary

## Decision

Build the controller as a **two-plane system**:

1. **Raspberry Pi control/render plane**
   - Runs headless
   - Creates or maintains its own Wi-Fi network
   - Hosts a phone-friendly control website
   - Handles effects, low-resolution video playback, media uploads, playlists, presets, diagnostics, and audio-reactive analysis
   - Serializes finished frames to USB

2. **Teensy 4.1 output plane**
   - Receives framed pixel data over USB Serial
   - Maintains a small double-buffered LED output path
   - Uses **OctoWS2811 DMA** to refresh the strips with minimal CPU impact[1]
   - Owns diagnostics, test patterns, and fail-safe output behavior

## Why this is the right architecture

### It matches the real bottlenecks
The Pi is good at:
- networking
- a headless web stack
- file/media management
- audio analysis
- easy iteration

The Teensy + Octo path is good at:
- deterministic timing
- 8-way parallel WS2812 output
- keeping USB / computation separate from LED wave timing via DMA[1]

Trying to make one device do both well is how you get jank.

## Hard numbers

### Physical installation
- **10 physical strips**
- **172 LEDs per strip**
- **1,720 LEDs total**

### Electrical output plan
- Pair neighboring strips in series
- Use **5 active Octo outputs**
- Each active output drives **344 LEDs**
- Outputs 6–8 remain unused but reserved

### Frame-rate ceiling
PJRC states that OctoWS2811 LED output time is:

`30 microseconds × LEDs per strip + 50 microseconds reset`[1]

For **344 LEDs per active channel**:

- LED shift time = `344 × 30 µs = 10,320 µs`
- Reset = `50 µs`
- Frame time = `10,370 µs = 10.37 ms`
- Theoretical max = `1 / 0.01037 ≈ 96.4 FPS`

A conservative WS2812B reset assumption of **280 µs** yields:

- `10,320 + 280 = 10,600 µs`
- `≈ 94.3 FPS`[4]

### USB payload
If the Pi sends only the 5 active channels:

- `5 channels × 344 LEDs × 3 bytes = 5,160 bytes/frame`

At:
- **30 FPS** → 154.8 KB/s
- **60 FPS** → 309.6 KB/s
- **90 FPS** → 464.4 KB/s

If the Pi sends a fixed 8-lane payload for simplicity:

- `8 × 344 × 3 = 8,256 bytes/frame`

At:
- **30 FPS** → 247.7 KB/s
- **60 FPS** → 495.4 KB/s
- **90 FPS** → 743.0 KB/s

PJRC documents Teensy 4.1 USB device at **480 Mbit/sec**, and USB Serial runs at **maximum USB speed**, ignoring baud-rate settings.[3]

USB is not the limiter.

## Recommended target modes

| Mode | FPS target | Use case |
|---|---:|---|
| Safe/default | 60 | main operating mode |
| Compatibility | 30 | if media pipeline is stressed |
| High-performance | 75 or 90 | optional mode for lighter effects/media |
| Absolute ceiling | ~94–96 | physical WS2812 limit at 344 LEDs/channel |

## Key product requirements

1. Plug in the Pi and Teensy.
2. The Pi comes up **headless** and exposes a local Wi-Fi network.[5][6]
3. iPhone joins the network.
4. User opens a phone UI at a fixed address such as `http://192.168.4.1` and optionally `http://pillar.local`.
5. User can:
   - pick effects
   - upload and play low-res videos/GIFs/images
   - enable sound-reactive modes
   - run diagnostics and wiring tests
   - save presets/scenes
   - start playlists / autoplay
   - adjust brightness and safety caps

## Recommended MVP boundary

### Must-have in v1
- hotspot networking
- phone UI
- solid/test patterns
- scene presets
- 60 FPS render loop
- Teensy USB protocol
- cylinder mapping
- basic audio-reactive effects
- uploaded media clips
- diagnostics

### Nice-to-have in v2
- captive portal auto-redirect
- OTA update helpers
- scheduling/calendar
- remote client mode on an existing Wi-Fi network
- multi-user roles
- cloud backup / sync

## Bottom line

This is a good architecture, and it is much better aligned to your goals than trying to stretch Pixelblaze into a media server / web appliance.

## References

[1] PJRC OctoWS2811 library  
[3] PJRC Teensy 4.1  
[4] WS2812B datasheet  
[5] Raspberry Pi headless setup  
[6] NetworkManager `nmcli` hotspot


---

# 02. System Architecture

![System Architecture](assets/system_architecture.svg)

## 2.1 Architecture decision

### Chosen architecture
**Raspberry Pi as control/render appliance + Teensy 4.1 as deterministic LED output coprocessor**

This is effectively a small distributed system:

- **Phone**
  - user interface only

- **Raspberry Pi**
  - control plane
  - render plane
  - media plane
  - scene/state plane

- **Teensy 4.1**
  - real-time output plane

- **OctoWS2811 adaptor**
  - signal conditioning / buffered output

- **LED power supplies**
  - pixel power only
  - not application compute power

## 2.2 High-level data flow

```text
iPhone
  ↓ Wi-Fi
Raspberry Pi hotspot / web app
  ↓ local API + WebSocket
Render scheduler
  ↓ frame serializer
USB Serial
  ↓
Teensy 4.1
  ↓ DMA-backed OctoWS2811 buffers
5 active WS2812 output channels
  ↓
10 physical strips arranged as 5 serpentine pairs on a cylinder
```

## 2.3 Component responsibilities

| Component | Responsibilities | Must not do |
|---|---|---|
| iPhone UI | scene control, parameter changes, uploads, status, diagnostics | real-time rendering |
| Raspberry Pi | web server, hotspot, media import/transcode, audio analysis, effect generation, frame pacing, USB transport | generate WS2812 timings directly |
| Teensy 4.1 | USB packet handling, frame validation, buffer swap, OctoWS2811 driving, local fallback tests | own application UX or media decoding |
| Octo adaptor | 5V buffering, line conditioning, RJ45/CAT6 breakout[2] | compute |
| LED supplies | feed strips | power back into host USB path |

## 2.4 Why not use stock PJRC VideoDisplay unchanged?

PJRC's `VideoDisplay` example is useful because it proves the exact general idea: a computer, including a Raspberry Pi-class host, can stream video to Teensy boards over USB.[1]

But for this pillar, it is not a drop-in because:

- your logical render surface is **10 × 172**
- the stock example assumes layout conventions like **`LED_HEIGHT` multiple of 8**[1]
- you need a phone-first product UI, scene management, audio-reactive logic, and headless appliance behavior

So the example is a **reference**, not the final architecture.

## 2.5 Boot sequence

### Normal boot
1. Pi powers on.
2. Pi networking stack brings up hotspot / AP profile.
3. Pi application service starts.
4. Pi opens USB connection to Teensy.
5. Pi sends a `HELLO` / `CONFIG` packet.
6. Teensy responds with firmware + capability info.
7. UI becomes reachable from phone.
8. Last-used scene autostarts after a short delay.
9. Operator can override from phone.

### Degraded boot: Teensy missing
1. Pi hotspot and web UI still come up.
2. UI shows hardware-disconnected status.
3. Effects can still preview in software.
4. When Teensy appears, Pi auto-binds and resumes output.

### Degraded boot: Pi app crashes
1. Teensy holds the last valid frame or enters a configurable safe state.
2. systemd restarts the Pi service.
3. UI reconnects automatically.

## 2.6 Networking mode

### Recommended default
- Pi boots into **dedicated AP/hotspot mode**
- fixed SSID/password
- static local gateway IP, e.g. `192.168.4.1`
- local web UI on `http://192.168.4.1`
- mDNS alias such as `pillar.local`

Raspberry Pi supports headless deployment, and NetworkManager's `nmcli` supports creating a Wi-Fi hotspot connection profile.[5][6]

### Why AP-first is the correct decision
- no dependency on venue Wi-Fi
- predictable phone workflow
- no monitor required
- no LAN admin rights required
- field-deployable

### Optional future mode
- client mode on existing Wi-Fi
- still retain an AP fallback profile

## 2.7 Process / service model on Pi

### Preferred deployment model
One main application service with internal worker tasks plus one network profile:

- `pillar.service`
  - FastAPI web server
  - WebSocket live updates
  - render scheduler
  - audio analysis worker
  - media playback worker
  - USB transport worker
  - persistent state manager

- NetworkManager hotspot profile
  - configured once
  - auto-start on boot

This is simpler than splitting everything into several IPC-heavy services.

## 2.8 Control-plane vs output-plane contract

The Pi owns:
- **what to show**
- scene graph / presets
- timing target
- transitions
- audio analysis
- media selection
- diagnostics triggers

The Teensy owns:
- **how to get pixels on the wire**
- bounded-latency packet ingestion
- frame validation
- output DMA
- strip test modes

This separation is the entire point of the architecture.

## 2.9 Failure containment rules

| Failure | Containment rule |
|---|---|
| UI browser disconnects | current scene continues |
| Audio source disappears | effect falls back to non-audio mode |
| Video decode stalls | last good frame holds; transport does not send partial data |
| USB hiccup | Teensy drops malformed frame, keeps last valid frame |
| Hotspot unavailable | Pi app still boots and logs error |
| One LED PSU fails | only that powered segment should go dark; controller survives |

## 2.10 Core design principles for implementation

1. **Keep USB protocol binary and explicit.**
2. **Do not let the Teensy perform heavyweight rendering.**
3. **Do not let the Pi try to generate WS2812 timings directly under Linux.**
4. **Treat wiring/mapping as a first-class subsystem.**
5. **Optimize for stable 60 FPS first; chase higher FPS only after instrumentation.**
6. **Make diagnostics accessible from the phone UI.**

## References

[1] PJRC OctoWS2811 library  
[2] PJRC OctoWS2811 adaptor board  
[5] Raspberry Pi headless setup  
[6] NetworkManager `nmcli` hotspot


---

# 03. Performance Budget

## 3.1 Project geometry and channelization

| Item | Value |
|---|---:|
| Physical strips | 10 |
| LEDs per strip | 172 |
| Total LEDs | 1,720 |
| Electrical arrangement | 5 serpentine pairs |
| LEDs per active channel | 344 |
| Active Octo outputs | 5 |
| Spare Octo outputs | 3 |

## 3.2 WS2812 refresh ceiling

PJRC documents OctoWS2811 timing as:

- output update time = **30 µs per LED**
- plus **50 µs reset**[1]

Worldsemi's WS2812B datasheet separately documents 800 Kbps timing and reset timing above **50 µs**.[4]

### For 344 LEDs/channel
- `344 × 30 µs = 10,320 µs`
- `+ 50 µs = 10,370 µs`
- `max FPS ≈ 96.43`

### Conservative reset assumption
Some WS2812-family parts and variants are treated more conservatively with a larger reset margin. Using `280 µs` instead:

- `10,320 + 280 = 10,600 µs`
- `max FPS ≈ 94.34`

### For the earlier 340-LED/channel case
- `340 × 30 + 50 = 10,250 µs`
- `max FPS ≈ 97.56`

Conservative:
- `340 × 30 + 280 = 10,480 µs`
- `max FPS ≈ 95.42`

## 3.3 Practical FPS recommendation

| Mode | Recommended | Why |
|---|---:|---|
| Guaranteed operating mode | 60 FPS | healthy margin below strip ceiling |
| Legacy/safe mode | 30 FPS | for debugging or heavy imports |
| Performance mode | 75–90 FPS | optional; depends on Pi-side pipeline |
| Not worth targeting | 95+ FPS | no safety margin; little UX gain |

The correct product target is **stable 60 FPS**.

## 3.4 USB transport budget

PJRC documents Teensy 4.1 USB device as **480 Mbit/sec**, and USB Serial transfers bytes in both directions at **maximum USB speed**, ignoring baud-rate settings.[3]

### Payload option A — 5 active channels only
`5 × 344 × 3 = 5,160 bytes/frame`

| FPS | Payload/sec |
|---|---:|
| 30 | 154.8 KB/s |
| 60 | 309.6 KB/s |
| 90 | 464.4 KB/s |

### Payload option B — fixed 8-lane payload
`8 × 344 × 3 = 8,256 bytes/frame`

| FPS | Payload/sec |
|---|---:|
| 30 | 247.7 KB/s |
| 60 | 495.4 KB/s |
| 90 | 743.0 KB/s |

Either option is tiny compared with USB headroom.

### Recommendation
Use **Option A** in protocol v1 for cleaner semantics:
- less wasted bandwidth
- clearer packet definitions
- explicit active channel count

If implementation simplicity matters more than bandwidth, Option B is still fine.

## 3.5 Teensy memory budget

PJRC documents:
- Teensy 4.1 has **1024K RAM**
- OctoWS2811 needs display memory of **6 integers × ledsPerStrip**
- drawing memory is another **6 integers × ledsPerStrip**, or can be `NULL`[1][3]

For `ledsPerStrip = 344`:

- one buffer = `6 × 344 ints`
- if `int = 4 bytes`, one buffer = `6 × 344 × 4 = 8,256 bytes`
- two buffers = `16,512 bytes`

This is trivial on Teensy 4.1.

## 3.6 Pi-side render cost

Your physical output is only **10 × 172 = 1,720 pixels**.

That is tiny.

The real CPU cost on the Pi is not pixel count. It is:
- video decode
- image resampling
- audio FFT / beat detection
- web stack overhead
- Python / JS runtime overhead
- filesystem / media import work

That is why the implementation should:
1. transcode uploads into a project-native cached format;
2. keep the real-time render surface small;
3. keep the Teensy protocol binary.

## 3.7 Suggested render strategy

### Real-time scene canvas
Maintain a logical frame buffer of:
- `height = 172`
- `width = 10`
- `channels = RGB`

### Optional supersampled internal canvas
For smoother rotational or video motion, render internally at:
- `width = 40` or `60`
- `height = 172`

Then downsample to the 10 physical columns before serialization.

This helps because 10 angular columns around a cylinder is extremely low horizontal resolution.

## 3.8 End-to-end latency model

The LED output time is the hard floor once a new frame starts transmission to the strips: about **10.37 ms** at 344 LEDs/channel.[1]

A realistic software pipeline at 60 FPS should budget roughly:

| Stage | Target budget |
|---|---:|
| scene/effect evaluation | 1–3 ms |
| media/frame extraction | 0–4 ms |
| audio feature update | 0–2 ms |
| mapping + serialization | <1 ms |
| USB write | <1 ms |
| Teensy parse / copy | <1 ms |
| LED wire time | 10.37 ms |

That keeps you in the right ballpark for a one-frame real-time loop.

## 3.9 Power budget

Adafruit's practical guidance:
- worst-case WS2812/NeoPixel current is **60 mA/pixel** at full-white
- practical rule of thumb is **20 mA/pixel** average for many animations[7]

### Whole installation
- worst-case: `1,720 × 60 mA = 103.2 A`
- rule-of-thumb typical: `1,720 × 20 mA = 34.4 A`

### Per physical strip (172 LEDs)
- worst-case: `172 × 60 mA = 10.32 A`
- rule-of-thumb: `172 × 20 mA = 3.44 A`

### Consequence
You should not design the mechanical/power system assuming "one little 5V supply somewhere."

Use:
- distributed power injection
- common ground
- conservative brightness cap in software
- heavy gauge power distribution

## 3.10 Engineering conclusions

1. **USB is not the bottleneck.**
2. **Teensy RAM is not the bottleneck.**
3. **The WS2812 channel length is the real hard FPS limiter.**
4. **Stable 60 FPS is comfortably achievable.**
5. **The only way to lose is sloppy Pi software, bad power distribution, or wrong mapping.**

## References

[1] PJRC OctoWS2811 library  
[3] PJRC Teensy 4.1  
[4] WS2812B datasheet  
[7] Adafruit NeoPixel power guidance


---

# 04. Wiring and Mapping

![Cylinder Mapping](assets/cylinder_mapping.svg)

## 4.1 Physical assumption

This planning packet assumes the following physical arrangement:

- The pillar is viewed from the **outside**
- The 10 strips are numbered around the circumference as:
  - `S0, S1, S2, ... S9`
- Numbering increases **clockwise when viewed from above**
- Each strip runs vertically
- Each adjacent pair is wired in series so the signal:
  - starts at the **bottom** of the first strip in the pair
  - runs **up** that strip
  - jumps across at the **top**
  - then runs **down** the neighboring strip

If the real mechanical build ends up mirrored or seam-shifted, only the mapping config changes. The software architecture does not.

## 4.2 Electrical pairing plan

| Octo output | Physical strips | Direction on first strip | Direction on second strip | LEDs total |
|---|---|---|---|---:|
| CH0 | S0 + S1 | bottom → top | top → bottom | 344 |
| CH1 | S2 + S3 | bottom → top | top → bottom | 344 |
| CH2 | S4 + S5 | bottom → top | top → bottom | 344 |
| CH3 | S6 + S7 | bottom → top | top → bottom | 344 |
| CH4 | S8 + S9 | bottom → top | top → bottom | 344 |
| CH5 | unused | — | — | 0 |
| CH6 | unused | — | — | 0 |
| CH7 | unused | — | — | 0 |

OctoWS2811 is designed for 8 equal-length strips, but PJRC explicitly allows shorter or unused outputs, addressed as if 8 full strips existed.[1]

## 4.3 Standard Octo pin assignments

If you use the OctoWS2811 adaptor and default pin list, PJRC documents the default output pins for Teensy 4.0/4.1 as:[1]

| Strip / channel | Teensy pin |
|---|---:|
| CH0 | 2 |
| CH1 | 14 |
| CH2 | 7 |
| CH3 | 8 |
| CH4 | 6 |
| CH5 | 20 |
| CH6 | 21 |
| CH7 | 5 |

Teensy 4.x can also use custom pin groups if needed, but default pins are fine for this build.[1]

## 4.4 Logical render model

### Rule
The Pi should always render to a **logical cylindrical canvas**:

- width = **10** columns
- height = **172** rows
- each column corresponds to one physical strip
- row `0` = **bottom**
- row `171` = **top**

This is the only sane way to keep effects/video understandable.

### Why this matters
The wiring is serpentine.
The content should not be.

The mapping layer exists specifically so:
- artists and UI controls think in **10 strip-columns**
- hardware gets the **5 chained channels** it physically needs

## 4.5 Mapping formula

Let:

- `x` = logical strip column, `0..9`
- `y` = logical row, `0..171`
- `N = 172`

Then:

```python
channel = x // 2

if x % 2 == 0:
    # first strip in the pair, wired bottom -> top
    index = y
else:
    # second strip in the pair, wired top -> bottom
    index = (2 * N - 1) - y   # 343 - y
```

Examples:

| Logical pixel `(x, y)` | Physical channel | Channel index |
|---|---:|---:|
| (0, 0) | CH0 | 0 |
| (0, 171) | CH0 | 171 |
| (1, 171) | CH0 | 172 |
| (1, 0) | CH0 | 343 |
| (2, 0) | CH1 | 0 |
| (3, 0) | CH1 | 343 |
| (9, 0) | CH4 | 343 |

## 4.6 Unwrapped cylinder view

```text
Viewed as a flat unwrapped surface, outside of cylinder facing you:

   x=0   x=1   x=2   x=3   x=4   x=5   x=6   x=7   x=8   x=9
   S0    S1    S2    S3    S4    S5    S6    S7    S8    S9

top  ↑     ↓     ↑     ↓     ↑     ↓     ↑     ↓     ↑     ↓
     |     |     |     |     |     |     |     |     |     |
     |     |     |     |     |     |     |     |     |     |
bot  0    343    0    343    0    343    0    343    0    343
```

A more useful mental model is:

```text
CH0: S0 bottom -> top, jump, S1 top -> bottom
CH1: S2 bottom -> top, jump, S3 top -> bottom
CH2: S4 bottom -> top, jump, S5 top -> bottom
CH3: S6 bottom -> top, jump, S7 top -> bottom
CH4: S8 bottom -> top, jump, S9 top -> bottom
```

## 4.7 Seam handling

The pillar has a natural seam between:
- `S9` and `S0`

Effects and video should support wraparound horizontally:
- `x = -1` maps to `x = 9`
- `x = 10` maps to `x = 0`

This matters for:
- rotating effects
- scrolling text
- polar/radial visualizations
- seamless video panoramas

## 4.8 Commissioning tests

The UI must expose these tests:

| Test | Purpose |
|---|---|
| one-strip-at-a-time color wash | verify physical strip numbering |
| bottom-to-top white sweep | verify vertical orientation |
| paired serpentine chase | verify top jumper direction |
| seam marker on S0/S9 | verify wrap boundary |
| channel test CH0..CH4 | verify Octo output assignment |
| RGB order test | verify GRB vs RGB assumption |

## 4.9 Wiring quality requirements

PJRC recommends:
- LED power supply ground and Teensy signal ground should meet at or near the strip signal inputs
- LED power supplies should be close to the strips with large-diameter wires
- the Octo adaptor's 74HCT245 buffer and 100-ohm impedance matching help signal quality[2]

Adafruit recommends:
- common ground first
- 300–500 Ω resistor near the first pixel input
- separate LED power should be applied before the microcontroller if possible
- 5V-powered NeoPixels ideally receive a 5V data signal[7]

Because the Octo adaptor already includes a **74HCT245 buffer chip and 100 Ω matching resistors**, do **not** add another level shifter in front of those same outputs unless you know exactly why.[2]

## 4.10 Power distribution rules

### Required
- Each supply's **ground** must be common with controller ground.
- Different supplies' **+5V rails must not be tied together** across different strip runs.[7]
- Inject power at minimum at:
  - the bottom of each physical strip
  - and preferably both ends of long runs

### Strong recommendation
Treat each **172-pixel physical strip** as a power-injection segment even though two are chained for data.

## 4.11 USB power vs external 5V caution

PJRC notes that VIN and VUSB are tied on Teensy unless the board pads are cut, and external 5V should not be back-fed into the computer over USB.[2][3]

So if the Octo adaptor / LED-side 5V also powers the Teensy, you must make a clean power decision:

### Option A — preferred
- Pi USB powers Teensy logic
- LED supplies power only the strips and Octo buffer side
- grounds common
- no 5V back-feed path to the Pi

### Option B
- external 5V powers Teensy
- **cut VIN-VUSB link** as PJRC instructs[2][3]
- keep USB data connected only

Do not wing this part.

## References

[1] PJRC OctoWS2811 library  
[2] PJRC OctoWS2811 adaptor board  
[3] PJRC Teensy 4.1  
[7] Adafruit NeoPixel best practices


---

# 05. Raspberry Pi Software Specification

## 5.1 Product role of the Pi

The Raspberry Pi is the **user-facing appliance**.

It should feel like a headless media controller with an LED-specific operating surface:
- plug in power
- connect phone to local Wi-Fi
- open the control site
- pick content
- pillar responds immediately

The Pi should not try to bit-bang LEDs directly.

## 5.2 Operating system baseline

### Recommended baseline
- Raspberry Pi OS Lite, 64-bit
- headless install
- SSH enabled for development
- systemd-managed app startup
- local persistent storage for media, config, and logs

Raspberry Pi documents headless setup as a supported workflow.[5]

### Hardware assumption
Assume **Pi 4B or newer** for full feature set.
A Pi 3 can be made to work for some cases, but it is the wrong baseline if you care about clean media and web responsiveness.

## 5.3 Networking / headless UX

### Required behavior
On boot, the Pi should:
1. bring up a dedicated Wi-Fi hotspot;
2. expose a fixed local IP;
3. run the web UI;
4. be operable from an iPhone with no other infrastructure.

### Recommended implementation
Use a persistent NetworkManager hotspot profile on `wlan0`.

NetworkManager documents hotspot creation via `nmcli dev wifi hotspot ...`.[6]

### Suggested network defaults
- SSID: `Pillar-Control`
- Password: project-specific
- Pi IP: `192.168.4.1`
- UI URL: `http://192.168.4.1`
- optional mDNS: `http://pillar.local`

### UX note
A captive portal is nice, but not required for v1.
The fixed IP and mDNS name are enough.

## 5.4 Service layout

### Preferred deployment
One main Python application service:

`pillar.service`

Modules:
- `api` — REST + WebSocket
- `state` — config, presets, scenes, playlists
- `renderer` — effects + media compositing
- `audio` — FFT / onset / beat detection
- `mapping` — logical-to-physical transform
- `transport` — USB protocol to Teensy
- `diagnostics` — previews, test patterns, logs
- `media` — import/transcode/cache

### Why one service
- simpler deployment
- simpler restart semantics
- no internal IPC tax
- easier to debug

Threads and asyncio tasks are enough here.

## 5.5 Suggested software stack

### Backend
- Python 3.11+
- FastAPI
- uvicorn
- Pydantic models
- WebSockets for live status and UI updates
- pyserial for Teensy transport
- NumPy for frame ops
- Pillow for image work
- PyAV or ffmpeg-based import/transcode
- aubio + NumPy for beat/onset/FFT
- watchdog or polling for media library updates

### Frontend
- responsive mobile-first web app
- static frontend served by FastAPI
- installable PWA optional
- live preview canvas
- large touch-friendly controls
- no desktop-specific assumptions

### UI framework
Any of these is acceptable:
- plain HTML/CSS/JS with a very small framework
- Svelte
- Vue

Do **not** choose a framework that creates a giant build chain for no gain.

### Recommendation
Use:
- **FastAPI backend**
- **vanilla JS or a light framework**
- **WebSocket for live state**
- **CSS grid/flex for phone layout**

This is enough.

## 5.6 UI information architecture

### Main screens

#### 1. Live
- current scene
- master brightness
- FPS
- connection status
- quick scene buttons
- preview

#### 2. Effects
- generative effect list
- per-effect parameters
- palette selection
- speed / scale / rotation / blend controls

#### 3. Media
- upload image/GIF/video
- preview
- fit mode
- playback rate
- loop / hold / bounce

#### 4. Audio
- select input device
- sensitivity
- gain
- smoothing
- beat/onset indicators
- effect modulation routing

#### 5. Mapping / Diagnostics
- strip numbering test
- orientation test
- RGB order test
- seam test
- brightness test
- identify physical channel

#### 6. Scenes / Presets
- save current state
- recall presets
- playlist builder
- autoplay / schedule hooks

#### 7. System
- Wi-Fi settings
- hostname / mDNS
- logs
- restart app
- reboot Pi
- firmware version
- Teensy stats

## 5.7 Render model

### Canonical logical frame
The renderer outputs a logical RGB canvas:

- shape: `(height=172, width=10, channels=3)`

### Optional supersampled internal canvas
For smoother motion:
- internal width 40 or 60
- same height 172
- downsample to width 10 before physical mapping

### Scene processing order
1. base source
   - effect
   - video
   - image
   - diagnostic
2. optional modulation
   - audio envelope
   - beat trigger
   - timebase
3. optional transforms
   - rotate / wrap
   - hue / palette
   - mirror
   - fade / blend
4. postprocess
   - brightness clamp
   - gamma
   - dither optional
5. map to 5 electrical channels
6. serialize and send

## 5.8 Media handling strategy

### Strong recommendation
Do **not** rely exclusively on decoding arbitrary user video formats in the real-time loop.

Instead:

### On import
- decode source asset
- resize/crop to pillar-native or pillar-virtual size
- normalize frame rate
- store a cached project-native representation

### Project-native cache options
- compressed NumPy frame arrays
- zstd-packed RGB frames
- image sequence + metadata
- prebuilt sprite/video clip format

### Why
- deterministic playback
- easier scrubbing
- less CPU jitter
- faster preview

### Recommended v1 choice
Import media to:
- `virtual_width = 40`
- `height = 172`
- target playback rates: 30 or 60 FPS
- cache as compressed frame arrays + JSON metadata

## 5.9 Audio-reactive pipeline

### Inputs
Prefer one of:
- USB audio interface with line in
- USB microphone
- compatible I2S microphone

### Features to compute
- RMS / loudness
- FFT band energies
- beat/onset
- tempo estimate
- peak hold / decay

### Output controls
Audio features can modulate:
- brightness
- hue shift
- effect speed
- radial motion
- pulse size
- strobe triggers
- video blend amount

### Rule
Audio analysis runs on its own cadence.
It should update shared modulation state.
It should not block the frame loop.

## 5.10 Effect inventory for v1

### Base effects
- solid color
- vertical gradient
- rainbow rotate
- plasma
- twinkle
- spark
- noise wash
- color wipe
- scanline / comet
- fire-ish vertical effect
- sine-wave bands
- cylinder rotate
- seam pulse
- diagnostic labels

### Audio-reactive effects
- VU pulse
- low/mid/high color bands
- beat flash
- rotating energy ring
- spectral column glow
- kick-snare-hat mapper

### Media modes
- video clip
- animated GIF
- still image
- scrolling text
- playlist item with transition

## 5.11 Persistent data model

### Must persist
- system config
- hotspot config
- mapping config
- scene definitions
- presets
- playlists
- media metadata
- last used scene
- brightness cap
- calibration data

### Suggested layout
```text
/opt/pillar/
  app/
  ui/
  config/
  media/
  cache/
  logs/
```

## 5.12 API design

### Control API classes
- `/api/system/*`
- `/api/scenes/*`
- `/api/effects/*`
- `/api/media/*`
- `/api/audio/*`
- `/api/mapping/*`
- `/api/diagnostics/*`
- `/api/transport/*`

### Live updates
Use WebSocket for:
- current FPS
- Teensy online/offline
- current scene state
- audio meter
- upload progress
- error banners

## 5.13 Safety and guardrails

The Pi app must enforce:
- global brightness cap
- per-scene FPS target
- input validation for uploads
- no partial frame transmit
- crash-safe last-known config writes
- rate-limited reboot/system actions from UI

## 5.14 Observability

Expose:
- current FPS
- dropped frame count
- USB reconnect count
- frame send latency
- current scene name
- audio input status
- CPU temperature
- Pi CPU utilization
- Teensy firmware version

## 5.15 Product-level UX requirements

The UI must be usable one-handed on an iPhone.
That means:
- large tap targets
- no desktop hover dependencies
- minimal nested menus
- persistent quick-access controls for brightness and blackout
- scene recall in one tap
- diagnostics reachable without SSH

## 5.16 Non-negotiable implementation rules

1. Phone UI must work without internet.
2. Application must auto-start on boot.
3. A browser refresh must not interrupt playback.
4. Media upload/transcode must not block the live frame loop.
5. USB disconnect/reconnect must self-heal.
6. Mapping must be configurable in a file, not hard-coded in ten places.

## References

[5] Raspberry Pi headless setup  
[6] NetworkManager hotspot  
[1] PJRC OctoWS2811 library


---

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

## 6.7 Suggested FRAME packet schema

```text
magic         4 bytes   e.g. 'PILL'
version       1 byte
type          1 byte    FRAME
flags         2 bytes
frame_id      4 bytes
timestamp_us  8 bytes
channels      1 byte    5
leds_per_ch   2 bytes   344
payload_len   4 bytes
payload       N bytes   channel-major RGB data
crc32         4 bytes
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


---

# 07. Implementation Plan

## 7.1 Repository structure

```text
pillar-controller/
  README.md
  docs/
  pi/
    app/
      api/
      audio/
      core/
      diagnostics/
      effects/
      mapping/
      media/
      models/
      transport/
      ui/
    config/
    scripts/
    systemd/
    tests/
  teensy/
    firmware/
      src/
      include/
      test/
  tools/
    asset_prep/
    bench/
```

## 7.2 Build order

### Phase 1 — Hardware bring-up
Deliverables:
- Teensy firmware that drives 5 outputs
- test patterns
- verified color order
- verified strip numbering
- verified safe power-up sequence

Exit criteria:
- every strip can be identified
- mapping assumptions are confirmed physically
- no flicker at 60 FPS static updates

### Phase 2 — USB protocol
Deliverables:
- Pi ↔ Teensy hello/config handshake
- frame packet send/receive
- stats endpoint
- packet CRC validation

Exit criteria:
- Pi can push full frames reliably for at least 10 minutes
- no frame corruption
- reconnect works

### Phase 3 — Minimal web UI
Deliverables:
- hotspot boot
- phone-accessible UI
- live preview
- scene select
- brightness slider
- blackout button
- diagnostics tab

Exit criteria:
- user can control the pillar from an iPhone with no monitor

### Phase 4 — Effects engine
Deliverables:
- at least 8 core effects
- per-effect parameter model
- preset save/load
- scene recall

Exit criteria:
- effects stable at 60 FPS

### Phase 5 — Media pipeline
Deliverables:
- upload image/GIF/video
- import/transcode cache
- playback controls
- loop / speed / fit controls

Exit criteria:
- cached video clips play smoothly at 30 and 60 FPS modes

### Phase 6 — Audio-reactive
Deliverables:
- audio device selection
- FFT + beat detection
- modulation routing
- at least 3 audio-reactive scenes

Exit criteria:
- live audio visibly modulates effects with stable frame output

### Phase 7 — Hardening
Deliverables:
- crash-safe config writes
- startup restore
- reconnect robustness
- logs / metrics
- system actions from UI

Exit criteria:
- appliance-like behavior after repeated reboots and disconnects

## 7.3 Work packages for Claude / Opus

### WP1 — teensy firmware scaffold
Implement:
- Octo init
- output config
- test patterns
- stats counters
- USB serial setup

### WP2 — protocol library
Implement shared packet schema:
- framing
- CRC32
- serializer/deserializer
- typed command objects

### WP3 — Pi transport
Implement:
- device discovery
- reconnect loop
- handshake
- async frame send
- stats query

### WP4 — mapping engine
Implement:
- logical 10x172 frame
- 5x344 electrical serializer
- seam wrap
- config-driven strip order / inversion

### WP5 — core renderer
Implement:
- scene loop
- effect registry
- timing clock
- brightness clamp
- gamma correction

### WP6 — UI backend
Implement:
- FastAPI app
- REST models
- WebSocket status stream
- config persistence

### WP7 — UI frontend
Implement:
- mobile-first control panel
- diagnostics
- media upload
- scene list
- audio controls
- system page

### WP8 — media import
Implement:
- upload endpoint
- validation
- ffmpeg/PyAV transcode
- cache format
- preview generation

### WP9 — audio analysis
Implement:
- input device selection
- beat/onset
- band levels
- modulator state

### WP10 — system integration
Implement:
- systemd units
- first-run scripts
- hotspot profile instructions
- logs and health endpoints

## 7.4 Recommended task ordering inside each work package

Always implement in this order:
1. deterministic core
2. instrumentation
3. UI wrapper
4. polish

Do not start with beautiful screens while transport is still wrong.

## 7.5 Configuration files to define early

Create these on day 1:

### `hardware.yaml`
- active channels
- leds per strip
- color order
- strip numbering
- seam position
- inversions

### `system.yaml`
- hotspot SSID/password
- hostname
- UI port
- brightness cap
- startup scene

### `effects.yaml`
- built-in effect defaults
- scene parameters
- palette defaults

## 7.6 Suggested coding standards

- strict packet schemas
- no magic numbers in mapping code
- structured logs
- config-driven behavior
- unit tests for mapping math
- integration tests for protocol parsing
- explicit versioning for packet format

## 7.7 Critical path risks

| Risk | Why it matters | Mitigation |
|---|---|---|
| wrong physical strip order | breaks all rendering | diagnostics + mapping config |
| power injection too weak | flicker / wrong colors | power test before software blame |
| USB back-feed | can damage or destabilize host | explicit power plan |
| media decode jitter | causes dropped frames | import/transcode cache |
| audio device weirdness | blocks sound-reactive features | support device selection and fallback |
| trying to over-generalize | slows delivery | build for this pillar first |

## 7.8 Milestone acceptance checklist

| Milestone | Acceptance |
|---|---|
| M1 | all strips identified and mapped correctly |
| M2 | full frame transport stable |
| M3 | iPhone control works headless |
| M4 | core effects stable at 60 FPS |
| M5 | imported media plays correctly |
| M6 | audio-reactive scenes working |
| M7 | system survives reboot/disconnect cycles |

## 7.9 Engineering stance

The right move is to get to a robust, narrow, pillar-specific implementation first.
Do not waste time building a general-purpose lighting platform until this device behaves like an appliance.



---

# 08. Test and Acceptance Plan

## 8.1 Bring-up test sequence

### Test 1 — power-off continuity and wiring review
Verify:
- strip labeling
- pair jumpers at the top
- PSU segmentation
- common ground plan
- no shared +5V rails between different supplies unless explicitly designed

### Test 2 — Teensy + Octo only
Run local Teensy diagnostics:
- CH0..CH4 identify
- RGB order
- white sweep
- blackout

Pass criteria:
- every expected strip lights on the correct channel
- no random flicker
- no swapped colors

### Test 3 — serpentine verification
Run a bottom-to-top sweep on each logical strip.

Pass criteria:
- S0 rises bottom→top
- S1 also appears bottom→top in logical view, even though its wiring is physically reversed
- same for all pairs

### Test 4 — seam verification
Display markers on S0 and S9.

Pass criteria:
- seam is exactly where expected in the mechanical build

## 8.2 Protocol tests

### Test 5 — handshake
- Pi connects
- Teensy reports firmware + config
- UI shows connected status

### Test 6 — bad packet rejection
Inject malformed payloads.

Pass criteria:
- no crash
- bad packet counters increment
- last valid frame persists

### Test 7 — reconnect
Unplug/replug USB.

Pass criteria:
- Pi reconnects automatically
- playback resumes without operator SSH

## 8.3 Performance tests

### Test 8 — static 60 FPS
Send constant frames at 60 FPS for 30 minutes.

Pass criteria:
- zero visible judder
- no protocol errors
- no unexplained flicker

### Test 9 — animated 60 FPS
Run heavy built-in effects for 30 minutes.

Pass criteria:
- frame time stable
- UI responsive
- no thermal crash

### Test 10 — media playback
Play cached clips at 30 and 60 FPS.

Pass criteria:
- no tearing
- no obvious stalls
- playback controls work

### Test 11 — stress mode
Run 75 or 90 FPS mode if implemented.

Pass criteria:
- acceptable stability
- if not stable, system gracefully falls back to 60

## 8.4 Audio tests

### Test 12 — live input selection
Switch between supported audio devices.

Pass criteria:
- meter updates
- audio-reactive scenes respond
- no render loop stalls

### Test 13 — beat/onset
Use strong transient music.

Pass criteria:
- beat-triggered scenes react cleanly
- sensitivity controls are meaningful

## 8.5 UX tests

### Test 14 — iPhone-only operation
Use only an iPhone after plugging in the system.

Pass criteria:
- join hotspot
- open UI
- select scenes
- upload media
- run diagnostics
- reboot app from UI if needed

### Test 15 — no-monitor recovery
Reboot entire system with no keyboard, mouse, or monitor.

Pass criteria:
- hotspot returns
- last scene or idle scene loads
- system is operable from phone

## 8.6 Safety tests

### Test 16 — brightness cap
Attempt to set max white and high brightness.

Pass criteria:
- software cap applies
- no PSU instability
- no major voltage drop artifacts

### Test 17 — brownout-ish behavior
Lower brightness margins / long-run test.

Pass criteria:
- if supply issues exist, diagnostics reveal them clearly
- system does not corrupt state

## 8.7 Final acceptance criteria

The system is accepted when all of the following are true:

1. 10-strip cylinder is mapped correctly.
2. Headless Pi boot produces a working local control network.
3. iPhone UI can control effects and media reliably.
4. Teensy output is stable at **60 FPS**.
5. Imported media playback works.
6. Audio-reactive modes work.
7. Reconnect/reboot behavior is appliance-grade.
8. Diagnostics make physical troubleshooting possible without recompiling code.

## 8.8 Nice-to-have acceptance targets

- 75/90 FPS optional mode
- PWA install prompt
- mDNS access works on iPhone
- captive portal redirect
- scene scheduling


---

# 09. Claude / Opus Handoff Prompt

Use this prompt as the first instruction when handing the planning packet to Claude Code / Opus.

---

You are implementing a headless Raspberry Pi + Teensy 4.1 LED pillar controller.

Read these files in order:

1. `00_INDEX.md`
2. `01_EXECUTIVE_SUMMARY.md`
3. `02_SYSTEM_ARCHITECTURE.md`
4. `03_PERFORMANCE_BUDGET.md`
5. `04_WIRING_AND_MAPPING.md`
6. `05_PI_SOFTWARE_SPEC.md`
7. `06_TEENSY_FIRMWARE_SPEC.md`
8. `07_IMPLEMENTATION_PLAN.md`
9. `08_TEST_AND_ACCEPTANCE_PLAN.md`
10. `10_SOURCES.md`

Then do the following:

## Mission

Build a working implementation for a cylindrical LED pillar made from:
- 10 physical WS2812 strips
- 172 LEDs per strip
- paired into 5 serpentine chains of 344 LEDs each
- driven by a Teensy 4.1 + OctoWS2811 adaptor
- controlled by a Raspberry Pi over USB
- operated from an iPhone via a headless local Wi-Fi control site

## Required architectural rules

1. The Raspberry Pi owns:
   - hotspot/AP behavior
   - phone UI
   - media uploads and caching
   - video/effects/audio-reactive rendering
   - scene state
   - USB frame transmission

2. The Teensy owns:
   - USB packet handling
   - OctoWS2811 DMA output
   - diagnostics / local test patterns
   - bounded-latency frame application

3. The Pi renders a **logical 10 x 172 cylindrical frame**.
4. The Pi maps that logical frame to **5 x 344 electrical channels** before sending.
5. The Teensy should not perform heavyweight rendering.
6. The first stable milestone is **60 FPS**.
7. Design for appliance behavior, not lab-demo behavior.

## Implementation priorities

First:
- hardware config
- Teensy firmware skeleton
- USB framing protocol
- Pi transport layer
- mapping math
- diagnostic test patterns

Second:
- minimal web UI
- hotspot boot behavior
- scene save/load
- stable live frame loop

Third:
- media upload/import
- cached playback
- audio-reactive features
- playlists and system page

## Constraints

- Do not over-abstract.
- Do not build a general-purpose lighting engine before the pillar works.
- Keep config file driven.
- Write tests for mapping and packet parsing.
- Prefer clear, debuggable code over clever code.
- Surface useful logs and diagnostics.

## Deliverables

Produce:
1. repo structure
2. Pi application
3. Teensy firmware
4. systemd service files
5. setup docs
6. test docs
7. any helper scripts required to deploy to the Pi

## If you hit ambiguity

Prefer the assumptions in the planning packet.
If a question is still blocking, ask only tightly scoped implementation questions.

---

End of prompt.


---

# Sources and factual basis

These are the primary references used to anchor the planning packet.

1. PJRC — OctoWS2811 library  
   https://www.pjrc.com/teensy/td_libs_OctoWS2811.html

2. PJRC — OctoWS2811 adaptor board  
   https://www.pjrc.com/store/octo28_adaptor.html

3. PJRC — Teensy 4.1 development board  
   https://www.pjrc.com/store/teensy41.html

4. Worldsemi / SparkFun mirror — WS2812B datasheet  
   https://cdn.sparkfun.com/assets/e/6/1/f/4/WS2812B-LED-datasheet.pdf

5. Raspberry Pi documentation — headless setup  
   https://www.raspberrypi.com/documentation/computers/getting-started.html

6. NetworkManager reference manual — nmcli hotspot  
   https://networkmanager.dev/docs/api/latest/nmcli.html

7. Adafruit NeoPixel Überguide — powering and best practices  
   https://learn.adafruit.com/adafruit-neopixel-uberguide/powering-neopixels  
   https://learn.adafruit.com/adafruit-neopixel-uberguide?view=all

## Key extracted facts

- OctoWS2811 updates up to 8 strips simultaneously with DMA and minimal CPU impact.
- `show()` returns quickly and LED output time is `30 microseconds × LEDs per strip + 50 microseconds reset`.
- OctoWS2811 expects equal strip lengths; shorter/unused outputs are allowed.
- Teensy 4.1 USB device port runs at 480 Mbit/sec and USB Serial transfers at maximum USB speed.
- Raspberry Pi can be set up headless and NetworkManager can create a Wi-Fi hotspot.
- Separate LED power supplies must share ground but should not have their +5V rails tied together across strips fed by different supplies.
- NeoPixel rule-of-thumb power is ~20 mA/pixel typical and 60 mA/pixel worst-case full-white.
