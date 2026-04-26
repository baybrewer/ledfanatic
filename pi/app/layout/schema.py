"""
Layout schema — data models for the declarative LED layout config.

Defines the structure of layout.yaml: matrix dimensions, outputs with
segments (linear or explicit), and parsing from raw dict/YAML.
"""

from dataclasses import dataclass, field
from typing import Optional


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
    points: tuple[tuple[int, int], ...]
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
                points = tuple((p["x"], p["y"]) for p in seg_raw["points"])
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
