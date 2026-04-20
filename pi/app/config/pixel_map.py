"""
Pixel map — single source of truth for all LED geometry.

Defines the data model for mapping logical grid positions to physical
LED segments/outputs. Supports loading from YAML, validation, and
compilation into forward/reverse LUTs for fast rendering.

Schema v2: flat list of SegmentConfig (no strips, no nesting).
Backward compat: v1 YAML with strips key is auto-migrated.
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
class SegmentConfig:
  """A run of LEDs along a single axis with its own color order and output pin."""

  start: tuple[int, int]
  end: tuple[int, int]
  output: int
  color_order: str = "BGR"
  led_offset: int = -1  # -1 = auto-calculate, >=0 = explicit offset on this output

  def _validate_axis_aligned(self) -> tuple[int, int]:
    """Return (dx, dy) or raise if diagonal."""
    dx = self.end[0] - self.start[0]
    dy = self.end[1] - self.start[1]
    if dx != 0 and dy != 0:
      raise ValueError(
        f"Segment must be axis-aligned (horizontal or vertical), "
        f"got start={self.start} end={self.end}"
      )
    return dx, dy

  def led_count(self) -> int:
    """Number of LEDs in this segment (inclusive of both endpoints)."""
    dx, dy = self._validate_axis_aligned()
    return abs(dx) + abs(dy) + 1

  def positions(self) -> list[tuple[int, int]]:
    """Ordered list of (x, y) grid positions covered by this segment."""
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


# Backward-compatible aliases so existing imports don't break
LineConfig = SegmentConfig
ScanlineConfig = SegmentConfig


@dataclass
class PixelMapConfig:
  """Top-level pixel map configuration loaded from YAML."""

  origin: str = "bottom-left"
  grid_width: int = 0   # 0 = auto-derive from segments
  grid_height: int = 0  # 0 = auto-derive from segments
  teensy_outputs: int = 8
  teensy_max_leds_per_output: int = 1200
  teensy_wire_order: str = "BGR"
  teensy_signal_family: str = "ws281x_800khz"
  teensy_octo_pins: list[int] = field(default_factory=lambda: [2, 14, 7, 8, 6, 20, 21, 5])
  segments: list[SegmentConfig] = field(default_factory=list)
  pixel_overrides: dict[str, tuple[int, int]] = field(default_factory=dict)


# Removed — StripConfig no longer exists in the flat model.
StripConfig = None


@dataclass
class CompiledPixelMap:
  """Pre-compiled lookup tables and metadata for fast rendering."""

  width: int
  height: int
  origin: str
  forward_lut: np.ndarray       # (width, height, 2) int16 — [segment_index, led_index]
  reverse_lut: list[list]       # reverse_lut[segment_index][led_index] → (x, y, swizzle)
  output_config: list[int]      # LEDs per output pin [0..7], 8 entries
  segment_offsets: list[int]    # auto-calculated offset for each segment on its output
  segments: list[SegmentConfig]
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
  """Parse a raw YAML dict into a PixelMapConfig.

  Supports both schema v2 (segments key) and v1 (strips key, auto-migrated).
  """
  teensy = data.get("teensy", {})

  # Schema v2: flat segments list
  if "segments" in data:
    segments = [
      SegmentConfig(
        start=tuple(seg["start"]),
        end=tuple(seg["end"]),
        output=seg["output"],
        color_order=seg.get("color_order", "BGR"),
        led_offset=seg.get("led_offset", -1),
      )
      for seg in data["segments"]
    ]
    overrides = {}
    for ov in data.get("pixel_overrides", []):
      overrides[str(ov["led_key"])] = tuple(ov["position"])
    return PixelMapConfig(
      origin=data.get("origin", "bottom-left"),
      grid_width=data.get("grid_width", 0),
      grid_height=data.get("grid_height", 0),
      teensy_outputs=teensy.get("outputs", 8),
      teensy_max_leds_per_output=teensy.get("max_leds_per_output", 1200),
      teensy_wire_order=teensy.get("wire_order", "BGR"),
      teensy_signal_family=teensy.get("signal_family", "ws281x_800khz"),
      teensy_octo_pins=teensy.get("octo_pins", [2, 14, 7, 8, 6, 20, 21, 5]),
      segments=segments,
      pixel_overrides=overrides,
    )

  # Schema v1: strips with nested lines — migrate to flat segments
  if "strips" in data:
    return _migrate_v1_to_v2(data, teensy)

  # Empty / unknown
  return PixelMapConfig(
    origin=data.get("origin", "bottom-left"),
    teensy_outputs=teensy.get("outputs", 8),
    teensy_max_leds_per_output=teensy.get("max_leds_per_output", 1200),
    teensy_wire_order=teensy.get("wire_order", "BGR"),
    teensy_signal_family=teensy.get("signal_family", "ws281x_800khz"),
    teensy_octo_pins=teensy.get("octo_pins", [2, 14, 7, 8, 6, 20, 21, 5]),
  )


def _migrate_v1_to_v2(data: dict, teensy: dict) -> PixelMapConfig:
  """Migrate schema v1 (strips with nested lines) to flat segments."""
  segments: list[SegmentConfig] = []
  all_overrides: dict[str, tuple[int, int]] = {}

  for strip in data.get("strips", []):
    output = strip["output"]
    for ln in strip.get("lines", []):
      segments.append(SegmentConfig(
        start=tuple(ln["start"]),
        end=tuple(ln["end"]),
        output=output,
        color_order=ln.get("color_order", "BGR"),
      ))
    # Migrate per-strip pixel_overrides using "seg_idx:led_idx" keys
    # We don't have segment indices yet for overrides in v1 format,
    # so we skip them (they were rarely used and the setup UI will recreate)

  logger.info(f"Migrated v1 pixel_map (strips) to v2 (segments): {len(segments)} segments")
  return PixelMapConfig(
    origin=data.get("origin", "bottom-left"),
    teensy_outputs=teensy.get("outputs", 8),
    teensy_max_leds_per_output=teensy.get("max_leds_per_output", 1200),
    teensy_wire_order=teensy.get("wire_order", "BGR"),
    teensy_signal_family=teensy.get("signal_family", "ws281x_800khz"),
    teensy_octo_pins=teensy.get("octo_pins", [2, 14, 7, 8, 6, 20, 21, 5]),
    segments=segments,
    pixel_overrides=all_overrides,
  )


def save_pixel_map(config: PixelMapConfig, config_dir: Path) -> None:
  """Atomically save PixelMapConfig to pixel_map.yaml (schema v2)."""
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
  """Convert PixelMapConfig to a dict suitable for YAML serialization (schema v2)."""
  segments = []
  for seg in config.segments:
    d = {
      "start": list(seg.start),
      "end": list(seg.end),
      "output": seg.output,
      "color_order": seg.color_order,
    }
    if seg.led_offset >= 0:
      d["led_offset"] = seg.led_offset
    segments.append(d)

  result: dict = {
    "schema_version": 2,
    "origin": config.origin,
    "grid_width": config.grid_width,
    "grid_height": config.grid_height,
    "teensy": {
      "outputs": config.teensy_outputs,
      "max_leds_per_output": config.teensy_max_leds_per_output,
      "wire_order": config.teensy_wire_order,
      "signal_family": config.teensy_signal_family,
      "octo_pins": config.teensy_octo_pins,
    },
    "segments": segments,
  }

  if config.pixel_overrides:
    result["pixel_overrides"] = [
      {"led_key": key, "position": list(pos)}
      for key, pos in config.pixel_overrides.items()
    ]

  return result


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

  all_positions: set[tuple[int, int]] = set()

  for seg_idx, seg in enumerate(config.segments):
    prefix = f"Segment {seg_idx}"

    # --- Output index must be 0-7 ---
    if seg.output < 0 or seg.output >= 8:
      errors.append(
        f"{prefix}: output {seg.output} out of range [0, 7] "
        f"(OctoWS2811 has exactly 8 outputs)"
      )

    # --- Validate axis-aligned ---
    try:
      seg.led_count()
    except ValueError as exc:
      errors.append(f"{prefix}: {exc}")

    # --- Valid color_order ---
    if seg.color_order not in SWIZZLE_MAP:
      errors.append(
        f"{prefix}: invalid color_order '{seg.color_order}' — "
        f"must be one of {sorted(SWIZZLE_MAP.keys())}"
      )

    # --- No negative coordinates ---
    for coord_name, coord in [("start", seg.start), ("end", seg.end)]:
      if coord[0] < 0 or coord[1] < 0:
        errors.append(
          f"{prefix}: {coord_name} has negative coordinate {coord} — "
          f"all coordinates must be non-negative"
        )

    # --- No duplicate grid positions ---
    try:
      for pos in seg.positions():
        if pos in all_positions:
          errors.append(f"{prefix}: duplicate grid position {pos}")
        all_positions.add(pos)
    except ValueError:
      pass  # already reported above

  # --- Output overflow: for each pin, sum LED counts of all segments on it ---
  from collections import defaultdict
  pin_leds: dict[int, int] = defaultdict(int)
  for seg in config.segments:
    try:
      pin_leds[seg.output] += seg.led_count()
    except ValueError:
      pass  # already reported above

  for pin, total in pin_leds.items():
    if total > config.teensy_max_leds_per_output:
      errors.append(
        f"Output pin {pin}: total LEDs ({total}) exceeds "
        f"max_leds_per_output ({config.teensy_max_leds_per_output})"
      )

  return errors


# ---------------------------------------------------------------------------
# Compilation
# ---------------------------------------------------------------------------

def compile_pixel_map(config: PixelMapConfig) -> CompiledPixelMap:
  """
  Compile a validated PixelMapConfig into fast-lookup structures.

  Returns a CompiledPixelMap with forward LUT, reverse LUT, output config,
  and auto-calculated segment offsets.
  """
  # First pass: expand all segment positions and apply overrides
  segment_positions: list[list[tuple[int, int]]] = []

  for seg in config.segments:
    positions = seg.positions()
    segment_positions.append(positions)

  # Apply top-level pixel overrides (key format: "seg_idx:led_idx")
  for key, pos in config.pixel_overrides.items():
    try:
      seg_idx_str, led_idx_str = key.split(":")
      seg_idx = int(seg_idx_str)
      led_idx = int(led_idx_str)
      if seg_idx < len(segment_positions) and led_idx < len(segment_positions[seg_idx]):
        segment_positions[seg_idx][led_idx] = pos
    except (ValueError, IndexError):
      pass

  # Determine grid dimensions from all mapped positions
  all_pos = []
  for positions in segment_positions:
    all_pos.extend(positions)

  if not all_pos:
    # Use declared dimensions if set, else empty
    w = config.grid_width if config.grid_width > 0 else 0
    h = config.grid_height if config.grid_height > 0 else 0
    return CompiledPixelMap(
      width=w,
      height=h,
      origin=config.origin,
      forward_lut=np.zeros((max(w, 0), max(h, 0), 2), dtype=np.int16),
      reverse_lut=[],
      output_config=[0] * 8,
      segment_offsets=[],
      segments=config.segments,
      total_mapped_leds=0,
      teensy_outputs=config.teensy_outputs,
      teensy_max_leds_per_output=config.teensy_max_leds_per_output,
    )

  # Use declared grid dimensions if set, otherwise derive from segment positions
  max_x = max(p[0] for p in all_pos)
  max_y = max(p[1] for p in all_pos)
  width = config.grid_width if config.grid_width > 0 else max_x + 1
  height = config.grid_height if config.grid_height > 0 else max_y + 1

  # Build forward LUT: (width, height, 2) → [segment_index, led_index]
  forward_lut = np.full((width, height, 2), -1, dtype=np.int16)

  # Build reverse LUT: reverse_lut[segment_index][led_index] → (x, y, swizzle)
  reverse_lut: list[list] = [[] for _ in range(len(config.segments))]

  # Calculate segment offsets: use explicit led_offset if set, else auto-stack
  segment_offsets: list[int] = []
  pin_running_offset: dict[int, int] = {}
  for seg in config.segments:
    pin = seg.output
    if seg.led_offset >= 0:
      # Explicit offset — use it directly
      offset = seg.led_offset
    else:
      # Auto-calculate: stack after previous segments on this pin
      offset = pin_running_offset.get(pin, 0)
    segment_offsets.append(offset)
    try:
      end_pos = offset + seg.led_count()
      # Track the highest end position for output_config
      pin_running_offset[pin] = max(pin_running_offset.get(pin, 0), end_pos)
    except ValueError:
      pass

  # Build output_config: 8 entries, one per pin, value = max LED position on that pin
  output_config = [0] * 8
  for pin, total in pin_running_offset.items():
    if 0 <= pin < 8:
      output_config[pin] = total

  total_mapped = 0

  for seg_idx, seg in enumerate(config.segments):
    positions = segment_positions[seg_idx]
    swizzle = SWIZZLE_MAP.get(seg.color_order, (0, 1, 2))
    seg_reverse: list[tuple[int, int, tuple[int, int, int]]] = []

    for led_idx, (x, y) in enumerate(positions):
      forward_lut[x, y] = [seg_idx, led_idx]
      seg_reverse.append((x, y, swizzle))
      total_mapped += 1

    reverse_lut[seg_idx] = seg_reverse

  return CompiledPixelMap(
    width=width,
    height=height,
    origin=config.origin,
    forward_lut=forward_lut,
    reverse_lut=reverse_lut,
    output_config=output_config,
    segment_offsets=segment_offsets,
    segments=config.segments,
    total_mapped_leds=total_mapped,
    teensy_outputs=config.teensy_outputs,
    teensy_max_leds_per_output=config.teensy_max_leds_per_output,
  )
