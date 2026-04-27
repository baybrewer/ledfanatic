"""
Layout compiler — validates layout config and compiles to mapping tables.

Startup-only: validates all rules, then produces a CompiledLayout with
precomputed forward/reverse LUTs and a flat mapping entry list for
fast per-frame packing.
"""

from dataclasses import dataclass, field
from typing import Optional

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
        return [(x, y) for x, y in seg.points]
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


# --- Color order swizzle ---
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
    forward_lut: list[list[Optional[tuple[int, int]]]]
    reverse_lut: dict[int, dict[int, Optional[tuple[int, int]]]]
    entries: list[MappingEntry]
    output_sizes: dict[int, int]
    color_swizzle: dict[int, tuple[int, int, int]]
    total_mapped: int


def compile_layout(config: LayoutConfig) -> CompiledLayout:
    """Compile a validated LayoutConfig into fast-lookup structures."""
    w, h = config.matrix.width, config.matrix.height

    forward_lut: list[list[Optional[tuple[int, int]]]] = [
        [None] * h for _ in range(w)
    ]

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

            # Per-segment color_order overrides output-level
            seg_co = getattr(seg, 'color_order', '') or ''
            seg_swizzle = SWIZZLE_MAP.get(seg_co, swizzle) if seg_co else swizzle

            positions = _expand_segment(seg)

            for i, (px, py) in enumerate(positions):
                phys_idx = seg.physical_offset + i
                forward_lut[px][py] = (ch, phys_idx)
                reverse_lut[ch][phys_idx] = (px, py)
                entries.append(MappingEntry(
                    x=px, y=py, channel=ch,
                    pixel_index=phys_idx, swizzle=seg_swizzle,
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
