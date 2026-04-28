"""
Layout packer — converts logical frame to physical output buffers.

Uses precomputed NumPy index arrays from CompiledLayout for O(1)-setup
vectorized packing. No Python loops at runtime.
"""

import numpy as np
from .compiler import CompiledLayout


def pack_frame(frame: np.ndarray, layout: CompiledLayout) -> bytes:
    """
    Pack a (width, height, 3) uint8 frame into contiguous output buffer.

    Returns bytes laid out as: [channel_0_data][channel_1_data]...[channel_7_data]
    where each channel's data is output_sizes[ch] * 3 bytes.
    """
    if layout.pack_buf_size == 0:
        return b''
    buf = np.zeros(layout.pack_buf_size, dtype=np.uint8)
    flat = frame.ravel()
    raw = flat[layout.pack_src]
    # Apply per-segment brightness correction via LUT
    if layout.cal_luts.shape[0] > 0:
        raw = layout.cal_luts[layout.cal_seg_idx_expanded, layout.cal_logical_ch, raw]
    buf[layout.pack_dst] = raw
    return buf.tobytes()


def output_config_list(layout: CompiledLayout) -> list[int]:
    """
    Return 8-entry list of LEDs per output channel (for Teensy CONFIG packet).
    Index = channel number, value = number of LEDs on that channel.
    """
    return [layout.output_sizes.get(ch, 0) for ch in range(8)]
