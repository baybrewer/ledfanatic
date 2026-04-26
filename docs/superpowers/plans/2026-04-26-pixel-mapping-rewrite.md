# Pixel Mapping Rewrite — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the broken pixel mapping system with a declarative segment-based architecture using compiled lookup tables.

**Architecture:** Three cleanly separated layers: (1) declarative layout config (YAML) declaring outputs with linear/explicit segments, (2) a compiler that validates and produces a flat mapping table at startup, (3) a packer that iterates the precomputed table to build output buffers each frame. Effects remain unchanged — they write to `framebuffer[x, y]`.

**Tech Stack:** Python 3.13, NumPy, PyYAML, Pydantic (API schemas), pytest

---

## File Structure

| File | Responsibility | Action |
|------|---------------|--------|
| `pi/config/layout.yaml` | Declarative LED layout (SSOT) | Create |
| `pi/app/layout/schema.py` | Pydantic models for layout config | Create |
| `pi/app/layout/compiler.py` | Validate config, compile to mapping table | Create |
| `pi/app/layout/packer.py` | Pack logical frame to physical output buffers | Create |
| `pi/app/layout/__init__.py` | Public API: load/compile/pack | Create |
| `pi/app/api/routes/layout.py` | REST API for layout CRUD | Create |
| `pi/app/core/renderer.py` | Update imports: use new layout module | Modify |
| `pi/app/main.py` | Update startup: load new layout | Modify |
| `pi/app/api/server.py` | Mount new layout router | Modify |
| `pi/app/api/deps.py` | Replace pixel_map fields with layout fields | Modify |
| `pi/tests/test_layout_schema.py` | Schema parsing tests | Create |
| `pi/tests/test_layout_compiler.py` | Compilation + validation tests | Create |
| `pi/tests/test_layout_packer.py` | Packer output buffer tests | Create |
| `pi/tests/test_layout_integration.py` | End-to-end: config → compile → pack | Create |
| `pi/app/config/pixel_map.py` | Old mapping module | Delete (after migration) |
| `pi/app/mapping/packer.py` | Old packer | Delete (after migration) |
| `pi/app/api/routes/pixel_map.py` | Old pixel map routes | Delete (after migration) |
| `pi/tests/test_pixel_map.py` | Old tests | Delete (after migration) |
| `pi/tests/test_packer.py` | Old packer tests | Delete (after migration) |

---

## Task 1: Layout Schema — Data Models

**Files:**
- Create: `pi/app/layout/__init__.py`
- Create: `pi/app/layout/schema.py`
- Test: `pi/tests/test_layout_schema.py`

- [ ] **Step 1: Write failing test for linear segment parsing**

```python
# pi/tests/test_layout_schema.py
import pytest
from app.layout.schema import LayoutConfig, OutputConfig, LinearSegment, parse_layout


def test_parse_minimal_layout():
    """Minimal layout with one output, one linear segment."""
    raw = {
        "version": 1,
        "matrix": {"width": 10, "height": 83, "origin": "bottom_left"},
        "outputs": [
            {
                "id": "ch0",
                "channel": 0,
                "color_order": "BGR",
                "segments": [
                    {
                        "id": "col_0",
                        "start": {"x": 0, "y": 0},
                        "direction": "+y",
                        "length": 83,
                        "physical_offset": 0,
                    }
                ],
            }
        ],
    }
    config = parse_layout(raw)
    assert config.matrix.width == 10
    assert config.matrix.height == 83
    assert len(config.outputs) == 1
    assert len(config.outputs[0].segments) == 1
    seg = config.outputs[0].segments[0]
    assert seg.id == "col_0"
    assert seg.direction == "+y"
    assert seg.length == 83
    assert seg.physical_offset == 0


def test_parse_explicit_segment():
    """Explicit segment with point list."""
    raw = {
        "version": 1,
        "matrix": {"width": 3, "height": 1, "origin": "bottom_left"},
        "outputs": [
            {
                "id": "ch0",
                "channel": 0,
                "color_order": "RGB",
                "segments": [
                    {
                        "id": "custom",
                        "type": "explicit",
                        "points": [{"x": 2, "y": 0}, {"x": 0, "y": 0}, {"x": 1, "y": 0}],
                        "physical_offset": 0,
                    }
                ],
            }
        ],
    }
    config = parse_layout(raw)
    seg = config.outputs[0].segments[0]
    assert seg.type == "explicit"
    assert len(seg.points) == 3
    assert seg.points[0] == (2, 0)


def test_parse_direction_variants():
    """All four direction values are accepted."""
    for d in ("+x", "-x", "+y", "-y"):
        raw = {
            "version": 1,
            "matrix": {"width": 5, "height": 5, "origin": "bottom_left"},
            "outputs": [
                {
                    "id": "ch0",
                    "channel": 0,
                    "color_order": "BGR",
                    "segments": [
                        {"id": "s", "start": {"x": 0, "y": 0}, "direction": d, "length": 5, "physical_offset": 0}
                    ],
                }
            ],
        }
        config = parse_layout(raw)
        assert config.outputs[0].segments[0].direction == d


def test_reject_invalid_direction():
    """Invalid direction raises ValueError."""
    raw = {
        "version": 1,
        "matrix": {"width": 5, "height": 5, "origin": "bottom_left"},
        "outputs": [
            {
                "id": "ch0",
                "channel": 0,
                "color_order": "BGR",
                "segments": [
                    {"id": "s", "start": {"x": 0, "y": 0}, "direction": "up", "length": 5, "physical_offset": 0}
                ],
            }
        ],
    }
    with pytest.raises((ValueError, KeyError)):
        parse_layout(raw)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd pi && source .venv/bin/activate && PYTHONPATH=. pytest tests/test_layout_schema.py -v`
Expected: ImportError — `app.layout.schema` does not exist

- [ ] **Step 3: Implement schema module**

```python
# pi/app/layout/__init__.py
"""LED layout mapping — declarative config, compiled lookup tables, fast packer."""

# pi/app/layout/schema.py
"""
Layout schema — data models for the declarative LED layout config.

Defines the structure of layout.yaml: matrix dimensions, outputs with
segments (linear or explicit), and parsing from raw dict/YAML.
"""

from dataclasses import dataclass, field
from typing import Literal, Optional


VALID_DIRECTIONS = ("+x", "-x", "+y", "-y")
VALID_COLOR_ORDERS = ("RGB", "RBG", "GRB", "GBR", "BRG", "BGR")
VALID_ORIGINS = ("bottom_left", "top_left", "bottom_right", "top_right")


@dataclass(frozen=True)
class MatrixConfig:
    width: int
    height: int
    origin: str = "bottom_left"


@dataclass(frozen=True)
class LinearSegment:
    """A run of LEDs along one axis."""
    id: str
    start: tuple[int, int]  # (x, y)
    direction: str          # "+x", "-x", "+y", "-y"
    length: int
    physical_offset: int
    type: str = "linear"
    enabled: bool = True


@dataclass(frozen=True)
class ExplicitSegment:
    """A segment defined by explicit (x, y) points."""
    id: str
    points: list[tuple[int, int]]
    physical_offset: int
    type: str = "explicit"
    enabled: bool = True


# Union type for segments
Segment = LinearSegment | ExplicitSegment


@dataclass(frozen=True)
class OutputConfig:
    """One hardware output channel with its segments."""
    id: str
    channel: int
    color_order: str = "BGR"
    chipset: str = "WS2812"
    max_pixels: int = 1200
    segments: list[Segment] = field(default_factory=list)


@dataclass(frozen=True)
class LayoutConfig:
    """Top-level layout configuration."""
    version: int
    matrix: MatrixConfig
    outputs: list[OutputConfig] = field(default_factory=list)


def parse_layout(raw: dict) -> LayoutConfig:
    """Parse a raw dict (from YAML) into a validated LayoutConfig."""
    version = raw.get("version", 1)

    # Matrix
    m = raw["matrix"]
    origin = m.get("origin", "bottom_left")
    if origin not in VALID_ORIGINS:
        raise ValueError(f"Invalid origin '{origin}', must be one of {VALID_ORIGINS}")
    matrix = MatrixConfig(width=m["width"], height=m["height"], origin=origin)

    # Outputs
    outputs = []
    for out_raw in raw.get("outputs", []):
        color_order = out_raw.get("color_order", "BGR")
        if color_order not in VALID_COLOR_ORDERS:
            raise ValueError(f"Invalid color_order '{color_order}', must be one of {VALID_COLOR_ORDERS}")

        segments = []
        for seg_raw in out_raw.get("segments", []):
            seg_type = seg_raw.get("type", "linear")
            if seg_type == "explicit":
                points = [(p["x"], p["y"]) for p in seg_raw["points"]]
                segments.append(ExplicitSegment(
                    id=seg_raw["id"],
                    points=points,
                    physical_offset=seg_raw.get("physical_offset", 0),
                    enabled=seg_raw.get("enabled", True),
                ))
            elif seg_type == "linear":
                direction = seg_raw["direction"]
                if direction not in VALID_DIRECTIONS:
                    raise ValueError(f"Invalid direction '{direction}', must be one of {VALID_DIRECTIONS}")
                start = (seg_raw["start"]["x"], seg_raw["start"]["y"])
                segments.append(LinearSegment(
                    id=seg_raw["id"],
                    start=start,
                    direction=direction,
                    length=seg_raw["length"],
                    physical_offset=seg_raw.get("physical_offset", 0),
                    enabled=seg_raw.get("enabled", True),
                ))
            else:
                raise ValueError(f"Unknown segment type '{seg_type}'")

        outputs.append(OutputConfig(
            id=out_raw["id"],
            channel=out_raw["channel"],
            color_order=color_order,
            chipset=out_raw.get("chipset", "WS2812"),
            max_pixels=out_raw.get("max_pixels", 1200),
            segments=segments,
        ))

    return LayoutConfig(version=version, matrix=matrix, outputs=outputs)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd pi && source .venv/bin/activate && PYTHONPATH=. pytest tests/test_layout_schema.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add pi/app/layout/__init__.py pi/app/layout/schema.py pi/tests/test_layout_schema.py
git commit -m "feat(layout): schema data models with linear + explicit segments"
```

---

## Task 2: Layout Compiler — Validation

**Files:**
- Create: `pi/app/layout/compiler.py`
- Test: `pi/tests/test_layout_compiler.py`

- [ ] **Step 1: Write failing tests for validation rules**

```python
# pi/tests/test_layout_compiler.py
import pytest
from app.layout.schema import LayoutConfig, MatrixConfig, OutputConfig, LinearSegment, ExplicitSegment
from app.layout.compiler import validate_layout


def _make_config(outputs, width=10, height=83):
    return LayoutConfig(
        version=1,
        matrix=MatrixConfig(width=width, height=height),
        outputs=outputs,
    )


class TestValidation:
    def test_valid_config_no_errors(self):
        """A correct serpentine pair on one output passes validation."""
        config = _make_config([
            OutputConfig(id="ch0", channel=0, color_order="BGR", segments=[
                LinearSegment(id="col0", start=(0, 0), direction="+y", length=83, physical_offset=0),
                LinearSegment(id="col1", start=(1, 82), direction="-y", length=83, physical_offset=83),
            ])
        ])
        errors = validate_layout(config)
        assert errors == []

    def test_segment_out_of_bounds(self):
        """Segment extending outside matrix bounds is rejected."""
        config = _make_config([
            OutputConfig(id="ch0", channel=0, segments=[
                LinearSegment(id="s", start=(0, 0), direction="+y", length=100, physical_offset=0),
            ])
        ], height=83)
        errors = validate_layout(config)
        assert any("bounds" in e.lower() for e in errors)

    def test_duplicate_logical_pixel(self):
        """Two segments mapping to the same logical pixel is rejected."""
        config = _make_config([
            OutputConfig(id="ch0", channel=0, segments=[
                LinearSegment(id="a", start=(0, 0), direction="+y", length=5, physical_offset=0),
                LinearSegment(id="b", start=(0, 0), direction="+y", length=5, physical_offset=5),
            ])
        ])
        errors = validate_layout(config)
        assert any("duplicate" in e.lower() or "collision" in e.lower() for e in errors)

    def test_duplicate_physical_index(self):
        """Two segments with overlapping physical offsets on same output is rejected."""
        config = _make_config([
            OutputConfig(id="ch0", channel=0, segments=[
                LinearSegment(id="a", start=(0, 0), direction="+y", length=5, physical_offset=0),
                LinearSegment(id="b", start=(1, 0), direction="+y", length=5, physical_offset=3),
            ])
        ])
        errors = validate_layout(config)
        assert any("physical" in e.lower() or "overlap" in e.lower() for e in errors)

    def test_exceeds_max_pixels(self):
        """Total pixels on one output exceeding max_pixels is rejected."""
        config = _make_config([
            OutputConfig(id="ch0", channel=0, max_pixels=100, segments=[
                LinearSegment(id="s", start=(0, 0), direction="+y", length=83, physical_offset=0),
                LinearSegment(id="s2", start=(1, 0), direction="+y", length=83, physical_offset=83),
            ])
        ])
        errors = validate_layout(config)
        assert any("max" in e.lower() or "exceed" in e.lower() for e in errors)

    def test_disabled_segment_skipped(self):
        """Disabled segments are excluded from validation."""
        config = _make_config([
            OutputConfig(id="ch0", channel=0, segments=[
                LinearSegment(id="a", start=(0, 0), direction="+y", length=83, physical_offset=0),
                LinearSegment(id="b", start=(0, 0), direction="+y", length=83, physical_offset=83, enabled=False),
            ])
        ])
        errors = validate_layout(config)
        assert errors == []

    def test_explicit_segment_out_of_bounds(self):
        """Explicit segment with points outside matrix is rejected."""
        config = _make_config([
            OutputConfig(id="ch0", channel=0, segments=[
                ExplicitSegment(id="e", points=[(0, 0), (99, 99)], physical_offset=0),
            ])
        ], width=10, height=10)
        errors = validate_layout(config)
        assert any("bounds" in e.lower() for e in errors)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd pi && PYTHONPATH=. pytest tests/test_layout_compiler.py -v`
Expected: ImportError — `app.layout.compiler` does not exist

- [ ] **Step 3: Implement validate_layout**

```python
# pi/app/layout/compiler.py
"""
Layout compiler — validates layout config and compiles to mapping tables.

Startup-only: validates all rules, then produces a CompiledLayout with
precomputed forward/reverse LUTs and a flat mapping entry list for
fast per-frame packing.
"""

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from .schema import (
    LayoutConfig, OutputConfig, LinearSegment, ExplicitSegment, Segment,
    VALID_DIRECTIONS,
)


def _expand_linear(seg: LinearSegment) -> list[tuple[int, int]]:
    """Expand a linear segment into its (x, y) positions in physical order."""
    x, y = seg.start
    dx, dy = 0, 0
    if seg.direction == "+x":
        dx = 1
    elif seg.direction == "-x":
        dx = -1
    elif seg.direction == "+y":
        dy = 1
    elif seg.direction == "-y":
        dy = -1
    positions = []
    for i in range(seg.length):
        positions.append((x + dx * i, y + dy * i))
    return positions


def _expand_segment(seg: Segment) -> list[tuple[int, int]]:
    """Expand any segment type into its (x, y) positions."""
    if isinstance(seg, ExplicitSegment):
        return list(seg.points)
    return _expand_linear(seg)


def validate_layout(config: LayoutConfig) -> list[str]:
    """
    Validate a LayoutConfig. Returns list of error strings (empty = valid).

    Checks:
    - Segments stay within matrix bounds
    - No duplicate logical (x, y) assignments
    - No overlapping physical indices on same output
    - Total pixels per output don't exceed max_pixels
    """
    errors: list[str] = []
    w, h = config.matrix.width, config.matrix.height
    logical_seen: dict[tuple[int, int], str] = {}  # (x,y) -> segment id

    for output in config.outputs:
        physical_used: dict[int, str] = {}  # pixel_index -> segment id
        total_pixels = 0

        for seg in output.segments:
            if not seg.enabled:
                continue

            positions = _expand_segment(seg)

            # Bounds check
            for px, py in positions:
                if px < 0 or px >= w or py < 0 or py >= h:
                    errors.append(
                        f"Output '{output.id}' segment '{seg.id}': "
                        f"position ({px}, {py}) out of bounds "
                        f"(matrix is {w}x{h})"
                    )

            # Logical collision check
            for px, py in positions:
                key = (px, py)
                if key in logical_seen:
                    errors.append(
                        f"Output '{output.id}' segment '{seg.id}': "
                        f"duplicate logical pixel ({px}, {py}), "
                        f"already mapped by '{logical_seen[key]}'"
                    )
                else:
                    logical_seen[key] = seg.id

            # Physical overlap check
            for i in range(len(positions)):
                phys_idx = seg.physical_offset + i
                if phys_idx in physical_used:
                    errors.append(
                        f"Output '{output.id}' segment '{seg.id}': "
                        f"physical index {phys_idx} overlaps with "
                        f"segment '{physical_used[phys_idx]}'"
                    )
                else:
                    physical_used[phys_idx] = seg.id

            total_pixels = max(total_pixels, seg.physical_offset + len(positions))

        # Max pixels check
        if total_pixels > output.max_pixels:
            errors.append(
                f"Output '{output.id}': total pixels ({total_pixels}) "
                f"exceeds max_pixels ({output.max_pixels})"
            )

    return errors
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd pi && PYTHONPATH=. pytest tests/test_layout_compiler.py -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add pi/app/layout/compiler.py pi/tests/test_layout_compiler.py
git commit -m "feat(layout): validation rules for layout config"
```

---

## Task 3: Layout Compiler — Compilation to Mapping Table

**Files:**
- Modify: `pi/app/layout/compiler.py`
- Test: `pi/tests/test_layout_compiler.py` (append)

- [ ] **Step 1: Write failing tests for compilation**

Append to `pi/tests/test_layout_compiler.py`:

```python
from app.layout.compiler import compile_layout, CompiledLayout, MappingEntry


class TestCompilation:
    def test_compiled_dimensions(self):
        """Compiled layout reports correct grid dimensions."""
        config = _make_config([
            OutputConfig(id="ch0", channel=0, color_order="BGR", segments=[
                LinearSegment(id="col0", start=(0, 0), direction="+y", length=83, physical_offset=0),
            ])
        ])
        compiled = compile_layout(config)
        assert compiled.width == 10
        assert compiled.height == 83

    def test_forward_lut(self):
        """Forward LUT maps logical (x,y) to (channel, pixel_index)."""
        config = _make_config([
            OutputConfig(id="ch0", channel=0, color_order="BGR", segments=[
                LinearSegment(id="s", start=(0, 0), direction="+y", length=3, physical_offset=0),
            ])
        ], width=1, height=3)
        compiled = compile_layout(config)
        # (0, 0) -> channel 0, pixel 0
        assert compiled.forward_lut[0][0] == (0, 0)
        # (0, 1) -> channel 0, pixel 1
        assert compiled.forward_lut[0][1] == (0, 1)
        # (0, 2) -> channel 0, pixel 2
        assert compiled.forward_lut[0][2] == (0, 2)

    def test_reverse_lut(self):
        """Reverse LUT maps (channel, pixel_index) to logical (x, y)."""
        config = _make_config([
            OutputConfig(id="ch0", channel=0, color_order="BGR", segments=[
                LinearSegment(id="s", start=(2, 5), direction="-y", length=3, physical_offset=10),
            ])
        ], width=3, height=6)
        compiled = compile_layout(config)
        # Channel 0, pixel 10 -> (2, 5)
        assert compiled.reverse_lut[0][10] == (2, 5)
        # Channel 0, pixel 11 -> (2, 4)
        assert compiled.reverse_lut[0][11] == (2, 4)
        # Channel 0, pixel 12 -> (2, 3)
        assert compiled.reverse_lut[0][12] == (2, 3)

    def test_mapping_entries(self):
        """Flat mapping entries list for iteration-based packing."""
        config = _make_config([
            OutputConfig(id="ch0", channel=0, color_order="BGR", segments=[
                LinearSegment(id="s", start=(0, 0), direction="+y", length=3, physical_offset=0),
            ])
        ], width=1, height=3)
        compiled = compile_layout(config)
        assert len(compiled.entries) == 3
        e = compiled.entries[0]
        assert e.x == 0
        assert e.y == 0
        assert e.channel == 0
        assert e.pixel_index == 0

    def test_output_sizes(self):
        """output_sizes gives max pixel index + 1 per channel."""
        config = _make_config([
            OutputConfig(id="ch0", channel=0, segments=[
                LinearSegment(id="a", start=(0, 0), direction="+y", length=83, physical_offset=0),
                LinearSegment(id="b", start=(1, 82), direction="-y", length=83, physical_offset=83),
            ]),
            OutputConfig(id="ch1", channel=1, segments=[
                LinearSegment(id="c", start=(2, 0), direction="+y", length=83, physical_offset=0),
            ]),
        ])
        compiled = compile_layout(config)
        assert compiled.output_sizes[0] == 166
        assert compiled.output_sizes[1] == 83

    def test_color_swizzle_per_output(self):
        """Color swizzle tuple is stored per channel."""
        config = _make_config([
            OutputConfig(id="ch0", channel=0, color_order="BGR", segments=[
                LinearSegment(id="s", start=(0, 0), direction="+y", length=1, physical_offset=0),
            ]),
            OutputConfig(id="ch1", channel=1, color_order="GRB", segments=[
                LinearSegment(id="s2", start=(1, 0), direction="+y", length=1, physical_offset=0),
            ]),
        ], width=2, height=1)
        compiled = compile_layout(config)
        # BGR: wire byte order is B, G, R → source indices [2, 1, 0]
        assert compiled.color_swizzle[0] == (2, 1, 0)
        # GRB: wire byte order is G, R, B → source indices [1, 0, 2]
        assert compiled.color_swizzle[1] == (1, 0, 2)

    def test_unmapped_pixels_are_none(self):
        """Unmapped logical pixels have None in forward_lut."""
        config = _make_config([
            OutputConfig(id="ch0", channel=0, segments=[
                LinearSegment(id="s", start=(0, 0), direction="+y", length=1, physical_offset=0),
            ])
        ], width=2, height=2)
        compiled = compile_layout(config)
        assert compiled.forward_lut[0][0] is not None
        assert compiled.forward_lut[1][0] is None
        assert compiled.forward_lut[0][1] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd pi && PYTHONPATH=. pytest tests/test_layout_compiler.py::TestCompilation -v`
Expected: ImportError for `compile_layout`, `CompiledLayout`, `MappingEntry`

- [ ] **Step 3: Implement compile_layout**

Add to `pi/app/layout/compiler.py`:

```python
# --- Color order swizzle ---
# Maps color_order string to tuple of source RGB indices for wire output.
# Input frame is always [R, G, B]. Tuple tells which source index goes to each wire byte.
SWIZZLE_MAP: dict[str, tuple[int, int, int]] = {
    "RGB": (0, 1, 2),
    "RBG": (0, 2, 1),
    "GRB": (1, 0, 2),
    "GBR": (1, 2, 0),
    "BRG": (2, 0, 1),
    "BGR": (2, 1, 0),
}


@dataclass
class MappingEntry:
    """One logical-to-physical mapping. Used for fast iteration during packing."""
    x: int
    y: int
    channel: int
    pixel_index: int
    swizzle: tuple[int, int, int]


@dataclass
class CompiledLayout:
    """Precomputed mapping tables for fast rendering."""
    width: int
    height: int
    origin: str
    # forward_lut[x][y] -> (channel, pixel_index) or None
    forward_lut: list[list[Optional[tuple[int, int]]]]
    # reverse_lut[channel][pixel_index] -> (x, y) or None
    reverse_lut: dict[int, dict[int, Optional[tuple[int, int]]]]
    # Flat list of all mapping entries for iteration-based packing
    entries: list[MappingEntry]
    # Max pixel index + 1 per channel (8 entries, sparse)
    output_sizes: dict[int, int]
    # Color swizzle per channel
    color_swizzle: dict[int, tuple[int, int, int]]
    # Total mapped pixels
    total_mapped: int


def compile_layout(config: LayoutConfig) -> CompiledLayout:
    """
    Compile a validated LayoutConfig into fast-lookup structures.

    Call validate_layout() first — this function assumes valid input.
    """
    w, h = config.matrix.width, config.matrix.height

    # Initialize forward LUT with None
    forward_lut: list[list[Optional[tuple[int, int]]]] = [
        [None] * h for _ in range(w)
    ]

    # Build mapping entries and reverse LUT
    reverse_lut: dict[int, dict[int, Optional[tuple[int, int]]]] = {}
    entries: list[MappingEntry] = []
    output_sizes: dict[int, int] = {}
    color_swizzle: dict[int, tuple[int, int, int]] = {}
    total_mapped = 0

    for output in config.outputs:
        ch = output.channel
        swizzle = SWIZZLE_MAP.get(output.color_order, (0, 1, 2))
        color_swizzle[ch] = swizzle

        if ch not in reverse_lut:
            reverse_lut[ch] = {}

        max_idx = 0

        for seg in output.segments:
            if not seg.enabled:
                continue

            positions = _expand_segment(seg)

            for i, (px, py) in enumerate(positions):
                phys_idx = seg.physical_offset + i
                forward_lut[px][py] = (ch, phys_idx)
                reverse_lut[ch][phys_idx] = (px, py)
                entries.append(MappingEntry(
                    x=px, y=py, channel=ch,
                    pixel_index=phys_idx, swizzle=swizzle,
                ))
                total_mapped += 1
                if phys_idx + 1 > max_idx:
                    max_idx = phys_idx + 1

        output_sizes[ch] = max(output_sizes.get(ch, 0), max_idx)

    return CompiledLayout(
        width=w,
        height=h,
        origin=config.matrix.origin,
        forward_lut=forward_lut,
        reverse_lut=reverse_lut,
        entries=entries,
        output_sizes=output_sizes,
        color_swizzle=color_swizzle,
        total_mapped=total_mapped,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd pi && PYTHONPATH=. pytest tests/test_layout_compiler.py -v`
Expected: All 14 tests PASS (7 validation + 7 compilation)

- [ ] **Step 5: Commit**

```bash
git add pi/app/layout/compiler.py pi/tests/test_layout_compiler.py
git commit -m "feat(layout): compile layout config to mapping tables"
```

---

## Task 4: Layout Packer — Frame to Output Buffers

**Files:**
- Create: `pi/app/layout/packer.py`
- Test: `pi/tests/test_layout_packer.py`

- [ ] **Step 1: Write failing tests for packer**

```python
# pi/tests/test_layout_packer.py
"""Tests for the layout packer — logical frame to physical output buffers."""

import numpy as np
import pytest

from app.layout.schema import (
    LayoutConfig, MatrixConfig, OutputConfig, LinearSegment, ExplicitSegment,
    parse_layout,
)
from app.layout.compiler import compile_layout, validate_layout
from app.layout.packer import pack_frame


def _compile(outputs, width=10, height=83):
    config = LayoutConfig(
        version=1,
        matrix=MatrixConfig(width=width, height=height),
        outputs=outputs,
    )
    assert validate_layout(config) == []
    return compile_layout(config)


class TestPacker:
    def test_basic_bgr_packing(self):
        """Red pixel in RGB frame → BGR wire bytes [0, 0, 255]."""
        compiled = _compile([
            OutputConfig(id="ch0", channel=0, color_order="BGR", segments=[
                LinearSegment(id="s", start=(0, 0), direction="+y", length=1, physical_offset=0),
            ])
        ], width=1, height=1)
        frame = np.zeros((1, 1, 3), dtype=np.uint8)
        frame[0, 0] = [255, 0, 0]  # RGB red
        buf = pack_frame(frame, compiled)
        assert buf[0] == 0    # B
        assert buf[1] == 0    # G
        assert buf[2] == 255  # R

    def test_grb_swizzle(self):
        """GRB color order: [255, 128, 64] RGB → [128, 255, 64] wire."""
        compiled = _compile([
            OutputConfig(id="ch0", channel=0, color_order="GRB", segments=[
                LinearSegment(id="s", start=(0, 0), direction="+y", length=1, physical_offset=0),
            ])
        ], width=1, height=1)
        frame = np.zeros((1, 1, 3), dtype=np.uint8)
        frame[0, 0] = [255, 128, 64]
        buf = pack_frame(frame, compiled)
        assert buf[0] == 128  # G
        assert buf[1] == 255  # R
        assert buf[2] == 64   # B

    def test_multi_output_layout(self):
        """Two outputs produce contiguous buffer: ch0 then ch1."""
        compiled = _compile([
            OutputConfig(id="ch0", channel=0, color_order="RGB", segments=[
                LinearSegment(id="a", start=(0, 0), direction="+y", length=1, physical_offset=0),
            ]),
            OutputConfig(id="ch1", channel=1, color_order="RGB", segments=[
                LinearSegment(id="b", start=(1, 0), direction="+y", length=1, physical_offset=0),
            ]),
        ], width=2, height=1)
        frame = np.zeros((2, 1, 3), dtype=np.uint8)
        frame[0, 0] = [10, 20, 30]
        frame[1, 0] = [40, 50, 60]
        buf = pack_frame(frame, compiled)
        # Channel 0: 1 LED * 3 = 3 bytes, then channel 1: 3 bytes
        assert len(buf) == 6
        assert buf[0:3] == bytes([10, 20, 30])
        assert buf[3:6] == bytes([40, 50, 60])

    def test_unmapped_pixels_are_black(self):
        """Physical LEDs with no logical mapping emit zeros."""
        compiled = _compile([
            OutputConfig(id="ch0", channel=0, color_order="RGB", segments=[
                LinearSegment(id="s", start=(0, 0), direction="+y", length=1, physical_offset=2),
            ])
        ], width=1, height=1)
        frame = np.full((1, 1, 3), 200, dtype=np.uint8)
        buf = pack_frame(frame, compiled)
        # output_sizes[0] = 3 (offset 2 + length 1), so 9 bytes total
        assert len(buf) == 9
        # Pixels 0 and 1 unmapped → black
        assert buf[0:6] == bytes([0, 0, 0, 0, 0, 0])
        # Pixel 2 mapped → [200, 200, 200]
        assert buf[6:9] == bytes([200, 200, 200])

    def test_serpentine_pair(self):
        """Two segments on same output: col0 up, col1 down (pillar pattern)."""
        compiled = _compile([
            OutputConfig(id="ch0", channel=0, color_order="RGB", segments=[
                LinearSegment(id="col0", start=(0, 0), direction="+y", length=3, physical_offset=0),
                LinearSegment(id="col1", start=(1, 2), direction="-y", length=3, physical_offset=3),
            ])
        ], width=2, height=3)
        frame = np.zeros((2, 3, 3), dtype=np.uint8)
        # Col 0: bottom=red, mid=green, top=blue
        frame[0, 0] = [255, 0, 0]
        frame[0, 1] = [0, 255, 0]
        frame[0, 2] = [0, 0, 255]
        # Col 1: top=white, mid=cyan, bottom=yellow
        frame[1, 2] = [255, 255, 255]
        frame[1, 1] = [0, 255, 255]
        frame[1, 0] = [255, 255, 0]
        buf = pack_frame(frame, compiled)
        # 6 LEDs * 3 bytes = 18 bytes
        assert len(buf) == 18
        # Physical order: col0 LED0=(0,0), LED1=(0,1), LED2=(0,2),
        #                 col1 LED3=(1,2), LED4=(1,1), LED5=(1,0)
        assert buf[0:3] == bytes([255, 0, 0])      # (0,0) red
        assert buf[3:6] == bytes([0, 255, 0])      # (0,1) green
        assert buf[6:9] == bytes([0, 0, 255])      # (0,2) blue
        assert buf[9:12] == bytes([255, 255, 255]) # (1,2) white
        assert buf[12:15] == bytes([0, 255, 255])  # (1,1) cyan
        assert buf[15:18] == bytes([255, 255, 0])  # (1,0) yellow

    def test_output_config_list(self):
        """output_config_list() returns 8-entry list for Teensy CONFIG."""
        compiled = _compile([
            OutputConfig(id="ch0", channel=0, segments=[
                LinearSegment(id="a", start=(0, 0), direction="+y", length=83, physical_offset=0),
                LinearSegment(id="b", start=(1, 82), direction="-y", length=83, physical_offset=83),
            ]),
            OutputConfig(id="ch2", channel=2, segments=[
                LinearSegment(id="c", start=(2, 0), direction="+y", length=50, physical_offset=0),
            ]),
        ])
        from app.layout.packer import output_config_list
        oc = output_config_list(compiled)
        assert len(oc) == 8
        assert oc[0] == 166
        assert oc[1] == 0
        assert oc[2] == 50
        assert oc[3] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd pi && PYTHONPATH=. pytest tests/test_layout_packer.py -v`
Expected: ImportError — `app.layout.packer` does not exist

- [ ] **Step 3: Implement packer**

```python
# pi/app/layout/packer.py
"""
Layout packer — converts logical frame to physical output buffers.

Uses precomputed mapping entries from CompiledLayout for O(pixel_count)
per-frame performance with no geometry logic at runtime.
"""

import numpy as np
from .compiler import CompiledLayout


def pack_frame(frame: np.ndarray, layout: CompiledLayout) -> bytes:
    """
    Pack a (width, height, 3) uint8 frame into contiguous output buffer.

    Returns bytes laid out as: [channel_0_data][channel_1_data]...[channel_7_data]
    where each channel's data is output_sizes[ch] * 3 bytes.
    Channels with no mapped LEDs contribute 0 bytes.
    """
    # Build channel byte offsets and total buffer size
    # Always lay out channels 0-7 in order
    channel_offsets: dict[int, int] = {}
    offset = 0
    for ch in range(8):
        channel_offsets[ch] = offset
        offset += layout.output_sizes.get(ch, 0) * 3

    total_bytes = offset
    buf = bytearray(total_bytes)

    # Iterate precomputed entries — tight loop, no branching
    for entry in layout.entries:
        rgb = frame[entry.x, entry.y]
        pos = channel_offsets[entry.channel] + entry.pixel_index * 3
        s = entry.swizzle
        buf[pos] = rgb[s[0]]
        buf[pos + 1] = rgb[s[1]]
        buf[pos + 2] = rgb[s[2]]

    return bytes(buf)


def output_config_list(layout: CompiledLayout) -> list[int]:
    """
    Return 8-entry list of LEDs per output channel (for Teensy CONFIG packet).

    Index = channel number, value = number of LEDs on that channel.
    """
    return [layout.output_sizes.get(ch, 0) for ch in range(8)]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd pi && PYTHONPATH=. pytest tests/test_layout_packer.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add pi/app/layout/packer.py pi/tests/test_layout_packer.py
git commit -m "feat(layout): packer converts logical frame to output buffers"
```

---

## Task 5: Layout YAML — Load/Save + Default Config

**Files:**
- Create: `pi/config/layout.yaml`
- Modify: `pi/app/layout/__init__.py`
- Test: `pi/tests/test_layout_integration.py`

- [ ] **Step 1: Write failing integration test**

```python
# pi/tests/test_layout_integration.py
"""Integration tests: layout.yaml → parse → validate → compile → pack."""

import numpy as np
import pytest
from pathlib import Path

from app.layout import load_layout, compile_layout, validate_layout, pack_frame, output_config_list


class TestDefaultLayout:
    """Validate the shipped layout.yaml produces correct geometry."""

    @pytest.fixture(autouse=True)
    def setup(self):
        config_dir = Path(__file__).parent.parent / "config"
        self.config = load_layout(config_dir)
        errors = validate_layout(self.config)
        assert errors == [], f"Validation errors: {errors}"
        self.compiled = compile_layout(self.config)

    def test_grid_dimensions(self):
        assert self.compiled.width == 10
        assert self.compiled.height == 83

    def test_total_mapped_leds(self):
        assert self.compiled.total_mapped == 830

    def test_output_config(self):
        from app.layout.packer import output_config_list
        oc = output_config_list(self.compiled)
        assert oc == [166, 166, 166, 166, 166, 0, 0, 0]

    def test_serpentine_direction(self):
        """Even columns go up (+y), odd columns go down (-y)."""
        # (0, 0) is bottom-left of col 0, LED 0 → channel 0, pixel 0
        assert self.compiled.forward_lut[0][0] == (0, 0)
        # (0, 82) is top of col 0, LED 82 → channel 0, pixel 82
        assert self.compiled.forward_lut[0][82] == (0, 82)
        # (1, 82) is top of col 1, LED 0 → channel 0, pixel 83
        assert self.compiled.forward_lut[1][82] == (0, 83)
        # (1, 0) is bottom of col 1, LED 82 → channel 0, pixel 165
        assert self.compiled.forward_lut[1][0] == (0, 165)

    def test_pack_solid_frame(self):
        """Solid white frame packs to all-white buffer."""
        frame = np.full((10, 83, 3), 255, dtype=np.uint8)
        buf = pack_frame(frame, self.compiled)
        # 830 LEDs * 3 bytes = 2490
        assert len(buf) == 2490
        # Every byte should be 255
        assert all(b == 255 for b in buf)

    def test_no_unmapped_cells(self):
        """Every cell in 10x83 grid should be mapped."""
        for x in range(10):
            for y in range(83):
                assert self.compiled.forward_lut[x][y] is not None, \
                    f"Unmapped cell at ({x}, {y})"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd pi && PYTHONPATH=. pytest tests/test_layout_integration.py -v`
Expected: ImportError — `app.layout.load_layout` does not exist

- [ ] **Step 3: Create layout.yaml**

```yaml
# pi/config/layout.yaml
# LED layout — single source of truth for physical LED geometry.
#
# 10-column serpentine pillar: even columns go up (+y), odd go down (-y).
# Paired on 5 OctoWS2811 outputs (cols 0+1 on ch0, 2+3 on ch1, etc.)

version: 1

matrix:
  width: 10
  height: 83
  origin: bottom_left

outputs:
  - id: octo_ch0
    channel: 0
    chipset: WS2812
    color_order: BGR
    segments:
      - id: col_0
        start: {x: 0, y: 0}
        direction: "+y"
        length: 83
        physical_offset: 0
      - id: col_1
        start: {x: 1, y: 82}
        direction: "-y"
        length: 83
        physical_offset: 83

  - id: octo_ch1
    channel: 1
    chipset: WS2812
    color_order: BGR
    segments:
      - id: col_2
        start: {x: 2, y: 0}
        direction: "+y"
        length: 83
        physical_offset: 0
      - id: col_3
        start: {x: 3, y: 82}
        direction: "-y"
        length: 83
        physical_offset: 83

  - id: octo_ch2
    channel: 2
    chipset: WS2812
    color_order: BGR
    segments:
      - id: col_4
        start: {x: 4, y: 0}
        direction: "+y"
        length: 83
        physical_offset: 0
      - id: col_5
        start: {x: 5, y: 82}
        direction: "-y"
        length: 83
        physical_offset: 83

  - id: octo_ch3
    channel: 3
    chipset: WS2812
    color_order: BGR
    segments:
      - id: col_6
        start: {x: 6, y: 0}
        direction: "+y"
        length: 83
        physical_offset: 0
      - id: col_7
        start: {x: 7, y: 82}
        direction: "-y"
        length: 83
        physical_offset: 83

  - id: octo_ch4
    channel: 4
    chipset: WS2812
    color_order: BGR
    segments:
      - id: col_8
        start: {x: 8, y: 0}
        direction: "+y"
        length: 83
        physical_offset: 0
      - id: col_9
        start: {x: 9, y: 82}
        direction: "-y"
        length: 83
        physical_offset: 83
```

- [ ] **Step 4: Implement load_layout and update `__init__.py`**

```python
# pi/app/layout/__init__.py
"""LED layout mapping — declarative config, compiled lookup tables, fast packer."""

import logging
from pathlib import Path

import yaml

from .schema import LayoutConfig, parse_layout
from .compiler import validate_layout, compile_layout, CompiledLayout
from .packer import pack_frame, output_config_list

logger = logging.getLogger(__name__)


def load_layout(config_dir: Path) -> LayoutConfig:
    """Load layout.yaml from config directory."""
    path = config_dir / "layout.yaml"
    if not path.exists():
        logger.warning(f"No layout.yaml at {path} — using empty config")
        from .schema import MatrixConfig
        return LayoutConfig(version=1, matrix=MatrixConfig(width=0, height=0))
    try:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        logger.error(f"Failed to parse {path}: {e}")
        from .schema import MatrixConfig
        return LayoutConfig(version=1, matrix=MatrixConfig(width=0, height=0))
    return parse_layout(data)


def save_layout(config: LayoutConfig, config_dir: Path) -> None:
    """Atomically save layout config to layout.yaml."""
    import os
    import tempfile
    from .schema import LinearSegment, ExplicitSegment

    path = config_dir / "layout.yaml"
    config_dir.mkdir(parents=True, exist_ok=True)

    data = {
        "version": config.version,
        "matrix": {
            "width": config.matrix.width,
            "height": config.matrix.height,
            "origin": config.matrix.origin,
        },
        "outputs": [],
    }
    for output in config.outputs:
        out_data = {
            "id": output.id,
            "channel": output.channel,
            "chipset": output.chipset,
            "color_order": output.color_order,
            "segments": [],
        }
        for seg in output.segments:
            if isinstance(seg, ExplicitSegment):
                out_data["segments"].append({
                    "id": seg.id,
                    "type": "explicit",
                    "points": [{"x": p[0], "y": p[1]} for p in seg.points],
                    "physical_offset": seg.physical_offset,
                    "enabled": seg.enabled,
                })
            else:
                seg_data = {
                    "id": seg.id,
                    "start": {"x": seg.start[0], "y": seg.start[1]},
                    "direction": seg.direction,
                    "length": seg.length,
                    "physical_offset": seg.physical_offset,
                }
                if not seg.enabled:
                    seg_data["enabled"] = False
                out_data["segments"].append(seg_data)
        data["outputs"].append(out_data)

    fd, tmp_path = tempfile.mkstemp(dir=str(config_dir), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)
        os.replace(tmp_path, str(path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd pi && PYTHONPATH=. pytest tests/test_layout_integration.py -v`
Expected: All 6 tests PASS

- [ ] **Step 6: Commit**

```bash
git add pi/config/layout.yaml pi/app/layout/__init__.py pi/tests/test_layout_integration.py
git commit -m "feat(layout): load/save + default layout.yaml for 10x83 pillar"
```

---

## Task 6: Wire Into Renderer + Main

**Files:**
- Modify: `pi/app/core/renderer.py`
- Modify: `pi/app/main.py`
- Modify: `pi/app/api/deps.py`

- [ ] **Step 1: Update renderer to use new layout module**

In `pi/app/core/renderer.py`, replace:
```python
from ..mapping.packer import pack_frame
from ..config.pixel_map import CompiledPixelMap
```
With:
```python
from ..layout import CompiledLayout, pack_frame
```

Then replace all references to `CompiledPixelMap` with `CompiledLayout`. The interface is compatible:
- `pixel_map.width` → `layout.width`
- `pixel_map.height` → `layout.height`
- `pixel_map.origin` → `layout.origin`
- `pixel_map.segments[i].positions()` → need to update test strip logic

Update `Renderer.__init__` parameter name from `pixel_map` to `layout`:
```python
def __init__(self, transport: TeensyTransport, state: RenderState,
             brightness_engine: BrightnessEngine, layout: CompiledLayout):
    self.transport = transport
    self.state = state
    self.brightness_engine = brightness_engine
    self.layout = layout
    # ... rest unchanged, replacing self.pixel_map with self.layout
```

Update `apply_pixel_map` to `apply_layout`:
```python
def apply_layout(self, layout: CompiledLayout):
    """Hot-swap the compiled layout. Thread-safe: next frame picks it up."""
    self.layout = layout
    self._last_logical_frame = np.zeros((layout.width, layout.height, 3), dtype=np.uint8)
    self.state.grid_width = layout.width
    self.state.grid_height = layout.height
    self.state.origin = layout.origin
    if self.state.current_scene and self.state.current_scene in self.effect_registry:
        saved_scene = self.state.current_scene
        self.state.current_scene = None
        self.current_effect = None
        self._set_scene(saved_scene)
    logger.info(f"Layout applied: {layout.width}x{layout.height} grid, {layout.total_mapped} LEDs")
```

Update `_render_frame` to use `self.layout`:
```python
w = self.layout.width
h = self.layout.height
# ... (rest same)
pixel_bytes = pack_frame(logical_frame, self.layout)
```

Update test strip logic in `_render_frame` — replace `self.pixel_map.segments[idx].positions()` with a reverse lookup from the compiled layout. For the test-strip pattern, iterate `layout.entries` filtering by the target segment:
```python
if self._test_strip_id is not None:
    if time.monotonic() < self._test_strip_until:
        logical_frame[:] = 0
        # Light all pixels belonging to the target output segment
        seg_id = self._test_strip_id
        if seg_id < len(self._test_segment_ids):
            target_seg = self._test_segment_ids[seg_id]
            entries_for_seg = [e for e in self.layout.entries
                               if self._entry_in_segment(e, target_seg)]
            for idx, entry in enumerate(entries_for_seg):
                frac = idx / max(len(entries_for_seg) - 1, 1)
                logical_frame[entry.x, entry.y] = [int(255 * (1 - frac)), 0, int(255 * frac)]
    else:
        self._test_strip_id = None
```

Actually, simpler: store segment positions during `apply_layout` for the test pattern use case.

- [ ] **Step 2: Update main.py startup**

In `pi/app/main.py`, replace:
```python
from .config.pixel_map import load_pixel_map, compile_pixel_map, validate_pixel_map, PixelMapConfig
```
With:
```python
from .layout import load_layout, compile_layout, validate_layout, output_config_list, CompiledLayout
from .layout.schema import LayoutConfig
```

Replace the pixel map loading block:
```python
# Layout — load, validate, compile
layout_config = load_layout(config_dir)
errors = validate_layout(layout_config)
if errors:
    for err in errors:
        logger.error(f"Layout validation: {err}")
    logger.error("Layout has errors — using empty config (no LEDs)")
    from .layout.schema import MatrixConfig
    layout_config = LayoutConfig(version=1, matrix=MatrixConfig(width=0, height=0))
compiled_layout = compile_layout(layout_config)
logger.info(
    f"Layout: {compiled_layout.width}x{compiled_layout.height} grid, "
    f"{compiled_layout.total_mapped} LEDs"
)
```

Replace renderer construction:
```python
renderer = Renderer(transport, render_state, brightness_engine, compiled_layout)
```

Replace `_on_teensy_connect`:
```python
async def _on_teensy_connect():
    logger.info("Sending CONFIG to Teensy...")
    oc = output_config_list(compiled_layout)
    ok = await transport.send_config(oc)
    if ok:
        logger.info("CONFIG ACK received")
    else:
        logger.warning("CONFIG send failed (NAK/timeout)")
```

Replace `create_app(...)` call — rename `pixel_map_config` and `compiled_pixel_map` to `layout_config` and `compiled_layout`.

- [ ] **Step 3: Update deps.py**

In `pi/app/api/deps.py`, replace:
```python
# Pixel map — geometry SSOT
pixel_map_config: Optional[object] = None
compiled_pixel_map: Optional[object] = None
```
With:
```python
# Layout — geometry SSOT
layout_config: Optional[object] = None
compiled_layout: Optional[object] = None
```

- [ ] **Step 4: Update server.py**

In `pi/app/api/server.py`, update `create_app()` parameters and deps construction to use `layout_config` and `compiled_layout` instead of `pixel_map_config` and `compiled_pixel_map`.

- [ ] **Step 5: Run tests**

Run: `cd pi && PYTHONPATH=. pytest tests/ -v --ignore=tests/test_pixel_map.py --ignore=tests/test_packer.py -k "not test_matrix_rain_perf and not test_import_writes" 2>&1 | tail -20`
Expected: All tests pass (old pixel_map tests ignored)

- [ ] **Step 6: Commit**

```bash
git add pi/app/core/renderer.py pi/app/main.py pi/app/api/deps.py pi/app/api/server.py
git commit -m "refactor: wire renderer and main to new layout module"
```

---

## Task 7: Layout API Routes

**Files:**
- Create: `pi/app/api/routes/layout.py`
- Modify: `pi/app/api/server.py` (mount new router)

- [ ] **Step 1: Implement layout API routes**

```python
# pi/app/api/routes/layout.py
"""
Layout API routes — get, apply, validate, test-segment.

All mutations: validate → compile → send CONFIG to Teensy → ACK gate → commit.
"""

import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...layout import (
    load_layout, save_layout, validate_layout, compile_layout,
    output_config_list, pack_frame,
)
from ...layout.schema import parse_layout

logger = logging.getLogger(__name__)


class LayoutApplyRequest(BaseModel):
    """Full layout config as JSON (same structure as layout.yaml)."""
    version: int = 1
    matrix: dict
    outputs: list[dict]


def create_router(deps, require_auth) -> APIRouter:
    router = APIRouter(prefix="/api/layout", tags=["layout"])

    @router.get("/")
    async def get_layout():
        """Return current layout config + compiled stats."""
        config = deps.layout_config
        compiled = deps.compiled_layout
        if config is None:
            return {"error": "No layout loaded", "outputs": []}
        return {
            "version": config.version,
            "matrix": {
                "width": config.matrix.width,
                "height": config.matrix.height,
                "origin": config.matrix.origin,
            },
            "outputs": [
                {
                    "id": o.id,
                    "channel": o.channel,
                    "chipset": o.chipset,
                    "color_order": o.color_order,
                    "max_pixels": o.max_pixels,
                    "segments": [
                        _serialize_segment(s) for s in o.segments
                    ],
                }
                for o in config.outputs
            ],
            "compiled": {
                "width": compiled.width if compiled else 0,
                "height": compiled.height if compiled else 0,
                "total_mapped": compiled.total_mapped if compiled else 0,
                "output_sizes": output_config_list(compiled) if compiled else [0] * 8,
            },
        }

    @router.post("/apply", dependencies=[Depends(require_auth)])
    async def apply_layout(req: LayoutApplyRequest):
        """Replace entire layout config."""
        try:
            staged = parse_layout(req.model_dump())
        except (ValueError, KeyError) as e:
            raise HTTPException(422, detail=str(e))

        errors = validate_layout(staged)
        if errors:
            raise HTTPException(422, detail=errors)

        compiled = compile_layout(staged)
        oc = output_config_list(compiled)

        config_ok = await deps.transport.send_config(oc)
        if not config_ok:
            raise HTTPException(502, detail="Teensy rejected CONFIG or timed out")

        # ACK received — commit
        deps.layout_config = staged
        deps.compiled_layout = compiled
        deps.renderer.apply_layout(compiled)
        save_layout(staged, deps.config_dir)

        logger.info(f"Layout applied: {compiled.width}x{compiled.height}, {compiled.total_mapped} LEDs")
        return {"status": "ok", "width": compiled.width, "height": compiled.height, "total_mapped": compiled.total_mapped}

    @router.post("/validate")
    async def validate_layout_endpoint(req: LayoutApplyRequest):
        """Validate without applying."""
        try:
            staged = parse_layout(req.model_dump())
        except (ValueError, KeyError) as e:
            return {"valid": False, "errors": [str(e)]}
        errors = validate_layout(staged)
        return {"valid": len(errors) == 0, "errors": errors}

    @router.post("/test-segment/{seg_id}", dependencies=[Depends(require_auth)])
    async def test_segment(seg_id: str):
        """Light a single segment for identification (5 seconds)."""
        compiled = deps.compiled_layout
        if compiled is None:
            raise HTTPException(404, "No layout loaded")
        # Find segment index by id
        idx = None
        for i, output in enumerate(deps.layout_config.outputs):
            for j, seg in enumerate(output.segments):
                if seg.id == seg_id:
                    idx = (i, j)
                    break
        if idx is None:
            raise HTTPException(404, f"Segment '{seg_id}' not found")
        deps.renderer.set_test_strip(seg_id, duration=5.0)
        return {"status": "ok", "segment": seg_id}

    return router


def _serialize_segment(seg) -> dict:
    from ...layout.schema import ExplicitSegment
    if isinstance(seg, ExplicitSegment):
        return {
            "id": seg.id,
            "type": "explicit",
            "points": [{"x": p[0], "y": p[1]} for p in seg.points],
            "physical_offset": seg.physical_offset,
            "enabled": seg.enabled,
        }
    return {
        "id": seg.id,
        "type": "linear",
        "start": {"x": seg.start[0], "y": seg.start[1]},
        "direction": seg.direction,
        "length": seg.length,
        "physical_offset": seg.physical_offset,
        "enabled": seg.enabled,
    }
```

- [ ] **Step 2: Mount in server.py**

Add to `pi/app/api/server.py`:
```python
from .routes import layout as layout_routes
# ...
app.include_router(layout_routes.create_router(deps, require_auth))
```

- [ ] **Step 3: Run full test suite**

Run: `cd pi && PYTHONPATH=. pytest tests/ -v --ignore=tests/test_pixel_map.py --ignore=tests/test_packer.py 2>&1 | tail -10`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add pi/app/api/routes/layout.py pi/app/api/server.py
git commit -m "feat(layout): REST API routes for layout CRUD"
```

---

## Task 8: Remove Old Mapping Code

**Files:**
- Delete: `pi/app/config/pixel_map.py`
- Delete: `pi/app/mapping/packer.py`
- Delete: `pi/app/mapping/__init__.py`
- Delete: `pi/app/api/routes/pixel_map.py`
- Delete: `pi/tests/test_pixel_map.py`
- Delete: `pi/tests/test_packer.py`
- Delete: `pi/config/pixel_map.yaml`
- Modify: `pi/app/api/server.py` (remove old pixel_map router)
- Modify: `pi/app/api/routes/__init__.py` (if it imports pixel_map)

- [ ] **Step 1: Remove old pixel_map router from server.py**

Remove:
```python
from .routes import pixel_map as pixel_map_routes
# ...
app.include_router(pixel_map_routes.create_router(deps, require_auth))
```

- [ ] **Step 2: Delete old files**

```bash
rm pi/app/config/pixel_map.py
rm pi/app/mapping/packer.py
rm pi/app/mapping/__init__.py
rm -rf pi/app/mapping/
rm pi/app/api/routes/pixel_map.py
rm pi/tests/test_pixel_map.py
rm pi/tests/test_packer.py
rm pi/config/pixel_map.yaml
```

- [ ] **Step 3: Fix any remaining imports**

Search for and remove any remaining references to the old modules:
- `tests/test_width_policy.py` — update to use new layout module
- `tests/test_preview_isolation.py` — update to use new layout module
- `tools/bench_effects.py` ��� update to use new layout module
- `app/api/routes/setup.py` — references `pm.strips` which didn't work anyway; update or remove

- [ ] **Step 4: Run full test suite**

Run: `cd pi && PYTHONPATH=. pytest tests/ -v 2>&1 | tail -10`
Expected: All tests pass

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor: remove old pixel_map/packer modules, replaced by layout"
```

---

## Task 9: Deploy + Verify

**Files:** None (deployment task)

- [ ] **Step 1: Run full test suite one final time**

Run: `cd pi && PYTHONPATH=. pytest tests/ -v`
Expected: All pass

- [ ] **Step 2: Deploy to Pi**

```bash
bash pi/scripts/deploy.sh ledfanatic.local
```

- [ ] **Step 3: Verify Teensy CONFIG accepted**

Check Pi logs:
```bash
ssh jim@ledfanatic.local "journalctl -u pillar --since '1 min ago' | grep -i config"
```
Expected: "CONFIG ACK received"

- [ ] **Step 4: Verify LEDs display correctly**

Visually confirm:
- All 10 strips illuminate
- No dead LEDs at segment boundaries
- Colors are correct (no BGR/RGB mismatch)
- Effects render across full grid

- [ ] **Step 5: Commit + tag**

```bash
git tag v1.2.0-layout-rewrite
git push origin main --tags
```
