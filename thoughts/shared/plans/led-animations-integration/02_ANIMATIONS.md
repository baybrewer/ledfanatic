# Phase 2 — Port All 27 Animations

## Goal

Port every animation class from `led_sim.py` as a repo-native `Effect` subclass. Each animation uses `LEDBuffer` + engine modules, no Pygame.

## File layout

```
pi/app/effects/
  imported/
    __init__.py          # registers all ported effects
    classic.py           # 5 classic animations
    ambient.py           # 12 ambient animations
    sound.py             # 10 sound-reactive animations
```

## Adapter pattern

Each ported animation wraps the sim's update logic inside the repo Effect interface:

```python
class PortedEffect(Effect):
    CATEGORY = "imported_classic"  # or imported_ambient, imported_sound
    DISPLAY_NAME = "Rainbow Cycle"
    DESCRIPTION = "Smooth rainbow color cycling across the pillar"
    PALETTE_SUPPORT = True
    PARAMS = [
        {"name": "speed", "label": "Speed", "min": 0.1, "max": 5.0, "step": 0.1, "default": 1.0},
    ]

    def __init__(self, width=10, height=172, params=None):
        super().__init__(width, height, params)
        self.buf = LEDBuffer(width, height)
        self.palette_idx = 0
        self.speed = self.params.get('speed', 1.0)
        self._last_t = None

    def render(self, t, state):
        if self._last_t is None:
            self._last_t = t
        dt_ms = max(0, (t - self._last_t) * 1000)
        self._last_t = t

        # Animation-specific update using self.buf, engine modules
        self._update(dt_ms, state)
        return self.buf.get_frame()
```

## Classic animations to port (5)

| Name | Key logic | Params |
|------|-----------|--------|
| RainbowCycle | Rotating hue gradient | speed |
| FeldsteinEquation | Cylinder noise + traveling bars | speed, bar_speed |
| Feldstein2 (OG) | Three CHSV layers with fadeToBlack | speed, fade, palette (0-16) |
| BrettsFavorite | Sine bands with drift and kicks | speed, bands (4-32), damping |
| Fireplace | 2D convection fire with ember particles | fuel (master), speed |

## Ambient animations to port (12)

| Name | Key logic | Params |
|------|-----------|--------|
| Plasma | Overlapping sine waves + Perlin | speed, scale |
| Aurora | Perlin curtains + wave + shimmer | speed, wave, bright |
| LavaLamp | Gaussian blob orbits (Lissajous) | speed, blobs (2-12), size |
| OceanWaves | Multi-layer sine waves + depth | speed, depth, layers (1-5) |
| Starfield | Particle stars + twinkle | density, twinkle, speed |
| MatrixRain | Falling streaks with fade trails | speed, density, trail |
| Breathing | Full-matrix sinusoid pulse | speed, wave |
| Fireflies | Brownian particles with glow | count (3-60), speed, glow |
| Nebula | 2D Perlin FBM at two scales | speed, scale, layers (1-3) |
| Kaleidoscope | Radial mirror symmetry mandala | speed, segments (3-12), zoom |
| FlowField | Fidenza-style particle trails | speed, particles (10-200), fade, noise_scale |
| Moire | Overlapping rings from orbiting centers | speed, scale, centers (2-5) |

## Sound-reactive animations to port (10)

Each uses the `AudioCompatAdapter` to get the extended audio surface.

| Name | Key logic | Audio deps | Params |
|------|-----------|-----------|--------|
| Spectrum | FFT bars per column + peak decay | bands | gain, decay |
| VUMeter | Single volume bar + breakdown/drop states | volume, buildup, breakdown, drop | gain, decay |
| BeatPulse | Full-matrix beat flash + drop strobe | beat, drop, breakdown, time_s | decay, flash |
| BassFire | Fire driven by bass + beat/phrase flares | bands, bass, beat, beat_energy, drop, is_downbeat, is_phrase | gain, base_spark |
| SoundRipples | Concentric rings from kick/snare/hat | bass, mids, highs, beat, beat_energy, is_downbeat, is_phrase | gain, speed, decay, sensitivity |
| Spectrogram | Scrolling FFT waterfall | bands, drop | gain, scroll_speed |
| SoundWorm | Audio-driven sine worm | volume, buildup, drop | gain, speed, width |
| ParticleBurst | Beat-triggered fireworks | beat, buildup, breakdown, drop | gravity, speed, count |
| SoundPlasma | Plasma modulated by volume | volume, buildup, breakdown, drop | gain, base_speed |
| StrobeChaos | Segment strobe on beats/drops | beat, breakdown, drop | intensity, segments |

## Registration

In `pi/app/effects/imported/__init__.py`:

```python
IMPORTED_EFFECTS = {}  # name → class
IMPORTED_CLASSIC = {}
IMPORTED_AMBIENT = {}
IMPORTED_SOUND = {}

# Populate from each module
# Register into renderer in main.py
```

Update `main.py` to register all imported effects into the renderer and catalog.

## Tests

- Every animation returns `(10, 172, 3)` uint8
- Time continuity: render twice, no crash
- Palette switching works
- Speed param affects output
- Sound animations degrade gracefully with no audio (zeros)
- Batch gating: B2/B3 sound animations only activate when audio adapter provides required fields

## Gate

- All 27 animations render without error
- Registered in effect catalog with correct metadata
- Show in Effects tab grouped by category
