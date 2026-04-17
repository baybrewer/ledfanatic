"""
Output packer — maps rendered grid frame to serialized LED output buffer.

Uses the reverse LUT from CompiledPixelMap to read each LED's pixel
from the rendered frame, apply per-segment color order swizzle, and
write to the correct position in the output buffer.
"""

import numpy as np
from ..config.pixel_map import CompiledPixelMap


def _compute_leds_per_output(pixel_map: CompiledPixelMap) -> list[int]:
  """Compute LED count per output pin (0 through max used pin).

  For each output pin, the LED count is the maximum (offset + count)
  across all strips assigned to that pin. Unused pins get 0.
  """
  if not pixel_map.output_config:
    return []

  max_pin = max(pixel_map.output_config.keys())
  leds_per_output = [0] * (max_pin + 1)

  for pin, entries in pixel_map.output_config.items():
    for _strip_id, offset, count in entries:
      needed = offset + count
      if needed > leds_per_output[pin]:
        leds_per_output[pin] = needed

  return leds_per_output


def pack_frame(frame: np.ndarray, pixel_map: CompiledPixelMap) -> bytes:
  """Pack a (width, height, 3) rendered frame into output buffer.

  Returns bytes: contiguous blocks of leds_per_output[pin] * 3
  for each pin 0 through max used pin.
  """
  leds_per_output = _compute_leds_per_output(pixel_map)
  total_bytes = sum(n * 3 for n in leds_per_output)
  buf = bytearray(total_bytes)

  # Precompute byte offset for each output pin
  pin_offsets = []
  offset = 0
  for n in leds_per_output:
    pin_offsets.append(offset)
    offset += n * 3

  # Iterate strips and pack using reverse LUT
  for strip in pixel_map.strips:
    strip_reverse = pixel_map.reverse_lut[strip.id]
    pin = strip.output
    base = pin_offsets[pin] + strip.output_offset * 3

    for led_idx in range(strip.total_leds):
      entry = strip_reverse[led_idx]
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
