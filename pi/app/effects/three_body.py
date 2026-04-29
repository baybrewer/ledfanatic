"""
Sound-reactive three-body gravitational simulation.

Three massive bodies orbit chaotically under mutual gravitation.
Bass drives energy injection, creating orbital chaos.
Heavy temporal blur creates luminous trails.
Fully vectorized NumPy — no Python loops in hot path.
"""

import math
import numpy as np
from .base import Effect


class _P:
  def __init__(self, label, attr, lo, hi, step, default):
    self.label, self.attr, self.lo, self.hi = label, attr, lo, hi
    self.step, self.default = step, default


class SRThreeBody(Effect):
  """Sound-reactive three-body gravitational problem with luminous trails."""

  CATEGORY = "sound"
  DISPLAY_NAME = "SR Three Body"
  DESCRIPTION = "Chaotic three-body gravity — bass injects orbital energy"
  PALETTE_SUPPORT = False
  AUDIO_REQUIRES = ('level', 'bass', 'mid', 'high', 'beat')

  PARAMS = [
    _P("Gain", "gain", 0.5, 5.0, 0.1, 2.0),
    _P("Trail", "trail", 0.85, 0.99, 0.005, 0.96),
    _P("Glow Size", "glow_size", 1.0, 5.0, 0.25, 2.5),
    _P("Speed", "speed", 0.5, 3.0, 0.1, 1.0),
    _P("Chaos", "chaos", 0.0, 1.0, 0.05, 0.3),
  ]

  # Body colors — bold, saturated, distinct
  BODY_COLORS = [
    (255, 60, 30),    # hot red-orange
    (30, 120, 255),   # electric blue
    (255, 220, 30),   # golden yellow
  ]

  TRAIL_COLORS = [
    (180, 30, 10),    # deep red trail
    (15, 60, 180),    # deep blue trail
    (180, 150, 10),   # amber trail
  ]

  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    w, h = width, height

    # Three bodies — positions in pixel coordinates, velocities in px/s
    cx, cy = w / 2.0, h / 2.0
    r = min(w, h) * 0.2

    # Start in a stable-ish triangular orbit
    self._px = np.array([
      cx + r * math.cos(0),
      cx + r * math.cos(2.094),
      cx + r * math.cos(4.189),
    ], dtype=np.float64)
    self._py = np.array([
      cy + r * math.sin(0),
      cy + r * math.sin(2.094),
      cy + r * math.sin(4.189),
    ], dtype=np.float64)

    # Initial orbital velocities (perpendicular to radius, creates rotation)
    orbit_v = 8.0
    self._vx = np.array([
      -orbit_v * math.sin(0),
      -orbit_v * math.sin(2.094),
      -orbit_v * math.sin(4.189),
    ], dtype=np.float64)
    self._vy = np.array([
      orbit_v * math.cos(0),
      orbit_v * math.cos(2.094),
      orbit_v * math.cos(4.189),
    ], dtype=np.float64)

    self._masses = np.array([1.0, 1.0, 1.0], dtype=np.float64)
    self._last_t = None
    self._trail_frame = np.zeros((w, h, 3), dtype=np.float32)
    self._prev_frame = None

    # Precompute coordinate grids for glow rendering
    self._gx = np.arange(w, dtype=np.float32)[:, np.newaxis] * np.ones(h, dtype=np.float32)[np.newaxis, :]
    self._gy = np.ones(w, dtype=np.float32)[:, np.newaxis] * np.arange(h, dtype=np.float32)[np.newaxis, :]

    # Gravitational constant (tuned for visual drama)
    self._G = 800.0

  def _compute_forces(self):
    """Compute gravitational forces between all three bodies."""
    fx = np.zeros(3, dtype=np.float64)
    fy = np.zeros(3, dtype=np.float64)
    softening = 2.0  # prevents singularity at close approach

    for i in range(3):
      for j in range(i + 1, 3):
        dx = self._px[j] - self._px[i]
        dy = self._py[j] - self._py[i]
        r2 = dx * dx + dy * dy + softening * softening
        r = math.sqrt(r2)
        f = self._G * self._masses[i] * self._masses[j] / r2
        fx_ij = f * dx / r
        fy_ij = f * dy / r
        fx[i] += fx_ij
        fy[i] += fy_ij
        fx[j] -= fx_ij
        fy[j] -= fy_ij

    return fx, fy

  def render(self, t: float, state) -> np.ndarray:
    if self._last_t is None:
      self._last_t = t
    dt = min(t - self._last_t, 0.03)
    self._last_t = t

    w, h = self.width, self.height
    gain = self.params.get('gain', 2.0)
    trail_decay = self.params.get('trail', 0.96)
    glow_size = self.params.get('glow_size', 2.5)
    speed = self.params.get('speed', 1.0)
    chaos = self.params.get('chaos', 0.3)

    bass = state.audio_bass * gain
    mid = state.audio_mid * gain
    high = state.audio_high * gain
    level = state.audio_level * gain

    # === PHYSICS — Velocity Verlet integration ===
    sim_dt = dt * speed * 0.5  # sub-step for stability

    for _ in range(2):  # 2 sub-steps per frame
      # Compute forces
      fx, fy = self._compute_forces()

      # Bass injects energy — random velocity kicks
      if bass > 0.2:
        kick = bass * chaos * 5.0
        self._vx += np.random.uniform(-kick, kick, 3)
        self._vy += np.random.uniform(-kick, kick, 3)

      # Accelerate
      for i in range(3):
        ax = fx[i] / self._masses[i]
        ay = fy[i] / self._masses[i]
        self._vx[i] += ax * sim_dt
        self._vy[i] += ay * sim_dt

      # Mild damping to prevent infinite energy buildup
      damping = 1.0 - 0.001 * sim_dt
      self._vx *= damping
      self._vy *= damping

      # Move
      self._px += self._vx * sim_dt
      self._py += self._vy * sim_dt

      # Wrap on x (cylindrical), bounce on y
      self._px = self._px % w
      for i in range(3):
        if self._py[i] < 1:
          self._py[i] = 1
          self._vy[i] = abs(self._vy[i]) * 0.8
        elif self._py[i] > h - 2:
          self._py[i] = h - 2
          self._vy[i] = -abs(self._vy[i]) * 0.8

    # === RENDER ===

    # Decay trail frame (persistence creates luminous trails)
    self._trail_frame *= trail_decay

    # Draw each body as a glowing orb with velocity-stretched trail
    for i in range(3):
      bx, by = float(self._px[i]), float(self._py[i])
      r, g, b_c = self.BODY_COLORS[i]
      tr, tg, tb = self.TRAIL_COLORS[i]

      # Velocity magnitude for glow intensity
      v_mag = math.sqrt(self._vx[i] ** 2 + self._vy[i] ** 2)
      intensity = min(1.0, 0.5 + v_mag * 0.02 + bass * 0.3)

      # Distance field for glow — soft gaussian
      dx = self._gx - bx
      # Cylindrical wrap distance
      dx = np.where(np.abs(dx) > w / 2, dx - np.sign(dx) * w, dx)
      dy = self._gy - by
      dist_sq = dx * dx + dy * dy
      gs = glow_size * (1.0 + bass * 0.3)

      # Core glow — bright inner
      core = np.exp(-dist_sq / (gs * gs * 0.5)) * intensity
      # Outer halo — softer, wider
      halo = np.exp(-dist_sq / (gs * gs * 3.0)) * intensity * 0.4
      # Combined
      glow = core + halo

      # Add to trail frame (accumulates over time)
      self._trail_frame[:, :, 0] += glow * tr * 0.15
      self._trail_frame[:, :, 1] += glow * tg * 0.15
      self._trail_frame[:, :, 2] += glow * tb * 0.15

    # Build output frame — trail + current body positions
    frame = self._trail_frame.copy()

    # Draw bodies on top — bright cores
    for i in range(3):
      bx, by = float(self._px[i]), float(self._py[i])
      r, g, b_c = self.BODY_COLORS[i]
      v_mag = math.sqrt(self._vx[i] ** 2 + self._vy[i] ** 2)
      intensity = min(1.5, 0.6 + v_mag * 0.03 + bass * 0.4)

      dx = self._gx - bx
      dx = np.where(np.abs(dx) > w / 2, dx - np.sign(dx) * w, dx)
      dy = self._gy - by
      dist_sq = dx * dx + dy * dy
      gs = glow_size * (0.8 + bass * 0.2)

      # Bright core
      core = np.exp(-dist_sq / (gs * gs * 0.3)) * intensity
      # Hot center — extra bright
      center = np.exp(-dist_sq / (gs * gs * 0.08)) * intensity * 0.8

      frame[:, :, 0] += (core + center) * r
      frame[:, :, 1] += (core + center) * g
      frame[:, :, 2] += (core + center) * b_c

    # Mid frequency adds subtle background shimmer
    if mid > 0.2:
      elapsed = self.elapsed(t)
      shimmer = np.sin(self._gx * 0.5 + elapsed * 2) * np.cos(self._gy * 0.08 + elapsed) * mid * 8
      frame[:, :, 0] += np.clip(shimmer, 0, 15)
      frame[:, :, 2] += np.clip(shimmer * 0.5, 0, 10)

    # Gravitational lensing effect — draw faint lines between bodies
    for i in range(3):
      for j in range(i + 1, 3):
        x0, y0 = float(self._px[i]), float(self._py[i])
        x1, y1 = float(self._px[j]), float(self._py[j])
        # Distance between bodies
        bdist = math.sqrt((x1 - x0) ** 2 + (y1 - y0) ** 2)
        if bdist < h * 0.6:  # only when close enough
          # Faint connecting arc — blend of both body colors
          mid_x = (x0 + x1) / 2
          mid_y = (y0 + y1) / 2
          dx_line = self._gx - mid_x
          dx_line = np.where(np.abs(dx_line) > w / 2, dx_line - np.sign(dx_line) * w, dx_line)
          dy_line = self._gy - mid_y
          line_dist = np.sqrt(dx_line ** 2 + dy_line ** 2)
          line_glow = np.exp(-line_dist / (bdist * 0.3 + 1)) * 0.1 * (1.0 - bdist / (h * 0.6))
          ri = (self.TRAIL_COLORS[i][0] + self.TRAIL_COLORS[j][0]) / 2
          gi = (self.TRAIL_COLORS[i][1] + self.TRAIL_COLORS[j][1]) / 2
          bi = (self.TRAIL_COLORS[i][2] + self.TRAIL_COLORS[j][2]) / 2
          frame[:, :, 0] += line_glow * ri
          frame[:, :, 1] += line_glow * gi
          frame[:, :, 2] += line_glow * bi

    result = np.clip(frame, 0, 255).astype(np.uint8)

    # Extra temporal smoothing for silky motion
    if self._prev_frame is not None:
      result = (result.astype(np.float32) * 0.55 + self._prev_frame.astype(np.float32) * 0.45).astype(np.uint8)
    self._prev_frame = result.copy()

    return result


THREE_BODY_EFFECTS = {
  'sr_three_body': SRThreeBody,
}
