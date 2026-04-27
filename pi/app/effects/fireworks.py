"""
Sound-reactive fireworks — launches on beats, explodes into sparks.

Spark physics and rendering are fully vectorized with NumPy.
"""

import math
import random
import numpy as np
from .base import Effect


class _Param:
  def __init__(self, label, attr, lo, hi, step, default):
    self.label, self.attr, self.lo, self.hi = label, attr, lo, hi
    self.step, self.default = step, default


class _Rocket:
  __slots__ = ('x', 'y', 'vy', 'target_y', 'r', 'g', 'b', 'trail')

  def __init__(self, x, target_y, height, r, g, b):
    self.x = x
    self.y = float(height - 1)
    self.vy = 0.0
    self.target_y = target_y
    self.r = r
    self.g = g
    self.b = b
    self.trail = []


# Spark dtype for vectorized physics + rendering
_SPARK_DTYPE = np.dtype([
    ('x', np.float32), ('y', np.float32),
    ('vx', np.float32), ('vy', np.float32),
    ('r', np.float32), ('g', np.float32), ('b', np.float32),
    ('life', np.float32), ('max_life', np.float32),
])


class SRFireworks(Effect):
    """Sound-reactive fireworks — beat launches rockets, bass controls intensity."""

    CATEGORY = "sound"
    DISPLAY_NAME = "SR Fireworks"
    DESCRIPTION = "Beat-triggered fireworks with trailing sparks"
    PALETTE_SUPPORT = False

    PARAMS = [
        _Param("Gain", "gain", 0.5, 5.0, 0.1, 2.0),
        _Param("Gravity", "gravity", 10, 80, 5, 40),
        _Param("Sparks", "spark_count", 10, 80, 5, 40),
    ]

    _MAX_SPARKS = 600
    _MAX_ROCKETS = 10

    def __init__(self, width, height, params=None):
        super().__init__(width, height, params)
        self._rockets: list[_Rocket] = []
        self._sparks = np.empty(0, dtype=_SPARK_DTYPE)
        self._last_t = None
        self._prev_frame = None
        self._beat_cooldown = 0.0

    def _add_sparks(self, new_sparks: np.ndarray):
        """Append new sparks, respecting MAX_SPARKS limit."""
        if len(self._sparks) + len(new_sparks) > self._MAX_SPARKS:
            avail = self._MAX_SPARKS - len(self._sparks)
            if avail <= 0:
                return
            new_sparks = new_sparks[:avail]
        if len(self._sparks) == 0:
            self._sparks = new_sparks
        else:
            self._sparks = np.concatenate([self._sparks, new_sparks])

    def render(self, t: float, state) -> np.ndarray:
        if self._last_t is None:
            self._last_t = t
        dt = t - self._last_t
        self._last_t = t
        dt = min(dt, 0.05)

        cols = self.width
        rows = self.height
        gain = self.params.get('gain', 2.0)
        gravity = self.params.get('gravity', 40)
        spark_count = int(self.params.get('spark_count', 40))

        bass = state.audio_bass * gain
        beat = state.audio_beat
        level = state.audio_level * gain

        self._beat_cooldown = max(0, self._beat_cooldown - dt)

        # Launch rocket on beat
        if beat and self._beat_cooldown <= 0 and len(self._rockets) < self._MAX_ROCKETS:
            self._beat_cooldown = 0.15
            x = random.uniform(1, cols - 2)
            target_y = random.uniform(rows * 0.15, rows * 0.6)
            hue = random.random()
            r, g, b = self._hue_rgb(hue)
            rocket = _Rocket(x, target_y, rows, r, g, b)
            rocket.vy = -(rows - target_y) * 2.5
            self._rockets.append(rocket)

        # Auto-launch on bass energy
        if bass > 0.4 and random.random() < bass * dt * 3 and len(self._rockets) < self._MAX_ROCKETS:
            x = random.uniform(1, cols - 2)
            target_y = random.uniform(rows * 0.2, rows * 0.7)
            hue = random.random()
            r, g, b = self._hue_rgb(hue)
            rocket = _Rocket(x, target_y, rows, r, g, b)
            rocket.vy = -(rows - target_y) * 2.5
            self._rockets.append(rocket)

        # Update rockets (max 10, not worth vectorizing)
        alive_rockets = []
        for rk in self._rockets:
            rk.y += rk.vy * dt
            rk.vy += gravity * 0.3 * dt

            rk.trail.append((rk.x, rk.y))
            if len(rk.trail) > 8:
                rk.trail.pop(0)

            if rk.y <= rk.target_y or rk.vy >= 0:
                # Explode — create sparks as vectorized batch
                num = int(spark_count * (0.5 + bass * 0.5))
                num = min(num, self._MAX_SPARKS - len(self._sparks))
                if num > 0:
                    angles = np.random.uniform(0, 2 * math.pi, num).astype(np.float32)
                    speeds = np.random.uniform(15, 60, num).astype(np.float32) * (0.5 + level * 0.5)
                    lives = np.random.uniform(0.5, 2.0, num).astype(np.float32)
                    new = np.empty(num, dtype=_SPARK_DTYPE)
                    new['x'] = rk.x
                    new['y'] = rk.y
                    new['vx'] = np.cos(angles) * speeds
                    new['vy'] = np.sin(angles) * speeds
                    new['r'] = np.clip(rk.r + np.random.randint(-30, 31, num), 0, 255).astype(np.float32)
                    new['g'] = np.clip(rk.g + np.random.randint(-30, 31, num), 0, 255).astype(np.float32)
                    new['b'] = np.clip(rk.b + np.random.randint(-30, 31, num), 0, 255).astype(np.float32)
                    new['life'] = lives
                    new['max_life'] = lives
                    self._add_sparks(new)
            else:
                alive_rockets.append(rk)
        self._rockets = alive_rockets

        # Vectorized spark physics
        s = self._sparks
        if len(s) > 0:
            s['x'] += s['vx'] * dt
            s['y'] += s['vy'] * dt
            s['vy'] += gravity * dt
            s['vx'] *= 0.98
            s['life'] -= dt
            alive = (s['life'] > 0) & (s['y'] >= 0) & (s['y'] < rows)
            self._sparks = s[alive]

        # Render
        frame = np.zeros((cols, rows, 3), dtype=np.float32)

        # Draw rocket trails (max 10 rockets × 8 trail points — negligible)
        for rk in self._rockets:
            n_trail = len(rk.trail)
            for i, (tx, ty) in enumerate(rk.trail):
                ix = int(round(tx))
                iy = int(round(ty))
                if 0 <= ix < cols and 0 <= iy < rows:
                    fade = (i + 1) / n_trail
                    frame[ix, iy] = [rk.r * fade * 0.5, rk.g * fade * 0.5, rk.b * fade * 0.5]

        # Vectorized spark rendering
        s = self._sparks
        if len(s) > 0:
            ix = np.round(s['x']).astype(np.int32) % cols
            iy = np.round(s['y']).astype(np.int32)
            valid = (ix >= 0) & (ix < cols) & (iy >= 0) & (iy < rows)
            ix = ix[valid]
            iy = iy[valid]
            sv = s[valid]
            brightness = np.sqrt(sv['life'] / sv['max_life'])
            # Use np.add.at for safe accumulation (handles duplicate positions)
            np.add.at(frame, (ix, iy, 0), sv['r'] * brightness)
            np.add.at(frame, (ix, iy, 1), sv['g'] * brightness)
            np.add.at(frame, (ix, iy, 2), sv['b'] * brightness)

        result = np.clip(frame, 0, 255).astype(np.uint8)

        # Temporal fade for trailing glow
        if self._prev_frame is not None:
            result = np.maximum(result, (self._prev_frame * 0.6).astype(np.uint8))
        self._prev_frame = result.copy()

        return result

    @staticmethod
    def _hue_rgb(h):
        h = h % 1.0
        i = int(h * 6)
        f = h * 6 - i
        q = int(255 * (1 - f))
        t = int(255 * f)
        if i == 0: return 255, t, 0
        if i == 1: return q, 255, 0
        if i == 2: return 0, 255, t
        if i == 3: return 0, q, 255
        if i == 4: return t, 0, 255
        return 255, 0, q
