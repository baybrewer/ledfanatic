"""
Negative-space sound-reactive effects — darkness as the visual element.

Five effects that start from bright backgrounds and SUBTRACT dark shapes:
- SRShadowPulse: dark ripples expand from beat impact points
- SRVoidBreath: pulsing dark void with organic noise boundary
- SRLightningGap: dark branching cracks appear on beats
- SRNegativeRain: dark droplets falling on bright background
- SRSilhouette: dark metaballs floating and merging

All rendering is fully vectorized with NumPy — no Python for-loops on pixels.
"""

import numpy as np
from .base import Effect


# ─── Helpers ──────────────────────────────────────────────────────


class _P:
  def __init__(self, label, attr, lo, hi, step, default):
    self.label, self.attr, self.lo, self.hi = label, attr, lo, hi
    self.step, self.default = step, default


def _hsv_array(h, s, v):
  """Vectorized HSV->RGB. h,s,v are float arrays in [0,1]. Returns uint8 RGB."""
  h = h % 1.0
  i = (h * 6.0).astype(np.int32) % 6
  f = (h * 6.0) - np.floor(h * 6.0)
  p = v * (1.0 - s)
  q = v * (1.0 - s * f)
  t = v * (1.0 - s * (1.0 - f))

  r = np.where(i == 0, v, np.where(i == 1, q, np.where(i == 2, p,
       np.where(i == 3, p, np.where(i == 4, t, v)))))
  g = np.where(i == 0, t, np.where(i == 1, v, np.where(i == 2, v,
       np.where(i == 3, q, np.where(i == 4, p, p)))))
  b = np.where(i == 0, p, np.where(i == 1, p, np.where(i == 2, t,
       np.where(i == 3, v, np.where(i == 4, v, q)))))

  out = np.zeros(h.shape + (3,), dtype=np.uint8)
  out[..., 0] = np.clip(r * 255, 0, 255).astype(np.uint8)
  out[..., 1] = np.clip(g * 255, 0, 255).astype(np.uint8)
  out[..., 2] = np.clip(b * 255, 0, 255).astype(np.uint8)
  return out


def _noise_2d(x, y):
  """Fast 2D pseudo-noise via sine combinations. Returns values in [-1, 1]."""
  return (np.sin(x * 1.7 + y * 2.3) * np.cos(y * 1.3 - x * 0.7) +
          np.sin(x * 3.1 - y * 1.1) * 0.5 +
          np.cos(x * 0.9 + y * 4.1) * 0.3)


def _plasma_bg(gx, gy, elapsed, width, height):
  """Generate a vibrant animated plasma background. Returns (w, h, 3) uint8."""
  # Multi-layer sine plasma
  t = elapsed * 0.4
  cx = gx / max(width, 1) * 6.0
  cy = gy / max(height, 1) * 6.0

  v1 = np.sin(cx + t)
  v2 = np.sin(cy + t * 0.7)
  v3 = np.sin(cx + cy + t * 1.3)
  v4 = np.sin(np.sqrt(cx * cx + cy * cy) * 2.0 + t * 0.5)
  plasma = (v1 + v2 + v3 + v4) / 4.0  # [-1, 1]

  hue = ((plasma + 1.0) * 0.5 + elapsed * 0.02) % 1.0
  sat = np.clip(0.7 + plasma * 0.2, 0.5, 1.0)
  val = np.clip(0.85 + plasma * 0.15, 0.7, 1.0)

  return _hsv_array(
    hue.astype(np.float32),
    sat.astype(np.float32),
    val.astype(np.float32),
  )


# ──────────────────────────────────────────────────────────────────────
#  1. SRShadowPulse
# ──────────────────────────────────────────────────────────────────────

_RING_DTYPE = np.dtype([
  ('cx', np.float32), ('cy', np.float32),
  ('radius', np.float32), ('speed', np.float32),
  ('life', np.float32), ('max_life', np.float32),
  ('width', np.float32),
])

_MAX_RINGS = 30


class SRShadowPulse(Effect):
  """Bright background with dark ripples expanding from beat impacts."""

  CATEGORY = "sound"
  DISPLAY_NAME = "SR Shadow Pulse"
  DESCRIPTION = "Dark ripples expand outward from beat impact points on a bright surface"
  PALETTE_SUPPORT = False
  AUDIO_REQUIRES = ('level', 'bass', 'mid', 'high', 'beat')

  PARAMS = [
    _P("Gain", "gain", 0.5, 5.0, 0.1, 2.0),
    _P("Ring Speed", "ring_speed", 5.0, 60.0, 1.0, 25.0),
    _P("Ring Width", "ring_width", 1.0, 8.0, 0.5, 3.0),
    _P("Darkness", "darkness", 0.5, 1.0, 0.05, 0.95),
  ]

  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    self._rings = np.empty(0, dtype=_RING_DTYPE)
    self._last_t = None
    self._spawn_accum = 0.0
    # Precompute coordinate grids
    xs = np.arange(width, dtype=np.float32)
    ys = np.arange(height, dtype=np.float32)
    self._gx, self._gy = np.meshgrid(xs, ys, indexing='ij')

  def render(self, t: float, state) -> np.ndarray:
    if self._last_t is None:
      self._last_t = t
    dt = min(t - self._last_t, 0.05)
    self._last_t = t
    elapsed = self.elapsed(t)

    gain = self.params.get('gain', 2.0)
    ring_speed = self.params.get('ring_speed', 25.0)
    ring_width = self.params.get('ring_width', 3.0)
    darkness_strength = self.params.get('darkness', 0.95)

    bass = state.audio_bass * gain
    level = state.audio_level * gain

    # Continuous bass-driven spawning — accumulate fractional rings
    spawn_rate = bass * 12.0  # rings per second at full bass
    self._spawn_accum += spawn_rate * dt
    spawn_count = int(self._spawn_accum)
    if spawn_count > 0 and len(self._rings) < _MAX_RINGS:
      self._spawn_accum -= spawn_count
      count = min(spawn_count, _MAX_RINGS - len(self._rings))
      new = np.empty(count, dtype=_RING_DTYPE)
      new['cx'] = np.random.uniform(0, self.width, count).astype(np.float32)
      new['cy'] = np.random.uniform(0, self.height, count).astype(np.float32)
      new['radius'] = 0.0
      new['speed'] = ring_speed * (0.5 + bass * 0.8)
      life = 1.5 + bass * 1.5
      new['life'] = life
      new['max_life'] = life
      new['width'] = ring_width * (0.6 + bass * 0.6)
      if len(self._rings) == 0:
        self._rings = new
      else:
        self._rings = np.concatenate([self._rings, new])

    # Update rings
    if len(self._rings) > 0:
      self._rings['radius'] += self._rings['speed'] * dt
      self._rings['life'] -= dt
      alive = self._rings['life'] > 0
      self._rings = self._rings[alive]

    # Vibrant plasma background
    frame = _plasma_bg(self._gx, self._gy, elapsed, self.width, self.height).astype(np.float32)

    # Subtract dark rings — vectorized across all rings
    if len(self._rings) > 0:
      # Compute distance from each pixel to each ring center
      # Shape: (width, height, n_rings)
      dx = self._gx[:, :, np.newaxis] - self._rings['cx'][np.newaxis, np.newaxis, :]
      dy = self._gy[:, :, np.newaxis] - self._rings['cy'][np.newaxis, np.newaxis, :]
      dist = np.sqrt(dx * dx + dy * dy)

      radii = self._rings['radius'][np.newaxis, np.newaxis, :]
      widths = self._rings['width'][np.newaxis, np.newaxis, :]
      fade = self._rings['life'] / self._rings['max_life']
      fade = fade[np.newaxis, np.newaxis, :]

      # Ring intensity: Gaussian around the ring radius
      ring_dist = np.abs(dist - radii) / np.maximum(widths, 0.1)
      ring_mask = np.exp(-ring_dist * ring_dist * 2.0) * fade

      # Combine all rings (max darkness at each pixel)
      darkness = np.max(ring_mask, axis=2)
      darkness = np.clip(darkness * (0.7 + level * 0.5), 0.0, 1.0)

      # Apply darkness controlled by slider
      frame *= (1.0 - darkness[:, :, np.newaxis] * darkness_strength)

    return np.clip(frame, 0, 255).astype(np.uint8)


# ──────────────────────────────────────────────────────────────────────
#  2. SRVoidBreath
# ──────────────────────────────────────────────────────────────────────

class SRVoidBreath(Effect):
  """Bright nebula with a breathing dark void — bass swells the void."""

  CATEGORY = "sound"
  DISPLAY_NAME = "SR Void Breath"
  DESCRIPTION = "A breathing dark void with organic edges expands and contracts with bass"
  PALETTE_SUPPORT = False
  AUDIO_REQUIRES = ('level', 'bass', 'mid', 'high', 'beat')

  PARAMS = [
    _P("Gain", "gain", 0.5, 5.0, 0.1, 2.0),
    _P("Min Radius", "min_radius", 0.05, 0.3, 0.01, 0.1),
    _P("Max Radius", "max_radius", 0.3, 0.9, 0.05, 0.7),
    _P("Edge Detail", "edge_detail", 1.0, 8.0, 0.5, 3.0),
    _P("Darkness", "darkness", 0.5, 1.0, 0.05, 0.95),
  ]

  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    self._smooth_radius = 0.1
    self._last_t = None
    # Normalized coordinate grids centered at (0.5, 0.5)
    xs = np.linspace(-0.5, 0.5, width, dtype=np.float32)
    ys = np.linspace(-0.5, 0.5, height, dtype=np.float32)
    self._gx, self._gy = np.meshgrid(xs, ys, indexing='ij')
    self._dist = np.sqrt(self._gx ** 2 + self._gy ** 2)
    # Precompute angles for noise boundary
    self._angle = np.arctan2(self._gy, self._gx)

  def render(self, t: float, state) -> np.ndarray:
    if self._last_t is None:
      self._last_t = t
    dt = min(t - self._last_t, 0.05)
    self._last_t = t
    elapsed = self.elapsed(t)

    gain = self.params.get('gain', 2.0)
    min_r = self.params.get('min_radius', 0.1)
    max_r = self.params.get('max_radius', 0.7)
    edge_detail = self.params.get('edge_detail', 3.0)
    darkness_strength = self.params.get('darkness', 0.95)

    bass = state.audio_bass * gain
    mid = state.audio_mid * gain
    level = state.audio_level * gain

    # Target radius driven by bass
    target_r = min_r + (max_r - min_r) * np.clip(bass, 0, 1)
    # Smooth interpolation
    self._smooth_radius += (target_r - self._smooth_radius) * min(dt * 4.0, 1.0)
    radius = self._smooth_radius

    # Noise boundary — jaggedness controlled by mid frequency
    jaggedness = 0.02 + mid * 0.08
    noise_scale = edge_detail
    boundary_noise = _noise_2d(
      self._angle * noise_scale + elapsed * 1.5,
      self._angle * noise_scale * 0.7 + elapsed * 0.8
    )
    # Normalize noise to [0, 1] range roughly
    boundary_noise = (boundary_noise + 1.8) / 3.6
    noisy_radius = radius + boundary_noise * jaggedness

    # Vibrant plasma background
    # Use full coordinate grids (not normalized) for plasma
    gx_full = (self._gx + 0.5) * self.width
    gy_full = (self._gy + 0.5) * self.height
    frame_f = _plasma_bg(gx_full, gy_full, elapsed, self.width, self.height).astype(np.float32)

    # Void mask — dark where dist < noisy_radius
    # Soft edge transition
    edge_width = 0.03 + level * 0.02
    void_mask = np.clip((noisy_radius - self._dist) / max(edge_width, 0.001), 0, 1)

    # Apply void
    frame_f *= (1.0 - void_mask[:, :, np.newaxis] * darkness_strength)

    # Subtle glow at void edge
    edge_glow = np.exp(-((self._dist - noisy_radius) ** 2) / (0.002 + level * 0.003))
    edge_glow = np.clip(edge_glow * 0.4 * (0.5 + bass * 0.5), 0, 0.5)
    glow_hue = (elapsed * 0.1 + 0.6) % 1.0
    frame_f[:, :, 0] += edge_glow * 80
    frame_f[:, :, 1] += edge_glow * 40
    frame_f[:, :, 2] += edge_glow * 120

    return np.clip(frame_f, 0, 255).astype(np.uint8)


# ──────────────────────────────────────────────────────────────────────
#  3. SRLightningGap
# ──────────────────────────────────────────────────────────────────────

_CRACK_DTYPE = np.dtype([
  ('x', np.float32), ('y', np.float32),
  ('life', np.float32), ('max_life', np.float32),
])

_MAX_CRACK_POINTS = 800
_MAX_CRACKS = 15


class SRLightningGap(Effect):
  """Bright sky with dark branching cracks that appear on beats."""

  CATEGORY = "sound"
  DISPLAY_NAME = "SR Lightning Gap"
  DESCRIPTION = "Dark branching cracks spread across a bright sky on each beat"
  PALETTE_SUPPORT = False
  AUDIO_REQUIRES = ('level', 'bass', 'mid', 'high', 'beat')

  PARAMS = [
    _P("Gain", "gain", 0.5, 5.0, 0.1, 2.0),
    _P("Branch Prob", "branch_prob", 0.05, 0.5, 0.01, 0.15),
    _P("Crack Life", "crack_life", 0.5, 4.0, 0.1, 2.0),
    _P("Crack Width", "crack_width", 0.5, 3.0, 0.25, 1.0),
    _P("Darkness", "darkness", 0.5, 1.0, 0.05, 0.95),
  ]

  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    self._crack_points = np.empty(0, dtype=_CRACK_DTYPE)
    self._last_t = None
    self._spawn_accum = 0.0
    self._prev_bass = 0.0
    # Precompute coordinate grids
    xs = np.arange(width, dtype=np.float32)
    ys = np.arange(height, dtype=np.float32)
    self._gx, self._gy = np.meshgrid(xs, ys, indexing='ij')

  def _spawn_crack(self, bass):
    """Generate a branching crack as an array of points."""
    # Start from random position
    start_x = np.random.uniform(0, self.width)
    start_y = np.random.uniform(0, self.height)
    branch_prob = self.params.get('branch_prob', 0.15)
    crack_life = self.params.get('crack_life', 2.0) * (0.8 + bass * 0.4)

    # Generate crack segments using random walk
    max_segments = min(60 + int(bass * 40), 150)
    points = []

    # Stack-based branching: (x, y, angle, remaining_steps)
    stack = [(start_x, start_y, np.random.uniform(-np.pi, np.pi), max_segments)]

    while stack and len(points) < _MAX_CRACK_POINTS - len(self._crack_points):
      cx, cy, angle, remaining = stack.pop()
      step_len = np.random.uniform(0.5, 2.0)

      for _ in range(remaining):
        # Walk
        angle += np.random.uniform(-0.6, 0.6)
        cx += np.cos(angle) * step_len
        cy += np.sin(angle) * step_len

        if not (0 <= cx < self.width and 0 <= cy < self.height):
          break

        points.append((cx, cy, crack_life, crack_life))

        # Branch
        if np.random.random() < branch_prob and len(stack) < 8:
          branch_angle = angle + np.random.choice([-1, 1]) * np.random.uniform(0.4, 1.2)
          stack.append((cx, cy, branch_angle, remaining // 3))

        remaining -= 1
        if remaining <= 0:
          break

    if points:
      new = np.array(points, dtype=_CRACK_DTYPE)
      if len(self._crack_points) == 0:
        self._crack_points = new
      else:
        total = len(self._crack_points) + len(new)
        if total > _MAX_CRACK_POINTS:
          new = new[:_MAX_CRACK_POINTS - len(self._crack_points)]
        if len(new) > 0:
          self._crack_points = np.concatenate([self._crack_points, new])

  def render(self, t: float, state) -> np.ndarray:
    if self._last_t is None:
      self._last_t = t
    dt = min(t - self._last_t, 0.05)
    self._last_t = t
    elapsed = self.elapsed(t)

    gain = self.params.get('gain', 2.0)
    crack_width = self.params.get('crack_width', 1.0)
    darkness_strength = self.params.get('darkness', 0.95)

    bass = state.audio_bass * gain
    level = state.audio_level * gain
    high = state.audio_high * gain

    # Bass-reactive crack spawning — every bass surge triggers a crack
    bass_delta = max(0, bass - self._prev_bass)  # rising edge of bass
    self._prev_bass = bass * 0.7 + self._prev_bass * 0.3  # smooth tracking

    # Spawn on bass amplitude — accumulate fractional spawns
    spawn_rate = bass * 6.0 + bass_delta * 20.0  # steady + transient response
    self._spawn_accum += spawn_rate * dt
    while self._spawn_accum >= 1.0 and len(self._crack_points) < _MAX_CRACK_POINTS * 0.8:
      self._spawn_accum -= 1.0
      self._spawn_crack(bass)

    # Update crack lifetimes
    if len(self._crack_points) > 0:
      self._crack_points['life'] -= dt
      alive = self._crack_points['life'] > 0
      self._crack_points = self._crack_points[alive]

    # Vibrant plasma background
    frame_f = _plasma_bg(self._gx, self._gy, elapsed, self.width, self.height).astype(np.float32)

    # Render crack points — rasterize directly (no distance matrix)
    if len(self._crack_points) > 0:
      pts = self._crack_points
      fade = pts['life'] / pts['max_life']
      darkness = np.zeros((self.width, self.height), dtype=np.float32)

      # Rasterize each point into nearby pixels (much faster than full distance matrix)
      ix = np.clip(np.round(pts['x']).astype(np.int32), 0, self.width - 1)
      iy = np.clip(np.round(pts['y']).astype(np.int32), 0, self.height - 1)
      cw = int(crack_width + 0.5)

      # Scatter crack darkness into buffer
      np.add.at(darkness, (ix, iy), fade * 0.8)
      # Spread to neighbors for width
      for dx in range(-cw, cw + 1):
        for dy in range(-cw, cw + 1):
          if dx == 0 and dy == 0:
            continue
          dist = (dx * dx + dy * dy) ** 0.5
          if dist > cw + 0.5:
            continue
          falloff = max(0, 1.0 - dist / (cw + 0.5))
          nx = np.clip(ix + dx, 0, self.width - 1)
          ny = np.clip(iy + dy, 0, self.height - 1)
          np.add.at(darkness, (nx, ny), fade * 0.5 * falloff)

      darkness = np.clip(darkness, 0, 1)
      frame_f *= (1.0 - darkness[:, :, np.newaxis] * darkness_strength)

    return np.clip(frame_f, 0, 255).astype(np.uint8)


# ──────────────────────────────────────────────────────────────────────
#  4. SRNegativeRain
# ──────────────────────────────────────────────────────────────────────

_DROP_DTYPE = np.dtype([
  ('x', np.float32), ('y', np.float32),
  ('vy', np.float32), ('length', np.float32),
  ('brightness', np.float32),
])

_MAX_DROPS = 300


class SRNegativeRain(Effect):
  """Bright background with dark rain drops falling — inverse matrix rain."""

  CATEGORY = "sound"
  DISPLAY_NAME = "SR Negative Rain"
  DESCRIPTION = "Dark droplets fall through a bright world — inverse of matrix rain"
  PALETTE_SUPPORT = False
  AUDIO_REQUIRES = ('level', 'bass', 'mid', 'high', 'beat')

  PARAMS = [
    _P("Gain", "gain", 0.5, 5.0, 0.1, 2.0),
    _P("Drop Speed", "drop_speed", 5.0, 40.0, 1.0, 15.0),
    _P("Trail Length", "trail_length", 1.0, 8.0, 0.5, 3.0),
    _P("Density", "density", 0.5, 5.0, 0.1, 2.0),
    _P("Darkness", "darkness", 0.5, 1.0, 0.05, 0.95),
  ]

  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    self._drops = np.empty(0, dtype=_DROP_DTYPE)
    self._last_t = None
    self._prev_frame = None
    self._spawn_accum = 0.0  # fractional spawn accumulator
    # Precompute coordinate grids
    xs = np.arange(width, dtype=np.float32)
    ys = np.arange(height, dtype=np.float32)
    self._gx, self._gy = np.meshgrid(xs, ys, indexing='ij')

  def _spawn_drops(self, count, bass, speed_mult=1.0):
    """Spawn new drops at the top."""
    count = min(count, _MAX_DROPS - len(self._drops))
    if count <= 0:
      return
    trail_length = self.params.get('trail_length', 3.0)
    drop_speed = self.params.get('drop_speed', 15.0)

    new = np.empty(count, dtype=_DROP_DTYPE)
    new['x'] = np.random.randint(0, self.width, count).astype(np.float32)
    new['y'] = np.random.uniform(-2, 0, count).astype(np.float32)
    new['vy'] = np.random.uniform(0.7, 1.3, count).astype(np.float32) * drop_speed * speed_mult
    new['length'] = np.random.uniform(0.5, 1.0, count).astype(np.float32) * trail_length * (0.8 + bass * 0.4)
    new['brightness'] = np.random.uniform(0.6, 1.0, count).astype(np.float32)

    if len(self._drops) == 0:
      self._drops = new
    else:
      self._drops = np.concatenate([self._drops, new])

  def render(self, t: float, state) -> np.ndarray:
    if self._last_t is None:
      self._last_t = t
    dt = min(t - self._last_t, 0.05)
    self._last_t = t
    elapsed = self.elapsed(t)

    gain = self.params.get('gain', 2.0)
    density = self.params.get('density', 2.0)
    darkness_strength = self.params.get('darkness', 0.95)

    bass = state.audio_bass * gain
    level = state.audio_level * gain
    mid = state.audio_mid * gain

    # Continuous bass-driven spawning — accumulate fractional drops
    spawn_rate = density * (0.5 + bass * 3.0) * self.width * 0.5
    self._spawn_accum += spawn_rate * dt
    spawn_count = int(self._spawn_accum)
    if spawn_count > 0:
      self._spawn_accum -= spawn_count
      self._spawn_drops(spawn_count, bass, speed_mult=0.8 + bass * 0.5)

    # Update drops
    if len(self._drops) > 0:
      self._drops['y'] += self._drops['vy'] * dt
      alive = self._drops['y'] < self.height + 5
      self._drops = self._drops[alive]

    # Vibrant plasma background
    frame_f = _plasma_bg(self._gx, self._gy, elapsed, self.width, self.height).astype(np.float32)

    # Render drops — vectorized with column-based approach
    if len(self._drops) > 0:
      drops = self._drops
      darkness = np.zeros((self.width, self.height), dtype=np.float32)

      # For each drop, darken pixels in its column near its y position
      # Process in batches
      batch_size = 100
      for i in range(0, len(drops), batch_size):
        batch = drops[i:i + batch_size]
        # Column positions
        col_idx = np.clip(np.round(batch['x']).astype(np.int32), 0, self.width - 1)

        # For each pixel row, compute darkness from nearby drops
        row_pos = self._gy[0, :]  # (height,)
        drop_y = batch['y'][:, np.newaxis]  # (n_drops, 1)
        drop_len = batch['length'][:, np.newaxis]
        drop_bright = batch['brightness'][:, np.newaxis]

        # Distance from drop head, along trail
        dy = row_pos[np.newaxis, :] - drop_y  # (n_drops, height)
        # Trail goes upward from drop head
        trail_mask = (dy >= -0.5) & (dy <= drop_len)
        trail_intensity = np.where(
          trail_mask,
          (1.0 - dy / (drop_len + 0.01)) * drop_bright,
          0.0
        )
        trail_intensity = np.clip(trail_intensity, 0, 1)

        # Scatter into darkness buffer by column
        for j in range(len(batch)):
          col = col_idx[j]
          darkness[col] = np.maximum(darkness[col], trail_intensity[j])

      darkness = np.clip(darkness * (0.7 + level * 0.5), 0, 1)
      frame_f *= (1.0 - darkness[:, :, np.newaxis] * darkness_strength)

    result = np.clip(frame_f, 0, 255).astype(np.uint8)

    # Temporal blending for smoother trails
    if self._prev_frame is not None:
      result = np.maximum(
        (result.astype(np.float32) * 0.7).astype(np.uint8),
        (self._prev_frame.astype(np.float32) * 0.3).astype(np.uint8),
      )
    self._prev_frame = result.copy()

    return result


# ──────────────────────────────────────────────────────────────────────
#  5. SRSilhouette
# ──────────────────────────────────────────────────────────────────────

_BLOB_DTYPE = np.dtype([
  ('cx', np.float32), ('cy', np.float32),
  ('vx', np.float32), ('vy', np.float32),
  ('radius', np.float32), ('target_radius', np.float32),
  ('life', np.float32), ('hue_offset', np.float32),
])

_MAX_BLOBS = 20


class SRSilhouette(Effect):
  """Dark metaballs floating over a bright gradient — living shadow puppetry."""

  CATEGORY = "sound"
  DISPLAY_NAME = "SR Silhouette"
  DESCRIPTION = "Dark organic blobs float and merge against a bright gradient background"
  PALETTE_SUPPORT = False
  AUDIO_REQUIRES = ('level', 'bass', 'mid', 'high', 'beat')

  PARAMS = [
    _P("Gain", "gain", 0.5, 5.0, 0.1, 2.0),
    _P("Blob Speed", "blob_speed", 1.0, 15.0, 0.5, 5.0),
    _P("Min Size", "min_size", 1.0, 5.0, 0.5, 2.0),
    _P("Max Size", "max_size", 3.0, 12.0, 0.5, 6.0),
    _P("Darkness", "darkness", 0.5, 1.0, 0.05, 0.95),
  ]

  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    self._blobs = np.empty(0, dtype=_BLOB_DTYPE)
    self._last_t = None
    self._beat_cooldown = 0.0
    # Normalized coordinate grids
    xs = np.arange(width, dtype=np.float32)
    ys = np.arange(height, dtype=np.float32)
    self._gx, self._gy = np.meshgrid(xs, ys, indexing='ij')
    # Spawn a few initial blobs
    self._spawn_blobs(3, 0.5)

  def _spawn_blobs(self, count, bass):
    """Spawn new dark blobs."""
    count = min(count, _MAX_BLOBS - len(self._blobs))
    if count <= 0:
      return
    min_size = self.params.get('min_size', 2.0)
    max_size = self.params.get('max_size', 6.0)
    blob_speed = self.params.get('blob_speed', 5.0)

    new = np.empty(count, dtype=_BLOB_DTYPE)
    new['cx'] = np.random.uniform(0, self.width, count).astype(np.float32)
    new['cy'] = np.random.uniform(0, self.height, count).astype(np.float32)
    new['vx'] = np.random.uniform(-1, 1, count).astype(np.float32) * blob_speed
    new['vy'] = np.random.uniform(-1, 1, count).astype(np.float32) * blob_speed
    base_radius = min_size + (max_size - min_size) * bass
    new['radius'] = 0.5  # Start small, grow
    new['target_radius'] = np.random.uniform(0.7, 1.3, count).astype(np.float32) * base_radius
    new['life'] = np.random.uniform(5.0, 15.0, count).astype(np.float32)
    new['hue_offset'] = np.random.uniform(0, 1, count).astype(np.float32)

    if len(self._blobs) == 0:
      self._blobs = new
    else:
      self._blobs = np.concatenate([self._blobs, new])

  def render(self, t: float, state) -> np.ndarray:
    if self._last_t is None:
      self._last_t = t
    dt = min(t - self._last_t, 0.05)
    self._last_t = t
    elapsed = self.elapsed(t)

    gain = self.params.get('gain', 2.0)
    min_size = self.params.get('min_size', 2.0)
    max_size = self.params.get('max_size', 6.0)
    darkness_strength = self.params.get('darkness', 0.95)

    bass = state.audio_bass * gain
    level = state.audio_level * gain
    mid = state.audio_mid * gain

    # Continuous bass-driven blob spawning
    if bass > 0.15 and np.random.random() < bass * dt * 4 and len(self._blobs) < _MAX_BLOBS:
      self._spawn_blobs(1 + int(bass > 0.5), bass)

    # Update blobs
    if len(self._blobs) > 0:
      blobs = self._blobs

      # Move blobs
      blobs['cx'] += blobs['vx'] * dt
      blobs['cy'] += blobs['vy'] * dt

      # Grow toward target radius
      blobs['radius'] += (blobs['target_radius'] - blobs['radius']) * min(dt * 2.0, 1.0)

      # Bass modulates size
      size_mod = 1.0 + bass * 0.3
      # (applied during rendering, not stored)

      # Bounce off edges
      out_left = blobs['cx'] < blobs['radius']
      out_right = blobs['cx'] > self.width - blobs['radius']
      out_top = blobs['cy'] < blobs['radius']
      out_bottom = blobs['cy'] > self.height - blobs['radius']
      blobs['vx'] = np.where(out_left | out_right, -blobs['vx'], blobs['vx'])
      blobs['vy'] = np.where(out_top | out_bottom, -blobs['vy'], blobs['vy'])
      blobs['cx'] = np.clip(blobs['cx'], 0, self.width - 1)
      blobs['cy'] = np.clip(blobs['cy'], 0, self.height - 1)

      # Age and cull
      blobs['life'] -= dt
      alive = blobs['life'] > 0
      self._blobs = blobs[alive]

    # Keep minimum blob count — always active
    if len(self._blobs) < 5:
      self._spawn_blobs(3, bass)

    # Vibrant plasma background
    frame_f = _plasma_bg(self._gx, self._gy, elapsed, self.width, self.height).astype(np.float32)

    # Render metaballs — compute field from all blobs
    if len(self._blobs) > 0:
      blobs = self._blobs
      size_mod = 1.0 + bass * 0.3

      # Metaball field: sum of 1/dist^2 contributions from each blob
      field = np.zeros((self.width, self.height), dtype=np.float32)

      # Batch all blobs
      cx = blobs['cx'][np.newaxis, np.newaxis, :]
      cy = blobs['cy'][np.newaxis, np.newaxis, :]
      radii = blobs['radius'][np.newaxis, np.newaxis, :] * size_mod

      dx = self._gx[:, :, np.newaxis] - cx
      dy = self._gy[:, :, np.newaxis] - cy
      dist_sq = dx * dx + dy * dy + 0.01  # avoid division by zero

      # Metaball contribution: r^2 / dist^2
      contributions = (radii * radii) / dist_sq

      # Fade dying blobs
      life_fade = np.clip(blobs['life'] / 3.0, 0, 1)
      contributions *= life_fade[np.newaxis, np.newaxis, :]

      # Sum all blob contributions for smooth merging
      field = np.sum(contributions, axis=2)

      # Threshold for metaball surface — values > 1.0 are "inside"
      # Smooth transition
      darkness = np.clip((field - 0.5) * 2.0, 0, 1)
      darkness = np.clip(darkness * (0.7 + level * 0.5), 0, 1)

      # Apply darkness
      frame_f *= (1.0 - darkness[:, :, np.newaxis] * darkness_strength)

      # Subtle edge highlight where field ~ 1.0
      edge_band = np.exp(-((field - 1.0) ** 2) * 10)
      edge_band *= 0.2 * (0.5 + bass * 0.5)
      frame_f[:, :, 0] += edge_band * 30
      frame_f[:, :, 1] += edge_band * 50
      frame_f[:, :, 2] += edge_band * 60

    return np.clip(frame_f, 0, 255).astype(np.uint8)


# ─── Registry ─────────────────────────────────────────────────────

NEGATIVE_SPACE_EFFECTS = {
  'sr_shadow_pulse': SRShadowPulse,
  'sr_void_breath': SRVoidBreath,
  'sr_lightning_gap': SRLightningGap,
  'sr_negative_rain': SRNegativeRain,
  'sr_silhouette': SRSilhouette,
}
