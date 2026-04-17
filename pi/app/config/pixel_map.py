"""
Pixel map — single source of truth for all LED geometry.

Defines the data model for mapping logical grid positions to physical
LED strips/outputs. Supports loading from YAML, validation, and
compilation into forward/reverse LUTs for fast rendering.

This replaces the hardcoded 10x172 geometry with a user-configurable
pixel map that can describe any strip layout.
"""

import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
import numpy as np
import yaml

logger = logging.getLogger(__name__)

# Color order swizzle maps: channel name → (R-index, G-index, B-index)
# Input is always RGB; swizzle tells you which source channel goes to each output byte.
SWIZZLE_MAP: dict[str, tuple[int, int, int]] = {
  "RGB": (0, 1, 2),
  "RBG": (0, 2, 1),
  "GRB": (1, 0, 2),
  "GBR": (1, 2, 0),
  "BRG": (2, 0, 1),
  "BGR": (2, 1, 0),
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class LineConfig:
  """A run of LEDs along a single axis with its own color order."""

  start: tuple[int, int]
  end: tuple[int, int]
  color_order: str = "BGR"

  def _validate_axis_aligned(self) -> tuple[int, int]:
    """Return (dx, dy) or raise if diagonal."""
    dx = self.end[0] - self.start[0]
    dy = self.end[1] - self.start[1]
    if dx != 0 and dy != 0:
      raise ValueError(
        f"Line must be axis-aligned (horizontal or vertical), "
        f"got start={self.start} end={self.end}"
      )
    return dx, dy

  def led_count(self) -> int:
    """Number of LEDs in this line (inclusive of both endpoints)."""
    dx, dy = self._validate_axis_aligned()
    return abs(dx) + abs(dy) + 1

  def positions(self) -> list[tuple[int, int]]:
    """Ordered list of (x, y) grid positions covered by this line."""
    dx, dy = self._validate_axis_aligned()
    result = []
    count = self.led_count()
    sx = 0 if dx == 0 else (1 if dx > 0 else -1)
    sy = 0 if dy == 0 else (1 if dy > 0 else -1)
    x, y = self.start
    for _ in range(count):
      result.append((x, y))
      x += sx
      y += sy
    return result


# Backward-compatible alias so existing imports don't break in one shot
ScanlineConfig = LineConfig


@dataclass
class StripConfig:
  """One logical LED strip with its line geometry and output mapping."""

  id: int
  output: int
  output_offset: int
  lines: list[LineConfig]
  pixel_overrides: dict[int, tuple[int, int]] = field(default_factory=dict)

  @property
  def total_leds(self) -> int:
    """Derived from sum of line LED counts."""
    return sum(line.led_count() for line in self.lines)


@dataclass
class PixelMapConfig:
  """Top-level pixel map configuration loaded from YAML."""

  origin: str = "bottom-left"
  teensy_outputs: int = 8
  teensy_max_leds_per_output: int = 1200
  teensy_wire_order: str = "BGR"
  teensy_signal_family: str = "ws281x_800khz"
  teensy_octo_pins: list[int] = field(default_factory=lambda: [2, 14, 7, 8, 6, 20, 21, 5])
  strips: list[StripConfig] = field(default_factory=list)


@dataclass
class CompiledPixelMap:
  """Pre-compiled lookup tables and metadata for fast rendering."""

  width: int
  height: int
  origin: str
  forward_lut: np.ndarray   # (width, height, 2) int16 — [strip_id, led_index]
  reverse_lut: list[list]    # reverse_lut[strip_id][led_index] → (x, y, swizzle)
  output_config: dict        # output_idx → [(strip_id, offset, count), ...]
  strips: list[StripConfig]
  total_mapped_leds: int
  teensy_outputs: int
  teensy_max_leds_per_output: int


# ---------------------------------------------------------------------------
# Load / Save
# ---------------------------------------------------------------------------

def load_pixel_map(config_dir: Path) -> PixelMapConfig:
  """Load pixel_map.yaml from config directory. Falls back to empty config if missing."""
  path = config_dir / "pixel_map.yaml"
  if not path.exists():
    logger.warning(f"No pixel_map.yaml at {path} — using empty config")
    return PixelMapConfig()
  try:
    with open(path) as f:
      data = yaml.safe_load(f) or {}
  except yaml.YAMLError as e:
    logger.error(f"Failed to parse {path}: {e} — using empty config")
    return PixelMapConfig()
  return _parse_config(data)


def _parse_config(data: dict) -> PixelMapConfig:
  """Parse a raw YAML dict into a PixelMapConfig."""
  teensy = data.get("teensy", {})
  strips = []
  for s in data.get("strips", []):
    lines = [
      LineConfig(
        start=tuple(ln["start"]),
        end=tuple(ln["end"]),
        color_order=ln.get("color_order", "BGR"),
      )
      for ln in s.get("lines", [])
    ]
    overrides = {}
    for ov in s.get("pixel_overrides", []):
      overrides[ov["led_index"]] = tuple(ov["position"])
    strips.append(StripConfig(
      id=s["id"],
      output=s["output"],
      output_offset=s["output_offset"],
      lines=lines,
      pixel_overrides=overrides,
    ))
  return PixelMapConfig(
    origin=data.get("origin", "bottom-left"),
    teensy_outputs=teensy.get("outputs", 8),
    teensy_max_leds_per_output=teensy.get("max_leds_per_output", 1200),
    teensy_wire_order=teensy.get("wire_order", "BGR"),
    teensy_signal_family=teensy.get("signal_family", "ws281x_800khz"),
    teensy_octo_pins=teensy.get("octo_pins", [2, 14, 7, 8, 6, 20, 21, 5]),
    strips=strips,
  )


def save_pixel_map(config: PixelMapConfig, config_dir: Path) -> None:
  """Atomically save PixelMapConfig to pixel_map.yaml."""
  path = config_dir / "pixel_map.yaml"
  config_dir.mkdir(parents=True, exist_ok=True)
  data = _serialize_config(config)
  fd, tmp_path = tempfile.mkstemp(dir=str(config_dir), suffix=".tmp")
  try:
    with os.fdopen(fd, "w") as f:
      yaml.dump(data, f, default_flow_style=False, sort_keys=False)
    os.replace(tmp_path, str(path))
    logger.info("Saved pixel_map.yaml")
  except Exception:
    try:
      os.unlink(tmp_path)
    except OSError:
      pass
    raise


def _serialize_config(config: PixelMapConfig) -> dict:
  """Convert PixelMapConfig to a dict suitable for YAML serialization."""
  strips = []
  for s in config.strips:
    strip_dict: dict = {
      "id": s.id,
      "output": s.output,
      "output_offset": s.output_offset,
      "lines": [
        {
          "start": list(ln.start),
          "end": list(ln.end),
          "color_order": ln.color_order,
        }
        for ln in s.lines
      ],
    }
    if s.pixel_overrides:
      strip_dict["pixel_overrides"] = [
        {"led_index": idx, "position": list(pos)}
        for idx, pos in s.pixel_overrides.items()
      ]
    strips.append(strip_dict)

  return {
    "origin": config.origin,
    "teensy": {
      "outputs": config.teensy_outputs,
      "max_leds_per_output": config.teensy_max_leds_per_output,
      "wire_order": config.teensy_wire_order,
      "signal_family": config.teensy_signal_family,
      "octo_pins": config.teensy_octo_pins,
    },
    "strips": strips,
  }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_pixel_map(config: PixelMapConfig) -> list[str]:
  """
  Validate a PixelMapConfig, returning a list of error strings.

  Empty list means the config is valid.
  """
  errors: list[str] = []

  # --- OctoWS2811 requires exactly 8 outputs ---
  if config.teensy_outputs != 8:
    errors.append(
      f"teensy_outputs must be exactly 8 (OctoWS2811 hardware), got {config.teensy_outputs}"
    )

  # --- Duplicate strip IDs ---
  strip_ids = [s.id for s in config.strips]
  if len(strip_ids) != len(set(strip_ids)):
    dupes = [sid for sid in strip_ids if strip_ids.count(sid) > 1]
    errors.append(f"Duplicate strip IDs: {set(dupes)}")

  all_positions: set[tuple[int, int]] = set()

  for strip in config.strips:
    # --- Output index must be < 8 (OctoWS2811 hardware) ---
    if strip.output < 0 or strip.output >= 8:
      errors.append(
        f"Strip {strip.id}: output {strip.output} out of range [0, 7] "
        f"(OctoWS2811 has exactly 8 outputs)"
      )
    prefix = f"Strip {strip.id}"

    # --- Validate lines (axis-aligned, valid color_order) ---
    line_total = 0
    for ln in strip.lines:
      try:
        line_total += ln.led_count()
      except ValueError as exc:
        errors.append(f"{prefix}: {exc}")
      if ln.color_order not in SWIZZLE_MAP:
        errors.append(
          f"{prefix}: invalid color_order '{ln.color_order}' — "
          f"must be one of {sorted(SWIZZLE_MAP.keys())}"
        )

    # --- No negative coordinates ---
    for ln in strip.lines:
      for coord_name, coord in [("start", ln.start), ("end", ln.end)]:
        if coord[0] < 0 or coord[1] < 0:
          errors.append(
            f"{prefix}: line {coord_name} has negative coordinate {coord} — "
            f"all coordinates must be non-negative"
          )

    # --- Build effective positions (lines + overrides) ---
    effective_positions: list[tuple[int, int]] = []
    for ln in strip.lines:
      try:
        effective_positions.extend(ln.positions())
      except ValueError:
        pass  # already reported above

    # Apply pixel overrides (same logic as compile_pixel_map)
    for led_idx, pos in strip.pixel_overrides.items():
      if pos[0] < 0 or pos[1] < 0:
        errors.append(
          f"{prefix}: pixel_override LED {led_idx} has negative coordinate {pos} — "
          f"all coordinates must be non-negative"
        )
      if led_idx < len(effective_positions):
        effective_positions[led_idx] = pos

    # --- No duplicate grid positions ---
    for pos in effective_positions:
      if pos in all_positions:
        errors.append(
          f"{prefix}: duplicate grid position {pos}"
        )
      all_positions.add(pos)

    # --- Output overflow ---
    total = strip.total_leds
    if strip.output_offset + total > config.teensy_max_leds_per_output:
      errors.append(
        f"{prefix}: output overflow — offset ({strip.output_offset}) + "
        f"total_leds ({total}) = {strip.output_offset + total} "
        f"exceeds max_leds_per_output ({config.teensy_max_leds_per_output})"
      )

  # --- Overlapping output ranges on the same pin ---
  from collections import defaultdict
  pin_strips: dict[int, list[StripConfig]] = defaultdict(list)
  for strip in config.strips:
    pin_strips[strip.output].append(strip)

  for pin, strips_on_pin in pin_strips.items():
    for i, a in enumerate(strips_on_pin):
      for b in strips_on_pin[i + 1:]:
        a_start, a_end = a.output_offset, a.output_offset + a.total_leds - 1
        b_start, b_end = b.output_offset, b.output_offset + b.total_leds - 1
        if a_start <= b_end and b_start <= a_end:
          errors.append(
            f"Output pin {pin}: Strip {a.id} range [{a_start}..{a_end}] "
            f"overlaps Strip {b.id} range [{b_start}..{b_end}]"
          )

  return errors


# ---------------------------------------------------------------------------
# Compilation
# ---------------------------------------------------------------------------

def compile_pixel_map(config: PixelMapConfig) -> CompiledPixelMap:
  """
  Compile a validated PixelMapConfig into fast-lookup structures.

  Returns a CompiledPixelMap with forward LUT, reverse LUT, and output config.
  """
  # First pass: expand all lines + overrides to find grid bounds
  # and build per-strip position lists.
  strip_positions: dict[int, list[tuple[int, int]]] = {}

  for strip in config.strips:
    positions: list[tuple[int, int]] = []
    for ln in strip.lines:
      positions.extend(ln.positions())

    # Apply pixel overrides
    for led_idx, pos in strip.pixel_overrides.items():
      if led_idx < len(positions):
        positions[led_idx] = pos

    strip_positions[strip.id] = positions

  # Determine grid dimensions from all mapped positions
  all_positions = []
  for positions in strip_positions.values():
    all_positions.extend(positions)

  if not all_positions:
    return CompiledPixelMap(
      width=0,
      height=0,
      origin=config.origin,
      forward_lut=np.zeros((0, 0, 2), dtype=np.int16),
      reverse_lut=[],
      output_config={},
      strips=config.strips,
      total_mapped_leds=0,
      teensy_outputs=config.teensy_outputs,
      teensy_max_leds_per_output=config.teensy_max_leds_per_output,
    )

  max_x = max(p[0] for p in all_positions)
  max_y = max(p[1] for p in all_positions)
  width = max_x + 1
  height = max_y + 1

  # Build forward LUT: (width, height, 2) → [strip_id, led_index]
  forward_lut = np.full((width, height, 2), -1, dtype=np.int16)

  # Build reverse LUT: reverse_lut[strip_id][led_index] → (x, y, swizzle)
  # We need a mapping from strip.id to its index in the reverse_lut
  max_strip_id = max(s.id for s in config.strips)
  reverse_lut: list[list] = [[] for _ in range(max_strip_id + 1)]

  # Build per-strip LED-to-color-order mapping from lines
  strip_led_color_order: dict[int, list[str]] = {}
  for strip in config.strips:
    led_orders: list[str] = []
    for ln in strip.lines:
      led_orders.extend([ln.color_order] * ln.led_count())
    strip_led_color_order[strip.id] = led_orders

  total_mapped = 0

  for strip in config.strips:
    positions = strip_positions[strip.id]
    led_orders = strip_led_color_order[strip.id]
    strip_reverse: list[tuple[int, int, tuple[int, int, int]]] = []

    for led_idx, (x, y) in enumerate(positions):
      color_order = led_orders[led_idx] if led_idx < len(led_orders) else "RGB"
      swizzle = SWIZZLE_MAP.get(color_order, (0, 1, 2))

      forward_lut[x, y] = [strip.id, led_idx]
      strip_reverse.append((x, y, swizzle))
      total_mapped += 1

    reverse_lut[strip.id] = strip_reverse

  # Build output config: output_idx → [(strip_id, offset, count), ...]
  output_config: dict[int, list[tuple[int, int, int]]] = {}
  for strip in config.strips:
    entry = (strip.id, strip.output_offset, strip.total_leds)
    if strip.output not in output_config:
      output_config[strip.output] = []
    output_config[strip.output].append(entry)

  return CompiledPixelMap(
    width=width,
    height=height,
    origin=config.origin,
    forward_lut=forward_lut,
    reverse_lut=reverse_lut,
    output_config=output_config,
    strips=config.strips,
    total_mapped_leds=total_mapped,
    teensy_outputs=config.teensy_outputs,
    teensy_max_leds_per_output=config.teensy_max_leds_per_output,
  )
