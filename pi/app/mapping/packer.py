"""
Output packer — maps rendered grid frame to serialized LED output buffer.

Uses the reverse LUT from CompiledPixelMap to read each LED's pixel
from the rendered frame, apply per-segment color order swizzle, and
write to the correct position in the output buffer.
"""

import numpy as np
from ..config.pixel_map import CompiledPixelMap


def pack_frame(frame: np.ndarray, pixel_map: CompiledPixelMap) -> bytes:
  """Pack a (width, height, 3) rendered frame into output buffer.

  Returns bytes: contiguous blocks of output_config[pin] * 3
  for each pin 0 through 7.
  """
  output_config = pixel_map.output_config  # list[int], 8 entries
  total_bytes = sum(n * 3 for n in output_config)
  buf = bytearray(total_bytes)

  # Precompute byte offset for each output pin
  pin_offsets = []
  offset = 0
  for n in output_config:
    pin_offsets.append(offset)
    offset += n * 3

  # Pack each segment
  for seg_idx, segment in enumerate(pixel_map.segments):
    seg_reverse = pixel_map.reverse_lut[seg_idx]
    pin = segment.output
    seg_offset = pixel_map.segment_offsets[seg_idx]
    base = pin_offsets[pin] + seg_offset * 3

    for led_idx in range(len(seg_reverse)):
      entry = seg_reverse[led_idx]
      if entry is None:
        continue
      x, y, swizzle = entry
      if x >= frame.shape[0] or y >= frame.shape[1]:
        continue
      rgb = frame[x, y]
      pos = base + led_idx * 3
      buf[pos] = rgb[swizzle[0]]
      buf[pos + 1] = rgb[swizzle[1]]
      buf[pos + 2] = rgb[swizzle[2]]

  return bytes(buf)
