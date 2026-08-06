"""
Negative-space spin-offs of SR Negative Rain — darkness as the visual element.

Ten effects sharing the Negative Rain DNA: a vibrant plasma background with
soft dark shapes subtracted from it. The signature "gradual blur" look comes
from a shared persistent darkness field that decays AND diffuses every frame,
so shapes melt away softly instead of flashing hard contrast.

- SRNegativeJets: dark fluid jets spraying up from the floor
- SRNegativeFlow: dark ink tracers carried by a curl-noise fluid field
- SRNegativeFireworks: dark shells rise and burst into soft fading sparks
- SRNegativeSnow: slow dark flakes drifting and swaying downward
- SRNegativeComets: dark comets streaking across with melting trails
- SRNegativeVortex: differential-rotation swirl forming dark spiral arms
- SRNegativeAurora: wavering dark curtains, aurora in negative
- SRNegativeBubbles: dark bubble rings rising, wobbling, dissolving
- SRNegativeWaves: rolling dark swells passing over the panel
- SRNegativeOrbits: dark bodies on precessing elliptical orbits

All rendering is fully vectorized with NumPy — no Python for-loops on pixels.
"""

import numpy as np
from .base import Effect
from .negative_space import _P, _plasma_bg, _noise_2d


def _common_params(fade=1.0, softness=1.2, darkness=0.9):
  """The four params every negative spin-off shares (defaults tunable per effect)."""
  return [
    _P("Gain", "gain", 0.5, 5.0, 0.1, 2.0),
    _P("Softness", "softness", 0.0, 3.0, 0.1, softness),
    _P("Trail Fade", "fade", 0.2, 4.0, 0.1, fade),
    _P("Darkness", "darkness", 0.5, 1.0, 0.05, darkness),
  ]


class _NegativeFieldEffect(Effect):
  """Shared engine: persistent darkness field + decay + diffusion over plasma.

  Subclasses implement _update(dt, elapsed, bass, mid, high, level) and paint
  darkness via _stamp_gaussian/_stamp_ring or direct np.maximum into self._dark.
  """

  CATEGORY = "sound"
  PALETTE_SUPPORT = False
  AUDIO_REQUIRES = ('level', 'bass', 'mid', 'high', 'beat')
  PARAMS: list = []

  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    self._last_t = None
    xs = np.arange(width, dtype=np.float32)
    ys = np.arange(height, dtype=np.float32)
    self._gx, self._gy = np.meshgrid(xs, ys, indexing='ij')
    self._dark = np.zeros((width, height), dtype=np.float32)

  def _param(self, attr, fallback=0.0):
    if attr in self.params:
      return self.params[attr]
    for p in self.PARAMS:
      if p.attr == attr:
        return p.default
    return fallback

  # ── darkness painting helpers ──

  def _stamp_gaussian(self, xs, ys, radii, amps):
    """Max-blend soft dark blobs into the field. All args are (n,) arrays."""
    if len(xs) == 0:
      return
    dx = self._gx[:, :, np.newaxis] - xs[np.newaxis, np.newaxis, :]
    dy = self._gy[:, :, np.newaxis] - ys[np.newaxis, np.newaxis, :]
    sig = np.maximum(radii, 0.35)[np.newaxis, np.newaxis, :]
    g = np.exp(-(dx * dx + dy * dy) / (2.0 * sig * sig)) * amps[np.newaxis, np.newaxis, :]
    np.maximum(self._dark, g.max(axis=2), out=self._dark)

  def _stamp_ring(self, xs, ys, radii, widths, amps):
    """Max-blend soft dark rings (annuli) into the field."""
    if len(xs) == 0:
      return
    dx = self._gx[:, :, np.newaxis] - xs[np.newaxis, np.newaxis, :]
    dy = self._gy[:, :, np.newaxis] - ys[np.newaxis, np.newaxis, :]
    dist = np.sqrt(dx * dx + dy * dy)
    rr = radii[np.newaxis, np.newaxis, :]
    ww = np.maximum(widths, 0.25)[np.newaxis, np.newaxis, :]
    g = np.exp(-((dist - rr) ** 2) / (2.0 * ww * ww)) * amps[np.newaxis, np.newaxis, :]
    np.maximum(self._dark, g.max(axis=2), out=self._dark)

  def _diffuse(self, field):
    """One separable 3-tap blur pass with edge-clamped borders."""
    p = np.pad(field, ((1, 1), (0, 0)), mode='edge')
    fx = p[1:-1] * 0.5 + (p[:-2] + p[2:]) * 0.25
    p = np.pad(fx, ((0, 0), (1, 1)), mode='edge')
    return p[:, 1:-1] * 0.5 + (p[:, :-2] + p[:, 2:]) * 0.25

  # ── render loop ──

  def _update(self, dt, elapsed, bass, mid, high, level):
    raise NotImplementedError

  def render(self, t: float, state) -> np.ndarray:
    if self._last_t is None:
      self._last_t = t
    dt = float(np.clip(t - self._last_t, 1e-4, 0.05))
    self._last_t = t
    elapsed = self.elapsed(t)

    gain = self._param('gain', 2.0)
    bass = min(state.audio_bass * gain, 1.5)
    mid = min(state.audio_mid * gain, 1.5)
    high = min(state.audio_high * gain, 1.5)
    level = min(state.audio_level * gain, 1.5)

    # Trail persistence — old darkness melts away exponentially
    fade = max(self._param('fade', 1.0), 0.05)
    self._dark *= np.float32(np.exp(-dt / fade))

    self._update(dt, elapsed, bass, mid, high, level)

    # The gradual blur — aging shapes diffuse outward and soften
    blur_mix = min(self._param('softness', 1.2) * dt * 6.0, 1.0)
    if blur_mix > 0:
      self._dark += (self._diffuse(self._dark) - self._dark) * np.float32(blur_mix)

    frame = _plasma_bg(self._gx, self._gy, elapsed, self.width, self.height).astype(np.float32)
    d = np.clip(self._dark, 0.0, 1.0) * self._param('darkness', 0.9)
    frame *= (1.0 - d[:, :, np.newaxis])
    return np.clip(frame, 0, 255).astype(np.uint8)


# ──────────────────────────────────────────────────────────────────────
#  1. SRNegativeJets
# ──────────────────────────────────────────────────────────────────────

_JET_DTYPE = np.dtype([
  ('x', np.float32), ('y', np.float32),
  ('vx', np.float32), ('vy', np.float32),
  ('life', np.float32), ('max_life', np.float32),
])

_MAX_JET_PARTICLES = 250


class SRNegativeJets(_NegativeFieldEffect):
  """Dark fluid jets spray upward and dissolve — fountains of shadow."""

  DISPLAY_NAME = "SR Negative Jets"
  DESCRIPTION = "Dark fluid jets spray up from the floor and melt into the light"

  PARAMS = [
    _P("Jet Power", "jet_power", 0.5, 3.0, 0.1, 1.5),
    _P("Jets", "jets", 1, 4, 1, 2),
    *_common_params(fade=0.8, softness=1.4),
  ]

  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    self._particles = np.empty(0, dtype=_JET_DTYPE)
    self._spawn_accum = 0.0

  def _update(self, dt, elapsed, bass, mid, high, level):
    power = self._param('jet_power', 1.5)
    njets = max(1, int(self._param('jets', 2)))

    rate = njets * (8.0 + bass * 35.0)
    self._spawn_accum += rate * dt
    count = int(self._spawn_accum)
    if count > 0 and len(self._particles) < _MAX_JET_PARTICLES:
      self._spawn_accum -= count
      count = min(count, _MAX_JET_PARTICLES - len(self._particles))
      jet_idx = np.random.randint(0, njets, count)
      # Each nozzle sweeps side to side out of phase with its neighbors
      sweep = np.sin(elapsed * 1.3 + jet_idx * 2.1) * 0.45
      angle = sweep + np.random.uniform(-0.18, 0.18, count)
      speed = (self.height * 0.14) * power * (0.75 + bass * 0.6)
      speed = speed * np.random.uniform(0.8, 1.2, count)
      new = np.empty(count, dtype=_JET_DTYPE)
      new['x'] = (self.width * (jet_idx + 0.5) / njets).astype(np.float32)
      new['y'] = np.float32(self.height - 1)
      new['vx'] = (np.sin(angle) * speed).astype(np.float32)
      new['vy'] = (-np.cos(angle) * speed).astype(np.float32)
      life = np.random.uniform(0.9, 1.6, count).astype(np.float32)
      new['life'] = life
      new['max_life'] = life
      self._particles = new if len(self._particles) == 0 else np.concatenate([self._particles, new])

    p = self._particles
    if len(p) > 0:
      p['vy'] += (self.height * 0.09) * dt  # gravity pulls the spray back down
      p['x'] += p['vx'] * dt
      p['y'] += p['vy'] * dt
      p['life'] -= dt
      alive = (p['life'] > 0) & (p['y'] < self.height + 3) & (p['x'] > -3) & (p['x'] < self.width + 3)
      self._particles = p = p[alive]

    if len(p) > 0:
      age = 1.0 - p['life'] / p['max_life']
      radii = 0.5 + age * 1.6  # spray droplets diffuse as they age
      amps = (p['life'] / p['max_life']) * 0.85
      self._stamp_gaussian(p['x'], p['y'], radii, amps)


# ──────────────────────────────────────────────────────────────────────
#  2. SRNegativeFlow
# ──────────────────────────────────────────────────────────────────────

_FLOW_TRACERS = 110


class SRNegativeFlow(_NegativeFieldEffect):
  """Dark ink carried by an invisible fluid — curl-noise smoke in negative."""

  DISPLAY_NAME = "SR Negative Flow"
  DESCRIPTION = "Dark ink tracers swirl through an invisible fluid, leaving soft smoky wakes"

  PARAMS = [
    _P("Flow Speed", "flow_speed", 0.3, 3.0, 0.1, 1.0),
    _P("Swirl Scale", "swirl_scale", 0.05, 0.4, 0.01, 0.16),
    *_common_params(fade=1.6, softness=1.6),
  ]

  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    self._px = np.random.uniform(0, width, _FLOW_TRACERS).astype(np.float32)
    self._py = np.random.uniform(0, height, _FLOW_TRACERS).astype(np.float32)

  def _update(self, dt, elapsed, bass, mid, high, level):
    flow_speed = self._param('flow_speed', 1.0)
    s = self._param('swirl_scale', 0.16)
    T = elapsed * 0.35

    def noise(x, y):
      return _noise_2d(x * s + T, y * s - T * 0.7)

    # Curl of the noise field — divergence-free, so tracers never clump
    eps = 0.75
    vx = (noise(self._px, self._py + eps) - noise(self._px, self._py - eps)) / (2 * eps)
    vy = -(noise(self._px + eps, self._py) - noise(self._px - eps, self._py)) / (2 * eps)
    speed = flow_speed * max(self.width, self.height) * 0.09 * (0.6 + mid * 0.8)
    self._px = (self._px + vx * speed * dt) % self.width
    self._py = (self._py + vy * speed * dt) % self.height

    radii = np.full(_FLOW_TRACERS, 0.9, dtype=np.float32)
    amps = np.full(_FLOW_TRACERS, 0.55 + min(level, 1.0) * 0.3, dtype=np.float32)
    self._stamp_gaussian(self._px, self._py, radii, amps)


# ──────────────────────────────────────────────────────────────────────
#  3. SRNegativeFireworks
# ──────────────────────────────────────────────────────────────────────

_ROCKET_DTYPE = np.dtype([
  ('x', np.float32), ('y', np.float32),
  ('vy', np.float32), ('fuse', np.float32),
])

_SPARK_DTYPE = np.dtype([
  ('x', np.float32), ('y', np.float32),
  ('vx', np.float32), ('vy', np.float32),
  ('life', np.float32), ('max_life', np.float32),
])

_MAX_ROCKETS = 6
_MAX_SPARKS = 400


class SRNegativeFireworks(_NegativeFieldEffect):
  """Dark shells rise and burst into soft shadow sparks — fireworks in negative."""

  DISPLAY_NAME = "SR Negative Fireworks"
  DESCRIPTION = "Dark shells climb and burst into blooming shadow sparks that melt away"

  PARAMS = [
    _P("Launch Rate", "launch_rate", 0.2, 3.0, 0.1, 0.8),
    _P("Burst Size", "burst_size", 10, 60, 5, 30),
    *_common_params(fade=1.2, softness=1.5),
  ]

  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    self._rockets = np.empty(0, dtype=_ROCKET_DTYPE)
    self._sparks = np.empty(0, dtype=_SPARK_DTYPE)
    self._spawn_accum = 0.0

  def _burst(self, x, y, bass):
    count = int(self._param('burst_size', 30) * (0.8 + bass * 0.8))
    count = min(count, _MAX_SPARKS - len(self._sparks))
    if count <= 0:
      return
    angle = np.random.uniform(0, 2 * np.pi, count)
    speed = np.random.uniform(0.25, 1.0, count) * self.height * 0.12 * (0.8 + bass * 0.5)
    new = np.empty(count, dtype=_SPARK_DTYPE)
    new['x'] = np.float32(x)
    new['y'] = np.float32(y)
    new['vx'] = (np.cos(angle) * speed).astype(np.float32)
    new['vy'] = (np.sin(angle) * speed * 0.85).astype(np.float32)
    life = np.random.uniform(1.0, 1.8, count).astype(np.float32)
    new['life'] = life
    new['max_life'] = life
    self._sparks = new if len(self._sparks) == 0 else np.concatenate([self._sparks, new])

  def _update(self, dt, elapsed, bass, mid, high, level):
    rate = self._param('launch_rate', 0.8) * (1.0 + bass * 2.5)
    self._spawn_accum += rate * dt
    count = int(self._spawn_accum)
    if count > 0 and len(self._rockets) < _MAX_ROCKETS:
      self._spawn_accum -= count
      count = min(count, _MAX_ROCKETS - len(self._rockets))
      new = np.empty(count, dtype=_ROCKET_DTYPE)
      new['x'] = np.random.uniform(self.width * 0.1, self.width * 0.9, count).astype(np.float32)
      new['y'] = np.float32(self.height - 1)
      climb_time = np.random.uniform(0.8, 1.3, count).astype(np.float32)
      apex = np.random.uniform(0.25, 0.65, count) * self.height
      new['vy'] = (-(self.height - apex) / climb_time).astype(np.float32)
      new['fuse'] = climb_time
      self._rockets = new if len(self._rockets) == 0 else np.concatenate([self._rockets, new])

    r = self._rockets
    if len(r) > 0:
      r['y'] += r['vy'] * dt
      r['fuse'] -= dt
      exploding = r['fuse'] <= 0
      for rx, ry in zip(r['x'][exploding], r['y'][exploding]):
        self._burst(rx, ry, bass)
      self._rockets = r = r[~exploding]

    sp = self._sparks
    if len(sp) > 0:
      drag = np.float32(np.exp(-dt * 1.6))
      sp['vx'] *= drag
      sp['vy'] *= drag
      sp['vy'] += (self.height * 0.05) * dt  # sparks sink gently
      sp['x'] += sp['vx'] * dt
      sp['y'] += sp['vy'] * dt
      sp['life'] -= dt
      self._sparks = sp = sp[sp['life'] > 0]

    if len(r) > 0:
      self._stamp_gaussian(
        r['x'], r['y'],
        np.full(len(r), 0.55, dtype=np.float32),
        np.full(len(r), 0.85, dtype=np.float32),
      )
    if len(sp) > 0:
      frac = sp['life'] / sp['max_life']
      radii = 0.45 + (1.0 - frac) * 1.3  # sparks bloom outward as they fade
      amps = (frac ** 1.3) * 0.9
      self._stamp_gaussian(sp['x'], sp['y'], radii, amps)


# ──────────────────────────────────────────────────────────────────────
#  4. SRNegativeSnow
# ──────────────────────────────────────────────────────────────────────

_FLAKE_DTYPE = np.dtype([
  ('x', np.float32), ('y', np.float32),
  ('vy', np.float32), ('phase', np.float32),
  ('freq', np.float32), ('r', np.float32), ('amp', np.float32),
])

_MAX_FLAKES = 120


class SRNegativeSnow(_NegativeFieldEffect):
  """Slow dark flakes drift and sway downward — a gentle shadow snowfall."""

  DISPLAY_NAME = "SR Negative Snow"
  DESCRIPTION = "Soft dark flakes drift down through the light, swaying as they fall"

  PARAMS = [
    _P("Density", "density", 0.2, 3.0, 0.1, 1.0),
    _P("Fall Speed", "fall_speed", 1.0, 10.0, 0.5, 3.5),
    _P("Sway", "sway", 0.0, 3.0, 0.1, 1.2),
    *_common_params(fade=0.6, softness=1.0),
  ]

  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    self._flakes = np.empty(0, dtype=_FLAKE_DTYPE)
    self._spawn_accum = 0.0

  def _update(self, dt, elapsed, bass, mid, high, level):
    density = self._param('density', 1.0)
    fall_speed = self._param('fall_speed', 3.5)
    sway = self._param('sway', 1.2)

    rate = density * self.width * 0.35 * (0.7 + level * 0.6)
    self._spawn_accum += rate * dt
    count = int(self._spawn_accum)
    if count > 0 and len(self._flakes) < _MAX_FLAKES:
      self._spawn_accum -= count
      count = min(count, _MAX_FLAKES - len(self._flakes))
      new = np.empty(count, dtype=_FLAKE_DTYPE)
      new['x'] = np.random.uniform(0, self.width, count).astype(np.float32)
      new['y'] = np.random.uniform(-3, -0.5, count).astype(np.float32)
      new['vy'] = (np.random.uniform(0.6, 1.4, count) * fall_speed).astype(np.float32)
      new['phase'] = np.random.uniform(0, 2 * np.pi, count).astype(np.float32)
      new['freq'] = np.random.uniform(0.5, 1.8, count).astype(np.float32)
      new['r'] = np.random.uniform(0.5, 1.2, count).astype(np.float32)
      new['amp'] = np.random.uniform(0.5, 0.85, count).astype(np.float32)
      self._flakes = new if len(self._flakes) == 0 else np.concatenate([self._flakes, new])

    f = self._flakes
    if len(f) > 0:
      f['y'] += f['vy'] * (1.0 + bass * 0.5) * dt
      f['x'] += np.sin(elapsed * f['freq'] + f['phase']) * sway * 2.0 * dt
      self._flakes = f = f[f['y'] < self.height + 2]

    if len(f) > 0:
      self._stamp_gaussian(f['x'], f['y'], f['r'], f['amp'] * (0.8 + high * 0.3))


# ──────────────────────────────────────────────────────────────────────
#  5. SRNegativeComets
# ──────────────────────────────────────────────────────────────────────

_COMET_DTYPE = np.dtype([
  ('x', np.float32), ('y', np.float32),
  ('vx', np.float32), ('vy', np.float32),
  ('r', np.float32),
])


class SRNegativeComets(_NegativeFieldEffect):
  """Dark comets streak across the panel, trails melting behind them."""

  DISPLAY_NAME = "SR Negative Comets"
  DESCRIPTION = "Dark comets cross the light, their trails blurring and melting behind them"

  PARAMS = [
    _P("Comets", "comets", 1, 6, 1, 3),
    _P("Comet Speed", "comet_speed", 0.3, 3.0, 0.1, 1.0),
    *_common_params(fade=1.8, softness=1.6),
  ]

  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    self._comets = np.empty(0, dtype=_COMET_DTYPE)

  def _launch_one(self, speed_scale):
    base = max(self.width, self.height) * 0.18 * speed_scale
    speed = base * np.random.uniform(0.7, 1.3)
    # Tall panels look best with mostly vertical crossings
    side = np.random.choice(['top', 'bottom', 'left', 'right'], p=[0.3, 0.3, 0.2, 0.2])
    tilt = np.random.uniform(-0.6, 0.6)
    if side == 'top':
      x, y, vx, vy = np.random.uniform(0, self.width), -2.0, np.sin(tilt) * speed, np.cos(tilt) * speed
    elif side == 'bottom':
      x, y, vx, vy = np.random.uniform(0, self.width), self.height + 2.0, np.sin(tilt) * speed, -np.cos(tilt) * speed
    elif side == 'left':
      x, y, vx, vy = -2.0, np.random.uniform(0, self.height), np.cos(tilt) * speed, np.sin(tilt) * speed
    else:
      x, y, vx, vy = self.width + 2.0, np.random.uniform(0, self.height), -np.cos(tilt) * speed, np.sin(tilt) * speed
    new = np.array([(x, y, vx, vy, np.random.uniform(0.8, 1.4))], dtype=_COMET_DTYPE)
    self._comets = new if len(self._comets) == 0 else np.concatenate([self._comets, new])

  def _update(self, dt, elapsed, bass, mid, high, level):
    target = max(1, int(self._param('comets', 3)))
    speed_scale = self._param('comet_speed', 1.0) * (0.7 + level * 0.6)

    c = self._comets
    if len(c) > 0:
      c['x'] += c['vx'] * dt
      c['y'] += c['vy'] * dt
      margin = 4.0
      alive = ((c['x'] > -margin) & (c['x'] < self.width + margin) &
               (c['y'] > -margin) & (c['y'] < self.height + margin))
      self._comets = c = c[alive]

    while len(self._comets) < target:
      self._launch_one(speed_scale)
      c = self._comets

    self._stamp_gaussian(c['x'], c['y'], c['r'], np.full(len(c), 0.92, dtype=np.float32))


# ──────────────────────────────────────────────────────────────────────
#  6. SRNegativeVortex
# ──────────────────────────────────────────────────────────────────────

_VORTEX_PARTICLES = 90


class SRNegativeVortex(_NegativeFieldEffect):
  """Differential rotation drags dark particles into soft spiral arms."""

  DISPLAY_NAME = "SR Negative Vortex"
  DESCRIPTION = "A slow whirlpool of darkness — inner particles outrun outer ones into spiral arms"

  PARAMS = [
    _P("Spin", "spin", 0.2, 3.0, 0.1, 1.0),
    _P("Drift", "drift", 0.0, 2.0, 0.1, 0.6),
    *_common_params(fade=1.4, softness=1.5),
  ]

  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    self._angle = np.random.uniform(0, 2 * np.pi, _VORTEX_PARTICLES).astype(np.float32)
    self._nr = np.random.uniform(0.06, 0.5, _VORTEX_PARTICLES).astype(np.float32)

  def _update(self, dt, elapsed, bass, mid, high, level):
    spin = self._param('spin', 1.0) * (0.6 + bass * 0.8)
    drift = self._param('drift', 0.6)

    # Differential rotation: inner radii spin faster — that's what draws the arms
    omega = spin * 1.6 / (0.25 + self._nr)
    self._angle += omega * dt
    self._nr += drift * 0.02 * dt * (0.5 + mid)
    escaped = self._nr > 0.52
    self._nr[escaped] = np.random.uniform(0.05, 0.1, int(escaped.sum())).astype(np.float32)

    px = self.width * 0.5 + np.cos(self._angle) * self._nr * self.width * 0.95
    py = self.height * 0.5 + np.sin(self._angle) * self._nr * self.height * 0.95
    radii = np.full(_VORTEX_PARTICLES, 0.7, dtype=np.float32) + self._nr * 0.8
    amps = np.full(_VORTEX_PARTICLES, 0.8, dtype=np.float32)
    self._stamp_gaussian(px.astype(np.float32), py.astype(np.float32), radii, amps)


# ──────────────────────────────────────────────────────────────────────
#  7. SRNegativeAurora
# ──────────────────────────────────────────────────────────────────────

class SRNegativeAurora(_NegativeFieldEffect):
  """Wavering dark curtains drift across the light — an aurora in negative."""

  DISPLAY_NAME = "SR Negative Aurora"
  DESCRIPTION = "Dark curtains waver and drift like an aurora made of shadow"

  PARAMS = [
    _P("Wave Speed", "wave_speed", 0.2, 3.0, 0.1, 1.0),
    _P("Curtains", "curtains", 1, 4, 1, 3),
    _P("Curtain Width", "curtain_width", 0.6, 3.0, 0.1, 1.4),
    *_common_params(fade=0.5, softness=1.2),
  ]

  def _update(self, dt, elapsed, bass, mid, high, level):
    speed = self._param('wave_speed', 1.0)
    curtains = max(1, int(self._param('curtains', 3)))
    cw = self._param('curtain_width', 1.4) * (1.0 + mid * 0.5)

    for i in range(curtains):
      phase = i * 2.39996  # golden-angle offsets keep curtains from syncing
      sway = np.sin(self._gy * 0.10 + elapsed * speed * (0.5 + 0.2 * i) + phase)
      wob = _noise_2d(self._gy * 0.07 + phase, np.full_like(self._gy, elapsed * 0.25))
      cx = self.width * (0.5 + 0.30 * sway * 0.6 + 0.15 * wob * 0.3)
      dx = self._gx - cx
      band = np.exp(-dx * dx / (2.0 * cw * cw))
      envelope = 0.55 + 0.45 * np.sin(self._gy * 0.05 + elapsed * 0.8 * speed + phase * 2)
      contrib = (band * envelope * (0.6 + bass * 0.3)).astype(np.float32)
      np.maximum(self._dark, contrib, out=self._dark)


# ──────────────────────────────────────────────────────────────────────
#  8. SRNegativeBubbles
# ──────────────────────────────────────────────────────────────────────

_BUBBLE_DTYPE = np.dtype([
  ('x', np.float32), ('y', np.float32),
  ('vy', np.float32), ('phase', np.float32),
  ('r', np.float32), ('pop_y', np.float32),
])

_MAX_BUBBLES = 40


class SRNegativeBubbles(_NegativeFieldEffect):
  """Dark bubble rings rise, wobble, and dissolve into soft shadow puffs."""

  DISPLAY_NAME = "SR Negative Bubbles"
  DESCRIPTION = "Dark bubble rings wobble upward and pop into soft dissolving shadows"

  PARAMS = [
    _P("Bubble Rate", "bubble_rate", 0.5, 6.0, 0.5, 2.0),
    _P("Rise Speed", "rise_speed", 1.0, 10.0, 0.5, 4.0),
    _P("Bubble Size", "bubble_size", 0.5, 3.0, 0.1, 1.5),
    *_common_params(fade=0.7, softness=1.4),
  ]

  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    self._bubbles = np.empty(0, dtype=_BUBBLE_DTYPE)
    self._spawn_accum = 0.0

  def _update(self, dt, elapsed, bass, mid, high, level):
    rate = self._param('bubble_rate', 2.0) * (0.6 + bass * 0.8)
    rise = self._param('rise_speed', 4.0)
    size = self._param('bubble_size', 1.5)

    self._spawn_accum += rate * dt
    count = int(self._spawn_accum)
    if count > 0 and len(self._bubbles) < _MAX_BUBBLES:
      self._spawn_accum -= count
      count = min(count, _MAX_BUBBLES - len(self._bubbles))
      new = np.empty(count, dtype=_BUBBLE_DTYPE)
      new['x'] = np.random.uniform(1, self.width - 1, count).astype(np.float32)
      new['y'] = np.float32(self.height + 2)
      new['vy'] = (-np.random.uniform(0.7, 1.3, count) * rise).astype(np.float32)
      new['phase'] = np.random.uniform(0, 2 * np.pi, count).astype(np.float32)
      new['r'] = (np.random.uniform(0.6, 1.3, count) * size).astype(np.float32)
      new['pop_y'] = (np.random.uniform(0.10, 0.40, count) * self.height).astype(np.float32)
      self._bubbles = new if len(self._bubbles) == 0 else np.concatenate([self._bubbles, new])

    b = self._bubbles
    if len(b) > 0:
      b['y'] += b['vy'] * (1.0 + level * 0.4) * dt
      b['x'] += np.sin(elapsed * 2.2 + b['phase']) * 1.5 * dt
      b['r'] += 0.15 * dt  # bubbles swell slightly as they rise
      popped = b['y'] <= b['pop_y']
      if popped.any():
        # A pop leaves one soft dark puff that the fade/blur dissolves
        pb = b[popped]
        self._stamp_gaussian(pb['x'], pb['y'], pb['r'] * 1.5, np.full(len(pb), 0.55, dtype=np.float32))
      self._bubbles = b = b[~popped]

    if len(b) > 0:
      widths = np.full(len(b), 0.6, dtype=np.float32)
      amps = np.full(len(b), 0.8, dtype=np.float32)
      self._stamp_ring(b['x'], b['y'], b['r'], widths, amps)


# ──────────────────────────────────────────────────────────────────────
#  9. SRNegativeWaves
# ──────────────────────────────────────────────────────────────────────

class SRNegativeWaves(_NegativeFieldEffect):
  """Rolling dark swells pass over the panel like ocean waves of shadow."""

  DISPLAY_NAME = "SR Negative Waves"
  DESCRIPTION = "Soft dark swells roll across the light like slow ocean waves"

  PARAMS = [
    _P("Wave Speed", "wave_speed", 0.2, 3.0, 0.1, 1.0),
    _P("Waves", "waves", 1, 5, 1, 3),
    _P("Thickness", "thickness", 1.0, 6.0, 0.5, 2.5),
    *_common_params(fade=0.5, softness=1.2),
  ]

  def _update(self, dt, elapsed, bass, mid, high, level):
    speed = self._param('wave_speed', 1.0)
    nwaves = max(1, int(self._param('waves', 3)))
    thick = self._param('thickness', 2.5) * (1.0 + level * 0.6)

    pad = 12.0
    span = self.height + 2 * pad
    spacing = span / nwaves
    for k in range(nwaves):
      ridge = (elapsed * speed * self.height * 0.12 + k * spacing) % span - pad
      # Each swell's crest line wobbles across x so it reads organic, not scanline
      wobble = np.sin(self._gx * 0.6 + elapsed * 1.3 + k * 2.1) * 1.5
      dy = self._gy - (ridge + wobble)
      contrib = (np.exp(-dy * dy / (2.0 * thick * thick)) * (0.65 + bass * 0.25)).astype(np.float32)
      np.maximum(self._dark, contrib, out=self._dark)


# ──────────────────────────────────────────────────────────────────────
#  10. SRNegativeOrbits
# ──────────────────────────────────────────────────────────────────────

_MAX_BODIES = 8


class SRNegativeOrbits(_NegativeFieldEffect):
  """Dark bodies glide on precessing elliptical orbits, trails melting behind."""

  DISPLAY_NAME = "SR Negative Orbits"
  DESCRIPTION = "Dark bodies trace slow precessing orbits, their trails softening into shadow"

  PARAMS = [
    _P("Bodies", "bodies", 2, _MAX_BODIES, 1, 4),
    _P("Orbit Speed", "orbit_speed", 0.2, 3.0, 0.1, 1.0),
    *_common_params(fade=1.6, softness=1.5),
  ]

  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    fracs = np.linspace(0.35, 1.0, _MAX_BODIES).astype(np.float32)
    self._semi_a = width * 0.42 * fracs
    self._semi_b = height * 0.42 * fracs
    self._period = np.random.uniform(4.0, 10.0, _MAX_BODIES).astype(np.float32)
    self._phase = np.random.uniform(0, 2 * np.pi, _MAX_BODIES).astype(np.float32)
    self._prec = np.random.uniform(0.03, 0.09, _MAX_BODIES).astype(np.float32)
    self._r0 = np.random.uniform(0.7, 1.2, _MAX_BODIES).astype(np.float32)

  def _update(self, dt, elapsed, bass, mid, high, level):
    n = int(np.clip(self._param('bodies', 4), 2, _MAX_BODIES))
    speed = self._param('orbit_speed', 1.0)

    theta = elapsed * speed * 2 * np.pi / self._period[:n] + self._phase[:n]
    rot = elapsed * self._prec[:n]  # slow precession keeps the paths from repeating
    ox = self._semi_a[:n] * np.cos(theta)
    oy = self._semi_b[:n] * np.sin(theta)
    px = self.width * 0.5 + ox * np.cos(rot) - oy * np.sin(rot)
    py = self.height * 0.5 + ox * np.sin(rot) + oy * np.cos(rot)

    radii = self._r0[:n] * (1.0 + bass * 0.5)
    amps = np.full(n, 0.88, dtype=np.float32)
    self._stamp_gaussian(px.astype(np.float32), py.astype(np.float32), radii.astype(np.float32), amps)


# ─── Registry ─────────────────────────────────────────────────────

NEGATIVE_SPINOFF_EFFECTS = {
  'sr_negative_jets': SRNegativeJets,
  'sr_negative_flow': SRNegativeFlow,
  'sr_negative_fireworks': SRNegativeFireworks,
  'sr_negative_snow': SRNegativeSnow,
  'sr_negative_comets': SRNegativeComets,
  'sr_negative_vortex': SRNegativeVortex,
  'sr_negative_aurora': SRNegativeAurora,
  'sr_negative_bubbles': SRNegativeBubbles,
  'sr_negative_waves': SRNegativeWaves,
  'sr_negative_orbits': SRNegativeOrbits,
}
