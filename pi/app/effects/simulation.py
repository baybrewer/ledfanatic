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
    DISPLAY_NAME = "SR Fluid Dynamics"
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
    DISPLAY_NAME = "SR Wave Equation"
    DESCRIPTION = "2D wave simulation — beats create ripples that interfere and decay"
    PALETTE_SUPPORT = False
    AUDIO_REQUIRES = ('level', 'bass', 'beat')

    PARAMS = [
        type('P', (), {'label': 'Gain', 'attr': 'gain', 'lo': 0.5, 'hi': 5.0,
                        'step': 0.1, 'default': 2.0})(),
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

        gain = self.params.get('gain', 2.0)
        c = self.params.get('wave_speed', 0.12)
        damping = self.params.get('damping', 0.985)
        color_speed = self.params.get('color_speed', 0.3)

        w, h = self.width, self.height
        bass = state.audio_bass * gain
        beat = state.audio_beat
        level = state.audio_level * gain

        # Beat: drop a stone — amplitude scales with level
        if beat:
            cx = np.random.randint(0, w)
            cy = np.random.randint(h // 4, 3 * h // 4)
            amplitude = 0.5 + level * 3
            x_grid = np.arange(w)[:, np.newaxis]
            y_grid = np.arange(h)[np.newaxis, :]
            dist2 = ((x_grid - cx) % w) ** 2 + (y_grid - cy) ** 2
            dist2_wrap = np.minimum(dist2, ((x_grid - cx + w) % w) ** 2 + (y_grid - cy) ** 2)
            impulse = amplitude * np.exp(-dist2_wrap / 3.0).astype(np.float32)
            self._u += impulse

        # Bass: continuous bottom perturbation — only when bass is present
        if bass > 0.3:
            self._u[:, -3:] += (bass - 0.3) * 0.5

        # Mid: occasional side ripple
        if state.audio_mid * gain > 0.4:
            side_y = int(h * 0.3 + state.audio_mid * h * 0.4)
            side_y = min(side_y, h - 1)
            self._u[0, max(0, side_y - 1):side_y + 2] += state.audio_mid * gain * 0.4

        # Auto-drop ONLY when no audio at all (silent fallback)
        raw_level = state.audio_level  # before gain
        self._auto_drop_timer -= dt
        if self._auto_drop_timer <= 0 and raw_level < 0.02:
            self._auto_drop_timer = np.random.uniform(1.5, 4.0)
            cx = np.random.randint(0, w)
            cy = np.random.randint(h // 4, 3 * h // 4)
            x_grid = np.arange(w)[:, np.newaxis]
            y_grid = np.arange(h)[np.newaxis, :]
            dist2 = ((x_grid - cx) % w) ** 2 + (y_grid - cy) ** 2
            impulse = 0.5 * np.exp(-dist2 / 3.0).astype(np.float32)
            self._u += impulse

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
    DISPLAY_NAME = "SR Boids Flock"
    DESCRIPTION = "Emergent flocking — separation, alignment create a living swarm"
    PALETTE_SUPPORT = False
    AUDIO_REQUIRES = ('level', 'beat')

    PARAMS = [
        type('P', (), {'label': 'Gain', 'attr': 'gain', 'lo': 0.5, 'hi': 5.0,
                        'step': 0.1, 'default': 2.0})(),
        type('P', (), {'label': 'Count', 'attr': 'count', 'lo': 10, 'hi': 200,
                        'step': 10, 'default': 40})(),
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
        init_speed = float(self.params.get('speed', 25.0)) * 0.4
        self._wander_angle = np.random.uniform(0, 2 * math.pi, n).astype(np.float32)
        self._vx = (np.cos(self._wander_angle) * init_speed).astype(np.float32)
        self._vy = (np.sin(self._wander_angle) * init_speed).astype(np.float32)
        self._last_t = None
        self._prev_frame = None

    def update_params(self, params: dict):
        super().update_params(params)
        new_n = int(self.params.get('count', 40))
        if new_n != self._n:
            if new_n > self._n:
                extra = new_n - self._n
                self._x = np.concatenate([self._x, np.random.uniform(0, self.width, extra).astype(np.float32)])
                self._y = np.concatenate([self._y, np.random.uniform(0, self.height, extra).astype(np.float32)])
                self._vx = np.concatenate([self._vx, np.random.uniform(-1, 1, extra).astype(np.float32)])
                self._vy = np.concatenate([self._vy, np.random.uniform(-1, 1, extra).astype(np.float32)])
                self._wander_angle = np.concatenate([self._wander_angle, np.random.uniform(0, 2 * math.pi, extra).astype(np.float32)])
            else:
                self._x = self._x[:new_n]
                self._y = self._y[:new_n]
                self._vx = self._vx[:new_n]
                self._vy = self._vy[:new_n]
                self._wander_angle = self._wander_angle[:new_n]
            self._n = new_n

    def render(self, t: float, state) -> np.ndarray:
        if self._last_t is None:
            self._last_t = t
        dt = min(t - self._last_t, 0.05)
        self._last_t = t

        gain = self.params.get('gain', 2.0)
        speed = self.params.get('speed', 25.0)
        trail = self.params.get('trail', 0.7)
        color_speed = self.params.get('color_speed', 0.2)

        n = self._n
        w, h = self.width, self.height
        level = state.audio_level * gain
        beat = state.audio_beat

        # Beat: randomize wander angles
        if beat:
            self._wander_angle += np.random.uniform(-math.pi, math.pi, n).astype(np.float32)

        # Smoothly rotate each boid's wander heading (Brownian-style steering)
        self._wander_angle += np.random.uniform(-1.5, 1.5, n).astype(np.float32) * dt * 10

        # Primary drive: each boid follows its own wander heading
        target_speed = speed * (0.5 + level * 1.0)
        desired_vx = np.cos(self._wander_angle) * target_speed
        desired_vy = np.sin(self._wander_angle) * target_speed

        # Steer toward desired velocity — strong so wander always dominates
        steer_rate = 5.0 * dt
        self._vx += (desired_vx - self._vx) * steer_rate
        self._vy += (desired_vy - self._vy) * steer_rate

        # Separation — prevent overlap (correct vector math)
        dx = self._x[:, np.newaxis] - self._x[np.newaxis, :]
        dy = self._y[:, np.newaxis] - self._y[np.newaxis, :]
        dx = dx - w * np.round(dx / w)
        dist = np.sqrt(dx ** 2 + dy ** 2 + 1e-6)

        sep_radius = h * 0.1
        sep_weight = np.clip((sep_radius - dist) / sep_radius, 0, 1)
        np.fill_diagonal(sep_weight, 0)
        # Average (not sum) so force doesn't scale with boid count
        neighbor_count = (sep_weight > 0).sum(axis=1).clip(1)
        self._vx += (dx / dist * sep_weight).sum(axis=1) / neighbor_count * 5.0
        self._vy += (dy / dist * sep_weight).sum(axis=1) / neighbor_count * 5.0

        # Edge avoidance — steer wander angle away from walls
        margin = h * 0.15
        near_top = self._y < margin
        near_bot = self._y > (h - margin)
        # Point downward near top, upward near bottom
        self._wander_angle[near_top] = self._wander_angle[near_top] * 0.8 + (math.pi * 0.5) * 0.2
        self._wander_angle[near_bot] = self._wander_angle[near_bot] * 0.8 + (-math.pi * 0.5) * 0.2

        # Speed clamp
        v_mag = np.sqrt(self._vx ** 2 + self._vy ** 2 + 1e-6)
        max_speed = speed * (0.6 + level)
        too_fast = v_mag > max_speed
        self._vx[too_fast] *= (max_speed / v_mag[too_fast])
        self._vy[too_fast] *= (max_speed / v_mag[too_fast])
        v_mag = np.sqrt(self._vx ** 2 + self._vy ** 2 + 1e-6)

        # Move
        self._x = (self._x + self._vx * dt) % w
        new_y = self._y + self._vy * dt
        # Bounce off top/bottom
        bounce_top = new_y < 0
        bounce_bot = new_y >= h
        new_y[bounce_top] *= -1
        self._vy[bounce_top] *= -1
        self._wander_angle[bounce_top] *= -1  # flip wander too
        new_y[bounce_bot] = 2 * (h - 1) - new_y[bounce_bot]
        self._vy[bounce_bot] *= -1
        self._wander_angle[bounce_bot] *= -1
        self._y = np.clip(new_y, 0, h - 1)

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
#  Fluid Jets — colored puffs creating vortex streets
# ──────────────────────────────────────────────────────────────────────

class FluidJets(FluidSim):
    """Colored fluid jets pulsing from multiple directions, creating vortices.

    Inherits Navier-Stokes solver from FluidSim. Multiple injection points
    fire colored puffs that collide and create turbulent vortex streets.
    Each jet pulses on its own rhythm; beats trigger extra bursts.
    """

    CATEGORY = "simulation"
    DISPLAY_NAME = "SR Fluid Jets"
    DESCRIPTION = "Colored fluid puffs from all sides — collisions create vortices"
    PALETTE_SUPPORT = False
    AUDIO_REQUIRES = ('level', 'bass', 'mid', 'high', 'beat')

    PARAMS = [
        type('P', (), {'label': 'Gain', 'attr': 'gain', 'lo': 0.5, 'hi': 5.0,
                        'step': 0.1, 'default': 2.0})(),
        type('P', (), {'label': 'Jet Force', 'attr': 'force', 'lo': 5.0, 'hi': 80.0,
                        'step': 2.0, 'default': 40.0})(),
        type('P', (), {'label': 'Pulse Rate', 'attr': 'pulse_rate', 'lo': 0.5, 'hi': 5.0,
                        'step': 0.1, 'default': 1.5})(),
        type('P', (), {'label': 'Dye Intensity', 'attr': 'dye_rate', 'lo': 0.5, 'hi': 5.0,
                        'step': 0.1, 'default': 3.0})(),
        type('P', (), {'label': 'Pressure Iters', 'attr': 'pressure_iters', 'lo': 1, 'hi': 20,
                        'step': 1, 'default': 2})(),
    ]

    # Jet definitions: (side, position_frac, vx_dir, vy_dir, base_hue)
    # side: 'bottom', 'top', 'left', 'right'
    _JET_DEFS = [
        ('bottom', 0.3, 0.0, -1.0, 0.0),    # red, left-of-center, upward
        ('bottom', 0.7, 0.0, -1.0, 0.15),    # orange, right-of-center, upward
        ('top', 0.5, 0.0, 1.0, 0.55),         # cyan, center, downward
        ('left', 0.4, 1.0, 0.0, 0.3),         # green, upper-mid, rightward
        ('right', 0.6, -1.0, 0.0, 0.75),      # purple, lower-mid, leftward
    ]

    def __init__(self, width, height, params=None):
        super().__init__(width, height, params)
        # Per-jet pulse timers (staggered starts)
        self._jet_timers = [i * 0.3 for i in range(len(self._JET_DEFS))]
        self._jet_hue_offsets = [0.0] * len(self._JET_DEFS)
        self._pulse_index = 0

    def render(self, t: float, state) -> np.ndarray:
        if self._last_t is None:
            self._last_t = t
        dt = min(t - self._last_t, 0.05)
        self._last_t = t

        gain = self.params.get('gain', 2.0)
        force = self.params.get('force', 12.0)
        pulse_rate = self.params.get('pulse_rate', 1.5)
        dye_rate = self.params.get('dye_rate', 3.0)

        bass = state.audio_bass * gain
        mid = state.audio_mid * gain
        high = state.audio_high * gain
        beat = state.audio_beat
        level = state.audio_level * gain

        w, h = self.width, self.height
        pw, ph = w + 2, h + 2  # padded grid size

        # Advance jet timers and fire pulses
        for ji, (side, pos_frac, vx_dir, vy_dir, base_hue) in enumerate(self._JET_DEFS):
            self._jet_timers[ji] -= dt * pulse_rate * (0.5 + level)

            should_fire = self._jet_timers[ji] <= 0
            if should_fire:
                self._jet_timers[ji] = np.random.uniform(0.3, 0.8)
                self._jet_hue_offsets[ji] += 0.12  # shift hue each pulse

            # Also fire on beat for extra energy
            if beat and ji == (self._pulse_index % len(self._JET_DEFS)):
                should_fire = True
                self._pulse_index += 1

            if not should_fire:
                continue

            # Compute injection position on padded grid
            hue = (base_hue + self._jet_hue_offsets[ji]) % 1.0
            intensity = dye_rate * (0.5 + level)
            jet_force = force * (0.5 + bass * 0.5)

            if side == 'bottom':
                jx = int(pos_frac * w) + 1
                jx = max(1, min(jx, pw - 2))
                rx = slice(max(1, jx - 2), min(jx + 3, pw - 1))
                ry = slice(max(1, ph - 4), ph - 1)
                self._vy[rx, ry] += vy_dir * jet_force
                self._vx[rx, ry] += np.random.uniform(-jet_force * 0.4, jet_force * 0.4)
            elif side == 'top':
                jx = int(pos_frac * w) + 1
                jx = max(1, min(jx, pw - 2))
                rx = slice(max(1, jx - 2), min(jx + 3, pw - 1))
                ry = slice(1, 4)
                self._vy[rx, ry] += vy_dir * jet_force
                self._vx[rx, ry] += np.random.uniform(-jet_force * 0.4, jet_force * 0.4)
            elif side == 'left':
                jy = int(pos_frac * h) + 1
                jy = max(1, min(jy, ph - 2))
                rx = slice(1, 4)
                ry = slice(max(1, jy - 2), min(jy + 3, ph - 1))
                self._vx[rx, ry] += vx_dir * jet_force
                self._vy[rx, ry] += np.random.uniform(-jet_force * 0.4, jet_force * 0.4)
            elif side == 'right':
                jy = int(pos_frac * h) + 1
                jy = max(1, min(jy, ph - 2))
                rx = slice(pw - 4, pw - 1)
                ry = slice(max(1, jy - 2), min(jy + 3, ph - 1))
                self._vx[rx, ry] += vx_dir * jet_force
                self._vy[rx, ry] += np.random.uniform(-jet_force * 0.4, jet_force * 0.4)

            # Inject colored dye
            rv, gv, bv = self._hsv_fast(hue, 1.0, 1.0)
            self._dye[rx, ry] += np.array([rv, gv, bv], dtype=np.float32) * intensity

        # Run the Navier-Stokes solver (inherited from FluidSim)
        tmp1, tmp2 = self._tmp1, self._tmp2
        visc = self.params.get('viscosity', 0.0)
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

        # Advect dye (batched 3-channel)
        self._dye_tmp[:] = self._dye
        self._advect_3d(self._dye, self._dye_tmp, self._vx, self._vy, dt)

        # Slow decay so colors linger and mix
        self._dye *= 0.992

        # Extract interior and render
        frame = np.clip(self._dye[1:-1, 1:-1] * 255, 0, 255).astype(np.uint8)
        return frame


# ──────────────────────────────────────────────────────────────────────
#  Smoke Rings — continuous upward jets creating vortex pairs
# ──────────────────────────────────────────────────────────────────────

class SmokeRings(FluidSim):
    """Continuous upward jets that form vortex rings as they rise.

    Each jet injects momentum and dye from the bottom. The velocity
    shear at the jet edges rolls up into counter-rotating vortex pairs
    (2D cross-sections of toroidal smoke rings). Jets are evenly spaced
    and each gets its own slowly-cycling hue.
    """

    CATEGORY = "simulation"
    DISPLAY_NAME = "Smoke Rings"
    DESCRIPTION = "Upward jets that roll into vortex rings as they rise"
    PALETTE_SUPPORT = False

    PARAMS = [
        type('P', (), {'label': 'Jets', 'attr': 'num_jets', 'lo': 1, 'hi': 5,
                        'step': 1, 'default': 2})(),
        type('P', (), {'label': 'Impulse', 'attr': 'force', 'lo': 1.0, 'hi': 30.0,
                        'step': 0.5, 'default': 8.0})(),
        type('P', (), {'label': 'Interval', 'attr': 'interval', 'lo': 1.0, 'hi': 10.0,
                        'step': 0.5, 'default': 3.0})(),
        type('P', (), {'label': 'Dye Intensity', 'attr': 'dye_rate', 'lo': 0.5, 'hi': 5.0,
                        'step': 0.1, 'default': 2.5})(),
        type('P', (), {'label': 'Color Cycle', 'attr': 'color_speed', 'lo': 0.0, 'hi': 1.0,
                        'step': 0.05, 'default': 0.15})(),
        type('P', (), {'label': 'Viscosity', 'attr': 'viscosity', 'lo': 0.0, 'hi': 0.002,
                        'step': 0.0001, 'default': 0.0005})(),
        type('P', (), {'label': 'Pressure Iters', 'attr': 'pressure_iters', 'lo': 1, 'hi': 20,
                        'step': 1, 'default': 2})(),
    ]

    def __init__(self, width, height, params=None):
        super().__init__(width, height, params)
        self._jet_timers = []
        self._puff_count = 0
        self._rebuild_jets()

    def _rebuild_jets(self):
        n = int(self.params.get('num_jets', 2))
        interval = self.params.get('interval', 3.0)
        # Stagger timers so jets don't all fire at once
        self._jet_timers = [i * interval / max(n, 1) for i in range(n)]

    def update_params(self, params: dict):
        old_n = int(self.params.get('num_jets', 2))
        super().update_params(params)
        new_n = int(self.params.get('num_jets', 2))
        if new_n != old_n:
            self._rebuild_jets()

    def render(self, t: float, state) -> np.ndarray:
        if self._last_t is None:
            self._last_t = t
        dt = min(t - self._last_t, 0.05)
        self._last_t = t

        force = self.params.get('force', 8.0)
        interval = self.params.get('interval', 3.0)
        dye_rate = self.params.get('dye_rate', 2.5)
        color_speed = self.params.get('color_speed', 0.15)
        num_jets = int(self.params.get('num_jets', 2))

        w, h = self.width, self.height
        pw, ph = w + 2, h + 2
        elapsed = self.elapsed(t)

        # Each jet fires a single sharp impulse then waits
        for ji in range(num_jets):
            self._jet_timers[ji] -= dt
            if self._jet_timers[ji] > 0:
                continue

            # FIRE — one sharp puff
            self._jet_timers[ji] = interval

            jet_x_frac = (ji + 0.5) / num_jets
            jx = int(jet_x_frac * w) + 1
            jx = max(2, min(jx, pw - 3))

            # Upward impulse — center column strong, edges weaker (creates shear)
            ry = slice(ph - 4, ph - 1)
            self._vy[jx, ry] -= force          # center: strong up
            self._vy[jx - 1, ry] -= force * 0.3  # left edge: weaker
            self._vy[jx + 1, ry] -= force * 0.3  # right edge: weaker
            # Explicit opposite-sign vx on edges to seed vortex pair
            self._vx[jx - 1, ry] -= force * 0.4
            self._vx[jx + 1, ry] += force * 0.4

            # Inject dye
            self._puff_count += 1
            hue = (self._puff_count * 0.15 + ji / max(num_jets, 1) + elapsed * color_speed) % 1.0
            rv, gv, bv = self._hsv_fast(hue, 0.9, 1.0)
            rx = slice(jx - 1, jx + 2)
            self._dye[rx, ry] += np.array([rv, gv, bv], dtype=np.float32) * dye_rate

        # Velocity damping — prevents momentum buildup
        self._vx *= 0.97
        self._vy *= 0.97

        # Navier-Stokes solver
        tmp1, tmp2 = self._tmp1, self._tmp2
        visc = self.params.get('viscosity', 0.0005)
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

        # Advect dye
        self._dye_tmp[:] = self._dye
        self._advect_3d(self._dye, self._dye_tmp, self._vx, self._vy, dt)

        # Slow decay
        self._dye *= 0.996

        frame = np.clip(self._dye[1:-1, 1:-1] * 255, 0, 255).astype(np.uint8)
        return frame


# ──────────────────────────────────────────────────────────────────────
#  Registry
# ──────────────────────────────────────────────────────────────────────

SIMULATION_EFFECTS = {
    'fluid_sim': FluidSim,
    'fluid_jets': FluidJets,
    'smoke_rings': SmokeRings,
    'reaction_diffusion': ReactionDiffusion,
    'wave_equation': WaveEquation,
    'boids': Boids,
}
