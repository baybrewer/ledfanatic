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
class ScanlineConfig:
  """A run of LEDs along a single axis (horizontal or vertical)."""

  start: tuple[int, int]
  end: tuple[int, int]

  def _validate_axis_aligned(self) -> tuple[int, int]:
    """Return (dx, dy) or raise if diagonal."""
    dx = self.end[0] - self.start[0]
    dy = self.end[1] - self.start[1]
    if dx != 0 and dy != 0:
      raise ValueError(
        f"Scanline must be axis-aligned (horizontal or vertical), "
        f"got start={self.start} end={self.end}"
      )
    return dx, dy

  def led_count(self) -> int:
    """Number of LEDs in this scanline (inclusive of both endpoints)."""
    dx, dy = self._validate_axis_aligned()
    return abs(dx) + abs(dy) + 1

  def positions(self) -> list[tuple[int, int]]:
    """Ordered list of (x, y) grid positions covered by this scanline."""
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


@dataclass
class SegmentConfig:
  """A contiguous range of LEDs within a strip sharing a color order."""

  range_start: int
  range_end: int
  color_order: str


@dataclass
class StripConfig:
  """One logical LED strip with its scanline geometry and output mapping."""

  id: int
  output: int
  output_offset: int
  total_leds: int
  segments: list[SegmentConfig]
  scanlines: list[ScanlineConfig]
  pixel_overrides: dict[int, tuple[int, int]] = field(default_factory=dict)


@dataclass
class PixelMapConfig:
  """Top-level pixel map configuration loaded from YAML."""

  origin: str
  teensy_outputs: int
  teensy_max_leds_per_output: int
  teensy_wire_order: str
  teensy_signal_family: str
  teensy_octo_pins: list[int]
  strips: list[StripConfig]


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
  """Load pixel_map.yaml from a config directory."""
  path = config_dir / "pixel_map.yaml"
  with open(path) as f:
    data = yaml.safe_load(f)
  return _parse_config(data)


def _parse_config(data: dict) -> PixelMapConfig:
  """Parse a raw YAML dict into a PixelMapConfig."""
  teensy = data.get("teensy", {})
  strips = []
  for s in data.get("strips", []):
    segments = [
      SegmentConfig(
        range_start=seg["range_start"],
        range_end=seg["range_end"],
        color_order=seg["color_order"],
      )
      for seg in s.get("segments", [])
    ]
    scanlines = [
      ScanlineConfig(
        start=tuple(sc["start"]),
        end=tuple(sc["end"]),
      )
      for sc in s.get("scanlines", [])
    ]
    overrides = {}
    for ov in s.get("pixel_overrides", []):
      overrides[ov["led_index"]] = tuple(ov["position"])
    strips.append(StripConfig(
      id=s["id"],
      output=s["output"],
      output_offset=s["output_offset"],
      total_leds=s["total_leds"],
      segments=segments,
      scanlines=scanlines,
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
      "total_leds": s.total_leds,
      "segments": [
        {
          "range_start": seg.range_start,
          "range_end": seg.range_end,
          "color_order": seg.color_order,
        }
        for seg in s.segments
      ],
      "scanlines": [
        {
          "start": list(sc.start),
          "end": list(sc.end),
        }
        for sc in s.scanlines
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

  all_positions: set[tuple[int, int]] = set()

  for strip in config.strips:
    prefix = f"Strip {strip.id}"

    # --- Scanline total must match total_leds ---
    scanline_total = 0
    for sc in strip.scanlines:
      try:
        scanline_total += sc.led_count()
      except ValueError as exc:
        errors.append(f"{prefix}: {exc}")

    if scanline_total != strip.total_leds:
      errors.append(
        f"{prefix}: scanline LED count ({scanline_total}) != "
        f"total_leds ({strip.total_leds}) — mismatch"
      )

    # --- No negative coordinates ---
    for sc in strip.scanlines:
      for coord_name, coord in [("start", sc.start), ("end", sc.end)]:
        if coord[0] < 0 or coord[1] < 0:
          errors.append(
            f"{prefix}: scanline {coord_name} has negative coordinate {coord} — "
            f"all coordinates must be non-negative"
          )

    # --- Build effective positions (scanlines + overrides) ---
    effective_positions: list[tuple[int, int]] = []
    for sc in strip.scanlines:
      try:
        effective_positions.extend(sc.positions())
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
    if strip.output_offset + strip.total_leds > config.teensy_max_leds_per_output:
      errors.append(
        f"{prefix}: output overflow — offset ({strip.output_offset}) + "
        f"total_leds ({strip.total_leds}) = {strip.output_offset + strip.total_leds} "
        f"exceeds max_leds_per_output ({config.teensy_max_leds_per_output})"
      )

    # --- Segment coverage ---
    covered = set()
    for seg in strip.segments:
      # Segment range bounds check
      if seg.range_end >= strip.total_leds:
        errors.append(
          f"{prefix}: segment range_end ({seg.range_end}) exceeds "
          f"total_leds ({strip.total_leds}) — out of bounds"
        )
      for i in range(seg.range_start, seg.range_end + 1):
        covered.add(i)

    expected = set(range(strip.total_leds))
    if covered != expected:
      missing = expected - covered
      if missing:
        errors.append(
          f"{prefix}: segment coverage gap — LED indices {sorted(missing)} not covered"
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
  # First pass: expand all scanlines + overrides to find grid bounds
  # and build per-strip position lists.
  strip_positions: dict[int, list[tuple[int, int]]] = {}

  for strip in config.strips:
    positions: list[tuple[int, int]] = []
    for sc in strip.scanlines:
      positions.extend(sc.positions())

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

  # Build segment color order lookup per strip
  strip_segment_map: dict[int, list[SegmentConfig]] = {}
  for strip in config.strips:
    strip_segment_map[strip.id] = strip.segments

  total_mapped = 0

  for strip in config.strips:
    positions = strip_positions[strip.id]
    strip_reverse: list[tuple[int, int, tuple[int, int, int]]] = []

    for led_idx, (x, y) in enumerate(positions):
      # Find color order for this LED from segments
      color_order = "RGB"  # default fallback
      for seg in strip.segments:
        if seg.range_start <= led_idx <= seg.range_end:
          color_order = seg.color_order
          break

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
