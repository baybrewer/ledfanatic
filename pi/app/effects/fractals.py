"""
Fractal and mathematical visualization effects — fully vectorized with NumPy.

Five mesmerizing fractal effects:
- Mandelbrot zoom into spiral boundary
- Julia set with orbiting c parameter
- Burning Ship fractal with angular drama
- Fractal Flame (Electric Sheep-inspired IFS)
- Sierpinski flow with accumulating points
"""

import warnings
import numpy as np
from .base import Effect
from .engine.palettes import pal_color_grid, NUM_PALETTES, PALETTE_NAMES

# Fractal iterations intentionally overflow for escaped points — suppress noise
warnings.filterwarnings('ignore', category=RuntimeWarning, module=__name__)


# ─── Helpers ──────────────────────────────────────────────────────


class _P:
  def __init__(self, label, attr, lo, hi, step, default):
    self.label, self.attr, self.lo, self.hi = label, attr, lo, hi
    self.step, self.default = step, default


def _get_palette_idx(params: dict, default: int = 0) -> int:
  """Get palette index from params, handling string names or int values."""
  val = params.get('palette', default)
  if isinstance(val, str):
    try:
      return PALETTE_NAMES.index(val) % NUM_PALETTES
    except ValueError:
      return default % NUM_PALETTES
  return int(val) % NUM_PALETTES


# ──────────────────────────────────────────────────────────────────────
#  Mandelbrot Zoom
# ──────────────────────────────────────────────────────────────────────

class MandelbrotZoom(Effect):
  """Continuously zooming into fractal boundary."""

  CATEGORY = "simulation"
  DISPLAY_NAME = "Mandelbrot Zoom"
  DESCRIPTION = "Continuously zooming into fractal boundary"
  PALETTE_SUPPORT = True

  PARAMS = [
    _P("Speed", "speed", 0.1, 2.0, 0.05, 0.3),
    _P("Max Iter", "max_iter", 20, 200, 10, 80),
    _P("Color Cycle", "color_speed", 0.0, 1.0, 0.05, 0.2),
  ]

  # Deep spiral at the seahorse valley — visually rich at all zoom levels
  _CENTER_RE = -0.7435669
  _CENTER_IM = 0.1314023

  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    # Precompute normalized coordinate grids (0..1 range, centered)
    xs = np.linspace(-1.0, 1.0, width, dtype=np.float64)
    ys = np.linspace(-1.0, 1.0, height, dtype=np.float64)
    self._gx, self._gy = np.meshgrid(xs, ys, indexing='ij')
    # Aspect ratio correction
    aspect = width / max(height, 1)
    self._gx = self._gx * aspect

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)
    speed = self.params.get('speed', 0.3)
    max_iter = int(self.params.get('max_iter', 80))
    color_speed = self.params.get('color_speed', 0.2)
    pal_idx = _get_palette_idx(self.params)

    # Zoom level — exponential zoom into the center point
    zoom = 2.0 * np.exp(-speed * elapsed * 0.5)
    # Prevent underflow at extreme zoom
    zoom = max(zoom, 1e-15)

    # Map pixel coords to complex plane
    c_re = self._CENTER_RE + self._gx * zoom
    c_im = self._CENTER_IM + self._gy * zoom

    # Vectorized escape-time iteration
    z_re = np.zeros_like(c_re)
    z_im = np.zeros_like(c_im)
    iterations = np.zeros((self.width, self.height), dtype=np.float64)
    escaped = np.zeros((self.width, self.height), dtype=bool)

    for i in range(max_iter):
      # z = z^2 + c
      z_re_new = z_re * z_re - z_im * z_im + c_re
      z_im_new = 2.0 * z_re * z_im + c_im
      z_re = z_re_new
      z_im = z_im_new

      mag_sq = z_re * z_re + z_im * z_im
      newly_escaped = (mag_sq > 4.0) & ~escaped
      # Smooth iteration count for anti-banding
      iterations[newly_escaped] = i + 1 - np.log2(np.log2(np.maximum(mag_sq[newly_escaped], 1.0)))
      escaped |= newly_escaped

      # Early exit if all pixels escaped
      if escaped.all():
        break

    # Normalize iteration count to 0-1 for palette lookup
    mask = iterations > 0
    t_color = np.zeros_like(iterations)
    if mask.any():
      t_color[mask] = iterations[mask] / max_iter
    # Add color cycling
    t_color = (t_color + elapsed * color_speed) % 1.0
    # Interior points (never escaped) get black
    t_color[~escaped] = -1.0

    frame = pal_color_grid(pal_idx, t_color.astype(np.float32))
    # Black out interior
    frame[~escaped] = 0
    return frame


# ──────────────────────────────────────────────────────────────────────
#  Julia Explorer
# ──────────────────────────────────────────────────────────────────────

class JuliaExplorer(Effect):
  """Morphing Julia set — c parameter orbits slowly."""

  CATEGORY = "simulation"
  DISPLAY_NAME = "Julia Explorer"
  DESCRIPTION = "Morphing Julia set — c parameter orbits slowly"
  PALETTE_SUPPORT = True

  PARAMS = [
    _P("Speed", "speed", 0.1, 2.0, 0.05, 0.4),
    _P("Max Iter", "max_iter", 20, 150, 10, 60),
    _P("Zoom", "zoom", 0.5, 4.0, 0.1, 1.5),
  ]

  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    # Precompute coordinate grids
    xs = np.linspace(-1.0, 1.0, width, dtype=np.float64)
    ys = np.linspace(-1.0, 1.0, height, dtype=np.float64)
    self._gx, self._gy = np.meshgrid(xs, ys, indexing='ij')
    aspect = width / max(height, 1)
    self._gx = self._gx * aspect

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)
    speed = self.params.get('speed', 0.4)
    max_iter = int(self.params.get('max_iter', 60))
    zoom = self.params.get('zoom', 1.5)
    pal_idx = _get_palette_idx(self.params)

    # c parameter orbits on an interesting path in the complex plane
    # This traces through many beautiful Julia sets
    angle = elapsed * speed * 0.3
    c_re = 0.7885 * np.cos(angle)
    c_im = 0.7885 * np.sin(angle)

    # Map pixels to complex plane
    z_re = self._gx * zoom
    z_im = self._gy * zoom

    iterations = np.zeros((self.width, self.height), dtype=np.float64)
    escaped = np.zeros((self.width, self.height), dtype=bool)

    for i in range(max_iter):
      z_re_new = z_re * z_re - z_im * z_im + c_re
      z_im_new = 2.0 * z_re * z_im + c_im
      z_re = z_re_new
      z_im = z_im_new

      mag_sq = z_re * z_re + z_im * z_im
      newly_escaped = (mag_sq > 4.0) & ~escaped
      iterations[newly_escaped] = i + 1 - np.log2(np.log2(np.maximum(mag_sq[newly_escaped], 1.0)))
      escaped |= newly_escaped

      if escaped.all():
        break

    # Color mapping
    t_color = np.zeros_like(iterations, dtype=np.float32)
    mask = iterations > 0
    if mask.any():
      t_color[mask] = (iterations[mask] / max_iter).astype(np.float32)
    t_color = (t_color + np.float32(elapsed * speed * 0.1)) % 1.0

    frame = pal_color_grid(pal_idx, t_color)
    frame[~escaped] = 0
    return frame


# ──────────────────────────────────────────────────────────────────────
#  Burning Ship
# ──────────────────────────────────────────────────────────────────────

class BurningShip(Effect):
  """Angular fractal with dramatic ship-like structures."""

  CATEGORY = "simulation"
  DISPLAY_NAME = "Burning Ship"
  DESCRIPTION = "Angular fractal with dramatic ship-like structures"
  PALETTE_SUPPORT = True

  PARAMS = [
    _P("Speed", "speed", 0.1, 2.0, 0.05, 0.3),
    _P("Max Iter", "max_iter", 20, 200, 10, 80),
  ]

  # The main ship is centered around (-1.76, -0.028)
  _CENTER_RE = -1.756
  _CENTER_IM = -0.028

  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    xs = np.linspace(-1.0, 1.0, width, dtype=np.float64)
    ys = np.linspace(-1.0, 1.0, height, dtype=np.float64)
    self._gx, self._gy = np.meshgrid(xs, ys, indexing='ij')
    aspect = width / max(height, 1)
    self._gx = self._gx * aspect

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)
    speed = self.params.get('speed', 0.3)
    max_iter = int(self.params.get('max_iter', 80))
    pal_idx = _get_palette_idx(self.params)

    # Slow pan and zoom — oscillate around the interesting region
    pan_re = self._CENTER_RE + 0.3 * np.sin(elapsed * speed * 0.15)
    pan_im = self._CENTER_IM + 0.2 * np.cos(elapsed * speed * 0.12)
    zoom = 2.0 * np.exp(-speed * elapsed * 0.3)
    zoom = max(zoom, 1e-15)

    c_re = pan_re + self._gx * zoom
    c_im = pan_im + self._gy * zoom

    # Burning Ship iteration: z = (|Re(z)| + i|Im(z)|)^2 + c
    z_re = np.zeros_like(c_re)
    z_im = np.zeros_like(c_im)
    iterations = np.zeros((self.width, self.height), dtype=np.float64)
    escaped = np.zeros((self.width, self.height), dtype=bool)

    for i in range(max_iter):
      # Take absolute values before squaring
      z_re_abs = np.abs(z_re)
      z_im_abs = np.abs(z_im)
      z_re_new = z_re_abs * z_re_abs - z_im_abs * z_im_abs + c_re
      z_im_new = 2.0 * z_re_abs * z_im_abs + c_im
      z_re = z_re_new
      z_im = z_im_new

      mag_sq = z_re * z_re + z_im * z_im
      newly_escaped = (mag_sq > 4.0) & ~escaped
      iterations[newly_escaped] = i + 1 - np.log2(np.log2(np.maximum(mag_sq[newly_escaped], 1.0)))
      escaped |= newly_escaped

      if escaped.all():
        break

    # Color mapping — use lava palette by default for ship-like drama
    t_color = np.zeros_like(iterations, dtype=np.float32)
    mask = iterations > 0
    if mask.any():
      t_color[mask] = (iterations[mask] / max_iter).astype(np.float32)
    t_color = (t_color + np.float32(elapsed * 0.15)) % 1.0

    frame = pal_color_grid(pal_idx, t_color)
    frame[~escaped] = 0
    return frame


# ──────────────────────────────────────────────────────────────────────
#  Fractal Flame
# ──────────────────────────────────────────────────────────────────────

class FractalFlame(Effect):
  """Electric Sheep-inspired evolving fractal flame."""

  CATEGORY = "simulation"
  DISPLAY_NAME = "Fractal Flame"
  DESCRIPTION = "Electric Sheep-inspired evolving fractal flame"
  PALETTE_SUPPORT = True

  PARAMS = [
    _P("Speed", "speed", 0.1, 2.0, 0.05, 0.5),
    _P("Density", "density", 100, 5000, 100, 1000),
    _P("Brightness", "brightness", 0.5, 3.0, 0.1, 1.5),
  ]

  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    self._histogram = np.zeros((width, height), dtype=np.float32)
    self._color_hist = np.zeros((width, height), dtype=np.float32)
    # Initialize random point cloud for IFS iteration
    self._px = np.random.uniform(-1, 1, size=2000).astype(np.float32)
    self._py = np.random.uniform(-1, 1, size=2000).astype(np.float32)
    self._pc = np.random.uniform(0, 1, size=2000).astype(np.float32)

  def _make_transforms(self, elapsed, speed):
    """Generate 4 slowly-evolving affine transforms."""
    t = elapsed * speed * 0.2
    transforms = []
    for k in range(4):
      phase = t + k * 1.5708  # pi/2 spacing
      # Affine coefficients that evolve over time
      a = 0.5 * np.cos(phase * 0.7 + k)
      b = -0.5 * np.sin(phase * 0.5 + k * 0.3)
      c = 0.5 * np.sin(phase * 0.3 + k * 0.7)
      d = 0.5 * np.cos(phase * 0.6 + k * 0.5)
      e = 0.3 * np.sin(phase * 0.4 + k * 1.1)
      f = 0.3 * np.cos(phase * 0.35 + k * 0.9)
      transforms.append((a, b, c, d, e, f, k / 4.0))
    return transforms

  def _apply_variation(self, x, y, var_type, elapsed):
    """Apply nonlinear variation to points."""
    r_sq = x * x + y * y
    r = np.sqrt(r_sq + 1e-10)

    if var_type == 0:
      # Sinusoidal
      return np.sin(x), np.sin(y)
    elif var_type == 1:
      # Spherical
      inv_r = 1.0 / (r_sq + 1e-10)
      return x * inv_r, y * inv_r
    elif var_type == 2:
      # Swirl
      s, c = np.sin(r_sq), np.cos(r_sq)
      return x * s - y * c, x * c + y * s
    else:
      # Horseshoe
      inv_r = 1.0 / (r + 1e-10)
      return inv_r * (x - y) * (x + y), inv_r * 2.0 * x * y

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)
    speed = self.params.get('speed', 0.5)
    density = int(self.params.get('density', 1000))
    brightness = self.params.get('brightness', 1.5)
    pal_idx = _get_palette_idx(self.params)

    # Fade histogram for trail effect
    self._histogram *= 0.85
    self._color_hist *= 0.85

    transforms = self._make_transforms(elapsed, speed)
    num_points = len(self._px)

    # Run IFS iterations — accumulate into histogram
    for _ in range(max(1, density // num_points)):
      # Choose random transforms for each point
      choice = np.random.randint(0, len(transforms), size=num_points)

      new_x = np.zeros_like(self._px)
      new_y = np.zeros_like(self._py)
      new_c = np.zeros_like(self._pc)

      for idx, (a, b, c, d, e, f, color_val) in enumerate(transforms):
        mask = choice == idx
        if not mask.any():
          continue
        # Affine transform
        ax = a * self._px[mask] + b * self._py[mask] + e
        ay = c * self._px[mask] + d * self._py[mask] + f
        # Apply nonlinear variation (cycle through types)
        var_type = (idx + int(elapsed * speed * 0.5)) % 4
        vx, vy = self._apply_variation(ax, ay, var_type, elapsed)
        new_x[mask] = vx
        new_y[mask] = vy
        # Blend color index
        new_c[mask] = (self._pc[mask] + color_val) * 0.5

      self._px = new_x
      self._py = new_y
      self._pc = new_c

      # Map points to pixel coordinates
      px_screen = ((self._px + 2.0) / 4.0 * self.width).astype(np.int32)
      py_screen = ((self._py + 2.0) / 4.0 * self.height).astype(np.int32)

      # Clamp to valid indices
      valid = (
        (px_screen >= 0) & (px_screen < self.width) &
        (py_screen >= 0) & (py_screen < self.height)
      )

      if valid.any():
        vx = px_screen[valid]
        vy = py_screen[valid]
        vc = self._pc[valid]
        # Accumulate — use np.add.at for scatter-add
        np.add.at(self._histogram, (vx, vy), 1.0)
        np.add.at(self._color_hist, (vx, vy), vc)

    # Log-scale density for better dynamic range
    log_hist = np.log1p(self._histogram)
    max_val = log_hist.max()
    if max_val > 0:
      log_hist /= max_val

    # Color by average color index at each pixel
    safe_hist = np.maximum(self._histogram, 1e-10)
    avg_color = self._color_hist / safe_hist
    avg_color = np.clip(avg_color, 0, 1).astype(np.float32)

    # Combine density and color for palette lookup
    t_color = (avg_color + np.float32(elapsed * speed * 0.1)) % 1.0

    frame = pal_color_grid(pal_idx, t_color)
    # Scale brightness by log density
    scale = np.clip(log_hist * brightness, 0, 1).astype(np.float32)
    frame = (frame.astype(np.float32) * scale[:, :, np.newaxis]).astype(np.uint8)
    return frame


# ──────────────────────────────────────────────────────────────────────
#  Sierpinski Flow
# ──────────────────────────────────────────────────────────────────────

class SierpinskiFlow(Effect):
  """Flowing points accumulating into fractal geometry."""

  CATEGORY = "simulation"
  DISPLAY_NAME = "Sierpinski Flow"
  DESCRIPTION = "Flowing points accumulating into fractal geometry"
  PALETTE_SUPPORT = True

  PARAMS = [
    _P("Speed", "speed", 0.1, 2.0, 0.05, 0.5),
    _P("Points", "points", 500, 10000, 500, 3000),
    _P("Trail", "trail", 0.8, 0.99, 0.01, 0.95),
  ]

  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    self._canvas = np.zeros((width, height), dtype=np.float32)
    self._color_canvas = np.zeros((width, height), dtype=np.float32)
    # Chaos game state — points in [0,1] x [0,1]
    num_pts = int(self.params.get('points', 3000))
    self._px = np.random.uniform(0, 1, size=num_pts).astype(np.float32)
    self._py = np.random.uniform(0, 1, size=num_pts).astype(np.float32)
    # Vertices of the Sierpinski triangle + carpet attractors
    self._vertices = np.array([
      [0.5, 0.0],   # top center
      [0.0, 1.0],   # bottom left
      [1.0, 1.0],   # bottom right
    ], dtype=np.float32)

  def update_params(self, params):
    """Handle point count changes."""
    old_pts = int(self.params.get('points', 3000))
    super().update_params(params)
    new_pts = int(self.params.get('points', 3000))
    if new_pts != old_pts:
      self._px = np.random.uniform(0, 1, size=new_pts).astype(np.float32)
      self._py = np.random.uniform(0, 1, size=new_pts).astype(np.float32)

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)
    speed = self.params.get('speed', 0.5)
    trail = self.params.get('trail', 0.95)
    pal_idx = _get_palette_idx(self.params)

    # Fade canvas
    self._canvas *= trail
    self._color_canvas *= trail

    # Slowly rotate/morph the attractor vertices
    angle = elapsed * speed * 0.3
    # Base triangle with slow breathing/rotation
    cos_a = np.cos(angle * 0.2).astype(np.float32)
    sin_a = np.sin(angle * 0.2).astype(np.float32)
    cx, cy = np.float32(0.5), np.float32(0.5)

    verts = self._vertices.copy()
    # Gentle rotation around center
    dx = verts[:, 0] - cx
    dy = verts[:, 1] - cy
    verts[:, 0] = cx + dx * cos_a - dy * sin_a
    verts[:, 1] = cy + dx * sin_a + dy * cos_a

    # Occasionally add a 4th attractor for carpet-like patterns
    num_verts = 3
    if np.sin(elapsed * speed * 0.15) > 0.3:
      extra = np.array([[
        0.5 + 0.3 * np.sin(elapsed * speed * 0.4),
        0.5 + 0.3 * np.cos(elapsed * speed * 0.35),
      ]], dtype=np.float32)
      verts = np.vstack([verts, extra])
      num_verts = 4

    # Chaos game — jump halfway to a random vertex (Sierpinski rule)
    num_pts = len(self._px)
    # Multiple iterations per frame for density
    for _ in range(3):
      choices = np.random.randint(0, num_verts, size=num_pts)
      target_x = verts[choices, 0]
      target_y = verts[choices, 1]

      # The contraction ratio creates the fractal
      ratio = np.float32(0.5)
      self._px = self._px + (target_x - self._px) * ratio
      self._py = self._py + (target_y - self._py) * ratio

      # Map to pixel coordinates
      ix = (self._px * (self.width - 1)).astype(np.int32)
      iy = (self._py * (self.height - 1)).astype(np.int32)
      ix = np.clip(ix, 0, self.width - 1)
      iy = np.clip(iy, 0, self.height - 1)

      # Color based on which vertex was chosen
      color_t = choices.astype(np.float32) / max(num_verts - 1, 1)

      np.add.at(self._canvas, (ix, iy), 1.0)
      np.add.at(self._color_canvas, (ix, iy), color_t)

    # Log-scale for better visibility
    log_canvas = np.log1p(self._canvas)
    max_val = log_canvas.max()
    if max_val > 0:
      intensity = log_canvas / max_val
    else:
      intensity = log_canvas

    # Average color per pixel
    safe_canvas = np.maximum(self._canvas, 1e-10)
    avg_color = self._color_canvas / safe_canvas
    t_color = (np.clip(avg_color, 0, 1) + np.float32(elapsed * speed * 0.1)).astype(np.float32) % 1.0

    frame = pal_color_grid(pal_idx, t_color)
    # Scale by intensity
    frame = (frame.astype(np.float32) * intensity[:, :, np.newaxis]).astype(np.uint8)
    return frame


# ──────────────────────────────────────────────────────────────────────
#  Registry
# ──────────────────────────────────────────────────────────────────────

FRACTAL_EFFECTS = {
  'mandelbrot_zoom': MandelbrotZoom,
  'julia_explorer': JuliaExplorer,
  'burning_ship': BurningShip,
  'fractal_flame': FractalFlame,
  'sierpinski_flow': SierpinskiFlow,
}
