"""
Built-in generative effects for the LED pillar.
"""

import math
import numpy as np
from typing import Optional

from .base import Effect, hsv_to_rgb, hex_to_rgb, palette_sample, lerp_color
from .engine.palettes import pal_color_grid, NUM_PALETTES, PALETTE_NAMES


def _get_palette_idx(params: dict, default: int = 0) -> int:
  """Get palette index from params, handling string names or int values."""
  val = params.get('palette', default)
  if isinstance(val, str):
    try:
      return PALETTE_NAMES.index(val) % NUM_PALETTES
    except ValueError:
      return default % NUM_PALETTES
  return int(val) % NUM_PALETTES


def _hsv_array_to_rgb(h: np.ndarray, s: float, v: float) -> np.ndarray:
  """Vectorized HSV to RGB. h is an array of hues (0-1), s and v are scalars."""
  h = h % 1.0
  shape = h.shape
  frame = np.zeros((*shape, 3), dtype=np.uint8)

  i = (h * 6.0).astype(int) % 6
  f = h * 6.0 - (h * 6.0).astype(int)

  p = v * (1.0 - s)
  q = v * (1.0 - s * f)
  t_val = v * (1.0 - s * (1.0 - f))

  # Build RGB channels
  r = np.where(i == 0, v, np.where(i == 1, q, np.where(i == 2, p, np.where(i == 3, p, np.where(i == 4, t_val, v)))))
  g = np.where(i == 0, t_val, np.where(i == 1, v, np.where(i == 2, v, np.where(i == 3, q, np.where(i == 4, p, p)))))
  b = np.where(i == 0, p, np.where(i == 1, p, np.where(i == 2, t_val, np.where(i == 3, v, np.where(i == 4, v, q)))))

  frame[..., 0] = (r * 255).astype(np.uint8)
  frame[..., 1] = (g * 255).astype(np.uint8)
  frame[..., 2] = (b * 255).astype(np.uint8)
  return frame


class SolidColor(Effect):
  """Solid color fill — static or palette cycling.

  speed=0: static color from 'hue' param (0-1).
  speed>0: cycles through selected palette at that speed.
  """

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)
    speed = self.params.get('speed', 0.0)
    frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)

    if speed == 0:
      # Static mode — use color param or hue slider
      color_hex = self.params.get('color', None)
      if color_hex:
        color = hex_to_rgb(color_hex)
      else:
        hue = self.params.get('hue', 0.0)
        pal_idx = _get_palette_idx(self.params)
        color = tuple(pal_color_grid(pal_idx, np.array([hue]))[0])
      frame[:, :] = color
    else:
      # Fade-cycle mode — smoothly cycle through palette
      pal_idx = _get_palette_idx(self.params)
      pos = (elapsed * speed * 0.05) % 1.0
      color = pal_color_grid(pal_idx, np.array([pos]))[0]
      frame[:, :] = color

    return frame


class VerticalGradient(Effect):
  """Animated vertical gradient from a palette."""

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)
    speed = self.params.get('speed', 0.05)
    pal_idx = _get_palette_idx(self.params)

    ys = np.arange(self.height, dtype=np.float64) / self.height
    pos = (ys + elapsed * speed) % 1.0  # (height,)

    # Broadcast to (width, height) and lookup palette
    pos_2d = np.broadcast_to(pos[np.newaxis, :], (self.width, self.height))
    return pal_color_grid(pal_idx, pos_2d)


class RainbowRotate(Effect):
  """Rainbow that rotates around the cylinder."""

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)
    speed = self.params.get('speed', 1.0)
    scale = self.params.get('scale', 1.0)
    pal_idx = _get_palette_idx(self.params)

    xs = np.arange(self.width, dtype=np.float64) / self.width * scale
    ys = np.arange(self.height, dtype=np.float64) / self.height * 0.3
    xx, yy = np.meshgrid(xs, ys, indexing='ij')
    hue = (xx + yy + elapsed * speed * 0.1) % 1.0

    return pal_color_grid(pal_idx, hue)


class Plasma(Effect):
  """Plasma effect using overlapping sine waves."""

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)
    speed = self.params.get('speed', 1.0)
    scale = self.params.get('scale', 2.0)
    pal_idx = _get_palette_idx(self.params)

    tt = elapsed * speed

    xs = np.arange(self.width, dtype=np.float64) / self.width * scale * math.pi * 2
    ys = np.arange(self.height, dtype=np.float64) / self.height * scale * math.pi * 2
    xx, yy = np.meshgrid(xs, ys, indexing='ij')

    v1 = np.sin(xx + tt)
    v2 = np.sin(yy + tt * 0.7)
    v3 = np.sin(xx + yy + tt * 0.5)
    v4 = np.sin(np.sqrt(xx**2 + yy**2) + tt * 1.3)

    v = (v1 + v2 + v3 + v4) / 4.0
    hue = (v + 1.0) / 2.0

    return pal_color_grid(pal_idx, hue)


class Twinkle(Effect):
  """Random twinkling stars."""

  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self._rng = np.random.default_rng(42)
    self._stars = self._rng.random((self.width, self.height)) * 2 * math.pi

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)
    speed = self.params.get('speed', 1.0)
    density = self.params.get('density', 0.005)
    darkness = self.params.get('darkness', 0.0)
    pal_idx = _get_palette_idx(self.params)

    # Each star twinkles at its own rate — no global phase shift
    brightness = (np.sin(self._stars * 3.0 + self._stars * elapsed * speed * 0.5) + 1.0) / 2.0
    # Density controls how narrow the "bright" window is — lower density = fewer stars visible at once.
    # density=0.005 → threshold=0.99 (only brightest peaks), density=0.05 → threshold=0.9 (more visible).
    threshold = max(0.0, 1.0 - density * 20.0)
    visible = brightness > threshold

    # Palette position varies by row and time
    y_coords = np.arange(self.height, dtype=np.float64) / self.height * 0.3
    hue = (elapsed * 0.02 + y_coords[np.newaxis, :]) % 1.0

    frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)
    rgb = pal_color_grid(pal_idx, hue)
    # Apply per-pixel brightness and darkness
    dim = 1.0 - min(1.0, max(0.0, darkness))
    scaled = (rgb.astype(np.float32) * brightness[..., np.newaxis] * dim).astype(np.uint8)
    frame[visible] = scaled[visible]

    return frame


class Spark(Effect):
  """Upward-moving sparks."""

  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self._sparks: list[dict] = []
    self._last_spawn = 0.0

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)
    speed = self.params.get('speed', 2.0)
    rate = self.params.get('rate', 10)
    brightness = self.params.get('brightness', 1.0)
    pal_idx = _get_palette_idx(self.params)
    frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)

    # Spawn new sparks
    spawn_interval = 1.0 / max(1, rate)
    while elapsed - self._last_spawn > spawn_interval:
      self._last_spawn += spawn_interval
      self._sparks.append({
        'x': np.random.randint(0, self.width),
        'y': 0.0,
        'speed': speed * (0.5 + np.random.random()),
        'hue': np.random.random(),
        'life': 1.0,
      })

    # Update and draw
    alive = []
    for s in self._sparks:
      s['y'] += s['speed']
      s['life'] -= 0.01
      if s['life'] > 0 and int(s['y']) < self.height:
        yi = int(s['y'])
        # Palette color at a bright position (0.4-0.8 avoids dark edges)
        pal_pos = 0.4 + s['hue'] * 0.4
        c = pal_color_grid(pal_idx, np.array([pal_pos]))[0].astype(np.float32)
        b = s['life'] * max(0.1, brightness)
        # Spark head: blend toward white for hot bright core
        head_color = c * 0.3 + np.array([255, 255, 255], dtype=np.float32) * 0.7
        frame[s['x'] % self.width, yi] = np.clip(head_color * b, 0, 255).astype(np.uint8)
        # Tail: palette color, fading
        for tail in range(1, 4):
          ty = yi - tail
          if 0 <= ty < self.height:
            fade = s['life'] * (1 - tail * 0.25) * max(0.1, brightness)
            if fade > 0:
              frame[s['x'] % self.width, ty] = np.clip(c * fade, 0, 255).astype(np.uint8)
        alive.append(s)
    self._sparks = alive[-200:]
    return frame


class NoiseWash(Effect):
  """Smooth noise-based color wash."""

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)
    speed = self.params.get('speed', 0.5)
    scale = self.params.get('scale', 3.0)
    pal_idx = _get_palette_idx(self.params)

    nx = np.arange(self.width, dtype=np.float64) / self.width * scale
    ny = np.arange(self.height, dtype=np.float64) / self.height * scale
    nxx, nyy = np.meshgrid(nx, ny, indexing='ij')

    v = (np.sin(nxx * 2.1 + elapsed * speed) +
         np.sin(nyy * 1.7 + elapsed * speed * 0.8) +
         np.sin((nxx + nyy) * 1.3 + elapsed * speed * 0.6)) / 3.0
    hue = (v + 1.0) / 2.0

    return pal_color_grid(pal_idx, hue)


class ColorWipe(Effect):
  """Color wipe — sweeps one palette color over another, ping-pong."""

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)
    speed = self.params.get('speed', 0.5)
    pal_idx = _get_palette_idx(self.params)
    frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)

    # Two colors from the palette — current and next
    cycle_pos = (elapsed * speed * 0.1) % 1.0
    color_a_pos = cycle_pos % 1.0
    color_b_pos = (cycle_pos + 0.5) % 1.0
    color_a = pal_color_grid(pal_idx, np.array([color_a_pos]))[0]
    color_b = pal_color_grid(pal_idx, np.array([color_b_pos]))[0]

    # Ping-pong wipe position
    raw_pos = (elapsed * speed * 0.3) % 2.0
    if raw_pos > 1.0:
      wipe_frac = 2.0 - raw_pos  # bouncing back
    else:
      wipe_frac = raw_pos
    wipe_y = int(wipe_frac * self.height)

    # Fill: color_a below wipe, color_b above
    frame[:, :wipe_y] = color_a
    frame[:, wipe_y:] = color_b

    return frame


class Scanline(Effect):
  """Horizontal scanline with gaussian glow, ping-pong bounce."""

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)
    speed = self.params.get('speed', 0.5)
    width_param = self.params.get('width', 8)
    pal_idx = _get_palette_idx(self.params)
    frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)

    # Ping-pong position
    raw = (elapsed * speed * 0.3) % 2.0
    if raw > 1.0:
      pos = (2.0 - raw) * self.height
    else:
      pos = raw * self.height

    # Gaussian brightness around center
    ys = np.arange(self.height, dtype=np.float64)
    dist = np.abs(ys - pos)
    sigma = max(1.0, width_param)
    gaussian = np.exp(-0.5 * (dist / sigma) ** 2)

    # Center is blown-out white, edges get palette color with increasing saturation
    center_color = np.array([255, 255, 255], dtype=np.float64)
    # Palette position varies along the gaussian wings
    pal_pos = (ys / self.height + elapsed * 0.02) % 1.0
    pal_rgb = pal_color_grid(pal_idx, pal_pos)  # (height, 3) uint8

    # Blend: at center (gaussian ~1) -> white; at edges -> palette color
    # Use gaussian^2 for a tighter white core
    white_blend = gaussian ** 2
    result_1d = (
      center_color[np.newaxis, :] * white_blend[:, np.newaxis] +
      pal_rgb.astype(np.float64) * (1.0 - white_blend[:, np.newaxis])
    )
    # Scale by overall gaussian envelope for falloff
    result_1d = result_1d * gaussian[:, np.newaxis]
    result_1d = np.clip(result_1d, 0, 255).astype(np.uint8)

    # Broadcast across width
    frame[:, :, :] = result_1d[np.newaxis, :, :]
    return frame


class Fire(Effect):
  """Fire-like effect rising from the bottom. Fully vectorized."""

  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self._heat = np.zeros((self.width, self.height), dtype=np.float64)
    self._rng = np.random.default_rng(42)
    self._prev_frame = None

  def render(self, t: float, state) -> np.ndarray:
    cooling = self.params.get('cooling', 55)
    sparking = self.params.get('sparking', 120)
    pal_idx = _get_palette_idx(self.params, default=4)  # default Lava

    # Cool down
    cool_amount = self._rng.integers(
      0, max(1, (cooling * 10) // self.height + 2),
      size=(self.width, self.height)
    ) / 255.0
    self._heat = np.maximum(0, self._heat - cool_amount)

    # Heat rises: shift upward with averaging
    shifted = np.zeros_like(self._heat)
    shifted[:, 3:] = (
      self._heat[:, 2:-1] +
      self._heat[:, 1:-2] +
      self._heat[:, 1:-2]
    ) / 3.0
    shifted[:, :3] = self._heat[:, :3]
    self._heat = shifted

    # Sparks at bottom
    spark_mask = self._rng.integers(0, 255, size=self.width) < sparking
    for x in np.where(spark_mask)[0]:
      y = self._rng.integers(0, min(7, self.height))
      self._heat[x, y] = min(1.0, self._heat[x, y] + 0.4 + self._rng.random() * 0.4)

    # Palette-based color, with heat as brightness envelope
    rgb = pal_color_grid(pal_idx, self._heat)
    frame = (rgb.astype(np.float32) * self._heat[..., np.newaxis]).astype(np.uint8)

    # Flip vertically — heat sim uses y=0 as hot base, but physical pillar
    # needs hot pixels at high y (bottom of display)
    frame = frame[:, ::-1, :]

    # Temporal smoothing for relaxed flicker
    if self._prev_frame is None:
      self._prev_frame = frame.copy()
    smoothed = (frame.astype(np.float32) * 0.4 + self._prev_frame.astype(np.float32) * 0.6).astype(np.uint8)
    self._prev_frame = smoothed.copy()
    return smoothed


class SineBands(Effect):
  """Sine-wave color bands."""

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)
    freq = self.params.get('frequency', 3.0)
    speed = self.params.get('speed', 1.0)
    pal_idx = _get_palette_idx(self.params)

    ys = np.arange(self.height, dtype=np.float64)
    hue_1d = (np.sin(ys / self.height * freq * math.pi * 2 + elapsed * speed) + 1.0) / 2.0

    # Broadcast to (width, height)
    hue_2d = np.broadcast_to(hue_1d[np.newaxis, :], (self.width, self.height))

    # Palette lookup gives us the color; modulate brightness with the sine
    rgb = pal_color_grid(pal_idx, hue_2d)

    # Apply brightness modulation — brighter at sine peaks
    brightness = np.broadcast_to(hue_1d[np.newaxis, :], (self.width, self.height))
    return (rgb.astype(np.float32) * brightness[..., np.newaxis]).astype(np.uint8)


class CylinderRotate(Effect):
  """Color pattern that rotates around the cylinder."""

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)
    speed = self.params.get('speed', 1.0)
    pal_idx = _get_palette_idx(self.params)

    xs = np.arange(self.width, dtype=np.float64) / self.width
    ys = np.arange(self.height, dtype=np.float64)
    xx, yy = np.meshgrid(xs, ys, indexing='ij')

    hue = (xx + elapsed * speed * 0.1) % 1.0
    brightness = (np.sin(yy / self.height * math.pi * 4 + elapsed) + 1.0) / 2.0

    rgb = pal_color_grid(pal_idx, hue)
    return (rgb.astype(np.float32) * brightness[..., np.newaxis]).astype(np.uint8)


class SeamPulse(Effect):
  """Pulse that highlights the seam between S9 and S0."""

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)
    frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)

    pulse = (math.sin(elapsed * 3.0) + 1.0) / 2.0
    v = int(pulse * 255)

    # Light up S0 and S9
    frame[0, :] = (v, 0, 0)
    frame[self.width - 1, :] = (0, 0, v)

    return frame


class DiagnosticLabels(Effect):
  """Shows strip numbers as distinct colors for identification."""

  STRIP_COLORS = [
    (255, 0, 0),    # S0: red
    (0, 255, 0),    # S1: green
    (0, 0, 255),    # S2: blue
    (255, 255, 0),  # S3: yellow
    (255, 0, 255),  # S4: magenta
    (0, 255, 255),  # S5: cyan
    (255, 128, 0),  # S6: orange
    (128, 0, 255),  # S7: purple
    (0, 255, 128),  # S8: spring green
    (255, 255, 255),# S9: white
  ]

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)
    frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)

    for x in range(min(self.width, 10)):
      color = self.STRIP_COLORS[x % 10]
      # Show bottom quarter solid, rest dim
      quarter = self.height // 4
      frame[x, :quarter] = color
      frame[x, quarter:] = tuple(c // 8 for c in color)

      # Animated marker at strip number position
      marker_y = int((elapsed * 20) % self.height)
      if 0 <= marker_y < self.height:
        frame[x, marker_y] = (255, 255, 255)

    return frame


# Effect registry
class FramedFire(Effect):
  """Smooth fire contained within a glowing plasma border frame."""

  PALETTE_SUPPORT = True

  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    w, h = self.width, self.height
    self._border = 2
    # Fire sim runs in the interior only
    iw = max(1, w - self._border * 2)
    ih = max(1, h - self._border)  # no border at bottom (fire rises from there)
    self._heat = np.zeros((iw, ih), dtype=np.float64)
    self._rng = np.random.default_rng()
    self._prev_frame = None
    # Precompute border mask and coordinate grids for plasma
    self._border_mask = np.zeros((w, h), dtype=bool)
    self._border_mask[:self._border, :] = True   # left edge
    self._border_mask[-self._border:, :] = True  # right edge
    self._border_mask[:, :self._border] = True   # top edge
    # Coordinate grids for plasma border
    xs = np.arange(w, dtype=np.float32)
    ys = np.arange(h, dtype=np.float32)
    self._gx, self._gy = np.meshgrid(xs, ys, indexing='ij')
    # Distance from nearest edge (for border glow falloff)
    dx_edge = np.minimum(xs, w - 1 - xs)
    dy_edge = np.minimum(ys, h - 1 - ys)
    self._edge_dist = np.minimum(
      dx_edge[:, np.newaxis] * np.ones(h)[np.newaxis, :],
      np.ones(w)[:, np.newaxis] * dy_edge[np.newaxis, :]
    )

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)
    pal_idx = _get_palette_idx(self.params, default=4)
    w, h = self.width, self.height
    b = self._border
    iw = max(1, w - b * 2)
    ih = max(1, h - b)

    # === INTERIOR FIRE (smooth) ===
    cooling = self.params.get('cooling', 45)
    sparking = self.params.get('sparking', 140)

    # Cool down
    cool_amount = self._rng.integers(
      0, max(1, (cooling * 10) // ih + 2),
      size=(iw, ih)
    ) / 255.0
    self._heat = np.maximum(0, self._heat - cool_amount)

    # Heat rises with smooth averaging
    shifted = np.zeros_like(self._heat)
    if ih > 3:
      shifted[:, 3:] = (
        self._heat[:, 2:-1] +
        self._heat[:, 1:-2] +
        self._heat[:, 1:-2] +
        self._heat[:, :-3]
      ) / 4.0
      shifted[:, :3] = self._heat[:, :3]
    else:
      shifted = self._heat.copy()
    self._heat = shifted

    # Sparks at bottom (more generous)
    spark_mask = self._rng.integers(0, 255, size=iw) < sparking
    for x in np.where(spark_mask)[0]:
      y = self._rng.integers(0, min(5, ih))
      self._heat[x, y] = min(1.0, self._heat[x, y] + 0.5 + self._rng.random() * 0.3)

    # Color the fire
    fire_rgb = pal_color_grid(pal_idx, self._heat)
    fire_frame = (fire_rgb.astype(np.float32) * self._heat[..., np.newaxis]).astype(np.uint8)
    fire_frame = fire_frame[:, ::-1, :]  # flip so fire rises from bottom

    # === PLASMA BORDER ===
    tt = elapsed * 0.6
    cx = self._gx / max(w, 1) * 8.0
    cy = self._gy / max(h, 1) * 8.0
    v1 = np.sin(cx + tt * 1.2)
    v2 = np.sin(cy * 1.5 + tt * 0.8)
    v3 = np.sin((cx + cy) * 0.7 + tt * 1.5)
    v4 = np.sin(np.sqrt(cx * cx + cy * cy) * 1.5 + tt)
    plasma = (v1 + v2 + v3 + v4) / 4.0

    border_hue = ((plasma + 1.0) * 0.5 + elapsed * 0.05) % 1.0
    border_sat = np.clip(0.8 + plasma * 0.2, 0.6, 1.0)
    border_val = np.clip(0.9 + plasma * 0.1, 0.7, 1.0)

    # Glow falloff — brighter at very edge, fades inward
    glow = np.clip(1.0 - self._edge_dist / (b + 1.5), 0, 1) ** 0.8
    border_val = border_val * glow

    # HSV to RGB for border
    bh = (border_hue * 6.0).astype(np.float32)
    bi = bh.astype(np.int32) % 6
    bf = bh - np.floor(bh)
    bv = border_val.astype(np.float32)
    bs = border_sat.astype(np.float32)
    bp = bv * (1.0 - bs)
    bq = bv * (1.0 - bs * bf)
    bt = bv * (1.0 - bs * (1.0 - bf))

    br = np.where(bi == 0, bv, np.where(bi == 1, bq, np.where(bi == 2, bp,
         np.where(bi == 3, bp, np.where(bi == 4, bt, bv)))))
    bg = np.where(bi == 0, bt, np.where(bi == 1, bv, np.where(bi == 2, bv,
         np.where(bi == 3, bq, np.where(bi == 4, bp, bp)))))
    bb = np.where(bi == 0, bp, np.where(bi == 1, bp, np.where(bi == 2, bt,
         np.where(bi == 3, bv, np.where(bi == 4, bv, bq)))))

    border_frame = np.zeros((w, h, 3), dtype=np.uint8)
    border_frame[:, :, 0] = np.clip(br * 255, 0, 255).astype(np.uint8)
    border_frame[:, :, 1] = np.clip(bg * 255, 0, 255).astype(np.uint8)
    border_frame[:, :, 2] = np.clip(bb * 255, 0, 255).astype(np.uint8)

    # === COMPOSITE: border + fire interior ===
    frame = border_frame.copy()

    # Place fire in the interior
    if iw > 0 and ih > 0:
      frame[b:b + iw, b:b + ih] = fire_frame[:iw, :ih]

    # Heavy temporal smoothing for ultra-smooth fire
    if self._prev_frame is not None:
      frame = (frame.astype(np.float32) * 0.35 + self._prev_frame.astype(np.float32) * 0.65).astype(np.uint8)
    self._prev_frame = frame.copy()

    return frame


class TwinTorches(Effect):
  """Two medieval dungeon torches — wrapped rag heads engulfed in fire."""

  PALETTE_SUPPORT = True

  _SPARK_DTYPE = np.dtype([
    ('x', np.float32), ('y', np.float32),
    ('vx', np.float32), ('vy', np.float32),
    ('life', np.float32), ('brightness', np.float32),
  ])
  _MAX_SPARKS = 60

  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    w, h = self.width, self.height
    self._rng = np.random.default_rng()
    self._prev_frame = None
    self._sparks = np.empty(0, dtype=self._SPARK_DTYPE)
    self._t = 0.0
    self._last_t = None
    # Grids
    self._gx = np.arange(w, dtype=np.float32)[:, np.newaxis] * np.ones(h)[np.newaxis, :]
    self._gy = np.ones(w)[:, np.newaxis] * np.arange(h, dtype=np.float32)[np.newaxis, :]
    # Two torches
    self._torch_x = [w * 0.25, w * 0.75]
    # Anatomy of a dungeon torch:
    #   [top 10%]  — flames licking upward above head
    #   [10%-30%]  — TORCH HEAD: wrapped rags, fully engulfed in fire
    #   [30%-50%]  — flames dripping/licking below head
    #   [50%-100%] — wooden stick
    self._head_top = int(h * 0.10)      # top of wrapped head
    self._head_bot = int(h * 0.30)      # bottom of wrapped head
    self._fire_top = int(h * 0.05)      # flames extend above head (10% margin from display top)
    self._fire_bot = int(h * 0.40)      # flames drip below head
    self._stick_top = int(h * 0.30)     # stick starts at bottom of head
    # Head width (wider than stick — wrapped rags)
    self._head_w = max(2, w // 4)

  def render(self, t: float, state) -> np.ndarray:
    if self._last_t is None:
      self._last_t = t
    dt = min(t - self._last_t, 0.05)
    self._last_t = t
    self._t += dt

    w, h = self.width, self.height
    pal_idx = _get_palette_idx(self.params, default=4)
    sparking = self.params.get('sparking', 140) if 'sparking' in self.params else 140
    tt = self._t
    frame = np.zeros((w, h, 3), dtype=np.float32)

    for ti, tx in enumerate(self._torch_x):
      ix = int(tx)

      # === STICK (dim purple, bottom 50%) ===
      for dx in range(-1, 2):
        sx = ix + dx
        if 0 <= sx < w:
          c = np.array([50, 14, 65] if dx == 0 else [35, 8, 45], dtype=np.float32)
          frame[sx, self._stick_top:] = c

      # === TORCH HEAD (wrapped rags — dark reddish-brown, visible through fire) ===
      hw = self._head_w
      for dx in range(-hw, hw + 1):
        sx = ix + dx
        if 0 <= sx < w:
          # Wrapped texture — alternating dark bands
          for row in range(self._head_top, self._head_bot):
            band = ((row + dx) % 3 == 0)
            c = [60, 25, 10] if band else [45, 18, 8]  # dark brown rags
            frame[sx, row] = c

      # === FIRE ENGULFING THE HEAD ===
      # Fire zone: from fire_top to fire_bot, centered on head
      ft = self._fire_top
      fb = self._fire_bot
      fh = fb - ft
      if fh <= 0:
        continue

      fx = self._gx[:, ft:fb]
      fy = self._gy[:, ft:fb]

      # Normalized position within fire zone
      dx_norm = (fx - tx) / max(w * 0.15, 1)
      fy_local = (fy - ft) / max(fh, 1)  # 0=top, 1=bottom

      # Head center is at ~35% of fire zone height
      head_center_norm = (self._head_top + self._head_bot) / 2.0
      head_center_in_fire = (head_center_norm - ft) / max(fh, 1)

      # Fire shape: ENGULFS the head — widest at head center, tapers above and below
      # Distance from head center (vertical)
      dist_from_head = np.abs(fy_local - head_center_in_fire)

      # Multi-layer noise for organic swirly fire
      phase = ti * 77
      n1 = np.sin(dx_norm * 5.0 + tt * 4.0 + phase) * 0.25
      n2 = np.sin(fy_local * 6.0 - tt * 3.0 + phase + 40) * 0.2
      n3 = np.cos(dx_norm * 3.0 + fy_local * 4.0 + tt * 2.5 + phase) * 0.15
      swirl = n1 + n2 + n3

      # Flame envelope — widest around head, tapers above and below
      # At head: wide (hw pixels), above head: narrows to a licking tip, below: narrows quickly
      above_head = fy_local < head_center_in_fire
      width_above = 0.6 * (1.0 - (head_center_in_fire - fy_local) / max(head_center_in_fire, 0.01)) ** 0.7
      width_below = 0.5 * (1.0 - (fy_local - head_center_in_fire) / max(1.0 - head_center_in_fire, 0.01)) ** 1.5
      flame_width = np.where(above_head, width_above, width_below)
      flame_width = np.clip(flame_width * (1.0 + swirl * 0.4), 0.05, 1.0)

      # Wobble — draft sway
      wobble = np.sin(tt * 1.5 + ti * 2.3) * 0.08 + np.sin(tt * 2.8 + ti * 1.1) * 0.04
      dist_from_center = np.abs(dx_norm - wobble + swirl * 0.06)
      envelope = np.clip(1.0 - dist_from_center / np.maximum(flame_width, 0.01), 0, 1) ** 0.8

      # Vertical intensity — brightest at head, fades at tips
      vert_intensity = np.clip(1.0 - dist_from_head * 2.5, 0.1, 1.0)
      intensity = envelope * vert_intensity

      # Color — palette driven, intensity maps to palette position
      hue = np.clip(intensity * 0.85, 0, 0.95)
      fire_rgb = pal_color_grid(pal_idx, hue.astype(np.float32))
      contrib = fire_rgb.astype(np.float32) * intensity[:, :, np.newaxis]

      # Additive blend fire over the head
      frame[:, ft:fb] = np.maximum(frame[:, ft:fb], contrib)

    # === SPARKS ===
    for tx in self._torch_x:
      if self._rng.random() < sparking / 255.0 * 1.5:
        count = 1 + int(self._rng.random() > 0.7)
        new = np.empty(count, dtype=self._SPARK_DTYPE)
        new['x'] = tx + self._rng.uniform(-2, 2, count).astype(np.float32)
        new['y'] = self._rng.uniform(self._fire_top, self._head_top, count).astype(np.float32)
        new['vx'] = self._rng.uniform(-2.5, 2.5, count).astype(np.float32)
        new['vy'] = self._rng.uniform(-10, -3, count).astype(np.float32)
        new['life'] = self._rng.uniform(0.2, 0.7, count).astype(np.float32)
        new['brightness'] = self._rng.uniform(0.6, 1.0, count).astype(np.float32)
        if len(self._sparks) < self._MAX_SPARKS:
          self._sparks = np.concatenate([self._sparks, new]) if len(self._sparks) > 0 else new

    if len(self._sparks) > 0:
      s = self._sparks
      s['x'] += s['vx'] * dt
      s['y'] += s['vy'] * dt
      s['vy'] += 20 * dt
      s['life'] -= dt
      alive = (s['life'] > 0) & (s['y'] >= 0) & (s['y'] < h)
      self._sparks = s[alive]

    if len(self._sparks) > 0:
      s = self._sparks
      ix = np.clip(np.round(s['x']).astype(np.int32), 0, w - 1)
      iy = np.clip(np.round(s['y']).astype(np.int32), 0, h - 1)
      fade = (s['life'] / 0.7) ** 0.5 * s['brightness']
      np.add.at(frame[:, :, 0], (ix, iy), fade * 255)
      np.add.at(frame[:, :, 1], (ix, iy), fade * 130)
      np.add.at(frame[:, :, 2], (ix, iy), fade * 15)

    result = np.clip(frame, 0, 255).astype(np.uint8)

    # Smooth flicker — 55% blend with previous
    if self._prev_frame is not None:
      result = (result.astype(np.float32) * 0.45 + self._prev_frame.astype(np.float32) * 0.55).astype(np.uint8)
    self._prev_frame = result.copy()

    return result


EFFECTS = {
  'solid_color': SolidColor,
  'vertical_gradient': VerticalGradient,
  'rainbow_rotate': RainbowRotate,
  'plasma': Plasma,
  'twinkle': Twinkle,
  'spark': Spark,
  'noise_wash': NoiseWash,
  'color_wipe': ColorWipe,
  'scanline': Scanline,
  'fire': Fire,
  'framed_fire': FramedFire,
  'twin_torches': TwinTorches,
}
