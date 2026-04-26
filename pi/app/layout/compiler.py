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
