"""
Numerically intensive simulation effects — fully vectorized with NumPy.

These effects showcase real physics simulations running in real-time:
- Navier-Stokes fluid dynamics (Jos Stam's stable fluids)
- Gray-Scott reaction-diffusion
- 2D wave equation
- Boids flocking simulation
"""

import math
import numpy as np
from .base import Effect


# ──────────────────────────────────────────────────────────────────────
#  Navier-Stokes Fluid Simulation
# ──────────────────────────────────────────────────────────────────────

class FluidSim(Effect):
    """Real-time Navier-Stokes fluid dynamics with audio-reactive forcing.

    Implements Jos Stam's "Stable Fluids" (1999) — unconditionally stable
    semi-Lagrangian advection with Gauss-Seidel pressure solve.
    Bass drives upward force, beats inject dye bursts.
    """

    CATEGORY = "simulation"
    DISPLAY_NAME = "Fluid Dynamics"
    DESCRIPTION = "Navier-Stokes fluid simulation — audio drives the flow"
    PALETTE_SUPPORT = False
    AUDIO_REQUIRES = ('level', 'bass', 'mid', 'high', 'beat')

    PARAMS = [
        type('P', (), {'label': 'Viscosity', 'attr': 'viscosity', 'lo': 0.0, 'hi': 0.001,
                        'step': 0.00005, 'default': 0.0})(),
        type('P', (), {'label': 'Diffusion', 'attr': 'diffusion', 'lo': 0.0, 'hi': 0.001,
                        'step': 0.00005, 'default': 0.0})(),
        type('P', (), {'label': 'Force', 'attr': 'force', 'lo': 1.0, 'hi': 20.0,
                        'step': 0.5, 'default': 8.0})(),
        type('P', (), {'label': 'Dye Rate', 'attr': 'dye_rate', 'lo': 0.5, 'hi': 5.0,
                        'step': 0.1, 'default': 2.0})(),
        type('P', (), {'label': 'Pressure Iters', 'attr': 'pressure_iters', 'lo': 1, 'hi': 20,
                        'step': 1, 'default': 2})(),
    ]

    def __init__(self, width, height, params=None):
        super().__init__(width, height, params)
        # Fluid grids — padded by 1 on each side for boundary handling
        w, h = width + 2, height + 2
        self._vx = np.zeros((w, h), dtype=np.float32)
        self._vy = np.zeros((w, h), dtype=np.float32)
        self._dye = np.zeros((w, h, 3), dtype=np.float32)  # RGB batched
        # Pre-allocated scratch buffers (avoid alloc per frame)
        self._tmp1 = np.zeros((w, h), dtype=np.float32)
        self._tmp2 = np.zeros((w, h), dtype=np.float32)
        self._dye_tmp = np.zeros((w, h, 3), dtype=np.float32)
        self._div = np.zeros((w, h), dtype=np.float32)
        self._p = np.zeros((w, h), dtype=np.float32)
        # Pre-computed grid coords for advection
        self._grid_j = np.arange(1, w - 1, dtype=np.float32)[:, np.newaxis]
        self._grid_i = np.arange(1, h - 1, dtype=np.float32)[np.newaxis, :]
        self._last_t = None
        self._hue_phase = 0.0
        self._auto_inject_timer = 0.0

    def _set_boundary(self, b, x):
        """Set boundary conditions (b=0: scalar, b=1: x-vel, b=2: y-vel)."""
        if b == 1:
            x[0, :] = -x[1, :]
            x[-1, :] = -x[-2, :]
        else:
            x[0, :] = x[1, :]
            x[-1, :] = x[-2, :]
        if b == 2:
            x[:, 0] = -x[:, 1]
            x[:, -1] = -x[:, -2]
        else:
            x[:, 0] = x[:, 1]
            x[:, -1] = x[:, -2]
        x[0, 0] = 0.5 * (x[1, 0] + x[0, 1])
        x[-1, 0] = 0.5 * (x[-2, 0] + x[-1, 1])
        x[0, -1] = 0.5 * (x[1, -1] + x[0, -2])
        x[-1, -1] = 0.5 * (x[-2, -1] + x[-1, -2])

    def _diffuse(self, b, x, x0, diff, dt):
        """Gauss-Seidel diffusion solve."""
        if diff <= 0:
            x[:] = x0
            return
        w, h = x.shape
        a = dt * diff * (w - 2) * (h - 2)
        iters = int(self.params.get('pressure_iters', 8))
        for _ in range(iters):
            x[1:-1, 1:-1] = (x0[1:-1, 1:-1] + a * (
                x[:-2, 1:-1] + x[2:, 1:-1] +
                x[1:-1, :-2] + x[1:-1, 2:]
            )) / (1 + 4 * a)
        self._set_boundary(b, x)

    def _compute_advect_coords(self, vx, vy, dt):
        """Compute advection source coordinates (shared by 2D and 3D advect)."""
        w, h = vx.shape
        iw, ih = w - 2, h - 2
        x = self._grid_j - dt * iw * vx[1:-1, 1:-1]
        y = self._grid_i - dt * ih * vy[1:-1, 1:-1]
        x = np.clip(x, 0.5, iw + 0.5)
        y = np.clip(y, 0.5, ih + 0.5)
        i0 = x.astype(np.int32)
        j0 = y.astype(np.int32)
        return i0, j0, i0 + 1, j0 + 1, x - i0, y - j0

    def _advect(self, b, d, d0, vx, vy, dt):
        """Semi-Lagrangian advection for a 2D field."""
        i0, j0, i1, j1, sx, sy = self._compute_advect_coords(vx, vy, dt)
        d[1:-1, 1:-1] = (
            (1 - sx) * ((1 - sy) * d0[i0, j0] + sy * d0[i0, j1]) +
            sx * ((1 - sy) * d0[i1, j0] + sy * d0[i1, j1])
        )
        self._set_boundary(b, d)

    def _advect_3d(self, d, d0, vx, vy, dt):
        """Semi-Lagrangian advection for a 3D field (batched RGB)."""
        i0, j0, i1, j1, sx, sy = self._compute_advect_coords(vx, vy, dt)
        # Broadcast sx/sy to (..., 1) for 3-channel multiply
        sx3 = sx[:, :, np.newaxis]
        sy3 = sy[:, :, np.newaxis]
        d[1:-1, 1:-1] = (
            (1 - sx3) * ((1 - sy3) * d0[i0, j0] + sy3 * d0[i0, j1]) +
            sx3 * ((1 - sy3) * d0[i1, j0] + sy3 * d0[i1, j1])
        )
        # Scalar boundary for each channel
        d[0, :] = d[1, :]
        d[-1, :] = d[-2, :]
        d[:, 0] = d[:, 1]
        d[:, -1] = d[:, -2]

    def _project(self, vx, vy):
        """Pressure projection — make velocity field divergence-free."""
        w, h = vx.shape
        div = self._div
        p = self._p
        div[:] = 0
        p[:] = 0

        div[1:-1, 1:-1] = -0.5 * (
            vx[2:, 1:-1] - vx[:-2, 1:-1] +
            vy[1:-1, 2:] - vy[1:-1, :-2]
        ) / max(w - 2, 1)
        self._set_boundary(0, div)

        iters = int(self.params.get('pressure_iters', 4))
        for _ in range(iters):
            p[1:-1, 1:-1] = (div[1:-1, 1:-1] +
                p[:-2, 1:-1] + p[2:, 1:-1] +
                p[1:-1, :-2] + p[1:-1, 2:]) * 0.25
        self._set_boundary(0, p)

        vx[1:-1, 1:-1] -= 0.5 * (w - 2) * (p[2:, 1:-1] - p[:-2, 1:-1])
        vy[1:-1, 1:-1] -= 0.5 * (h - 2) * (p[1:-1, 2:] - p[1:-1, :-2])
        self._set_boundary(1, vx)
        self._set_boundary(2, vy)

    def render(self, t: float, state) -> np.ndarray:
        if self._last_t is None:
            self._last_t = t
        dt = min(t - self._last_t, 0.05)
        self._last_t = t

        visc = self.params.get('viscosity', 0.0)
        diff = self.params.get('diffusion', 0.0)
        force = self.params.get('force', 8.0)
        dye_rate = self.params.get('dye_rate', 2.0)

        bass = state.audio_bass
        mid = state.audio_mid
        high = state.audio_high
        beat = state.audio_beat
        level = state.audio_level

        w, h = self.width, self.height
        self._hue_phase += dt * 0.3

        # Audio-reactive forcing: bass pushes up from bottom, mid from sides
        cx = w // 2 + 1
        bottom = h
        force_scale = force * (0.5 + level * 2)

        # Continuous upward force from bass (bottom center)
        if bass > 0.1:
            region_x = slice(max(1, cx - 2), min(cx + 3, w + 1))
            region_y = slice(max(1, bottom - 3), bottom + 1)
            self._vy[region_x, region_y] -= bass * force_scale * 3
            hue = self._hue_phase % 1.0
            r, g, b = self._hsv_fast(hue, 1.0, bass)
            self._dye[region_x, region_y] += np.array([r, g, b], dtype=np.float32) * dye_rate

        # Beat burst — inject from random position
        if beat:
            bx = np.random.randint(2, w)
            by = np.random.randint(h // 3, h)
            r_burst = slice(max(1, bx - 1), min(bx + 2, w + 1))
            c_burst = slice(max(1, by - 1), min(by + 2, h + 1))
            angle = np.random.uniform(0, 2 * math.pi)
            self._vx[r_burst, c_burst] += math.cos(angle) * force_scale * 5
            self._vy[r_burst, c_burst] += math.sin(angle) * force_scale * 5
            hue2 = np.random.random()
            r, g, b = self._hsv_fast(hue2, 1.0, 1.0)
            self._dye[r_burst, c_burst] += np.array([r, g, b], dtype=np.float32) * dye_rate * 1.5

        # Mid energy — side injection
        if mid > 0.2:
            side_y = h // 2 + 1
            self._vx[1, side_y - 1:side_y + 2] += mid * force_scale * 2
            self._vx[-2, side_y - 1:side_y + 2] -= mid * force_scale * 2
            hue3 = (self._hue_phase + 0.33) % 1.0
            r, g, b = self._hsv_fast(hue3, 0.8, mid)
            self._dye[1, side_y - 1:side_y + 2] += np.array([r, g, b], dtype=np.float32) * dye_rate * 0.5

        # Auto-injection: always keep fluid alive even without audio
        self._auto_inject_timer -= dt
        if self._auto_inject_timer <= 0:
            self._auto_inject_timer = np.random.uniform(0.3, 1.0)
            # Inject from bottom with upward force
            ix = np.random.randint(1, w + 1)
            region_x = slice(max(1, ix - 1), min(ix + 2, w + 1))
            region_y = slice(max(1, bottom - 2), bottom + 1)
            self._vy[region_x, region_y] -= force * 2
            hue_auto = self._hue_phase % 1.0
            r, g, b = self._hsv_fast(hue_auto, 1.0, 0.8)
            self._dye[region_x, region_y] += np.array([r, g, b], dtype=np.float32) * dye_rate
            # Also inject from sides occasionally
            if np.random.random() < 0.4:
                side = 1 if np.random.random() < 0.5 else w
                sy = np.random.randint(h // 4 + 1, 3 * h // 4 + 1)
                region_sy = slice(max(1, sy - 1), min(sy + 2, h + 1))
                direction = 1.0 if side == 1 else -1.0
                self._vx[side, region_sy] += direction * force * 1.5
                hue_side = (self._hue_phase + 0.5) % 1.0
                r, g, b = self._hsv_fast(hue_side, 0.9, 0.7)
                self._dye[side, region_sy] += np.array([r, g, b], dtype=np.float32) * dye_rate * 0.8

        # Velocity step: diffuse (if needed) → advect → project
        tmp1, tmp2 = self._tmp1, self._tmp2
        if visc > 0.00001:
            tmp1[:] = self._vx
            tmp2[:] = self._vy
            self._diffuse(1, self._vx, tmp1, visc, dt)
            self._diffuse(2, self._vy, tmp2, visc, dt)
            self._project(self._vx, self._vy)

        tmp1[:] = self._vx
        tmp2[:] = self._vy
        self._advect(1, self._vx, tmp1, tmp1, tmp2, dt)
        self._advect(2, self._vy, tmp2, tmp1, tmp2, dt)
        self._project(self._vx, self._vy)

        # Dye step: batched 3-channel advection (single call instead of 3)
        self._dye_tmp[:] = self._dye
        self._advect_3d(self._dye, self._dye_tmp, self._vx, self._vy, dt)

        # Gentle decay
        self._dye *= 0.995

        # Extract interior and render
        frame = np.clip(self._dye[1:-1, 1:-1] * 255, 0, 255).astype(np.uint8)
        return frame

    @staticmethod
    def _hsv_fast(h, s, v):
        h = h % 1.0
        i = int(h * 6)
        f = h * 6 - i
        p = v * (1 - s)
        q = v * (1 - s * f)
        t = v * (1 - s * (1 - f))
        if i == 0: return v, t, p
        if i == 1: return q, v, p
        if i == 2: return p, v, t
        if i == 3: return p, q, v
        if i == 4: return t, p, v
        return v, p, q


# ──────────────────────────────────────────────────────────────────────
#  Gray-Scott Reaction-Diffusion
# ──────────────────────────────────────────────────────────────────────

class ReactionDiffusion(Effect):
    """Gray-Scott reaction-diffusion — organic growing patterns.

    Two chemicals (U and V) interact: U + 2V → 3V, V → inert.
    Feed rate and kill rate control the pattern morphology.
    Audio modulates these rates for living, breathing patterns.
    """

    CATEGORY = "simulation"
    DISPLAY_NAME = "Reaction Diffusion"
    DESCRIPTION = "Gray-Scott model — organic coral-like patterns that grow and breathe"
    PALETTE_SUPPORT = False

    PARAMS = [
        type('P', (), {'label': 'Feed Rate', 'attr': 'feed', 'lo': 0.01, 'hi': 0.08,
                        'step': 0.001, 'default': 0.037})(),
        type('P', (), {'label': 'Kill Rate', 'attr': 'kill', 'lo': 0.04, 'hi': 0.075,
                        'step': 0.001, 'default': 0.06})(),
        type('P', (), {'label': 'Speed', 'attr': 'speed', 'lo': 1, 'hi': 20,
                        'step': 1, 'default': 8})(),
        type('P', (), {'label': 'Color Cycle', 'attr': 'color_speed', 'lo': 0.0, 'hi': 1.0,
                        'step': 0.05, 'default': 0.2})(),
    ]

    def __init__(self, width, height, params=None):
        super().__init__(width, height, params)
        # Chemical concentrations
        self._u = np.ones((width, height), dtype=np.float32)
        self._v = np.zeros((width, height), dtype=np.float32)
        # Seed initial perturbation — a few spots of V
        for _ in range(5):
            cx = np.random.randint(1, width - 1)
            cy = np.random.randint(1, height - 1)
            r = max(1, min(width, height) // 6)
            x_lo = max(0, cx - r)
            x_hi = min(width, cx + r + 1)
            y_lo = max(0, cy - r)
            y_hi = min(height, cy + r + 1)
            self._v[x_lo:x_hi, y_lo:y_hi] = 1.0
            self._u[x_lo:x_hi, y_lo:y_hi] = 0.5
        self._last_t = None

    def _laplacian(self, x):
        """5-point Laplacian stencil with wrap boundary."""
        return (
            np.roll(x, 1, axis=0) + np.roll(x, -1, axis=0) +
            np.roll(x, 1, axis=1) + np.roll(x, -1, axis=1) -
            4 * x
        )

    def render(self, t: float, state) -> np.ndarray:
        if self._last_t is None:
            self._last_t = t
        dt_real = min(t - self._last_t, 0.05)
        self._last_t = t

        feed = self.params.get('feed', 0.037)
        kill = self.params.get('kill', 0.06)
        speed = int(self.params.get('speed', 8))
        color_speed = self.params.get('color_speed', 0.2)

        # Audio modulation — subtle feed/kill perturbation
        bass = state.audio_bass
        mid = state.audio_mid
        feed_mod = feed + bass * 0.01
        kill_mod = kill + mid * 0.005

        # Beat: inject new seed
        if state.audio_beat:
            cx = np.random.randint(1, self.width - 1)
            cy = np.random.randint(1, self.height - 1)
            r = max(1, min(self.width, self.height) // 8)
            x_lo = max(0, cx - r)
            x_hi = min(self.width, cx + r + 1)
            y_lo = max(0, cy - r)
            y_hi = min(self.height, cy + r + 1)
            self._v[x_lo:x_hi, y_lo:y_hi] = 1.0
            self._u[x_lo:x_hi, y_lo:y_hi] = 0.5

        # Simulation steps (multiple per frame for visible evolution)
        du = 0.21  # diffusion rate of U
        dv = 0.105  # diffusion rate of V
        dt_sim = 1.0  # simulation timestep

        for _ in range(speed):
            lu = self._laplacian(self._u)
            lv = self._laplacian(self._v)
            uvv = self._u * self._v * self._v
            self._u += dt_sim * (du * lu - uvv + feed_mod * (1 - self._u))
            self._v += dt_sim * (dv * lv + uvv - (feed_mod + kill_mod) * self._v)

        self._u = np.clip(self._u, 0, 1)
        self._v = np.clip(self._v, 0, 1)

        # Colorize — V concentration drives hue, U drives brightness
        elapsed = self.elapsed(t)
        hue_offset = elapsed * color_speed
        v_norm = self._v
        hue = (v_norm * 0.7 + hue_offset) % 1.0
        sat = np.clip(v_norm * 3, 0, 1)
        val = np.clip(v_norm * 2.5, 0, 1)

        # Vectorized HSV to RGB
        frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)
        h6 = hue * 6.0
        i = h6.astype(np.int32) % 6
        f = h6 - h6.astype(np.int32)
        p = val * (1 - sat)
        q = val * (1 - sat * f)
        t_val = val * (1 - sat * (1 - f))

        # Assign RGB based on hue sector
        for sector in range(6):
            mask = i == sector
            if sector == 0:
                frame[mask, 0] = (val[mask] * 255).astype(np.uint8)
                frame[mask, 1] = (t_val[mask] * 255).astype(np.uint8)
                frame[mask, 2] = (p[mask] * 255).astype(np.uint8)
            elif sector == 1:
                frame[mask, 0] = (q[mask] * 255).astype(np.uint8)
                frame[mask, 1] = (val[mask] * 255).astype(np.uint8)
                frame[mask, 2] = (p[mask] * 255).astype(np.uint8)
            elif sector == 2:
                frame[mask, 0] = (p[mask] * 255).astype(np.uint8)
                frame[mask, 1] = (val[mask] * 255).astype(np.uint8)
                frame[mask, 2] = (t_val[mask] * 255).astype(np.uint8)
            elif sector == 3:
                frame[mask, 0] = (p[mask] * 255).astype(np.uint8)
                frame[mask, 1] = (q[mask] * 255).astype(np.uint8)
                frame[mask, 2] = (val[mask] * 255).astype(np.uint8)
            elif sector == 4:
                frame[mask, 0] = (t_val[mask] * 255).astype(np.uint8)
                frame[mask, 1] = (p[mask] * 255).astype(np.uint8)
                frame[mask, 2] = (val[mask] * 255).astype(np.uint8)
            else:
                frame[mask, 0] = (val[mask] * 255).astype(np.uint8)
                frame[mask, 1] = (p[mask] * 255).astype(np.uint8)
                frame[mask, 2] = (q[mask] * 255).astype(np.uint8)

        return frame


# ──────────────────────────────────────────────────────────────────────
#  2D Wave Equation
# ──────────────────────────────────────────────────────────────────────

class WaveEquation(Effect):
    """2D wave equation simulation — audio beats create ripples.

    Numerov-style finite difference with damping. Beats drop stones,
    bass energy creates continuous disturbance. Cylindrical wrap on x-axis.
    """

    CATEGORY = "simulation"
    DISPLAY_NAME = "Wave Equation"
    DESCRIPTION = "2D wave simulation — beats create ripples that interfere and decay"
    PALETTE_SUPPORT = False
    AUDIO_REQUIRES = ('level', 'bass', 'beat')

    PARAMS = [
        type('P', (), {'label': 'Wave Speed', 'attr': 'wave_speed', 'lo': 0.02, 'hi': 1.0,
                        'step': 0.02, 'default': 0.12})(),
        type('P', (), {'label': 'Damping', 'attr': 'damping', 'lo': 0.9, 'hi': 0.999,
                        'step': 0.001, 'default': 0.985})(),
        type('P', (), {'label': 'Color Cycle', 'attr': 'color_speed', 'lo': 0.0, 'hi': 1.0,
                        'step': 0.05, 'default': 0.3})(),
    ]

    def __init__(self, width, height, params=None):
        super().__init__(width, height, params)
        self._u = np.zeros((width, height), dtype=np.float32)  # current
        self._u_prev = np.zeros((width, height), dtype=np.float32)  # previous
        self._last_t = None
        self._auto_drop_timer = 0.0

    def render(self, t: float, state) -> np.ndarray:
        if self._last_t is None:
            self._last_t = t
        dt = min(t - self._last_t, 0.05)
        self._last_t = t

        c = self.params.get('wave_speed', 0.5)
        damping = self.params.get('damping', 0.985)
        color_speed = self.params.get('color_speed', 0.3)

        w, h = self.width, self.height
        bass = state.audio_bass
        beat = state.audio_beat
        level = state.audio_level

        # Beat: drop a stone at random position
        if beat:
            cx = np.random.randint(0, w)
            cy = np.random.randint(h // 4, 3 * h // 4)
            amplitude = 1.0 + level * 2
            # Gaussian impulse
            x_grid = np.arange(w)[:, np.newaxis]
            y_grid = np.arange(h)[np.newaxis, :]
            dist2 = ((x_grid - cx) % w) ** 2 + (y_grid - cy) ** 2
            # Wrap distance on x-axis (cylindrical)
            dist2_wrap = np.minimum(dist2, ((x_grid - cx + w) % w) ** 2 + (y_grid - cy) ** 2)
            impulse = amplitude * np.exp(-dist2_wrap / 3.0).astype(np.float32)
            self._u += impulse

        # Auto-drop for non-audio mode
        self._auto_drop_timer -= dt
        if self._auto_drop_timer <= 0 and level < 0.05:
            self._auto_drop_timer = np.random.uniform(0.5, 2.0)
            cx = np.random.randint(0, w)
            cy = np.random.randint(h // 4, 3 * h // 4)
            x_grid = np.arange(w)[:, np.newaxis]
            y_grid = np.arange(h)[np.newaxis, :]
            dist2 = ((x_grid - cx) % w) ** 2 + (y_grid - cy) ** 2
            impulse = 0.8 * np.exp(-dist2 / 3.0).astype(np.float32)
            self._u += impulse

        # Bass: continuous bottom perturbation
        if bass > 0.15:
            self._u[:, -3:] += bass * 0.3

        # Wave equation: u_next = 2*u - u_prev + c^2 * laplacian(u)
        # Cylindrical boundary on x (wrap), reflective on y
        laplacian = (
            np.roll(self._u, 1, axis=0) + np.roll(self._u, -1, axis=0) +  # x wraps
            np.concatenate([self._u[:, :1], self._u[:, :-1]], axis=1) +     # y reflect bottom
            np.concatenate([self._u[:, 1:], self._u[:, -1:]], axis=1) -     # y reflect top
            4 * self._u
        )

        u_next = 2 * self._u - self._u_prev + c * c * laplacian
        u_next *= damping

        self._u_prev = self._u
        self._u = u_next

        # Colorize — amplitude drives color
        elapsed = self.elapsed(t)
        amplitude = self._u

        # Map amplitude to hue (positive = warm, negative = cool)
        hue_base = elapsed * color_speed
        pos_mask = amplitude > 0
        neg_mask = amplitude < 0

        frame = np.zeros((w, h, 3), dtype=np.float32)
        abs_amp = np.abs(amplitude)
        brightness = np.clip(abs_amp * 3, 0, 1)

        # Positive amplitude: red-yellow
        frame[pos_mask, 0] = brightness[pos_mask]
        frame[pos_mask, 1] = (brightness[pos_mask] * 0.4)
        frame[pos_mask, 2] = (brightness[pos_mask] * 0.05)

        # Negative amplitude: blue-cyan
        frame[neg_mask, 0] = (brightness[neg_mask] * 0.05)
        frame[neg_mask, 1] = (brightness[neg_mask] * 0.3)
        frame[neg_mask, 2] = brightness[neg_mask]

        # Hue rotation over time
        cos_h = math.cos(hue_base * 2 * math.pi)
        sin_h = math.sin(hue_base * 2 * math.pi)
        r = frame[:, :, 0] * cos_h - frame[:, :, 2] * sin_h
        b = frame[:, :, 0] * sin_h + frame[:, :, 2] * cos_h
        frame[:, :, 0] = np.abs(r)
        frame[:, :, 2] = np.abs(b)

        return np.clip(frame * 255, 0, 255).astype(np.uint8)


# ──────────────────────────────────────────────────────────────────────
#  Boids Flocking Simulation
# ──────────────────────────────────────────────────────────────────────

class Boids(Effect):
    """Craig Reynolds' boids — emergent flocking behavior.

    Separation, alignment, and cohesion rules create lifelike swarm
    motion. Fully vectorized with NumPy distance matrices.
    Audio level controls speed, beats scatter the flock.
    """

    CATEGORY = "simulation"
    DISPLAY_NAME = "Boids Flock"
    DESCRIPTION = "Emergent flocking — separation, alignment, cohesion create a living swarm"
    PALETTE_SUPPORT = False

    PARAMS = [
        type('P', (), {'label': 'Count', 'attr': 'count', 'lo': 20, 'hi': 200,
                        'step': 10, 'default': 80})(),
        type('P', (), {'label': 'Speed', 'attr': 'speed', 'lo': 5.0, 'hi': 60.0,
                        'step': 1.0, 'default': 25.0})(),
        type('P', (), {'label': 'Trail', 'attr': 'trail', 'lo': 0.0, 'hi': 0.95,
                        'step': 0.05, 'default': 0.7})(),
        type('P', (), {'label': 'Color Cycle', 'attr': 'color_speed', 'lo': 0.0, 'hi': 1.0,
                        'step': 0.05, 'default': 0.2})(),
    ]

    def __init__(self, width, height, params=None):
        super().__init__(width, height, params)
        n = int(self.params.get('count', 80))
        self._n = n
        self._x = np.random.uniform(0, width, n).astype(np.float32)
        self._y = np.random.uniform(0, height, n).astype(np.float32)
        self._vx = np.random.uniform(-1, 1, n).astype(np.float32)
        self._vy = np.random.uniform(-1, 1, n).astype(np.float32)
        self._last_t = None
        self._prev_frame = None

    def update_params(self, params: dict):
        super().update_params(params)
        new_n = int(self.params.get('count', 80))
        if new_n != self._n:
            # Resize arrays
            if new_n > self._n:
                extra = new_n - self._n
                self._x = np.concatenate([self._x, np.random.uniform(0, self.width, extra).astype(np.float32)])
                self._y = np.concatenate([self._y, np.random.uniform(0, self.height, extra).astype(np.float32)])
                self._vx = np.concatenate([self._vx, np.random.uniform(-1, 1, extra).astype(np.float32)])
                self._vy = np.concatenate([self._vy, np.random.uniform(-1, 1, extra).astype(np.float32)])
            else:
                self._x = self._x[:new_n]
                self._y = self._y[:new_n]
                self._vx = self._vx[:new_n]
                self._vy = self._vy[:new_n]
            self._n = new_n

    def render(self, t: float, state) -> np.ndarray:
        if self._last_t is None:
            self._last_t = t
        dt = min(t - self._last_t, 0.05)
        self._last_t = t

        speed = self.params.get('speed', 25.0)
        trail = self.params.get('trail', 0.7)
        color_speed = self.params.get('color_speed', 0.2)

        n = self._n
        w, h = self.width, self.height
        level = state.audio_level
        beat = state.audio_beat

        # Beat: scatter
        if beat:
            self._vx += np.random.uniform(-3, 3, n).astype(np.float32)
            self._vy += np.random.uniform(-3, 3, n).astype(np.float32)

        # Compute pairwise distances (vectorized)
        dx = self._x[:, np.newaxis] - self._x[np.newaxis, :]  # (n, n)
        dy = self._y[:, np.newaxis] - self._y[np.newaxis, :]
        # Wrap x for cylindrical topology
        dx = dx - w * np.round(dx / w)
        dist = np.sqrt(dx ** 2 + dy ** 2 + 1e-6)

        # Separation: push away from nearby boids (strong, short range)
        sep_radius = max(w, h) * 0.2
        sep_mask = (dist < sep_radius) & (dist > 0.01)
        sep_x = np.where(sep_mask, -dx / (dist ** 2 + 0.1), 0).sum(axis=1)
        sep_y = np.where(sep_mask, -dy / (dist ** 2 + 0.1), 0).sum(axis=1)

        # Alignment: match velocity of nearby boids (medium range)
        align_radius = max(w, h) * 0.35
        align_mask = (dist < align_radius) & (dist > 0.01)
        align_count = align_mask.sum(axis=1).clip(1)
        align_x = (np.where(align_mask, self._vx[np.newaxis, :], 0).sum(axis=1) / align_count) - self._vx
        align_y = (np.where(align_mask, self._vy[np.newaxis, :], 0).sum(axis=1) / align_count) - self._vy

        # Cohesion: steer toward center of nearby boids (weak, prevents collapse)
        coh_x = (np.where(align_mask, self._x[np.newaxis, :], 0).sum(axis=1) / align_count) - self._x
        coh_y = (np.where(align_mask, self._y[np.newaxis, :], 0).sum(axis=1) / align_count) - self._y

        # Apply forces — separation dominates to prevent collapse
        self._vx += sep_x * 3.0 + align_x * 0.8 + coh_x * 0.1
        self._vy += sep_y * 3.0 + align_y * 0.8 + coh_y * 0.1

        # Add gentle random wandering to prevent stagnation
        self._vx += np.random.uniform(-0.5, 0.5, n).astype(np.float32)
        self._vy += np.random.uniform(-0.5, 0.5, n).astype(np.float32)

        # Speed limit with minimum speed (prevents stopping)
        max_speed = speed * (0.5 + level * 1.5)
        min_speed = speed * 0.15
        v_mag = np.sqrt(self._vx ** 2 + self._vy ** 2 + 1e-6)
        too_fast = v_mag > max_speed
        scale = max_speed / v_mag
        self._vx[too_fast] *= scale[too_fast]
        self._vy[too_fast] *= scale[too_fast]
        # Boost boids that are too slow
        too_slow = v_mag < min_speed
        if too_slow.any():
            boost = min_speed / v_mag
            self._vx[too_slow] *= boost[too_slow]
            self._vy[too_slow] *= boost[too_slow]

        # Update positions
        self._x += self._vx * dt
        self._y += self._vy * dt

        # Wrap x (cylindrical), reflect y
        self._x = self._x % w
        self._y = np.clip(self._y, 0, h - 1)
        bounce = (self._y <= 0.1) | (self._y >= h - 1.1)
        self._vy[bounce] *= -0.8

        # Render — each boid is a point with color based on velocity direction
        elapsed = self.elapsed(t)
        frame = np.zeros((w, h, 3), dtype=np.float32)

        ix = self._x.astype(np.int32).clip(0, w - 1)
        iy = self._y.astype(np.int32).clip(0, h - 1)
        hues = (np.arctan2(self._vy, self._vx) / (2 * math.pi) + 0.5 + elapsed * color_speed) % 1.0
        brightness = np.clip(v_mag / max_speed * 1.5, 0.3, 1.0)

        # HSV to RGB vectorized
        h6 = hues * 6.0
        sector = h6.astype(np.int32) % 6
        f = h6 - h6.astype(np.int32)
        for s in range(6):
            mask = sector == s
            if not mask.any():
                continue
            v_s = brightness[mask]
            f_s = f[mask]
            if s == 0:
                r_s, g_s, b_s = v_s, v_s * f_s, np.zeros_like(v_s)
            elif s == 1:
                r_s, g_s, b_s = v_s * (1 - f_s), v_s, np.zeros_like(v_s)
            elif s == 2:
                r_s, g_s, b_s = np.zeros_like(v_s), v_s, v_s * f_s
            elif s == 3:
                r_s, g_s, b_s = np.zeros_like(v_s), v_s * (1 - f_s), v_s
            elif s == 4:
                r_s, g_s, b_s = v_s * f_s, np.zeros_like(v_s), v_s
            else:
                r_s, g_s, b_s = v_s, np.zeros_like(v_s), v_s * (1 - f_s)
            np.add.at(frame, (ix[mask], iy[mask], 0), r_s)
            np.add.at(frame, (ix[mask], iy[mask], 1), g_s)
            np.add.at(frame, (ix[mask], iy[mask], 2), b_s)

        result = np.clip(frame * 255, 0, 255).astype(np.uint8)

        # Trail effect
        if self._prev_frame is not None and trail > 0:
            result = np.maximum(result, (self._prev_frame * trail).astype(np.uint8))
        self._prev_frame = result.copy()

        return result


# ──────────────────────────────────────────────────────────────────────
#  Registry
# ──────────────────────────────────────────────────────────────────────

SIMULATION_EFFECTS = {
    'fluid_sim': FluidSim,
    'reaction_diffusion': ReactionDiffusion,
    'wave_equation': WaveEquation,
    'boids': Boids,
}
