# Dynamic Grid Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace all hardcoded LED geometry with a dynamic pixel map so any arrangement of LED strips maps to an arbitrary rectangular grid.

**Architecture:** A `pixel_map.yaml` config file defines strips with scanlines that map LEDs to (x,y) grid positions. At startup, this compiles into forward/reverse lookup tables. Effects render to the grid dimensions. A new output packer maps the rendered frame to strip data using the reverse LUT, with per-segment color order swizzle. Teensy is configured at runtime via a CONFIG packet.

**Tech Stack:** Python 3.11, FastAPI, NumPy, YAML, C++ (Teensy 4.1, OctoWS2811)

---

## File Structure

### New Files
| File | Responsibility |
|------|---------------|
| `pi/app/config/pixel_map.py` | Load, validate, compile pixel_map.yaml → CompiledPixelMap |
| `pi/app/mapping/packer.py` | pack_frame(): rendered grid → serialized output buffer |
| `pi/app/api/routes/pixel_map.py` | CRUD API for pixel map editing |
| `pi/config/pixel_map.yaml` | Default pixel map config (replaces hardware.yaml geometry + installation.yaml) |
| `pi/tests/test_pixel_map.py` | Tests for pixel map loading, validation, compilation |
| `pi/tests/test_packer.py` | Tests for output packing + color order swizzle |

### Deleted Files
| File | Replaced By |
|------|------------|
| `pi/app/mapping/cylinder.py` | `pixel_map.py` + `packer.py` |
| `pi/app/mapping/runtime_mapper.py` | `packer.py` |
| `pi/app/mapping/runtime_plan.py` | `pixel_map.py` (CompiledPixelMap) |
| `pi/app/config/installation.py` | `pixel_map.py` |
| `pi/app/hardware_constants.py` | Grid dimensions derived from pixel map |

### Modified Files (key changes only)
| File | Change |
|------|--------|
| `pi/app/core/renderer.py` | Use CompiledPixelMap, call pack_frame(), handle RENDER_SCALE, propagate origin to RenderState |
| `pi/app/core/renderer.py:RenderState` | Add `origin: str` and `grid_width: int`, `grid_height: int` fields |
| `pi/app/effects/base.py` | Remove N, no defaults, add RENDER_SCALE |
| `pi/app/effects/engine/buffer.py` | Remove default cols/rows |
| `pi/app/effects/generative.py` | Remove N import, use self.height |
| `pi/app/effects/audio_reactive.py` | Remove N, dynamic bin resampling |
| `pi/app/effects/imported/*.py` | Remove N import (5 files) |
| `pi/app/effects/switcher.py` | Remove hardcoded height |
| `pi/app/diagnostics/patterns.py` | Remove hardcoded constants |
| `pi/app/preview/service.py` | Use pixel map dimensions |
| `pi/app/transport/usb.py` | Remove default params, add send_config + wait_for_config_ack |
| `pi/app/models/protocol.py` | CONFIG packet payload, CONFIG_ACK/CONFIG_NAK packet types |
| `pi/app/main.py` | Load pixel map, send CONFIG, wire dimensions, store in deps |
| `pi/app/api/deps.py` (or equivalent) | Add `pixel_map_config`, `compiled_pixel_map` fields to AppDeps |
| `pi/app/api/server.py` | Register pixel_map router, remove old setup router |
| `teensy/firmware/include/config.h` | Remove hardcoded geometry, add defaults, add CONFIG_ACK/NAK types |
| `teensy/firmware/src/main.cpp` | CONFIG handler with ACK/NAK, max-sized static DMAMEM allocation, EEPROM persistence |

---

### Task 1: Pixel Map Data Model

**Files:**
- Create: `pi/app/config/pixel_map.py`
- Create: `pi/tests/test_pixel_map.py`
- Create: `pi/config/pixel_map.yaml`

- [ ] **Step 1: Write tests for pixel map loading and validation**

```python
# pi/tests/test_pixel_map.py
import pytest
import numpy as np
from pathlib import Path
from app.config.pixel_map import (
    load_pixel_map, compile_pixel_map, validate_pixel_map,
    PixelMapConfig, StripConfig, ScanlineConfig, SegmentConfig,
    CompiledPixelMap,
)


def _simple_map() -> PixelMapConfig:
    """2-column, 3-row grid. Strip 0: col 0 up, col 1 down."""
    return PixelMapConfig(
        origin='bottom_left',
        teensy_outputs=8,
        teensy_max_leds_per_output=1200,
        strips=[
            StripConfig(
                id=0,
                output=0,
                output_offset=0,
                total_leds=6,
                segments=[SegmentConfig(range_start=0, range_end=5, color_order='BGR')],
                scanlines=[
                    ScanlineConfig(start=(0, 0), end=(0, 2)),  # 3 LEDs up
                    ScanlineConfig(start=(1, 2), end=(1, 0)),  # 3 LEDs down
                ],
            ),
        ],
    )


class TestScanlineLedCount:
    def test_vertical_up(self):
        sl = ScanlineConfig(start=(0, 0), end=(0, 5))
        assert sl.led_count() == 6

    def test_vertical_down(self):
        sl = ScanlineConfig(start=(1, 5), end=(1, 0))
        assert sl.led_count() == 6

    def test_horizontal_right(self):
        sl = ScanlineConfig(start=(0, 3), end=(5, 3))
        assert sl.led_count() == 6

    def test_horizontal_left(self):
        sl = ScanlineConfig(start=(5, 3), end=(0, 3))
        assert sl.led_count() == 6

    def test_diagonal_rejected(self):
        sl = ScanlineConfig(start=(0, 0), end=(3, 4))
        with pytest.raises(ValueError, match="axis-aligned"):
            sl.led_count()


class TestValidation:
    def test_valid_map(self):
        cfg = _simple_map()
        errors = validate_pixel_map(cfg)
        assert errors == []

    def test_scanline_total_mismatch(self):
        cfg = _simple_map()
        cfg.strips[0].total_leds = 99  # doesn't match scanlines (6)
        errors = validate_pixel_map(cfg)
        assert any('total_leds' in e for e in errors)

    def test_duplicate_grid_position(self):
        cfg = _simple_map()
        # Make second scanline overlap first
        cfg.strips[0].scanlines[1] = ScanlineConfig(start=(0, 0), end=(0, 2))
        errors = validate_pixel_map(cfg)
        assert any('duplicate' in e.lower() or 'overlap' in e.lower() for e in errors)

    def test_output_overflow(self):
        cfg = _simple_map()
        cfg.strips[0].output_offset = 1199
        cfg.strips[0].total_leds = 6
        errors = validate_pixel_map(cfg)
        assert any('exceed' in e.lower() for e in errors)

    def test_segment_coverage(self):
        cfg = _simple_map()
        cfg.strips[0].segments = [
            SegmentConfig(range_start=0, range_end=2, color_order='BGR'),
            # Gap: 3-5 not covered
        ]
        errors = validate_pixel_map(cfg)
        assert any('segment' in e.lower() for e in errors)

    def test_negative_coordinates(self):
        cfg = _simple_map()
        cfg.strips[0].scanlines[0] = ScanlineConfig(start=(-1, 0), end=(-1, 2))
        errors = validate_pixel_map(cfg)
        assert any('negative' in e.lower() or 'non-negative' in e.lower() for e in errors)

    def test_segment_range_exceeds_total_leds(self):
        cfg = _simple_map()
        cfg.strips[0].segments = [
            SegmentConfig(range_start=0, range_end=99, color_order='BGR'),
        ]
        errors = validate_pixel_map(cfg)
        assert any('segment' in e.lower() for e in errors)


class TestCompilation:
    def test_grid_dimensions(self):
        cfg = _simple_map()
        compiled = compile_pixel_map(cfg)
        assert compiled.width == 2
        assert compiled.height == 3

    def test_forward_lut(self):
        cfg = _simple_map()
        compiled = compile_pixel_map(cfg)
        # Col 0, row 0 = strip 0, LED 0
        assert compiled.forward_lut[0, 0, 0] == 0  # strip_id
        assert compiled.forward_lut[0, 0, 1] == 0  # led_index
        # Col 1, row 2 = strip 0, LED 3 (first LED of second scanline)
        assert compiled.forward_lut[1, 2, 0] == 0
        assert compiled.forward_lut[1, 2, 1] == 3

    def test_reverse_lut(self):
        cfg = _simple_map()
        compiled = compile_pixel_map(cfg)
        # LED 0 on strip 0 → (0, 0)
        x, y, co = compiled.reverse_lut[0][0]
        assert (x, y) == (0, 0)
        # LED 5 on strip 0 → (1, 0) — last LED of second scanline going down
        x, y, co = compiled.reverse_lut[0][5]
        assert (x, y) == (1, 0)

    def test_output_config(self):
        cfg = _simple_map()
        compiled = compile_pixel_map(cfg)
        assert compiled.output_config[0] == 6  # 6 LEDs on output 0
        assert compiled.output_config[1] == 0  # nothing on output 1

    def test_unmapped_cells(self):
        cfg = _simple_map()
        compiled = compile_pixel_map(cfg)
        # All cells are mapped in this 2x3 grid, so no unmapped
        assert compiled.total_mapped_leds == 6


class TestLoadFromYaml:
    def test_load_default(self, tmp_path):
        yaml_content = """
schema_version: 1
origin: bottom_left
teensy:
  outputs: 8
  max_leds_per_output: 1200
strips:
  - id: 0
    output: 0
    output_offset: 0
    total_leds: 4
    segments:
      - range: [0, 3]
        color_order: BGR
    scanlines:
      - start: [0, 0]
        end: [0, 3]
"""
        p = tmp_path / "pixel_map.yaml"
        p.write_text(yaml_content)
        cfg = load_pixel_map(tmp_path)
        assert cfg.strips[0].total_leds == 4
        assert cfg.origin == 'bottom_left'
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd pi && PYTHONPATH=. pytest tests/test_pixel_map.py -v
```
Expected: `ModuleNotFoundError: No module named 'app.config.pixel_map'`

- [ ] **Step 3: Implement pixel_map.py**

```python
# pi/app/config/pixel_map.py
"""
Pixel map — single source of truth for LED geometry.

Loads pixel_map.yaml, validates strip/scanline/segment definitions,
compiles into forward and reverse lookup tables for the render pipeline.
"""

import logging
import yaml
import numpy as np
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

VALID_COLOR_ORDERS = ('RGB', 'RBG', 'GRB', 'GBR', 'BRG', 'BGR')

# Precomputed swizzle indices: maps source RGB to wire order
_SWIZZLE_MAP = {
    'RGB': (0, 1, 2),
    'RBG': (0, 2, 1),
    'GRB': (1, 0, 2),
    'GBR': (1, 2, 0),
    'BRG': (2, 0, 1),
    'BGR': (2, 1, 0),
}


@dataclass
class ScanlineConfig:
    start: tuple[int, int]  # (x, y)
    end: tuple[int, int]    # (x, y)

    def led_count(self) -> int:
        dx = self.end[0] - self.start[0]
        dy = self.end[1] - self.start[1]
        if dx != 0 and dy != 0:
            raise ValueError(
                f"Scanline must be axis-aligned (horizontal or vertical), "
                f"got start={self.start} end={self.end}"
            )
        return abs(dx) + abs(dy) + 1

    def positions(self) -> list[tuple[int, int]]:
        """Generate (x, y) for each LED in order from start to end."""
        dx = self.end[0] - self.start[0]
        dy = self.end[1] - self.start[1]
        if dx != 0 and dy != 0:
            raise ValueError(
                f"Scanline must be axis-aligned, got start={self.start} end={self.end}"
            )
        count = abs(dx) + abs(dy) + 1
        step_x = (1 if dx > 0 else -1) if dx != 0 else 0
        step_y = (1 if dy > 0 else -1) if dy != 0 else 0
        return [
            (self.start[0] + i * step_x, self.start[1] + i * step_y)
            for i in range(count)
        ]


@dataclass
class SegmentConfig:
    range_start: int
    range_end: int
    color_order: str = 'BGR'


@dataclass
class StripConfig:
    id: int
    output: int
    output_offset: int
    total_leds: int
    segments: list[SegmentConfig] = field(default_factory=list)
    scanlines: list[ScanlineConfig] = field(default_factory=list)
    pixel_overrides: dict[int, tuple[int, int]] = field(default_factory=dict)
    # pixel_overrides: {led_index: (x, y)} — applied after scanline expansion


@dataclass
class PixelMapConfig:
    origin: str = 'bottom_left'
    teensy_outputs: int = 8
    teensy_max_leds_per_output: int = 1200
    teensy_wire_order: str = 'BGR'
    teensy_signal_family: str = 'ws281x_800khz'
    teensy_octo_pins: list[int] = field(default_factory=lambda: [2, 14, 7, 8, 6, 20, 21, 5])
    strips: list[StripConfig] = field(default_factory=list)


@dataclass(frozen=True)
class CompiledPixelMap:
    width: int
    height: int
    origin: str
    forward_lut: np.ndarray       # (width, height, 2) int16: [strip_id, led_index] or [-1, -1]
    reverse_lut: list[list[tuple]] # reverse_lut[strip_id][led_index] → (x, y, swizzle)
    output_config: list[int]       # LEDs needed per output pin [0..7]
    strips: tuple                  # frozen strip metadata
    total_mapped_leds: int
    teensy_outputs: int
    teensy_max_leds_per_output: int


def load_pixel_map(config_dir: Path) -> PixelMapConfig:
    """Load pixel_map.yaml from config directory."""
    path = config_dir / 'pixel_map.yaml'
    if not path.exists():
        logger.warning(f"No pixel_map.yaml found at {path}, using empty config")
        return PixelMapConfig()

    with open(path) as f:
        data = yaml.safe_load(f)

    teensy = data.get('teensy', {})
    strips = []
    for s in data.get('strips', []):
        segments = [
            SegmentConfig(
                range_start=seg['range'][0],
                range_end=seg['range'][1],
                color_order=seg.get('color_order', 'BGR'),
            )
            for seg in s.get('segments', [])
        ]
        scanlines = [
            ScanlineConfig(
                start=tuple(sl['start']),
                end=tuple(sl['end']),
            )
            for sl in s.get('scanlines', [])
        ]
        pixel_overrides = {
            int(k): tuple(v) for k, v in s.get('pixel_overrides', {}).items()
        }
        strips.append(StripConfig(
            id=s['id'],
            output=s.get('output', 0),
            output_offset=s.get('output_offset', 0),
            total_leds=s.get('total_leds', 0),
            segments=segments,
            scanlines=scanlines,
            pixel_overrides=pixel_overrides,
        ))

    return PixelMapConfig(
        origin=data.get('origin', 'bottom_left'),
        teensy_outputs=teensy.get('outputs', 8),
        teensy_max_leds_per_output=teensy.get('max_leds_per_output', 1200),
        teensy_wire_order=teensy.get('controller_wire_order', 'BGR'),
        teensy_signal_family=teensy.get('signal_family', 'ws281x_800khz'),
        teensy_octo_pins=teensy.get('octo_pins', [2, 14, 7, 8, 6, 20, 21, 5]),
        strips=strips,
    )


def save_pixel_map(config: PixelMapConfig, config_dir: Path):
    """Save pixel_map.yaml atomically."""
    import os
    import tempfile

    path = config_dir / 'pixel_map.yaml'
    config_dir.mkdir(parents=True, exist_ok=True)

    data = {
        'schema_version': 1,
        'origin': config.origin,
        'teensy': {
            'outputs': config.teensy_outputs,
            'max_leds_per_output': config.teensy_max_leds_per_output,
            'controller_wire_order': config.teensy_wire_order,
            'signal_family': config.teensy_signal_family,
            'octo_pins': config.teensy_octo_pins,
        },
        'strips': [
            {
                'id': s.id,
                'output': s.output,
                'output_offset': s.output_offset,
                'total_leds': s.total_leds,
                'segments': [
                    {'range': [seg.range_start, seg.range_end], 'color_order': seg.color_order}
                    for seg in s.segments
                ],
                'scanlines': [
                    {'start': list(sl.start), 'end': list(sl.end)}
                    for sl in s.scanlines
                ],
                **(
                    {'pixel_overrides': {str(k): list(v) for k, v in s.pixel_overrides.items()}}
                    if s.pixel_overrides else {}
                ),
            }
            for s in config.strips
        ],
    }

    fd, tmp_path = tempfile.mkstemp(dir=str(config_dir), suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)
        os.replace(tmp_path, str(path))
        logger.info("Saved pixel_map.yaml")
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def validate_pixel_map(config: PixelMapConfig) -> list[str]:
    """Validate pixel map config. Returns list of error strings (empty = valid)."""
    errors = []
    seen_positions: dict[tuple[int, int], tuple[int, int]] = {}  # (x,y) → (strip_id, led_idx)

    for strip in config.strips:
        # Output pin range
        if not 0 <= strip.output < config.teensy_outputs:
            errors.append(f"Strip {strip.id}: output {strip.output} out of range [0, {config.teensy_outputs - 1}]")

        # Output overflow
        if strip.output_offset + strip.total_leds > config.teensy_max_leds_per_output:
            errors.append(
                f"Strip {strip.id}: output_offset + total_leds "
                f"({strip.output_offset + strip.total_leds}) exceeds "
                f"max_leds_per_output ({config.teensy_max_leds_per_output})"
            )

        # Scanline LED count vs total_leds
        scanline_total = 0
        for sl in strip.scanlines:
            try:
                scanline_total += sl.led_count()
            except ValueError as e:
                errors.append(f"Strip {strip.id}: {e}")

        if scanline_total != strip.total_leds:
            errors.append(
                f"Strip {strip.id}: scanline total ({scanline_total}) "
                f"!= total_leds ({strip.total_leds})"
            )

        # Segment coverage
        covered = set()
        for seg in strip.segments:
            if seg.color_order not in VALID_COLOR_ORDERS:
                errors.append(f"Strip {strip.id}: invalid color_order '{seg.color_order}'")
            for i in range(seg.range_start, seg.range_end + 1):
                if i in covered:
                    errors.append(f"Strip {strip.id}: overlapping segment at LED {i}")
                covered.add(i)
        expected = set(range(strip.total_leds))
        missing = expected - covered
        if missing:
            errors.append(f"Strip {strip.id}: segment gaps at LEDs {sorted(missing)[:5]}...")

        # Coordinate non-negativity
        for sl in strip.scanlines:
            for coord in [sl.start, sl.end]:
                if coord[0] < 0 or coord[1] < 0:
                    errors.append(
                        f"Strip {strip.id}: non-negative coordinates required, "
                        f"got {coord}"
                    )

        # Segment range bounds
        for seg in strip.segments:
            if seg.range_end >= strip.total_leds:
                errors.append(
                    f"Strip {strip.id}: segment range_end {seg.range_end} "
                    f">= total_leds {strip.total_leds}"
                )

        # Check for duplicate grid positions
        led_idx = 0
        for sl in strip.scanlines:
            try:
                for pos in sl.positions():
                    if pos in seen_positions:
                        other = seen_positions[pos]
                        errors.append(
                            f"Duplicate grid position {pos}: strip {strip.id} LED {led_idx} "
                            f"and strip {other[0]} LED {other[1]}"
                        )
                    else:
                        seen_positions[pos] = (strip.id, led_idx)
                    led_idx += 1
            except ValueError:
                led_idx += 1  # already reported above

    return errors


def compile_pixel_map(config: PixelMapConfig) -> CompiledPixelMap:
    """Compile a validated PixelMapConfig into lookup tables."""
    # Collect all positions to determine grid size
    all_positions: list[tuple[int, int, int, int]] = []  # (x, y, strip_id, led_index)
    for strip in config.strips:
        led_idx = 0
        for sl in strip.scanlines:
            for pos in sl.positions():
                all_positions.append((pos[0], pos[1], strip.id, led_idx))
                led_idx += 1
        # Apply single-pixel overrides (replace scanline-derived positions)
        for led_idx_override, (ox, oy) in strip.pixel_overrides.items():
            # Remove the scanline-derived entry for this LED and replace
            all_positions = [
                p for p in all_positions
                if not (p[2] == strip.id and p[3] == led_idx_override)
            ]
            all_positions.append((ox, oy, strip.id, led_idx_override))

    if not all_positions:
        return CompiledPixelMap(
            width=0, height=0, origin=config.origin,
            forward_lut=np.zeros((0, 0, 2), dtype=np.int16),
            reverse_lut=[], output_config=[0] * 8,
            strips=(), total_mapped_leds=0,
            teensy_outputs=config.teensy_outputs,
            teensy_max_leds_per_output=config.teensy_max_leds_per_output,
        )

    max_x = max(p[0] for p in all_positions)
    max_y = max(p[1] for p in all_positions)
    width = max_x + 1
    height = max_y + 1

    # Forward LUT: grid[x][y] → (strip_id, led_index) or (-1, -1)
    forward_lut = np.full((width, height, 2), -1, dtype=np.int16)
    for x, y, strip_id, led_idx in all_positions:
        forward_lut[x, y] = [strip_id, led_idx]

    # Build segment lookup: for each strip, led_index → swizzle tuple
    strip_id_to_idx = {s.id: i for i, s in enumerate(config.strips)}
    segment_swizzles: dict[int, dict[int, tuple[int, int, int]]] = {}
    for strip in config.strips:
        led_swizzle = {}
        for seg in strip.segments:
            swizzle = _SWIZZLE_MAP.get(seg.color_order, (0, 1, 2))
            for i in range(seg.range_start, seg.range_end + 1):
                led_swizzle[i] = swizzle
        segment_swizzles[strip.id] = led_swizzle

    # Reverse LUT: reverse_lut[strip_array_idx][led_index] → (x, y, swizzle)
    max_strip_id = max(s.id for s in config.strips)
    reverse_lut: list[list[tuple]] = [[] for _ in range(max_strip_id + 1)]
    for strip in config.strips:
        leds = [None] * strip.total_leds
        reverse_lut[strip.id] = leds

    for x, y, strip_id, led_idx in all_positions:
        swizzle = segment_swizzles.get(strip_id, {}).get(led_idx, (0, 1, 2))
        reverse_lut[strip_id][led_idx] = (x, y, swizzle)

    # Output config: max LED index per output pin
    output_config = [0] * 8
    for strip in config.strips:
        pin = strip.output
        needed = strip.output_offset + strip.total_leds
        if needed > output_config[pin]:
            output_config[pin] = needed

    return CompiledPixelMap(
        width=width,
        height=height,
        origin=config.origin,
        forward_lut=forward_lut,
        reverse_lut=reverse_lut,
        output_config=output_config,
        strips=tuple(config.strips),
        total_mapped_leds=len(all_positions),
        teensy_outputs=config.teensy_outputs,
        teensy_max_leds_per_output=config.teensy_max_leds_per_output,
    )
```

- [ ] **Step 4: Run tests**

```bash
cd pi && PYTHONPATH=. pytest tests/test_pixel_map.py -v
```
Expected: All tests PASS

- [ ] **Step 5: Create default pixel_map.yaml matching current hardware**

```yaml
# pi/config/pixel_map.yaml
# Default pixel map — matches original 10-strip serpentine pillar
schema_version: 1
origin: bottom_left

teensy:
  outputs: 8
  max_leds_per_output: 1200
  controller_wire_order: BGR       # OctoWS2811 configured color order
  signal_family: ws281x_800khz     # LED signal type
  octo_pins: [2, 14, 7, 8, 6, 20, 21, 5]  # Teensy 4.1 OctoWS2811 pin mapping

strips:
  - id: 0
    output: 0
    output_offset: 0
    total_leds: 172
    segments:
      - range: [0, 171]
        color_order: BGR
    scanlines:
      - start: [0, 0]
        end: [0, 171]

  - id: 1
    output: 0
    output_offset: 172
    total_leds: 172
    segments:
      - range: [0, 171]
        color_order: BGR
    scanlines:
      - start: [1, 171]
        end: [1, 0]

  - id: 2
    output: 1
    output_offset: 0
    total_leds: 172
    segments:
      - range: [0, 171]
        color_order: BGR
    scanlines:
      - start: [2, 0]
        end: [2, 171]

  - id: 3
    output: 1
    output_offset: 172
    total_leds: 172
    segments:
      - range: [0, 171]
        color_order: BGR
    scanlines:
      - start: [3, 171]
        end: [3, 0]

  - id: 4
    output: 2
    output_offset: 0
    total_leds: 172
    segments:
      - range: [0, 171]
        color_order: BGR
    scanlines:
      - start: [4, 0]
        end: [4, 171]

  - id: 5
    output: 2
    output_offset: 172
    total_leds: 172
    segments:
      - range: [0, 171]
        color_order: BGR
    scanlines:
      - start: [5, 171]
        end: [5, 0]

  - id: 6
    output: 3
    output_offset: 0
    total_leds: 172
    segments:
      - range: [0, 171]
        color_order: BGR
    scanlines:
      - start: [6, 0]
        end: [6, 171]

  - id: 7
    output: 3
    output_offset: 172
    total_leds: 172
    segments:
      - range: [0, 171]
        color_order: BGR
    scanlines:
      - start: [7, 171]
        end: [7, 0]

  - id: 8
    output: 4
    output_offset: 0
    total_leds: 172
    segments:
      - range: [0, 171]
        color_order: BGR
    scanlines:
      - start: [8, 0]
        end: [8, 171]

  - id: 9
    output: 4
    output_offset: 172
    total_leds: 172
    segments:
      - range: [0, 171]
        color_order: BGR
    scanlines:
      - start: [9, 171]
        end: [9, 0]
```

- [ ] **Step 6: Commit**

```bash
git add pi/app/config/pixel_map.py pi/tests/test_pixel_map.py pi/config/pixel_map.yaml
git commit -m "feat: pixel map data model — load, validate, compile YAML to LUTs"
```

---

### Task 2: Output Packer

**Files:**
- Create: `pi/app/mapping/packer.py`
- Create: `pi/tests/test_packer.py`

- [ ] **Step 1: Write tests for output packing**

```python
# pi/tests/test_packer.py
import pytest
import numpy as np
from app.config.pixel_map import (
    PixelMapConfig, StripConfig, ScanlineConfig, SegmentConfig,
    compile_pixel_map,
)
from app.mapping.packer import pack_frame


def _simple_compiled():
    """2x3 grid, 1 strip, 6 LEDs, BGR order."""
    cfg = PixelMapConfig(
        strips=[StripConfig(
            id=0, output=0, output_offset=0, total_leds=6,
            segments=[SegmentConfig(range_start=0, range_end=5, color_order='BGR')],
            scanlines=[
                ScanlineConfig(start=(0, 0), end=(0, 2)),
                ScanlineConfig(start=(1, 2), end=(1, 0)),
            ],
        )],
    )
    return compile_pixel_map(cfg)


class TestPackFrame:
    def test_basic_packing(self):
        pm = _simple_compiled()
        # 2x3 frame, all red
        frame = np.zeros((2, 3, 3), dtype=np.uint8)
        frame[:, :] = [255, 0, 0]  # RGB red
        buf = pack_frame(frame, pm)
        # BGR order: red RGB → [0, 0, 255] in wire
        # 6 LEDs × 3 bytes on output 0 = 18 bytes for output 0
        assert len(buf) >= 18

    def test_color_order_swizzle(self):
        cfg = PixelMapConfig(
            strips=[StripConfig(
                id=0, output=0, output_offset=0, total_leds=1,
                segments=[SegmentConfig(range_start=0, range_end=0, color_order='GRB')],
                scanlines=[ScanlineConfig(start=(0, 0), end=(0, 0))],
            )],
        )
        pm = compile_pixel_map(cfg)
        frame = np.zeros((1, 1, 3), dtype=np.uint8)
        frame[0, 0] = [255, 128, 64]  # R=255, G=128, B=64
        buf = pack_frame(frame, pm)
        # GRB swizzle: G=128, R=255, B=64
        assert buf[0] == 128  # G
        assert buf[1] == 255  # R
        assert buf[2] == 64   # B

    def test_multi_output(self):
        cfg = PixelMapConfig(
            strips=[
                StripConfig(
                    id=0, output=0, output_offset=0, total_leds=1,
                    segments=[SegmentConfig(0, 0, 'RGB')],
                    scanlines=[ScanlineConfig(start=(0, 0), end=(0, 0))],
                ),
                StripConfig(
                    id=1, output=2, output_offset=0, total_leds=1,
                    segments=[SegmentConfig(0, 0, 'RGB')],
                    scanlines=[ScanlineConfig(start=(1, 0), end=(1, 0))],
                ),
            ],
        )
        pm = compile_pixel_map(cfg)
        frame = np.zeros((2, 1, 3), dtype=np.uint8)
        frame[0, 0] = [10, 20, 30]
        frame[1, 0] = [40, 50, 60]
        buf = pack_frame(frame, pm)
        # Output 0: 1 LED = 3 bytes, Output 1: 0 LEDs, Output 2: 1 LED = 3 bytes
        # Total = output_config[0]*3 + output_config[1]*3 + output_config[2]*3
        assert len(buf) == sum(pm.output_config[i] * 3 for i in range(8))

    def test_unmapped_cells_are_black(self):
        """Frame pixels with no LED mapped should not appear in output."""
        cfg = PixelMapConfig(
            strips=[StripConfig(
                id=0, output=0, output_offset=0, total_leds=1,
                segments=[SegmentConfig(0, 0, 'RGB')],
                scanlines=[ScanlineConfig(start=(0, 0), end=(0, 0))],
            )],
        )
        pm = compile_pixel_map(cfg)
        # Grid is 1x1, only (0,0) mapped
        frame = np.full((1, 1, 3), 255, dtype=np.uint8)
        buf = pack_frame(frame, pm)
        assert buf[0] == 255
        assert buf[1] == 255
        assert buf[2] == 255
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd pi && PYTHONPATH=. pytest tests/test_packer.py -v
```
Expected: `ModuleNotFoundError: No module named 'app.mapping.packer'`

- [ ] **Step 3: Implement packer.py**

```python
# pi/app/mapping/packer.py
"""
Output packer — maps rendered grid frame to serialized LED output buffer.

Uses the reverse LUT from CompiledPixelMap to read each LED's pixel
from the rendered frame, apply per-segment color order swizzle, and
write to the correct position in the output buffer.
"""

import numpy as np
from ..config.pixel_map import CompiledPixelMap


def pack_frame(frame: np.ndarray, pixel_map: CompiledPixelMap) -> bytes:
    """Pack a (width, height, 3) rendered frame into output buffer.

    Returns bytes sized to fit all active outputs: contiguous blocks of
    leds_per_output[pin] * 3 for each pin 0-7.
    """
    output_config = pixel_map.output_config
    total_bytes = sum(n * 3 for n in output_config)
    buf = bytearray(total_bytes)

    # Precompute byte offsets for each output pin
    pin_offsets = []
    offset = 0
    for n in output_config:
        pin_offsets.append(offset)
        offset += n * 3

    # Iterate strips and pack
    for strip in pixel_map.strips:
        strip_reverse = pixel_map.reverse_lut[strip.id]
        pin = strip.output
        base = pin_offsets[pin] + strip.output_offset * 3

        for led_idx in range(strip.total_leds):
            entry = strip_reverse[led_idx]
            if entry is None:
                continue
            x, y, swizzle = entry
            if x >= frame.shape[0] or y >= frame.shape[1]:
                continue
            rgb = frame[x, y]
            pos = base + led_idx * 3
            buf[pos] = rgb[swizzle[0]]
            buf[pos + 1] = rgb[swizzle[1]]
            buf[pos + 2] = rgb[swizzle[2]]

    return bytes(buf)
```

- [ ] **Step 4: Run tests**

```bash
cd pi && PYTHONPATH=. pytest tests/test_packer.py -v
```
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add pi/app/mapping/packer.py pi/tests/test_packer.py
git commit -m "feat: output packer — grid frame to LED buffer with color order swizzle"
```

---

### Task 3: Effect Base Class + Buffer Changes

**Files:**
- Modify: `pi/app/effects/base.py`
- Modify: `pi/app/effects/engine/buffer.py`

- [ ] **Step 1: Update base.py — remove N, add RENDER_SCALE**

In `pi/app/effects/base.py`, make these changes:

1. Remove the line `from ..mapping.cylinder import N` (if present)
2. Change `__init__` signature: remove default values for `width` and `height`
3. Add `RENDER_SCALE = 1` class attribute

The `__init__` becomes:
```python
class Effect(ABC):
    RENDER_SCALE = 1

    def __init__(self, width: int, height: int, params: Optional[dict] = None):
        self.width = width
        self.height = height
        self.params = params or {}
        self._start_time: Optional[float] = None
```

- [ ] **Step 2: Update buffer.py — remove defaults**

In `pi/app/effects/engine/buffer.py`, change:
```python
# Old:
def __init__(self, cols=10, rows=172):

# New:
def __init__(self, cols: int, rows: int):
```

- [ ] **Step 3: Run existing tests to check for breakage**

```bash
cd pi && PYTHONPATH=. pytest tests/ -v --timeout=30 2>&1 | tail -20
```

Fix any immediate import errors (effects that pass no args to LEDBuffer).

- [ ] **Step 4: Commit**

```bash
git add pi/app/effects/base.py pi/app/effects/engine/buffer.py
git commit -m "feat: effect base class — required width/height, RENDER_SCALE attribute"
```

---

### Task 4: Effect Migration — Remove Hardcoded Geometry

This is a mechanical bulk change applied to every effect file. The pattern is identical for each:

1. Remove `from ..mapping.cylinder import N` (or `from ...mapping.cylinder import N`)
2. Replace any `height=N` defaults with just `height` (no default)
3. Replace any `width=10` defaults with just `width` (no default)
4. Replace any bare `N` references with `self.height`
5. Remove any `NATIVE_WIDTH` class attribute
6. Fix `LEDBuffer()` calls to pass `self.width, self.height`

**Files to modify (apply the same pattern to each):**

- [ ] **Step 1: Migrate `pi/app/effects/generative.py`**

```
- Remove line: from ..mapping.cylinder import N
- In every effect class __init__: change (width=10, height=N, ...) → (width, height, ...)
- Replace N with self.height in method bodies
- Fix LEDBuffer() calls: LEDBuffer(self.width, self.height)
```

- [ ] **Step 2: Migrate `pi/app/effects/audio_reactive.py`**

```
- Remove line: from ..mapping.cylinder import N
- In every effect class __init__: change (width=10, height=N, ...) → (width, height, ...)
- Remove NATIVE_WIDTH = 10 from EnergyRing
- Replace _resample_16_to_10 with _resample_bins:
```

```python
def _resample_bins(self, spectrum, target_width):
    """Resample 16-bin spectrum to target_width bands via mean pooling."""
    if spectrum is None:
        return np.zeros(target_width, dtype=np.float32)
    src = np.asarray(spectrum, dtype=np.float32)
    if len(src) == 0:
        return np.zeros(target_width, dtype=np.float32)
    out = np.zeros(target_width, dtype=np.float32)
    ratio = len(src) / target_width
    for i in range(target_width):
        lo = i * ratio
        hi = (i + 1) * ratio
        lo_i = int(lo)
        hi_i = min(int(hi) + 1, len(src))
        out[i] = float(np.mean(src[lo_i:hi_i])) if hi_i > lo_i else 0.0
    return out
```

Call as `self._resample_bins(spectrum, self.width)` instead of `self._resample_16_to_10(spectrum)`.

- [ ] **Step 3: Migrate imported effects (5 files)**

Apply the same pattern to each:
- `pi/app/effects/imported/ambient_a.py` — remove `from ...mapping.cylinder import N`
- `pi/app/effects/imported/ambient_b.py` — remove `from ...mapping.cylinder import N`
- `pi/app/effects/imported/classic.py` — remove `from ...mapping.cylinder import N`
- `pi/app/effects/imported/sound.py` — remove `from ...mapping.cylinder import N`
- `pi/app/effects/imported/sound_variants.py` — remove `from ...mapping.cylinder import N`

In each: change all `height=N` → `height`, `width=10` → `width`, bare `N` → `self.height`.

- [ ] **Step 4: Migrate `pi/app/effects/switcher.py`**

```
- Change __init__(self, width=10, height=172, ...) → __init__(self, width, height, ...)
```

- [ ] **Step 5: Migrate `pi/app/diagnostics/patterns.py`**

```
- Remove: from ..mapping.cylinder import N
- Change all __init__ defaults to required params
- Replace min(self.width, 10) with self.width
- Replace hardcoded 5 channel count with reading from pixel map config
- Replace 344-LED chain logic with dynamic values
```

- [ ] **Step 6: Run all tests**

```bash
cd pi && PYTHONPATH=. pytest tests/ -v --timeout=30
```

Fix any remaining hardcoded references. Expected: All tests pass (tests may need updating too if they instantiate effects without width/height).

- [ ] **Step 7: Commit**

```bash
git add pi/app/effects/ pi/app/diagnostics/
git commit -m "feat: remove hardcoded geometry from all effects — dynamic width/height"
```

---

### Task 5: Renderer Integration

**Files:**
- Modify: `pi/app/core/renderer.py`
- Modify: `pi/app/preview/service.py`

- [ ] **Step 1: Update renderer to use CompiledPixelMap**

In `pi/app/core/renderer.py`:

1. Remove imports of `map_frame_fast`, `serialize_channels`, `downsample_width`, `N` from `cylinder.py`
2. Remove imports of `map_frame_compiled`, `serialize_channels_compiled` from `runtime_mapper.py`
3. Add import: `from ..mapping.packer import pack_frame`
4. Change `__init__`:
   - Remove `internal_width` param
   - Add `pixel_map: CompiledPixelMap` param
   - Store `self.pixel_map = pixel_map`
   - `_last_logical_frame` uses `np.zeros((pixel_map.width, pixel_map.height, 3), dtype=np.uint8)`
5. Change `_set_scene`:
   - Effect instantiation uses `pixel_map.width` and `pixel_map.height` (multiplied by `RENDER_SCALE`)
   - Remove `NATIVE_WIDTH` check
6. Change `_render_frame`:
   - Remove downsample_width call
   - If `RENDER_SCALE > 1`, downsample the rendered frame via area averaging
   - Replace `map_frame_compiled` / `map_frame_fast` with `pack_frame(logical_frame, self.pixel_map)`
   - Replace `serialize_channels` / `serialize_channels_compiled` — `pack_frame` returns bytes directly
7. Add `apply_pixel_map(pixel_map)` for hot-swapping (same pattern as old `apply_output_plan`)
8. Propagate origin to `RenderState`: add `state.origin = pixel_map.origin` so effects can access it via `state.origin`. Add `state.grid_width` and `state.grid_height` too.

- [ ] **Step 2: Update preview service**

In `pi/app/preview/service.py`:

1. Remove `from ..mapping.cylinder import N` and `downsample_width`
2. Use renderer's pixel_map dimensions for effect instantiation
3. Remove the `downsample_width(frame, 10)` call in `render_frame`

- [ ] **Step 3: Run tests**

```bash
cd pi && PYTHONPATH=. pytest tests/ -v --timeout=30
```

- [ ] **Step 4: Commit**

```bash
git add pi/app/core/renderer.py pi/app/preview/service.py
git commit -m "feat: renderer uses CompiledPixelMap — dynamic dimensions, pack_frame output"
```

---

### Task 6: Transport + Protocol Changes

**Files:**
- Modify: `pi/app/transport/usb.py`
- Modify: `pi/app/models/protocol.py`

- [ ] **Step 1: Add CONFIG packet payload builder to protocol.py**

```python
# Add to pi/app/models/protocol.py

# CONFIG payload: active_outputs(u8) + leds_per_output(u16 × 8) = 17 bytes
CONFIG_PAYLOAD_FORMAT = '<B8H'
CONFIG_PAYLOAD_SIZE = struct.calcsize(CONFIG_PAYLOAD_FORMAT)

def build_config_payload(output_config: list[int]) -> bytes:
    """Build CONFIG packet payload from output config (LEDs per pin, 8 entries)."""
    assert len(output_config) == 8
    active = sum(1 for n in output_config if n > 0)
    return struct.pack(CONFIG_PAYLOAD_FORMAT, active, *output_config)
```

- [ ] **Step 2: Update send_frame to use dynamic sizing**

In `pi/app/transport/usb.py`:

```python
# Old:
async def send_frame(self, channel_data: bytes, channels: int = 5,
                     leds_per_channel: int = 344) -> bool:

# New:
async def send_frame(self, pixel_data: bytes) -> bool:
```

The `pixel_data` is already the packed output from `pack_frame()`. The FRAME payload format changes post-CONFIG: no more 3-byte header (channels + leds_per_ch). The payload is just raw pixel bytes, sized to match `sum(leds_per_output[i] * 3)`. The Teensy validates the frame size against its stored CONFIG.

The old `build_frame_payload(channels, leds_per_channel, channel_data)` call is replaced — `send_frame` builds the packet directly from the raw pixel bytes:

```python
async def send_frame(self, pixel_data: bytes) -> bool:
    """Send a FRAME packet with raw pixel data (post-CONFIG format)."""
    if not self.connected or not self.serial:
        return False
    self.frame_id += 1
    timestamp_us = int(time.monotonic() * 1_000_000) & 0xFFFFFFFFFFFFFFFF
    packet = build_packet(
        PacketType.FRAME, pixel_data,
        frame_id=self.frame_id, timestamp_us=timestamp_us,
    )
    framed = frame_packet(packet)
    async with self._lock:
        try:
            await asyncio.to_thread(self.serial.write, framed)
            self.frames_sent += 1
            return True
        except (serial.SerialException, OSError) as e:
            self.send_errors += 1
            logger.error(f"Frame send failed: {e}")
            self.connected = False
            return False
```

Add CONFIG send with ACK/NAK handling:
```python
async def send_config(self, output_config: list[int], timeout: float = 3.0) -> bool:
    """Send CONFIG packet and wait for ACK/NAK response."""
    payload = build_config_payload(output_config)
    success = await self.send_command(PacketType.CONFIG, payload)
    if not success:
        return False
    # Wait for ACK/NAK response
    response = await self._wait_for_response(
        [PacketType.CONFIG_ACK, PacketType.CONFIG_NAK], timeout=timeout
    )
    if response is None:
        logger.error("CONFIG: no response from Teensy (timeout)")
        return False
    if response.packet_type == PacketType.CONFIG_NAK:
        logger.error(f"CONFIG NAK from Teensy: {response.payload}")
        return False
    logger.info("CONFIG ACK received — Teensy reconfigured")
    return True
```

Also add to `protocol.py`:
```python
# New packet types for CONFIG response
# Add to PacketType enum:
CONFIG_ACK = 0x04
CONFIG_NAK = 0x05
```

- [ ] **Step 3: Run tests**

```bash
cd pi && PYTHONPATH=. pytest tests/ -v --timeout=30
```

- [ ] **Step 4: Commit**

```bash
git add pi/app/transport/usb.py pi/app/models/protocol.py
git commit -m "feat: CONFIG packet + dynamic frame sizing in transport"
```

---

### Task 7: Main.py Integration

**Files:**
- Modify: `pi/app/main.py`

- [ ] **Step 1: Update main.py startup**

1. Replace imports:
   - Remove: `from .config.installation import load_installation`
   - Remove: `from .mapping.runtime_plan import load_controller_profile, compile_strip_plan`
   - Remove: `from .hardware_constants import ...` (all references)
   - Add: `from .config.pixel_map import load_pixel_map, compile_pixel_map, validate_pixel_map`

2. In startup sequence:
   - Load pixel map: `pixel_map_config = load_pixel_map(config_dir)`
   - Validate: `errors = validate_pixel_map(pixel_map_config)`
   - Compile: `compiled_pixel_map = compile_pixel_map(pixel_map_config)`
   - Create renderer with pixel map: `renderer = Renderer(transport, state, brightness_engine, compiled_pixel_map)`
   - Send CONFIG to Teensy: `await transport.send_config(compiled_pixel_map.output_config)`
   - Register effects as before (they now get width/height from pixel map via renderer)

3. Update `AppDeps` (in `pi/app/api/deps.py` or wherever deps are defined):
   - Remove `installation`, `controller_profile` fields
   - Add `pixel_map_config: PixelMapConfig` and `compiled_pixel_map: CompiledPixelMap`
   - Store both in deps during startup: `deps.pixel_map_config = pixel_map_config`, `deps.compiled_pixel_map = compiled_pixel_map`

4. Update `pi/app/api/server.py`:
   - Remove old setup router registration (`create_setup_router`)
   - Add pixel map router registration
   - Remove any references to `deps.installation` or `deps.controller_profile`

- [ ] **Step 2: Run the full application in dev mode**

```bash
cd pi && PILLAR_DEV=1 python -m app.main
```

Verify startup succeeds, loads pixel_map.yaml, compiles without errors.

- [ ] **Step 3: Commit**

```bash
git add pi/app/main.py
git commit -m "feat: main.py loads pixel map, sends CONFIG to Teensy at startup"
```

---

### Task 8: Delete Legacy Mapping Code

**Files:**
- Delete: `pi/app/mapping/cylinder.py`
- Delete: `pi/app/mapping/runtime_mapper.py`
- Delete: `pi/app/mapping/runtime_plan.py`
- Delete: `pi/app/config/installation.py`
- Delete: `pi/app/hardware_constants.py`
- Delete: `pi/config/hardware.yaml` (optional — keep as reference or delete)

- [ ] **Step 1: Delete legacy files**

```bash
git rm pi/app/mapping/cylinder.py
git rm pi/app/mapping/runtime_mapper.py
git rm pi/app/mapping/runtime_plan.py
git rm pi/app/config/installation.py
git rm pi/app/hardware_constants.py
```

- [ ] **Step 2: Fix any remaining imports**

Search for all references to deleted modules:
```bash
cd pi && grep -r "from.*mapping.cylinder" app/ --include="*.py"
cd pi && grep -r "from.*hardware_constants" app/ --include="*.py"
cd pi && grep -r "from.*config.installation" app/ --include="*.py"
cd pi && grep -r "from.*runtime_plan" app/ --include="*.py"
cd pi && grep -r "from.*runtime_mapper" app/ --include="*.py"
```

Fix every remaining import.

- [ ] **Step 3: Run all tests**

```bash
cd pi && PYTHONPATH=. pytest tests/ -v --timeout=30
```

Remove or update tests that reference deleted modules.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore: remove legacy mapping — cylinder.py, runtime_mapper, hardware_constants"
```

---

### Task 9: Teensy Firmware — CONFIG Packet Handler

**Files:**
- Modify: `teensy/firmware/include/config.h`
- Modify: `teensy/firmware/src/main.cpp`

- [ ] **Step 1: Update config.h — dynamic defaults**

```cpp
// teensy/firmware/include/config.h
// Remove hardcoded geometry, keep as defaults for pre-CONFIG compatibility
#define DEFAULT_LEDS_PER_STRIP 344
#define DEFAULT_ACTIVE_OUTPUTS 5
#define TOTAL_OUTPUTS 8
#define MAX_LEDS_PER_OUTPUT 1200
#define MAX_TOTAL_LEDS (TOTAL_OUTPUTS * MAX_LEDS_PER_OUTPUT)

// MAX_PAYLOAD_SIZE must accommodate largest possible frame
#define MAX_PAYLOAD_SIZE (TOTAL_OUTPUTS * MAX_LEDS_PER_OUTPUT * 3 + 8)
```

- [ ] **Step 2: Update main.cpp — dynamic buffers and CONFIG handler**

Key changes to `teensy/firmware/src/main.cpp`:

1. Replace static `pendingFrame` array with dynamically sized buffer
2. Add runtime config variables:
```cpp
// Runtime config (set by CONFIG packet or defaults)
uint8_t activeOutputs = DEFAULT_ACTIVE_OUTPUTS;
uint16_t ledsPerOutput[TOTAL_OUTPUTS] = {
    DEFAULT_LEDS_PER_STRIP, DEFAULT_LEDS_PER_STRIP,
    DEFAULT_LEDS_PER_STRIP, DEFAULT_LEDS_PER_STRIP,
    DEFAULT_LEDS_PER_STRIP, 0, 0, 0
};
uint32_t totalLeds = DEFAULT_ACTIVE_OUTPUTS * DEFAULT_LEDS_PER_STRIP;
uint32_t frameSize = totalLeds * 3;
```

3. Add `handleConfig`:
```cpp
void handleConfig(const uint8_t* payload, size_t len) {
    if (len < 17) { // 1 + 8*2
        stats.badFrame++;
        return;
    }
    uint8_t newActive = payload[0];
    uint16_t newLeds[TOTAL_OUTPUTS];
    uint32_t newTotal = 0;
    for (int i = 0; i < TOTAL_OUTPUTS; i++) {
        newLeds[i] = payload[1 + i*2] | (payload[2 + i*2] << 8);
        if (newLeds[i] > MAX_LEDS_PER_OUTPUT) {
            // NAK: exceeds max
            sendNak(0x01);
            return;
        }
        newTotal += newLeds[i];
    }
    // Apply config
    configReceived = true;
    activeOutputs = newActive;
    memcpy(ledsPerOutput, newLeds, sizeof(ledsPerOutput));
    totalLeds = newTotal;
    frameSize = newTotal * 3;

    // Reconfigure OctoWS2811 with max of ledsPerOutput as strip length
    uint16_t maxPerStrip = 0;
    for (int i = 0; i < TOTAL_OUTPUTS; i++) {
        if (newLeds[i] > maxPerStrip) maxPerStrip = newLeds[i];
    }
    // OctoWS2811 requires all strips same length
    leds.begin(maxPerStrip, displayMemory, drawingMemory, WS2811_BGR);

    // Save to EEPROM (optional, for power-cycle persistence)
    // saveConfigToEEPROM();

    // ACK
    sendAck();
}
```

4. Update `handleFrame` — support both legacy (3-byte header) and post-CONFIG (raw) formats:
```cpp
bool configReceived = false;  // set true by handleConfig

void handleFrame(const uint8_t* payload, size_t len) {
    const uint8_t* pixelData;
    size_t pixelLen;

    if (configReceived) {
        // Post-CONFIG: payload is raw pixel data, no header
        pixelData = payload;
        pixelLen = len;
    } else {
        // Legacy format: channels(1) + leds_per_ch(2) + pixel data
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
    pendingFrameReady = true;
    stats.framesReceived++;
}
```

5. Add `sendAck` and `sendNak` helpers:
```cpp
void sendAck() {
    // Send CONFIG_ACK packet (type 0x04, empty payload)
    sendResponse(PKT_CONFIG_ACK, nullptr, 0);
}

void sendNak(uint8_t reason) {
    // Send CONFIG_NAK packet (type 0x05, 1-byte reason code)
    sendResponse(PKT_CONFIG_NAK, &reason, 1);
}

void sendResponse(uint8_t type, const uint8_t* payload, size_t len) {
    // Build and send a response packet using existing packet infrastructure
    // (same as sendStats/sendCaps pattern)
    uint8_t buf[HEADER_SIZE + CRC_SIZE + 4];  // small response
    PacketHeader hdr = {PROTOCOL_VERSION, type, 0, 0, 0, (uint32_t)len};
    size_t total = buildPacket(buf, sizeof(buf), &hdr, payload, len);
    Serial.write(buf, total);
}
```

6. Add packet type constants to `config.h`:
```cpp
#define PKT_CONFIG_ACK  0x04
#define PKT_CONFIG_NAK  0x05
```

7. OctoWS2811 static buffer strategy — use max-sized DMAMEM allocation:
```cpp
// Allocate for worst case: 8 outputs × 1200 LEDs
DMAMEM uint8_t displayMemory[MAX_LEDS_PER_OUTPUT * TOTAL_OUTPUTS * 3];
DMAMEM uint8_t drawingMemory[MAX_LEDS_PER_OUTPUT * TOTAL_OUTPUTS * 3];
uint8_t pendingFrame[MAX_LEDS_PER_OUTPUT * TOTAL_OUTPUTS * 3];

// OctoWS2811 initialized with default, reconfigured on CONFIG
OctoWS2811 leds(DEFAULT_LEDS_PER_STRIP, displayMemory, drawingMemory, WS2811_BGR);
```

The `handleConfig` call to `leds.begin(maxPerStrip, ...)` reuses the same static buffers — OctoWS2811 doesn't reallocate, it just changes the active length. RAM check: `8 × 1200 × 3 × 2 = 57,600 bytes` for display+drawing memory — well within Teensy 4.1's 1MB RAM.

8. EEPROM persistence:
```cpp
#include <EEPROM.h>
#define EEPROM_CONFIG_ADDR 0
#define EEPROM_MAGIC 0xPM  // "pixel map"

void saveConfigToEEPROM() {
    EEPROM.write(EEPROM_CONFIG_ADDR, EEPROM_MAGIC >> 8);
    EEPROM.write(EEPROM_CONFIG_ADDR + 1, EEPROM_MAGIC & 0xFF);
    EEPROM.write(EEPROM_CONFIG_ADDR + 2, activeOutputs);
    for (int i = 0; i < TOTAL_OUTPUTS; i++) {
        EEPROM.write(EEPROM_CONFIG_ADDR + 3 + i*2, ledsPerOutput[i] & 0xFF);
        EEPROM.write(EEPROM_CONFIG_ADDR + 3 + i*2 + 1, ledsPerOutput[i] >> 8);
    }
}

bool loadConfigFromEEPROM() {
    uint16_t magic = (EEPROM.read(EEPROM_CONFIG_ADDR) << 8) | EEPROM.read(EEPROM_CONFIG_ADDR + 1);
    if (magic != EEPROM_MAGIC) return false;
    activeOutputs = EEPROM.read(EEPROM_CONFIG_ADDR + 2);
    uint32_t total = 0;
    for (int i = 0; i < TOTAL_OUTPUTS; i++) {
        ledsPerOutput[i] = EEPROM.read(EEPROM_CONFIG_ADDR + 3 + i*2) |
                           (EEPROM.read(EEPROM_CONFIG_ADDR + 3 + i*2 + 1) << 8);
        total += ledsPerOutput[i];
    }
    totalLeds = total;
    frameSize = total * 3;
    return true;
}
```
Call `loadConfigFromEEPROM()` in `setup()` before `leds.begin()`.
```

- [ ] **Step 3: Build firmware**

```bash
cd teensy/firmware && platformio run
```

- [ ] **Step 4: Commit**

```bash
git add teensy/firmware/include/config.h teensy/firmware/src/main.cpp
git commit -m "feat: Teensy handles CONFIG packet — dynamic OctoWS2811 buffer allocation"
```

---

### Task 10: Pixel Map API Routes

**Files:**
- Create: `pi/app/api/routes/pixel_map.py`
- Modify: `pi/app/api/server.py`

- [ ] **Step 1: Create pixel map CRUD routes**

```python
# pi/app/api/routes/pixel_map.py
"""Pixel map API routes — CRUD for strips, scanlines, segments."""

import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional

from ...config.pixel_map import (
    load_pixel_map, save_pixel_map, validate_pixel_map, compile_pixel_map,
    StripConfig, ScanlineConfig, SegmentConfig,
)

logger = logging.getLogger(__name__)


class ScanlineRequest(BaseModel):
    start: list[int]  # [x, y]
    end: list[int]    # [x, y]


class SegmentRequest(BaseModel):
    range_start: int
    range_end: int
    color_order: str = 'BGR'


class StripRequest(BaseModel):
    id: int
    output: int = 0
    output_offset: int = 0
    total_leds: int = 0
    segments: list[SegmentRequest] = []
    scanlines: list[ScanlineRequest] = []


class OriginRequest(BaseModel):
    origin: str  # "bottom_left" or "top_left"


def create_router(deps, require_auth) -> APIRouter:
    router = APIRouter(prefix="/api/pixel-map", tags=["pixel-map"])

    async def _recompile_and_apply():
        """Validate, compile, push CONFIG to Teensy (await ACK), then apply and save."""
        errors = validate_pixel_map(deps.pixel_map_config)
        if errors:
            raise HTTPException(422, detail=errors)
        compiled = compile_pixel_map(deps.pixel_map_config)

        # Push CONFIG to Teensy FIRST — must ACK before we change Pi's frame format
        config_ok = await deps.transport.send_config(compiled.output_config)
        if not config_ok:
            raise HTTPException(502, "Teensy rejected CONFIG or timed out — pixel map NOT applied")

        # Only after Teensy ACK: apply to renderer and save
        deps.compiled_pixel_map = compiled
        deps.renderer.apply_pixel_map(compiled)
        save_pixel_map(deps.pixel_map_config, deps.config_dir)

    @router.get("/")
    async def get_pixel_map():
        """Return the full pixel map config and grid dimensions."""
        compiled = deps.compiled_pixel_map
        return {
            'origin': deps.pixel_map_config.origin,
            'grid_width': compiled.width,
            'grid_height': compiled.height,
            'total_mapped_leds': compiled.total_mapped_leds,
            'strips': [
                {
                    'id': s.id,
                    'output': s.output,
                    'output_offset': s.output_offset,
                    'total_leds': s.total_leds,
                    'segments': [
                        {'range_start': seg.range_start, 'range_end': seg.range_end,
                         'color_order': seg.color_order}
                        for seg in s.segments
                    ],
                    'scanlines': [
                        {'start': list(sl.start), 'end': list(sl.end)}
                        for sl in s.scanlines
                    ],
                }
                for s in deps.pixel_map_config.strips
            ],
            'output_config': compiled.output_config,
        }

    @router.post("/strips")
    async def add_strip(req: StripRequest, auth=Depends(require_auth)):
        """Add a new strip to the pixel map."""
        strip = StripConfig(
            id=req.id,
            output=req.output,
            output_offset=req.output_offset,
            total_leds=req.total_leds,
            segments=[SegmentConfig(s.range_start, s.range_end, s.color_order) for s in req.segments],
            scanlines=[ScanlineConfig(tuple(s.start), tuple(s.end)) for s in req.scanlines],
        )
        # Check for duplicate ID
        if any(s.id == req.id for s in deps.pixel_map_config.strips):
            raise HTTPException(409, f"Strip {req.id} already exists")
        deps.pixel_map_config.strips.append(strip)
        await _recompile_and_apply()
        return await get_pixel_map()

    @router.post("/strips/{strip_id}")
    async def update_strip(strip_id: int, req: StripRequest, auth=Depends(require_auth)):
        """Update an existing strip."""
        # Check for ID collision if ID is changing
        if req.id != strip_id:
            if any(s.id == req.id for s in deps.pixel_map_config.strips):
                raise HTTPException(409, f"Strip {req.id} already exists")
        for i, s in enumerate(deps.pixel_map_config.strips):
            if s.id == strip_id:
                deps.pixel_map_config.strips[i] = StripConfig(
                    id=req.id,
                    output=req.output,
                    output_offset=req.output_offset,
                    total_leds=req.total_leds,
                    segments=[SegmentConfig(seg.range_start, seg.range_end, seg.color_order) for seg in req.segments],
                    scanlines=[ScanlineConfig(tuple(sl.start), tuple(sl.end)) for sl in req.scanlines],
                )
                await _recompile_and_apply()
                return await get_pixel_map()
        raise HTTPException(404, f"Strip {strip_id} not found")

    @router.delete("/strips/{strip_id}")
    async def delete_strip(strip_id: int, auth=Depends(require_auth)):
        """Delete a strip."""
        before = len(deps.pixel_map_config.strips)
        deps.pixel_map_config.strips = [s for s in deps.pixel_map_config.strips if s.id != strip_id]
        if len(deps.pixel_map_config.strips) == before:
            raise HTTPException(404, f"Strip {strip_id} not found")
        await _recompile_and_apply()
        return await get_pixel_map()

    @router.post("/origin")
    async def set_origin(req: OriginRequest, auth=Depends(require_auth)):
        """Set grid origin (bottom_left or top_left)."""
        if req.origin not in ('bottom_left', 'top_left'):
            raise HTTPException(422, "origin must be 'bottom_left' or 'top_left'")
        deps.pixel_map_config.origin = req.origin
        await _recompile_and_apply()
        return await get_pixel_map()

    @router.post("/pixel/{strip_id}/{led_index}")
    async def set_single_pixel(strip_id: int, led_index: int, x: int, y: int, auth=Depends(require_auth)):
        """Assign a single LED to a grid position (for edge case corrections)."""
        # Find the strip
        strip = None
        for s in deps.pixel_map_config.strips:
            if s.id == strip_id:
                strip = s
                break
        if strip is None:
            raise HTTPException(404, f"Strip {strip_id} not found")
        if led_index < 0 or led_index >= strip.total_leds:
            raise HTTPException(422, f"LED index {led_index} out of range [0, {strip.total_leds - 1}]")
        strip.pixel_overrides[led_index] = (x, y)
        await _recompile_and_apply()
        return await get_pixel_map()

    @router.post("/validate")
    async def validate_map():
        """Validate current pixel map without applying."""
        errors = validate_pixel_map(deps.pixel_map_config)
        return {'valid': len(errors) == 0, 'errors': errors}

    @router.get("/teensy-status")
    async def teensy_config_status():
        """Return Teensy config status — what config was last pushed and ACK'd."""
        return {
            'output_config': deps.compiled_pixel_map.output_config,
            'teensy_connected': deps.transport.connected,
            'last_config_ack': getattr(deps.transport, '_last_config_ack', None),
        }

    return router
```

- [ ] **Step 2: Register router in server.py**

In `pi/app/api/server.py`, add:
```python
from .routes.pixel_map import create_router as create_pixel_map_router
# ...
app.include_router(create_pixel_map_router(deps, require_auth))
```

- [ ] **Step 3: Commit**

```bash
git add pi/app/api/routes/pixel_map.py pi/app/api/server.py
git commit -m "feat: pixel map CRUD API — strips, scanlines, segments, validation"
```

---

### Task 11: Setup UI

**Files:**
- Modify: `pi/app/ui/static/index.html`
- Modify: `pi/app/ui/static/js/app.js`
- Modify: `pi/app/ui/static/css/app.css`

This task builds the Setup screen with strip/scanline editor and grid preview. The UI implementation is substantial — the implementer should:

- [ ] **Step 1: Add Setup HTML structure**

In the Setup section of `index.html`, replace the old strip mapping table with:
- Strip list (add/edit/delete)
- Per-strip: output pin, offset, total LEDs, expandable scanline list, expandable segment list
- Scanline rows: start (x,y) + end (x,y) fields, auto-calculated LED count
- Segment rows: range start/end + color order dropdown
- Grid preview canvas (shows mapped pixels colored by strip)
- Origin selector dropdown
- Teensy config status panel
- Validation error display

- [ ] **Step 2: Add grid preview canvas rendering**

In `app.js`, add a function that draws the grid:
- Fetch pixel map from `GET /api/pixel-map`
- Draw a canvas with one cell per grid position
- Color each cell by strip ID (use a distinct color palette)
- Empty cells shown as dark gray
- Highlight validation errors in red

- [ ] **Step 3: Add strip CRUD interactions**

In `app.js`:
- Add strip button → POST `/api/pixel-map/strips`
- Edit strip → POST `/api/pixel-map/strips/{id}`
- Delete strip → DELETE `/api/pixel-map/strips/{id}`
- Each action refreshes the grid preview and validation status

- [ ] **Step 4: Add scanline editor**

Per-strip expandable section with rows for each scanline:
- Start X, Start Y, End X, End Y inputs
- Auto-calculated LED count display
- Running total vs strip's total_leds

- [ ] **Step 5: Add CSS styles**

Style the setup panel to match existing UI patterns (compact inputs, expandable sections, canvas sizing).

- [ ] **Step 6: Test manually via dev mode**

```bash
cd pi && PILLAR_DEV=1 python -m app.main
```

Open `http://localhost:8000` in browser. Verify:
- Grid preview renders correctly for default pixel_map.yaml
- Adding/editing/deleting strips works
- Validation errors display correctly
- Teensy config status shows

- [ ] **Step 7: Commit**

```bash
git add pi/app/ui/static/
git commit -m "feat: Setup UI — strip/scanline editor with grid preview"
```

---

### Task 12: Deploy and Integration Test

- [ ] **Step 1: Run full test suite**

```bash
cd pi && PYTHONPATH=. pytest tests/ -v --timeout=30
```

All tests must pass.

- [ ] **Step 2: Deploy to Pi**

```bash
bash pi/scripts/deploy.sh ledfanatic.local
```

- [ ] **Step 3: Verify on hardware**

- Open `http://ledfanatic.local` in browser
- Check Setup screen loads with current pixel map
- Switch to an effect — verify it renders correctly on the pillar
- Check live preview matches physical output
- Verify Teensy receives CONFIG and accepts frames

- [ ] **Step 4: Commit any fixes**

```bash
git add -A
git commit -m "fix: integration fixes from hardware deployment"
```

---

## Self-Review Checklist

**Spec coverage:**
- Pixel map data model → Task 1
- Output packer with color order swizzle → Task 2
- Effect base class + RENDER_SCALE → Task 3
- Effect migration (all ~50 effects) → Task 4
- Renderer integration → Task 5
- Transport + CONFIG protocol → Task 6
- Main.py wiring → Task 7
- Delete legacy code → Task 8
- Teensy firmware → Task 9
- API routes → Task 10
- Setup UI → Task 11
- Deploy + test → Task 12

**Spec requirements verified:**
- ✅ Dynamic grid dimensions from pixel map
- ✅ Scanlines: start/end, axis-aligned
- ✅ Per-segment color order within strips
- ✅ Forward + reverse LUTs
- ✅ Output packing with swizzle
- ✅ Teensy CONFIG packet (runtime, no reflash)
- ✅ CONFIG ACK/NAK handshake with defined packet types
- ✅ EEPROM persistence on Teensy
- ✅ RENDER_SCALE per-effect
- ✅ Origin configurable (bottom_left default), propagated to RenderState
- ✅ Legacy code deletion
- ✅ Default pixel_map.yaml matching current hardware (including electrical settings)
- ✅ Vision auto-mapper integration noted (same data format)
- ✅ Single-pixel edit endpoint
- ✅ Teensy config status endpoint
- ✅ AppDeps updated with pixel_map_config/compiled_pixel_map
- ✅ Old setup router removed
- ✅ Frame payload format consistent (raw bytes post-CONFIG, no 3-byte header)
- ✅ OctoWS2811 static max-sized DMAMEM allocation (no dynamic realloc)
- ✅ Validation: non-negative coords, segment bounds, duplicate positions
