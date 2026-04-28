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
    BrightnessCal, VALID_DIRECTIONS,
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
    segment_key: tuple = ()  # (output_id, segment_id)


def _build_segment_lut(cal: BrightnessCal) -> np.ndarray:
    """Build (3, 256) uint8 LUT from 3-point calibration curve."""
    lut = np.zeros((3, 256), dtype=np.uint8)
    levels = np.array([0.0, 0.2, 0.5, 0.8, 1.0])
    for ch_idx, points in enumerate([cal.r, cal.g, cal.b]):
        muls = np.array([1.0, points[0], points[1], points[2], 1.0])
        for i in range(256):
            brightness = i / 255.0
            idx = min(np.searchsorted(levels, brightness, side='right') - 1, len(levels) - 2)
            idx = max(0, idx)
            t = (brightness - levels[idx]) / max(levels[idx + 1] - levels[idx], 1e-6)
            mul = muls[idx] * (1 - t) + muls[idx + 1] * t
            lut[ch_idx, i] = min(255, max(0, int(i * mul + 0.5)))
    return lut


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
    # Precomputed NumPy index arrays for vectorized pack_frame
    pack_src: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.int32))
    pack_dst: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.int32))
    pack_buf_size: int = 0
    # Per-segment brightness calibration LUTs
    cal_luts: np.ndarray = field(default_factory=lambda: np.empty((0, 3, 256), dtype=np.uint8))
    cal_seg_idx_expanded: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.int32))
    cal_logical_ch: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.int32))


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
                    segment_key=(output.id, seg.id),
                ))
                total_mapped += 1
                if phys_idx + 1 > max_idx:
                    max_idx = phys_idx + 1

        output_sizes[ch] = max(output_sizes.get(ch, 0), max_idx)

    # Precompute NumPy index arrays for vectorized pack_frame
    channel_offsets: dict[int, int] = {}
    offset = 0
    for ch in range(8):
        channel_offsets[ch] = offset
        offset += output_sizes.get(ch, 0) * 3
    pack_buf_size = offset

    n = len(entries)
    src = np.empty(n * 3, dtype=np.int32)
    dst = np.empty(n * 3, dtype=np.int32)
    for i, e in enumerate(entries):
        base_src = (e.x * h + e.y) * 3
        base_dst = channel_offsets[e.channel] + e.pixel_index * 3
        i3 = i * 3
        src[i3] = base_src + e.swizzle[0]
        src[i3 + 1] = base_src + e.swizzle[1]
        src[i3 + 2] = base_src + e.swizzle[2]
        dst[i3] = base_dst
        dst[i3 + 1] = base_dst + 1
        dst[i3 + 2] = base_dst + 2

    # Build per-segment brightness calibration LUTs
    seg_keys_ordered: list[tuple] = []
    seg_key_to_idx: dict[tuple, int] = {}
    segment_cal_map: dict[tuple, BrightnessCal] = {}

    for output in config.outputs:
        for seg in output.segments:
            if not seg.enabled:
                continue
            key = (output.id, seg.id)
            segment_cal_map[key] = seg.brightness_cal

    for e in entries:
        if e.segment_key not in seg_key_to_idx:
            seg_key_to_idx[e.segment_key] = len(seg_keys_ordered)
            seg_keys_ordered.append(e.segment_key)

    if seg_keys_ordered:
        cal_luts = np.stack([
            _build_segment_lut(segment_cal_map.get(key, BrightnessCal()))
            for key in seg_keys_ordered
        ])
    else:
        cal_luts = np.empty((0, 3, 256), dtype=np.uint8)

    cal_seg_idx = np.array(
        [seg_key_to_idx[e.segment_key] for e in entries], dtype=np.int32
    )
    cal_seg_idx_expanded = np.repeat(cal_seg_idx, 3)

    cal_logical_ch = np.empty(n * 3, dtype=np.int32)
    for i, e in enumerate(entries):
        i3 = i * 3
        cal_logical_ch[i3] = e.swizzle[0]
        cal_logical_ch[i3 + 1] = e.swizzle[1]
        cal_logical_ch[i3 + 2] = e.swizzle[2]

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
        pack_src=src,
        pack_dst=dst,
        pack_buf_size=pack_buf_size,
        cal_luts=cal_luts,
        cal_seg_idx_expanded=cal_seg_idx_expanded,
        cal_logical_ch=cal_logical_ch,
    )
