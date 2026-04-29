"""
Fluid and physics visualization effects — fully vectorized with NumPy.

Ten non-sound-reactive physics simulations:
- InkDrop: Gaussian ink blobs diffusing in water
- KelvinHelmholtz: Shear instability rolling waves
- ConvectionCells: Rayleigh-Benard heated convection
- LiquidCrystal: Slowly aligning colored domains
- MagneticField: Ferrofluid-style dipole field lines
- DoublePendulum: Chaotic trajectory traces
- LorenzAttractor: Strange attractor butterfly
- LatticeBoltzmann: Cellular automata fluid with vortex shedding
- TurbulentMix: Two fluids mixing through advection/diffusion
- PlasmaGlobe: Branching electric arcs from center
"""

import numpy as np
from .base import Effect


# ─── Helpers ──────────────────────────────────────────────────────


class _P:
  def __init__(self, label, attr, lo, hi, step, default):
    self.label, self.attr, self.lo, self.hi = label, attr, lo, hi
    self.step, self.default = step, default


def _hsv_array(h, s, v):
  """Vectorized HSV→RGB. h,s,v are float arrays in [0,1]. Returns uint8 RGB."""
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


def _simplex_2d(x, y):
  """Fast 2D pseudo-noise via sine combinations. Returns values in [-1, 1]."""
  return (np.sin(x * 1.7 + y * 2.3) * np.cos(y * 1.3 - x * 0.7) +
          np.sin(x * 3.1 - y * 1.1) * 0.5 +
          np.cos(x * 0.9 + y * 4.1) * 0.3)


# ──────────────────────────────────────────────────────────────────────
#  1. InkDrop
# ──────────────────────────────────────────────────────────────────────

class InkDrop(Effect):
  """Ink drops falling into water, spreading and diffusing with color mixing."""

  CATEGORY = "simulation"
  DISPLAY_NAME = "Ink Drop"
  DESCRIPTION = "Ink drops diffusing into water with beautiful color mixing"
  PALETTE_SUPPORT = False

  PARAMS = [
    _P("Drop Rate", "drop_rate", 0.5, 5.0, 0.1, 1.5),
    _P("Diffusion", "diffusion", 0.1, 0.95, 0.05, 0.6),
    _P("Trail", "trail", 0.9, 0.999, 0.001, 0.985),
  ]

  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    # RGB dye field
    self._dye = np.zeros((width, height, 3), dtype=np.float32)
    # Precompute coordinate grids
    self._gx = np.arange(width, dtype=np.float32)[:, np.newaxis]
    self._gy = np.arange(height, dtype=np.float32)[np.newaxis, :]
    self._next_drop = 0.0
    self._rng = np.random.default_rng(42)
    # Ink colors — rich saturated hues
    self._ink_colors = np.array([
      [0.0, 0.3, 1.0],   # deep blue
      [1.0, 0.0, 0.3],   # crimson
      [0.0, 0.8, 0.4],   # emerald
      [0.8, 0.0, 0.8],   # purple
      [1.0, 0.6, 0.0],   # amber
      [0.0, 0.7, 0.9],   # cyan
    ], dtype=np.float32)

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)
    drop_rate = self.params.get('drop_rate', 1.5)
    diffusion = self.params.get('diffusion', 0.6)
    trail = self.params.get('trail', 0.985)

    # Fade existing dye
    self._dye *= trail

    # Diffusion via box blur (shift-and-average)
    d = self._dye
    blurred = d * (1.0 - diffusion * 0.1)
    blurred[1:, :, :] += d[:-1, :, :] * (diffusion * 0.025)
    blurred[:-1, :, :] += d[1:, :, :] * (diffusion * 0.025)
    blurred[:, 1:, :] += d[:, :-1, :] * (diffusion * 0.025)
    blurred[:, :-1, :] += d[:, 1:, :] * (diffusion * 0.025)
    self._dye = blurred

    # Add new drops
    if elapsed >= self._next_drop:
      drop_x = self._rng.uniform(1, self.width - 1)
      drop_y = self._rng.uniform(0, self.height * 0.3)
      color_idx = self._rng.integers(0, len(self._ink_colors))
      color = self._ink_colors[color_idx]
      radius = self._rng.uniform(1.0, 3.0)

      # Gaussian blob
      dx = self._gx - drop_x
      dy = self._gy - drop_y
      dist_sq = dx * dx + dy * dy
      blob = np.exp(-dist_sq / (2.0 * radius * radius))
      self._dye += blob[:, :, np.newaxis] * color[np.newaxis, np.newaxis, :]

      # Gravity drip — elongate downward
      drip_y = drop_y + radius * 2
      dy_drip = self._gy - drip_y
      drip = np.exp(-(dx * dx + dy_drip * dy_drip) / (2.0 * (radius * 0.5) ** 2)) * 0.5
      self._dye += drip[:, :, np.newaxis] * color[np.newaxis, np.newaxis, :]

      interval = 1.0 / max(drop_rate, 0.1)
      self._next_drop = elapsed + interval

    # Gravity: shift dye downward slightly
    self._dye[:, 1:, :] += self._dye[:, :-1, :] * 0.008
    self._dye[:, :-1, :] *= 0.992

    frame = np.clip(self._dye * 255, 0, 255).astype(np.uint8)
    return frame


# ──────────────────────────────────────────────────────────────────────
#  2. KelvinHelmholtz
# ──────────────────────────────────────────────────────────────────────

class KelvinHelmholtz(Effect):
  """Kelvin-Helmholtz instability — rolling waves at a shear interface."""

  CATEGORY = "simulation"
  DISPLAY_NAME = "Kelvin-Helmholtz"
  DESCRIPTION = "Two fluid layers with shear instability creating rolling wave patterns"
  PALETTE_SUPPORT = False

  PARAMS = [
    _P("Speed", "speed", 0.1, 2.0, 0.05, 0.5),
    _P("Wavelength", "wavelength", 1.0, 8.0, 0.5, 3.0),
    _P("Amplitude", "amplitude", 0.1, 1.0, 0.05, 0.4),
  ]

  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    # Normalized coordinate grids
    self._nx = np.linspace(0, 1, width, dtype=np.float32)[:, np.newaxis]
    self._ny = np.linspace(0, 1, height, dtype=np.float32)[np.newaxis, :]

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)
    speed = self.params.get('speed', 0.5)
    wavelength = self.params.get('wavelength', 3.0)
    amplitude = self.params.get('amplitude', 0.4)

    et = elapsed * speed

    # Interface position with growing instability
    growth = np.minimum(np.float32(et * 0.15), np.float32(1.0))
    freq = 2.0 * np.pi * wavelength

    # Multiple wave modes creating complex rolling patterns
    interface = 0.5 + amplitude * growth * (
      0.5 * np.sin(freq * self._nx - et * 2.0) +
      0.3 * np.sin(freq * 1.7 * self._nx - et * 3.1 + 0.5) +
      0.2 * np.sin(freq * 2.3 * self._nx + et * 1.7 + 1.0)
    )

    # Rolling vortex curl near interface
    dist_to_interface = self._ny - interface
    curl = amplitude * growth * 0.3 * np.sin(
      freq * 1.5 * self._nx + dist_to_interface * 8.0 - et * 2.5
    ) * np.exp(-dist_to_interface ** 2 * 20.0)

    effective_y = self._ny - curl

    # Top layer: deep blue-purple
    # Bottom layer: warm orange-red
    blend = 1.0 / (1.0 + np.exp(-(effective_y - interface) * 15.0))  # sigmoid

    # Wisps and mixing detail near interface
    detail = np.exp(-dist_to_interface ** 2 * 8.0)
    wisp = 0.3 * detail * np.sin(
      freq * 3.0 * self._nx + effective_y * 20.0 - et * 4.0
    )

    frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)
    # Top layer: blue-indigo
    frame[..., 0] = np.clip((30 + wisp * 80) * (1 - blend) + blend * 220, 0, 255).astype(np.uint8)
    frame[..., 1] = np.clip((50 + wisp * 60) * (1 - blend) + blend * 100, 0, 255).astype(np.uint8)
    frame[..., 2] = np.clip((200 + wisp * 55) * (1 - blend) + blend * 30, 0, 255).astype(np.uint8)
    return frame


# ──────────────────────────────────────────────────────────────────────
#  3. ConvectionCells
# ──────────────────────────────────────────────────────────────────────

class ConvectionCells(Effect):
  """Rayleigh-Benard convection — hexagonal cells from bottom heating."""

  CATEGORY = "simulation"
  DISPLAY_NAME = "Convection Cells"
  DESCRIPTION = "Heated from below creates hexagonal convection cells with rising plumes"
  PALETTE_SUPPORT = False

  PARAMS = [
    _P("Speed", "speed", 0.1, 2.0, 0.05, 0.4),
    _P("Cell Count", "cells", 2.0, 8.0, 0.5, 4.0),
    _P("Intensity", "intensity", 0.3, 2.0, 0.1, 1.0),
  ]

  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    self._nx = np.linspace(0, 1, width, dtype=np.float32)[:, np.newaxis]
    self._ny = np.linspace(0, 1, height, dtype=np.float32)[np.newaxis, :]

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)
    speed = self.params.get('speed', 0.4)
    cells = self.params.get('cells', 4.0)
    intensity = self.params.get('intensity', 1.0)

    et = elapsed * speed

    # Hexagonal cell pattern using superimposed cosine waves
    # Three directions at 60-degree intervals for hex symmetry
    freq = cells * 2 * np.pi
    pattern = (
      np.cos(freq * self._nx + et * 0.5) +
      np.cos(freq * (0.5 * self._nx + 0.866 * self._ny) - et * 0.3) +
      np.cos(freq * (0.5 * self._nx - 0.866 * self._ny) + et * 0.4)
    )
    # Normalize to [0, 1] — cell centers are maxima
    cell_val = (pattern + 3.0) / 6.0

    # Temperature field: hot at bottom, cool at top, modulated by cells
    base_temp = 1.0 - self._ny  # hot bottom
    # Plumes rise in cell centers (high cell_val)
    plume = cell_val * base_temp * intensity

    # Add vertical convective flow detail
    flow_detail = 0.2 * np.sin(
      freq * 2.0 * self._nx + self._ny * 15.0 - et * 3.0
    ) * np.exp(-((self._ny - 0.5) ** 2) * 4.0)

    temperature = np.clip(plume + flow_detail * intensity, 0, 1)

    # Color mapping: black (cold) → blue → red → orange → yellow (hot)
    frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)
    temp = temperature.astype(np.float32)

    # Piecewise color ramp
    r = np.clip(temp * 3.0 - 0.5, 0, 1)
    g = np.clip(temp * 3.0 - 1.5, 0, 1)
    b = np.clip(np.minimum(temp * 4.0, 2.0 - temp * 2.0), 0, 1)

    frame[..., 0] = (r * 255).astype(np.uint8)
    frame[..., 1] = (g * 255).astype(np.uint8)
    frame[..., 2] = (b * 255).astype(np.uint8)
    return frame


# ──────────────────────────────────────────────────────────────────────
#  4. LiquidCrystal
# ──────────────────────────────────────────────────────────────────────

class LiquidCrystal(Effect):
  """Liquid crystal-like domains with sharp boundaries that evolve organically."""

  CATEGORY = "simulation"
  DISPLAY_NAME = "Liquid Crystal"
  DESCRIPTION = "Colored domains with sharp boundaries that slowly align and shift"
  PALETTE_SUPPORT = False

  PARAMS = [
    _P("Speed", "speed", 0.1, 2.0, 0.05, 0.3),
    _P("Domains", "domains", 2.0, 10.0, 0.5, 5.0),
    _P("Sharpness", "sharpness", 1.0, 10.0, 0.5, 4.0),
  ]

  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    self._nx = np.linspace(-1, 1, width, dtype=np.float32)[:, np.newaxis]
    self._ny = np.linspace(-1, 1, height, dtype=np.float32)[np.newaxis, :]

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)
    speed = self.params.get('speed', 0.3)
    domains = self.params.get('domains', 5.0)
    sharpness = self.params.get('sharpness', 4.0)

    et = elapsed * speed

    # Director field: local orientation angle at each pixel
    # Built from slowly evolving sine/cosine combinations
    theta = (
      np.sin(domains * self._nx + et * 0.7) *
      np.cos(domains * 0.8 * self._ny - et * 0.5) +
      0.5 * np.sin(domains * 1.3 * (self._nx + self._ny) + et * 0.4) +
      0.3 * np.cos(domains * 0.6 * self._nx - domains * 1.1 * self._ny - et * 0.3)
    )

    # Sharp domain boundaries via high-frequency phase wrap
    # The sharpness parameter controls how abrupt the transitions are
    phase = np.sin(theta * sharpness * np.pi)
    phase2 = np.cos(theta * sharpness * np.pi * 0.7 + et * 0.2)

    # Map to liquid-crystal-like iridescent colors
    # Birefringence creates hue from local orientation
    hue = (theta * 0.5 + 0.5 + et * 0.05) % 1.0
    sat = np.clip(0.6 + 0.4 * np.abs(phase), 0, 1)
    val = np.clip(0.3 + 0.7 * (0.5 + 0.5 * phase2), 0, 1)

    frame = _hsv_array(hue.astype(np.float32), sat.astype(np.float32),
                       val.astype(np.float32))
    return frame


# ──────────────────────────────────────────────────────────────────────
#  5. MagneticField
# ──────────────────────────────────────────────────────────────────────

class MagneticField(Effect):
  """Ferrofluid-inspired magnetic dipole field visualization."""

  CATEGORY = "simulation"
  DISPLAY_NAME = "Magnetic Field"
  DESCRIPTION = "Ferrofluid-style magnetic field lines with flowing particles"
  PALETTE_SUPPORT = False

  PARAMS = [
    _P("Speed", "speed", 0.1, 2.0, 0.05, 0.5),
    _P("Poles", "poles", 1, 4, 1, 2),
    _P("Flow Rate", "flow_rate", 0.5, 5.0, 0.5, 2.0),
  ]

  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    self._nx = np.linspace(-1.5, 1.5, width, dtype=np.float32)[:, np.newaxis]
    self._ny = np.linspace(-1.5, 1.5, height, dtype=np.float32)[np.newaxis, :]

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)
    speed = self.params.get('speed', 0.5)
    num_poles = int(self.params.get('poles', 2))
    flow_rate = self.params.get('flow_rate', 2.0)

    et = elapsed * speed

    # Pole positions — slowly orbiting
    bx = np.zeros((self.width, self.height), dtype=np.float32)
    by = np.zeros((self.width, self.height), dtype=np.float32)

    for i in range(num_poles):
      angle = et * 0.3 + i * 2.0 * np.pi / num_poles
      px = 0.5 * np.cos(angle)
      py = 0.5 * np.sin(angle)
      sign = 1.0 if i % 2 == 0 else -1.0

      dx = self._nx - px
      dy = self._ny - py
      r_sq = dx * dx + dy * dy + 0.05
      r_cubed = r_sq ** 1.5

      bx += sign * dx / r_cubed
      by += sign * dy / r_cubed

    # Field magnitude
    mag = np.sqrt(bx * bx + by * by + 1e-10)
    # Normalize for direction
    nx = bx / mag
    ny = by / mag

    # Field lines via directional hash — particles flowing along field
    flow_phase = (self._nx * nx + self._ny * ny) * 5.0 + et * flow_rate
    lines = 0.5 + 0.5 * np.sin(flow_phase * 2.0 * np.pi)

    # Cross-hatch for field density visualization
    cross = 0.5 + 0.5 * np.sin(
      (self._nx * (-ny) + self._ny * nx) * 8.0
    )

    # Intensity from field magnitude (log scale, clamped)
    field_intensity = np.clip(np.log1p(mag * 2.0) * 0.5, 0, 1)

    # Ferrofluid coloring: dark iron with field-strength-dependent highlights
    spike = np.clip(lines * cross * field_intensity, 0, 1)

    frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)
    # Dark metallic base with cyan/blue highlights along field lines
    frame[..., 0] = (spike * 80).astype(np.uint8)
    frame[..., 1] = (spike * 180 + field_intensity * 40).astype(np.uint8)
    frame[..., 2] = np.clip(spike * 255 + field_intensity * 60, 0, 255).astype(np.uint8)
    return frame


# ──────────────────────────────────────────────────────────────────────
#  6. DoublePendulum
# ──────────────────────────────────────────────────────────────────────

class DoublePendulum(Effect):
  """Double pendulum chaotic trajectory traces on the grid."""

  CATEGORY = "simulation"
  DISPLAY_NAME = "Double Pendulum"
  DESCRIPTION = "Chaotic double pendulum traces — multiple initial conditions overlap"
  PALETTE_SUPPORT = False

  PARAMS = [
    _P("Speed", "speed", 0.1, 3.0, 0.1, 1.0),
    _P("Pendulums", "count", 1, 6, 1, 3),
    _P("Trail", "trail", 0.9, 0.999, 0.001, 0.990),
  ]

  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    self._canvas = np.zeros((width, height, 3), dtype=np.float32)
    count = int(self.params.get('count', 3))
    self._init_pendulums(count)

  def _init_pendulums(self, count):
    """Initialize pendulum states with slightly different angles."""
    self._states = []
    self._colors = []
    hues = np.linspace(0, 1, count, endpoint=False)
    for i in range(count):
      # [theta1, theta2, omega1, omega2]
      theta1 = np.pi * 0.5 + i * 0.05
      theta2 = np.pi * 0.5 + i * 0.03
      self._states.append(np.array([theta1, theta2, 0.0, 0.0], dtype=np.float64))
      # Bright saturated colors
      h = hues[i]
      r = max(0, np.sin(h * 2 * np.pi)) * 0.8 + 0.2
      g = max(0, np.sin(h * 2 * np.pi + 2.094)) * 0.8 + 0.2
      b = max(0, np.sin(h * 2 * np.pi + 4.189)) * 0.8 + 0.2
      self._colors.append(np.array([r, g, b], dtype=np.float32))
    self._gx = np.arange(self.width, dtype=np.float32)[:, np.newaxis]
    self._gy = np.arange(self.height, dtype=np.float32)[np.newaxis, :]

  def _step_pendulum(self, s, dt):
    """RK4 step for double pendulum dynamics."""
    g_val = 9.81
    l1 = l2 = 1.0
    m1 = m2 = 1.0

    def derivs(st):
      t1, t2, w1, w2 = st
      delta = t2 - t1
      denom1 = (m1 + m2) * l1 - m2 * l1 * np.cos(delta) ** 2
      denom2 = (l2 / l1) * denom1

      dw1 = (m2 * l1 * w1 ** 2 * np.sin(delta) * np.cos(delta) +
             m2 * g_val * np.sin(t2) * np.cos(delta) +
             m2 * l2 * w2 ** 2 * np.sin(delta) -
             (m1 + m2) * g_val * np.sin(t1)) / denom1
      dw2 = (-m2 * l2 * w2 ** 2 * np.sin(delta) * np.cos(delta) +
             (m1 + m2) * g_val * np.sin(t1) * np.cos(delta) -
             (m1 + m2) * l1 * w1 ** 2 * np.sin(delta) -
             (m1 + m2) * g_val * np.sin(t2)) / denom2
      return np.array([w1, w2, dw1, dw2])

    k1 = derivs(s)
    k2 = derivs(s + dt * 0.5 * k1)
    k3 = derivs(s + dt * 0.5 * k2)
    k4 = derivs(s + dt * k3)
    return s + (dt / 6.0) * (k1 + 2 * k2 + 2 * k3 + k4)

  def render(self, t: float, state) -> np.ndarray:
    self.elapsed(t)
    speed = self.params.get('speed', 1.0)
    trail = self.params.get('trail', 0.990)

    # Fade canvas
    self._canvas *= trail

    dt = (1.0 / 60.0) * speed
    substeps = 3

    for idx, s in enumerate(self._states):
      for _ in range(substeps):
        s = self._step_pendulum(s, dt / substeps)
      self._states[idx] = s

      # Position of second pendulum bob
      t1, t2 = s[0], s[1]
      x2 = np.sin(t1) + np.sin(t2)
      y2 = np.cos(t1) + np.cos(t2)

      # Map from [-2, 2] to pixel coords
      px = (x2 + 2.0) / 4.0 * self.width
      py = (y2 + 2.0) / 4.0 * self.height

      # Draw a soft dot at the pendulum position
      dx = self._gx - px
      dy = self._gy - py
      dot = np.exp(-(dx * dx + dy * dy) / 1.5)
      self._canvas += dot[:, :, np.newaxis] * self._colors[idx][np.newaxis, np.newaxis, :]

    frame = np.clip(self._canvas * 255, 0, 255).astype(np.uint8)
    return frame


# ──────────────────────────────────────────────────────────────────────
#  7. LorenzAttractor
# ──────────────────────────────────────────────────────────────────────

class LorenzAttractor(Effect):
  """Lorenz strange attractor — butterfly trajectory projected onto the grid."""

  CATEGORY = "simulation"
  DISPLAY_NAME = "Lorenz Attractor"
  DESCRIPTION = "Points flowing along the Lorenz butterfly — iconic chaos theory shape"
  PALETTE_SUPPORT = False

  PARAMS = [
    _P("Speed", "speed", 0.1, 3.0, 0.1, 1.0),
    _P("Particles", "count", 50, 500, 50, 200),
    _P("Trail", "trail", 0.9, 0.999, 0.001, 0.985),
  ]

  # Lorenz parameters
  _SIGMA = 10.0
  _RHO = 28.0
  _BETA = 8.0 / 3.0

  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    self._canvas = np.zeros((width, height, 3), dtype=np.float32)
    count = int(self.params.get('count', 200))
    self._init_particles(count)
    self._gx = np.arange(width, dtype=np.float32)[:, np.newaxis]
    self._gy = np.arange(height, dtype=np.float32)[np.newaxis, :]

  def _init_particles(self, count):
    """Initialize particles near the attractor."""
    self._px = np.random.uniform(-1, 1, count).astype(np.float64)
    self._py = np.random.uniform(-1, 1, count).astype(np.float64)
    self._pz = np.random.uniform(20, 30, count).astype(np.float64)

  def render(self, t: float, state) -> np.ndarray:
    self.elapsed(t)
    speed = self.params.get('speed', 1.0)
    trail = self.params.get('trail', 0.985)

    self._canvas *= trail

    dt = 0.005 * speed
    substeps = 4

    for _ in range(substeps):
      # Lorenz equations (vectorized over all particles)
      dx = self._SIGMA * (self._py - self._px)
      dy = self._px * (self._RHO - self._pz) - self._py
      dz = self._px * self._py - self._BETA * self._pz
      self._px += dx * dt
      self._py += dy * dt
      self._pz += dz * dt

    # Project (x, z) onto grid — x: [-20, 20], z: [0, 50]
    screen_x = (self._px + 20.0) / 40.0 * self.width
    screen_y = (50.0 - self._pz) / 50.0 * self.height

    # Color by z-position (wing identity)
    hue = (self._pz / 50.0).astype(np.float32)

    # Scatter particles onto canvas
    ix = np.clip(screen_x.astype(np.int32), 0, self.width - 1)
    iy = np.clip(screen_y.astype(np.int32), 0, self.height - 1)

    # Color channels from hue
    r_val = np.clip(np.sin(hue * np.pi * 2) * 0.5 + 0.5, 0, 1).astype(np.float32)
    g_val = np.clip(np.sin(hue * np.pi * 2 + 2.094) * 0.5 + 0.5, 0, 1).astype(np.float32)
    b_val = np.clip(np.sin(hue * np.pi * 2 + 4.189) * 0.5 + 0.5, 0, 1).astype(np.float32)

    np.add.at(self._canvas[:, :, 0], (ix, iy), r_val * 0.3)
    np.add.at(self._canvas[:, :, 1], (ix, iy), g_val * 0.3)
    np.add.at(self._canvas[:, :, 2], (ix, iy), b_val * 0.3)

    frame = np.clip(self._canvas * 255, 0, 255).astype(np.uint8)
    return frame


# ──────────────────────────────────────────────────────────────────────
#  8. LatticeBoltzmann
# ──────────────────────────────────────────────────────────────────────

class LatticeBoltzmann(Effect):
  """Lattice Boltzmann fluid — flow around obstacles with vortex shedding."""

  CATEGORY = "simulation"
  DISPLAY_NAME = "Lattice Boltzmann"
  DESCRIPTION = "Simplified LBM fluid flow with vortex shedding around obstacles"
  PALETTE_SUPPORT = False

  PARAMS = [
    _P("Flow Speed", "flow_speed", 0.02, 0.15, 0.01, 0.06),
    _P("Viscosity", "viscosity", 0.005, 0.05, 0.005, 0.02),
    _P("Obstacle Size", "obstacle_size", 0.05, 0.3, 0.05, 0.15),
  ]

  # D2Q9 velocity set
  _EX = np.array([0, 1, 0, -1, 0, 1, -1, -1, 1], dtype=np.float32)
  _EY = np.array([0, 0, 1, 0, -1, 1, 1, -1, -1], dtype=np.float32)
  _W = np.array([4/9, 1/9, 1/9, 1/9, 1/9, 1/36, 1/36, 1/36, 1/36], dtype=np.float32)

  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    self._rebuild()

  def _rebuild(self):
    w, h = self.width, self.height
    obstacle_size = self.params.get('obstacle_size', 0.15)

    # Initialize distribution functions to equilibrium
    rho0 = 1.0
    ux0 = self.params.get('flow_speed', 0.06)
    self._f = np.zeros((9, w, h), dtype=np.float32)
    for i in range(9):
      cu = self._EX[i] * ux0
      self._f[i] = self._W[i] * rho0 * (1.0 + 3.0 * cu + 4.5 * cu * cu - 1.5 * ux0 * ux0)

    # Obstacle mask — cylinder in the left third
    cx = w * 0.25
    cy = h * 0.5
    radius = min(w, h) * obstacle_size
    gx = np.arange(w, dtype=np.float32)[:, np.newaxis]
    gy = np.arange(h, dtype=np.float32)[np.newaxis, :]
    self._obstacle = ((gx - cx) ** 2 + (gy - cy) ** 2) < radius ** 2

    # Dye field for visualization
    self._dye = np.zeros((w, h, 3), dtype=np.float32)

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)
    flow_speed = self.params.get('flow_speed', 0.06)
    viscosity = self.params.get('viscosity', 0.02)

    omega = 1.0 / (3.0 * viscosity + 0.5)

    # Multiple LBM steps per frame for visible flow
    steps = 3
    for _ in range(steps):
      # Macroscopic quantities
      rho = np.sum(self._f, axis=0)
      ux = np.sum(self._f * self._EX[:, np.newaxis, np.newaxis], axis=0) / (rho + 1e-10)
      uy = np.sum(self._f * self._EY[:, np.newaxis, np.newaxis], axis=0) / (rho + 1e-10)

      # Collision step (BGK)
      u_sq = ux * ux + uy * uy
      for i in range(9):
        cu = self._EX[i] * ux + self._EY[i] * uy
        feq = self._W[i] * rho * (1.0 + 3.0 * cu + 4.5 * cu * cu - 1.5 * u_sq)
        self._f[i] += omega * (feq - self._f[i])

      # Streaming step
      for i in range(9):
        self._f[i] = np.roll(self._f[i], int(self._EX[i]), axis=0)
        self._f[i] = np.roll(self._f[i], int(self._EY[i]), axis=1)

      # Bounce-back on obstacle
      if self._obstacle.any():
        # Swap opposite directions: 1↔3, 2↔4, 5↔7, 6↔8
        for a, b in [(1, 3), (2, 4), (5, 7), (6, 8)]:
          temp = self._f[a][self._obstacle].copy()
          self._f[a][self._obstacle] = self._f[b][self._obstacle]
          self._f[b][self._obstacle] = temp

      # Inlet boundary: reset left edge to constant flow
      rho_in = 1.0
      for i in range(9):
        cu = self._EX[i] * flow_speed
        self._f[i][0, :] = self._W[i] * rho_in * (1.0 + 3.0 * cu + 4.5 * cu * cu - 1.5 * flow_speed * flow_speed)

    # Compute vorticity for visualization
    rho = np.sum(self._f, axis=0)
    ux = np.sum(self._f * self._EX[:, np.newaxis, np.newaxis], axis=0) / (rho + 1e-10)
    uy = np.sum(self._f * self._EY[:, np.newaxis, np.newaxis], axis=0) / (rho + 1e-10)

    # Vorticity = duy/dx - dux/dy
    duy_dx = np.zeros_like(uy)
    dux_dy = np.zeros_like(ux)
    duy_dx[1:-1, :] = (uy[2:, :] - uy[:-2, :]) * 0.5
    dux_dy[:, 1:-1] = (ux[:, 2:] - ux[:, :-2]) * 0.5
    vort = duy_dx - dux_dy

    # Speed for brightness
    speed_field = np.sqrt(ux * ux + uy * uy)

    # Color by vorticity: blue for CCW, red for CW, brightness from speed
    vort_norm = np.clip(vort * 30.0, -1, 1)
    brightness = np.clip(speed_field * 10.0 + 0.1, 0, 1)

    frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)
    # Positive vorticity → cyan, negative → orange
    frame[..., 0] = np.clip((-vort_norm * 0.8 + 0.2) * brightness * 255, 0, 255).astype(np.uint8)
    frame[..., 1] = np.clip((0.3 + np.abs(vort_norm) * 0.4) * brightness * 255, 0, 255).astype(np.uint8)
    frame[..., 2] = np.clip((vort_norm * 0.8 + 0.3) * brightness * 255, 0, 255).astype(np.uint8)

    # Mark obstacle
    frame[self._obstacle] = [30, 30, 30]
    return frame


# ──────────────────────────────────────────────────────────────────────
#  9. TurbulentMix
# ──────────────────────────────────────────────────────────────────────

class TurbulentMix(Effect):
  """Two colored fluids turbulently mixing through advection and diffusion."""

  CATEGORY = "simulation"
  DISPLAY_NAME = "Turbulent Mix"
  DESCRIPTION = "Two fluids mixing from a sharp boundary into complex turbulent patterns"
  PALETTE_SUPPORT = False

  PARAMS = [
    _P("Speed", "speed", 0.1, 2.0, 0.05, 0.5),
    _P("Turbulence", "turbulence", 0.5, 5.0, 0.5, 2.0),
    _P("Diffusion", "diffusion", 0.1, 0.9, 0.05, 0.3),
  ]

  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    # Concentration field: 0 = fluid A (left, blue), 1 = fluid B (right, orange)
    self._conc = np.zeros((width, height), dtype=np.float32)
    self._conc[width // 2:, :] = 1.0
    # Add some initial perturbation at the interface
    rng = np.random.default_rng(123)
    mid = width // 2
    noise_band = 2
    self._conc[mid - noise_band:mid + noise_band, :] += \
      rng.uniform(-0.3, 0.3, (noise_band * 2, height)).astype(np.float32)
    self._conc = np.clip(self._conc, 0, 1)
    # Velocity field
    self._vx = np.zeros((width, height), dtype=np.float32)
    self._vy = np.zeros((width, height), dtype=np.float32)
    self._nx = np.linspace(0, 1, width, dtype=np.float32)[:, np.newaxis]
    self._ny = np.linspace(0, 1, height, dtype=np.float32)[np.newaxis, :]

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)
    speed = self.params.get('speed', 0.5)
    turb = self.params.get('turbulence', 2.0)
    diff = self.params.get('diffusion', 0.3)

    et = elapsed * speed

    # Time-varying turbulent velocity field (multi-scale)
    self._vx = turb * 0.3 * (
      np.sin(6.0 * self._ny + et * 2.0) * np.cos(4.0 * self._nx - et * 1.5) +
      0.5 * np.sin(10.0 * self._nx + et * 3.0) * np.cos(8.0 * self._ny - et * 2.0)
    )
    self._vy = turb * 0.3 * (
      np.cos(5.0 * self._nx - et * 1.8) * np.sin(7.0 * self._ny + et * 2.3) +
      0.5 * np.cos(9.0 * self._ny - et * 2.5) * np.sin(6.0 * self._nx + et * 1.2)
    )

    # Semi-Lagrangian advection: trace back
    gx_idx = np.broadcast_to(self._nx * (self.width - 1), (self.width, self.height))
    gy_idx = np.broadcast_to(self._ny * (self.height - 1), (self.width, self.height))
    src_x = np.clip(gx_idx - self._vx, 0, self.width - 1.001)
    src_y = np.clip(gy_idx - self._vy, 0, self.height - 1.001)

    # Bilinear interpolation
    x0 = src_x.astype(np.int32)
    y0 = src_y.astype(np.int32)
    x1 = np.minimum(x0 + 1, self.width - 1)
    y1 = np.minimum(y0 + 1, self.height - 1)
    fx = src_x - x0
    fy = src_y - y0

    c = self._conc
    advected = (
      c[x0, y0] * (1 - fx) * (1 - fy) +
      c[x1, y0] * fx * (1 - fy) +
      c[x0, y1] * (1 - fx) * fy +
      c[x1, y1] * fx * fy
    )

    # Diffusion step (Laplacian)
    laplacian = np.zeros_like(advected)
    laplacian[1:-1, :] += advected[2:, :] + advected[:-2, :] - 2 * advected[1:-1, :]
    laplacian[:, 1:-1] += advected[:, 2:] + advected[:, :-2] - 2 * advected[:, 1:-1]
    self._conc = np.clip(advected + laplacian * diff * 0.1, 0, 1)

    # Color mapping: fluid A (c=0) → deep blue, fluid B (c=1) → warm orange
    conc = self._conc
    frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)
    frame[..., 0] = (conc * 255).astype(np.uint8)
    frame[..., 1] = (np.clip(0.4 * conc + 0.1 * (1 - conc), 0, 1) * 200).astype(np.uint8)
    frame[..., 2] = ((1 - conc) * 230).astype(np.uint8)
    return frame


# ──────────────────────────────────────────────────────────────────────
#  10. PlasmaGlobe
# ──────────────────────────────────────────────────────────────────────

class PlasmaGlobe(Effect):
  """Tesla coil / plasma globe — branching electric arcs from center."""

  CATEGORY = "simulation"
  DISPLAY_NAME = "Plasma Globe"
  DESCRIPTION = "Electric arcs branching from a central point, flickering and forking"
  PALETTE_SUPPORT = False

  PARAMS = [
    _P("Arc Count", "arcs", 2, 8, 1, 4),
    _P("Speed", "speed", 0.5, 5.0, 0.5, 2.0),
    _P("Branch", "branch", 0.1, 1.0, 0.1, 0.5),
  ]

  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    self._gx = np.arange(width, dtype=np.float32)[:, np.newaxis]
    self._gy = np.arange(height, dtype=np.float32)[np.newaxis, :]
    self._rng = np.random.default_rng(99)

  def render(self, t: float, state) -> np.ndarray:
    elapsed = self.elapsed(t)
    num_arcs = int(self.params.get('arcs', 4))
    speed = self.params.get('speed', 2.0)
    branch = self.params.get('branch', 0.5)

    et = elapsed * speed

    cx = self.width * 0.5
    cy = self.height * 0.5

    # Distance and angle from center
    dx = self._gx - cx
    dy = self._gy - cy
    dist = np.sqrt(dx * dx + dy * dy) + 1e-10
    angle = np.arctan2(dy, dx)
    max_dist = np.sqrt(cx * cx + cy * cy)
    norm_dist = dist / max_dist

    frame_f = np.zeros((self.width, self.height, 3), dtype=np.float32)

    # Central glow
    core_glow = np.exp(-dist * dist / (min(self.width, self.height) * 0.15) ** 2)
    frame_f[..., 0] = core_glow * 0.4
    frame_f[..., 1] = core_glow * 0.3
    frame_f[..., 2] = core_glow * 0.8

    # Generate arcs
    for i in range(num_arcs):
      # Arc base angle — slowly rotating with jitter
      base_angle = (i * 2.0 * np.pi / num_arcs) + et * 0.3 + \
        0.3 * np.sin(et * 1.7 + i * 2.1)

      # Arc path: angle deviation depends on distance from center
      # Use sine combinations for organic jitter
      jitter1 = branch * 0.8 * np.sin(
        norm_dist * 15.0 + et * 7.0 + i * 3.7
      )
      jitter2 = branch * 0.4 * np.sin(
        norm_dist * 25.0 - et * 11.0 + i * 5.3
      )
      jitter3 = branch * 0.2 * np.sin(
        norm_dist * 40.0 + et * 17.0 + i * 7.1
      )

      arc_angle = base_angle + jitter1 + jitter2 + jitter3

      # How close each pixel is to the arc's path
      angle_diff = angle - arc_angle
      # Wrap to [-pi, pi]
      angle_diff = (angle_diff + np.pi) % (2 * np.pi) - np.pi

      # Arc width narrows with distance, flickers over time
      flicker = 0.7 + 0.3 * np.sin(et * 13.0 + i * 4.0)
      arc_width = (0.15 + 0.1 * branch) / (1.0 + norm_dist * 3.0)
      arc_intensity = np.exp(-(angle_diff ** 2) / (2 * arc_width ** 2))

      # Fade arc at edges, brighten near center
      radial_fade = np.clip(1.0 - norm_dist * 0.5, 0, 1) * flicker
      # Cut off arc beyond the globe
      arc_intensity *= radial_fade * (norm_dist < 0.95).astype(np.float32)

      # Add branch forks
      fork_intensity = branch * 0.5 * np.exp(
        -(angle_diff - jitter2 * 0.5) ** 2 / (2 * (arc_width * 1.5) ** 2)
      ) * radial_fade

      total = arc_intensity + fork_intensity

      # White-blue-purple arc coloring
      frame_f[..., 0] += total * 0.6  # hint of warm
      frame_f[..., 1] += total * 0.5  # some green for white
      frame_f[..., 2] += total * 1.0  # strong blue

    # Ambient purple globe haze
    globe_mask = (norm_dist < 1.0).astype(np.float32)
    haze = 0.03 * globe_mask * (1.0 + 0.5 * np.sin(et * 0.5))
    frame_f[..., 0] += haze * 0.5
    frame_f[..., 1] += haze * 0.1
    frame_f[..., 2] += haze * 0.6

    frame = np.clip(frame_f * 255, 0, 255).astype(np.uint8)
    return frame


# ──────────────────────────────────────────────────────────────────────
#  Registry
# ──────────────────────────────────────────────────────────────────────

FLUID_EFFECTS = {
  'ink_drop': InkDrop,
  'kelvin_helmholtz': KelvinHelmholtz,
  'convection_cells': ConvectionCells,
  'liquid_crystal': LiquidCrystal,
  'magnetic_field': MagneticField,
  'double_pendulum': DoublePendulum,
  'lorenz_attractor': LorenzAttractor,
  'lattice_boltzmann': LatticeBoltzmann,
  'turbulent_mix': TurbulentMix,
  'plasma_globe': PlasmaGlobe,
}
