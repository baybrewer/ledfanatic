"""Color math utilities — ported from led_sim.py.

All functions operate on 0-255 integer ranges unless noted.
"""


def clamp(v, lo=0, hi=255):
  """Integer clamp."""
  return int(max(lo, min(hi, v)))


def clampf(v, lo=0.0, hi=1.0):
  """Float clamp."""
  return max(lo, min(hi, v))


def qsub8(a, b):
  """Saturating subtract (floor at 0)."""
  return max(0, a - b)


def qadd8(a, b):
  """Saturating add (ceiling at 255)."""
  return min(255, a + b)


def scale8(a, b):
  """Fixed-point 8-bit scaling: (a * b) >> 8."""
  return (a * b) >> 8


def hsv2rgb(h, s, v):
  """Convert HSV to RGB. All values 0-255 range.

  This is the led_sim.py integer-math version, distinct from
  base.py's float-range hsv_to_rgb.
  """
  if v == 0:
    return (0, 0, 0)
  if s == 0:
    return (v, v, v)
  region = (h * 6) >> 8
  frac = (h * 6) & 0xFF
  p = (v * (255 - s)) >> 8
  q = (v * (255 - ((s * frac) >> 8))) >> 8
  t = (v * (255 - ((s * (255 - frac)) >> 8))) >> 8
  lut = [(v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q)]
  return lut[min(region, 5)]
