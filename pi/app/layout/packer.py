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
    channel_offsets: dict[int, int] = {}
    offset = 0
    for ch in range(8):
        channel_offsets[ch] = offset
        offset += layout.output_sizes.get(ch, 0) * 3

    total_bytes = offset
    buf = bytearray(total_bytes)

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
