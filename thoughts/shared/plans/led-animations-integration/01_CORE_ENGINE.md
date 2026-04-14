# Phase 1 — Port Core Engine

## Goal

Port the shared rendering primitives from `led_sim.py` into repo-native modules. No Pygame. Pure numpy + math.

## Files to create

### `pi/app/effects/engine/` package

| File | Purpose |
|------|---------|
| `__init__.py` | Package init |
| `noise.py` | Perlin noise, FBM, cylinder-aware noise |
| `palettes.py` | All 10 standard palettes + 16 Feldstein palettes + fire palette + palette lookup |
| `color.py` | `hsv2rgb`, `clamp`, `clampf`, `scale8`, `qadd8`, `qsub8`, color math |
| `buffer.py` | LED buffer class: `set_led`, `add_led`, `clear`, `get_frame` → numpy array |

### Noise module (`noise.py`)

Port from led_sim.py lines ~100-180:
- `_perlin(x, y, z)` — 3D Perlin noise
- `noise01(x, y, z)` — normalized 0-1
- `_fbm(x, y, z, octaves, lacunarity, gain)` — fractal Brownian motion
- `cyl_noise(x, y, t, x_scale, y_scale)` — cylinder-wrapped
- `cyl_fbm(x, y, t, octaves, x_scale, y_scale)` — cylinder-wrapped FBM
- Shared permutation table `_p`

### Palettes module (`palettes.py`)

Port from led_sim.py lines ~60-100 and palette definitions:
- 10 standard palettes: Rainbow, Ocean, Sunset, Forest, Lava, Ice, Neon, Cyberpunk, Pastel, Vapor
- 16 Feldstein palettes (custom gradient stops)
- Fire palette (256-entry)
- `pal_color(pal_idx, t) → (r, g, b)` — lookup function
- `fire_color(h01) → (r, g, b)` — fire palette lookup
- `PALETTE_NAMES` — ordered list for UI dropdown

### Color module (`color.py`)

Port from led_sim.py:
- `hsv2rgb(h, s, v)` — 0-255 range HSV to RGB (the sim's version, distinct from base.py's 0-1 range)
- `clamp(v, lo, hi)`, `clampf(v, lo, hi)`
- `scale8(a, b)`, `qadd8(a, b)`, `qsub8(a, b)`

### Buffer module (`buffer.py`)

Replace the global `leds[]` array with a class:

```python
class LEDBuffer:
    def __init__(self, cols=10, rows=172):
        self.cols = cols
        self.rows = rows
        self.data = np.zeros((cols, rows, 3), dtype=np.uint8)

    def set_led(self, x, y, r, g, b):
        x = x % self.cols
        if 0 <= y < self.rows:
            self.data[x, y] = (clamp(r), clamp(g), clamp(b))

    def add_led(self, x, y, r, g, b):
        x = x % self.cols
        if 0 <= y < self.rows:
            self.data[x, y] = np.clip(
                self.data[x, y].astype(int) + [r, g, b], 0, 255
            ).astype(np.uint8)

    def clear(self):
        self.data[:] = 0

    def fade(self, factor: float):
        self.data = (self.data * factor).astype(np.uint8)

    def get_frame(self) -> np.ndarray:
        return self.data.copy()
```

## Tests

- `test_noise.py` — Perlin output range, cylinder wrapping seam-free, FBM octave count
- `test_palettes.py` — all 10 palettes produce valid RGB, fire palette range, Feldstein palettes exist
- `test_color.py` — hsv2rgb known values, clamp bounds, scale8 math
- `test_buffer.py` — set/add/clear/fade/get_frame shape and dtype

## Gate

- All engine tests pass
- No Pygame imports anywhere
- `imported_sim_helpers.py` can be deprecated in favor of engine modules
