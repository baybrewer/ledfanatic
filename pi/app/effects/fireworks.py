"""
Sound-reactive fireworks — launches on beats, explodes into sparks.
"""

import math
import random
import time
import numpy as np
from .base import Effect


class _Param:
  def __init__(self, label, attr, lo, hi, step, default):
    self.label, self.attr, self.lo, self.hi = label, attr, lo, hi
    self.step, self.default = step, default


class _Spark:
  __slots__ = ('x', 'y', 'vx', 'vy', 'r', 'g', 'b', 'life', 'max_life')

  def __init__(self, x, y, vx, vy, r, g, b, life):
    self.x = x
    self.y = y
    self.vx = vx
    self.vy = vy
    self.r = r
    self.g = g
    self.b = b
    self.life = life
    self.max_life = life


class _Rocket:
  __slots__ = ('x', 'y', 'vy', 'target_y', 'r', 'g', 'b', 'trail')

  def __init__(self, x, target_y, height, r, g, b):
    self.x = x
    self.y = float(height - 1)  # launches from screen bottom (high y)
    self.vy = 0.0
    self.target_y = target_y
    self.r = r
    self.g = g
    self.b = b
    self.trail = []


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
        self._sparks: list[_Spark] = []
        self._rockets: list[_Rocket] = []
        self._last_t = None
        self._prev_frame = None
        self._beat_cooldown = 0.0

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

        # Launch rocket on beat (screen coords: y=0 top, y=rows-1 bottom)
        # Rockets launch from bottom (high y) toward top (low y)
        if beat and self._beat_cooldown <= 0 and len(self._rockets) < self._MAX_ROCKETS:
            self._beat_cooldown = 0.15
            x = random.uniform(1, cols - 2)
            target_y = random.uniform(rows * 0.15, rows * 0.6)
            hue = random.random()
            r, g, b = self._hue_rgb(hue)
            rocket = _Rocket(x, target_y, rows, r, g, b)
            rocket.vy = -(rows - target_y) * 2.5  # negative = moving up (toward y=0)
            self._rockets.append(rocket)

        # Also auto-launch occasionally based on bass energy
        if bass > 0.4 and random.random() < bass * dt * 3 and len(self._rockets) < self._MAX_ROCKETS:
            x = random.uniform(1, cols - 2)
            target_y = random.uniform(rows * 0.2, rows * 0.7)
            hue = random.random()
            r, g, b = self._hue_rgb(hue)
            rocket = _Rocket(x, target_y, rows, r, g, b)
            rocket.vy = -(rows - target_y) * 2.5
            self._rockets.append(rocket)

        # Update rockets
        alive_rockets = []
        for rk in self._rockets:
            rk.y += rk.vy * dt
            rk.vy += gravity * 0.3 * dt  # decelerate (vy is negative, gravity slows it)

            # Trail
            rk.trail.append((rk.x, rk.y))
            if len(rk.trail) > 8:
                rk.trail.pop(0)

            # Explode when reaching target or stalling
            if rk.y <= rk.target_y or rk.vy >= 0:
                # BOOM — create sparks
                num = int(spark_count * (0.5 + bass * 0.5))
                for _ in range(min(num, self._MAX_SPARKS - len(self._sparks))):
                    angle = random.uniform(0, 2 * math.pi)
                    speed = random.uniform(15, 60) * (0.5 + level * 0.5)
                    vx = math.cos(angle) * speed
                    vy = math.sin(angle) * speed
                    life = random.uniform(0.5, 2.0)
                    # Slight color variation
                    cr = min(255, max(0, rk.r + random.randint(-30, 30)))
                    cg = min(255, max(0, rk.g + random.randint(-30, 30)))
                    cb = min(255, max(0, rk.b + random.randint(-30, 30)))
                    self._sparks.append(_Spark(rk.x, rk.y, vx, vy, cr, cg, cb, life))
            else:
                alive_rockets.append(rk)
        self._rockets = alive_rockets

        # Update sparks
        alive_sparks = []
        for sp in self._sparks:
            sp.x += sp.vx * dt
            sp.y += sp.vy * dt
            sp.vy += gravity * dt  # gravity pulls down (increasing y = screen down)
            sp.vx *= 0.98  # air resistance
            sp.life -= dt
            if sp.life > 0 and 0 <= sp.y < rows:
                alive_sparks.append(sp)
        self._sparks = alive_sparks

        # Render
        frame = np.zeros((cols, rows, 3), dtype=np.float32)

        # Draw rocket trails
        for rk in self._rockets:
            for i, (tx, ty) in enumerate(rk.trail):
                ix = int(round(tx))
                iy = int(round(ty))
                if 0 <= ix < cols and 0 <= iy < rows:
                    fade = (i + 1) / len(rk.trail)
                    frame[ix, iy] = [rk.r * fade * 0.5, rk.g * fade * 0.5, rk.b * fade * 0.5]

        # Draw sparks
        for sp in self._sparks:
            ix = int(round(sp.x)) % cols
            iy = int(round(sp.y))
            if 0 <= ix < cols and 0 <= iy < rows:
                brightness = (sp.life / sp.max_life) ** 0.5
                frame[ix, iy, 0] = min(255, frame[ix, iy, 0] + sp.r * brightness)
                frame[ix, iy, 1] = min(255, frame[ix, iy, 1] + sp.g * brightness)
                frame[ix, iy, 2] = min(255, frame[ix, iy, 2] + sp.b * brightness)

        result = np.clip(frame, 0, 255).astype(np.uint8)

        # Light temporal fade for trailing glow
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
