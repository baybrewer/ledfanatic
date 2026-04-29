"""
Game effects — classic arcade games on the LED pillar.

Three games with auto-play AI:
- Space Invaders: aliens descend, player shoots
- Snake: growing snake chases food
- Game of Life: Conway's cellular automata
"""

import time
import random
import numpy as np
from .base import Effect, hsv_to_rgb


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


# ──────────────────────────────────────────────────────────────────────
#  Space Invaders
# ──────────────────────────────────────────────────────────────────────


class SpaceInvaders(Effect):
  """Classic space invaders — auto-plays or use Game tab."""

  CATEGORY = "game"
  DISPLAY_NAME = "Space Invaders"
  DESCRIPTION = "Classic space invaders — auto-plays or use Game tab"
  PARAMS = [
    _P("Speed", "speed", 0.5, 3.0, 0.1, 1.0),
  ]

  # Alien row colors (from top row down)
  ALIEN_COLORS = [
    (255, 0, 0),      # red
    (255, 100, 0),     # orange
    (255, 255, 0),     # yellow
    (0, 255, 100),     # teal
    (100, 100, 255),   # light blue
    (200, 0, 255),     # purple
  ]

  PLAYER_COLOR = (0, 255, 0)
  BULLET_COLOR = (255, 255, 200)
  ALIEN_BULLET_COLOR = (255, 80, 80)

  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    self.auto_play = True
    self.last_input = 0.0
    self._input_queue = []
    self._reset_game()

  def _reset_game(self):
    """Initialize or restart the game state."""
    self.player_x = self.width // 2
    self.player_bullets = []   # list of (x, y)
    self.alien_bullets = []    # list of (x, y)
    self.game_over_time = 0.0
    self.last_move = 0.0
    self.last_alien_move = 0.0
    self.last_alien_shoot = 0.0
    self.last_player_shoot = 0.0
    self.alien_dir = 1         # 1=right, -1=left
    self.alien_drop = False
    self.score = 0

    # Build alien grid — rows of aliens near the top
    num_rows = min(4, max(2, self.height // 6))
    # Alien width: leave 1px margin on each side, space aliens 2px apart
    alien_cols = max(2, (self.width - 2) // 2)
    self.aliens = []  # list of (x, y, row_index)
    for row in range(num_rows):
      for col in range(alien_cols):
        ax = 1 + col * 2
        ay = 1 + row * 2
        if ax < self.width:
          self.aliens.append([ax, ay, row])

  def get_game_status(self):
    return {'score': self.score, 'game': 'space_invaders', 'aliens': len(self.aliens)}

  def handle_input(self, action: str):
    """Queue a player input: left, right, shoot."""
    self._input_queue.append(action)
    self.last_input = time.monotonic()
    self.auto_play = False

  def _process_input(self, action):
    if action == 'left':
      self.player_x = max(0, self.player_x - 1)
    elif action == 'right':
      self.player_x = min(self.width - 1, self.player_x + 1)
    elif action in ('shoot', 'rotate', 'up'):
      self._player_shoot()

  def _player_shoot(self):
    now = time.monotonic()
    speed = self.params.get('speed', 1.0)
    if now - self.last_player_shoot < 0.3 / speed:
      return
    self.last_player_shoot = now
    player_y = self.height - 1
    self.player_bullets.append([self.player_x, player_y - 1])

  def _auto_move(self, now):
    """AI: dodge alien bullets and shoot at aliens."""
    speed = self.params.get('speed', 1.0)
    if now - self.last_move < 0.08 / speed:
      return
    self.last_move = now

    player_y = self.height - 1

    # Find nearest threat (alien bullet heading toward us)
    threat_x = None
    min_dist_y = self.height
    for bx, by in self.alien_bullets:
      if by > player_y - 4 and abs(bx - self.player_x) <= 2:
        dist = player_y - by
        if dist < min_dist_y:
          min_dist_y = dist
          threat_x = bx

    # Find nearest alien column to shoot at
    target_x = None
    if self.aliens:
      # Prefer aliens directly above
      best_dist = self.width + 1
      for ax, ay, _ in self.aliens:
        dist = abs(ax - self.player_x)
        if dist < best_dist:
          best_dist = dist
          target_x = ax

    # Dodge bullets first
    if threat_x is not None and min_dist_y < 5:
      if threat_x <= self.player_x and self.player_x < self.width - 1:
        self._process_input('right')
      elif self.player_x > 0:
        self._process_input('left')
    elif target_x is not None:
      # Move toward target alien
      if self.player_x < target_x:
        self._process_input('right')
      elif self.player_x > target_x:
        self._process_input('left')

    # Shoot frequently
    self._player_shoot()

  def render(self, t: float, state) -> np.ndarray:
    now = time.monotonic()
    speed = self.params.get('speed', 1.0)

    # Re-enable auto-play after 5 seconds of no input
    if not self.auto_play and now - self.last_input > 5.0:
      self.auto_play = True

    # Handle game over pause
    if self.game_over_time > 0:
      if now - self.game_over_time > 2.0:
        self._reset_game()
      else:
        # Flash the frame during game over
        frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)
        if int((now - self.game_over_time) * 6) % 2 == 0:
          frame[:, :] = (60, 0, 0)
        return frame

    # Process queued inputs
    while self._input_queue:
      self._process_input(self._input_queue.pop(0))

    # Auto-play
    if self.auto_play:
      self._auto_move(now)

    # Move aliens
    alien_interval = max(0.05, 0.4 / speed - len(self.aliens) * 0.001)
    if now - self.last_alien_move > alien_interval:
      self.last_alien_move = now

      # Check if any alien hits an edge
      hit_edge = False
      for alien in self.aliens:
        next_x = alien[0] + self.alien_dir
        if next_x < 0 or next_x >= self.width:
          hit_edge = True
          break

      if self.alien_drop:
        # Drop down one row
        for alien in self.aliens:
          alien[1] += 1
        self.alien_drop = False
      elif hit_edge:
        self.alien_dir *= -1
        self.alien_drop = True
      else:
        for alien in self.aliens:
          alien[0] += self.alien_dir

    # Alien shooting
    shoot_interval = max(0.15, 0.6 / speed)
    if self.aliens and now - self.last_alien_shoot > shoot_interval:
      self.last_alien_shoot = now
      # Random alien shoots
      shooter = random.choice(self.aliens)
      self.alien_bullets.append([shooter[0], shooter[1] + 1])

    # Move bullets
    bullet_speed_interval = max(0.02, 0.06 / speed)
    # Player bullets move up
    new_player_bullets = []
    for bullet in self.player_bullets:
      bullet[1] -= 1
      if bullet[1] >= 0:
        new_player_bullets.append(bullet)
    self.player_bullets = new_player_bullets

    # Alien bullets move down
    new_alien_bullets = []
    for bullet in self.alien_bullets:
      bullet[1] += 1
      if bullet[1] < self.height:
        new_alien_bullets.append(bullet)
    self.alien_bullets = new_alien_bullets

    # Collision: player bullets vs aliens
    hit_bullets = set()
    hit_aliens = set()
    for bi, (bx, by) in enumerate(self.player_bullets):
      for ai, (ax, ay, _) in enumerate(self.aliens):
        if bx == ax and by == ay:
          hit_bullets.add(bi)
          hit_aliens.add(ai)
          self.score += 10

    self.player_bullets = [b for i, b in enumerate(self.player_bullets) if i not in hit_bullets]
    self.aliens = [a for i, a in enumerate(self.aliens) if i not in hit_aliens]

    # Collision: alien bullets vs player
    player_y = self.height - 1
    for bx, by in self.alien_bullets:
      if by >= player_y and abs(bx - self.player_x) <= 0:
        self.game_over_time = now
        return np.zeros((self.width, self.height, 3), dtype=np.uint8)

    # Collision: aliens reach player row
    for ax, ay, _ in self.aliens:
      if ay >= player_y - 1:
        self.game_over_time = now
        return np.zeros((self.width, self.height, 3), dtype=np.uint8)

    # All aliens dead — respawn
    if not self.aliens:
      self._reset_game()

    # Render
    frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)

    # Draw aliens
    for ax, ay, row_idx in self.aliens:
      if 0 <= ax < self.width and 0 <= ay < self.height:
        color = self.ALIEN_COLORS[row_idx % len(self.ALIEN_COLORS)]
        frame[ax, ay] = color

    # Draw player (1-2 pixels wide depending on grid width)
    if self.width >= 8:
      # 2-pixel wide ship
      for dx in range(-1, 1):
        px = self.player_x + dx
        if 0 <= px < self.width:
          frame[px, player_y] = self.PLAYER_COLOR
    else:
      frame[max(0, min(self.player_x, self.width - 1)), player_y] = self.PLAYER_COLOR

    # Draw player bullets
    for bx, by in self.player_bullets:
      if 0 <= bx < self.width and 0 <= by < self.height:
        frame[bx, by] = self.BULLET_COLOR

    # Draw alien bullets
    for bx, by in self.alien_bullets:
      if 0 <= bx < self.width and 0 <= by < self.height:
        frame[bx, by] = self.ALIEN_BULLET_COLOR

    return frame


# ──────────────────────────────────────────────────────────────────────
#  Snake
# ──────────────────────────────────────────────────────────────────────


class Snake(Effect):
  """Classic snake — auto-plays endlessly."""

  CATEGORY = "game"
  DISPLAY_NAME = "Snake"
  DESCRIPTION = "Classic snake — auto-plays endlessly"
  PARAMS = [
    _P("Speed", "speed", 0.5, 5.0, 0.1, 1.5),
  ]

  FOOD_COLOR = (255, 0, 0)

  # Direction vectors: right, down, left, up
  DIRS = [(1, 0), (0, 1), (-1, 0), (0, -1)]
  DIR_NAMES = {'right': 0, 'down': 1, 'left': 2, 'up': 3}

  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    self.auto_play = True
    self.last_input = 0.0
    self._input_queue = []
    self._reset_game()

  def _reset_game(self):
    """Initialize or restart the game."""
    cx = self.width // 2
    cy = self.height // 2
    self.snake = [(cx, cy), (cx - 1, cy), (cx - 2, cy)]  # head first
    self.direction = 0  # index into DIRS (right)
    self.food = None
    self._place_food()
    self.last_step = 0.0
    self.game_over_time = 0.0
    self.score = 0
    self.grow_pending = 0

  def _place_food(self):
    """Place food at a random position not on the snake."""
    snake_set = set(self.snake)
    free = [
      (x, y)
      for x in range(self.width)
      for y in range(self.height)
      if (x, y) not in snake_set
    ]
    if free:
      self.food = random.choice(free)
    else:
      # Snake fills entire grid — you win, restart
      self.game_over_time = time.monotonic()

  def get_game_status(self):
    return {'score': self.score, 'game': 'snake', 'length': len(self.snake)}

  def handle_input(self, action: str):
    """Queue a player input: left, right, up, down."""
    self._input_queue.append(action)
    self.last_input = time.monotonic()
    self.auto_play = False

  def _process_input(self, action):
    if action in self.DIR_NAMES:
      new_dir = self.DIR_NAMES[action]
      # Prevent 180-degree reversal
      if (new_dir + 2) % 4 != self.direction:
        self.direction = new_dir
    elif action == 'rotate':
      # Rotate clockwise
      self.direction = (self.direction + 1) % 4
    elif action in ('drop', 'shoot', 'fast'):
      # Speed boost — move immediately
      self._move_snake()

  def _auto_move(self):
    """Simple AI: chase food while avoiding self-collision."""
    if self.food is None:
      return

    head_x, head_y = self.snake[0]
    snake_set = set(self.snake[:-1])  # exclude tail (it will move)

    # Score each direction
    best_dir = self.direction
    best_score = -999

    for d in range(4):
      # Don't reverse
      if (d + 2) % 4 == self.direction:
        continue

      dx, dy = self.DIRS[d]
      nx = (head_x + dx) % self.width  # wrap x
      ny = head_y + dy

      # Wall collision on y-axis
      if ny < 0 or ny >= self.height:
        continue

      # Self collision
      if (nx, ny) in snake_set:
        continue

      # Score: manhattan distance to food (lower is better)
      # Wrap-aware x distance
      dist_x = min(abs(nx - self.food[0]), self.width - abs(nx - self.food[0]))
      dist_y = abs(ny - self.food[1])
      dist = dist_x + dist_y
      score = -dist

      # Prefer continuing current direction (slight bias to reduce jitter)
      if d == self.direction:
        score += 0.5

      if score > best_score:
        best_score = score
        best_dir = d

    self.direction = best_dir

  def render(self, t: float, state) -> np.ndarray:
    now = time.monotonic()
    speed = self.params.get('speed', 1.5)

    # Re-enable auto-play after 5 seconds of no input
    if not self.auto_play and now - self.last_input > 5.0:
      self.auto_play = True

    # Handle game over pause
    if self.game_over_time > 0:
      if now - self.game_over_time > 1.5:
        self._reset_game()
      else:
        # Flash effect
        frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)
        if int((now - self.game_over_time) * 4) % 2 == 0:
          for sx, sy in self.snake:
            if 0 <= sx < self.width and 0 <= sy < self.height:
              frame[sx, sy] = (255, 0, 0)
        return frame

    # Process queued inputs
    while self._input_queue:
      self._process_input(self._input_queue.pop(0))

    # Step the game
    step_interval = max(0.05, 0.25 / speed)
    if now - self.last_step >= step_interval:
      self.last_step = now

      # Auto-play decides direction before moving
      if self.auto_play:
        self._auto_move()

      # Move snake
      head_x, head_y = self.snake[0]
      dx, dy = self.DIRS[self.direction]
      new_x = (head_x + dx) % self.width  # wrap x (cylindrical)
      new_y = head_y + dy

      # Wall collision on y-axis
      if new_y < 0 or new_y >= self.height:
        self.game_over_time = now
        return self._render_frame()

      # Self collision
      if (new_x, new_y) in set(self.snake):
        self.game_over_time = now
        return self._render_frame()

      self.snake.insert(0, (new_x, new_y))

      # Check food
      if self.food and (new_x, new_y) == self.food:
        self.grow_pending += 1
        self.score += 10
        self._place_food()

      if self.grow_pending > 0:
        self.grow_pending -= 1
      else:
        self.snake.pop()

    return self._render_frame()

  def _render_frame(self) -> np.ndarray:
    frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)

    # Draw snake with rainbow gradient
    length = len(self.snake)
    for i, (sx, sy) in enumerate(self.snake):
      if 0 <= sx < self.width and 0 <= sy < self.height:
        # Head is brightest, tail dims out
        frac = i / max(1, length - 1)
        hue = (frac * 0.8) % 1.0  # rainbow spread along body
        brightness = max(0.3, 1.0 - frac * 0.6)
        r, g, b = hsv_to_rgb(hue, 1.0, brightness)
        frame[sx, sy] = (r, g, b)

    # Draw food
    if self.food:
      fx, fy = self.food
      if 0 <= fx < self.width and 0 <= fy < self.height:
        frame[fx, fy] = self.FOOD_COLOR

    return frame


# ──────────────────────────────────────────────────────────────────────
#  Conway's Game of Life
# ──────────────────────────────────────────────────────────────────────


class GameOfLife(Effect):
  """Conway's cellular automata — self-seeding."""

  CATEGORY = "game"
  DISPLAY_NAME = "Game of Life"
  DESCRIPTION = "Conway's cellular automata — self-seeding"
  PARAMS = [
    _P("Speed", "speed", 0.5, 10.0, 0.5, 3.0),
    _P("Density", "density", 0.1, 0.5, 0.05, 0.25),
  ]

  # Age color ramp: young=bright cyan -> mature=blue -> old=dim purple
  MAX_AGE = 60

  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    self.grid = np.zeros((self.width, self.height), dtype=np.int32)
    self.age = np.zeros((self.width, self.height), dtype=np.int32)
    self.last_step = 0.0
    self.stagnation_count = 0
    self.prev_population = 0
    self.generation = 0
    self._seed()

  def _seed(self):
    """Random seed the grid."""
    density = self.params.get('density', 0.25)
    self.grid = (np.random.random((self.width, self.height)) < density).astype(np.int32)
    self.age = np.where(self.grid, 1, 0).astype(np.int32)
    self.stagnation_count = 0
    self.prev_population = 0
    self.generation = 0

  def _step(self):
    """Apply B3/S23 rules using vectorized neighbor counting."""
    # Count neighbors with toroidal wrapping
    neighbors = np.zeros_like(self.grid)
    for dx in (-1, 0, 1):
      for dy in (-1, 0, 1):
        if dx == 0 and dy == 0:
          continue
        neighbors += np.roll(np.roll(self.grid, dx, axis=0), dy, axis=1)

    # B3/S23: born if 3 neighbors, survive if 2 or 3
    born = (self.grid == 0) & (neighbors == 3)
    survive = (self.grid == 1) & ((neighbors == 2) | (neighbors == 3))

    new_grid = np.zeros_like(self.grid)
    new_grid[born] = 1
    new_grid[survive] = 1

    # Update ages
    new_age = np.zeros_like(self.age)
    new_age[survive] = np.minimum(self.age[survive] + 1, self.MAX_AGE)
    new_age[born] = 1

    self.grid = new_grid
    self.age = new_age
    self.generation += 1

    # Check for stagnation
    population = int(np.sum(self.grid))
    if population == self.prev_population:
      self.stagnation_count += 1
    else:
      self.stagnation_count = 0
    self.prev_population = population

    # Re-seed if stagnant (stable or oscillating) or dead
    if population == 0 or self.stagnation_count > 30:
      self._seed()

  def render(self, t: float, state) -> np.ndarray:
    now = time.monotonic()
    speed = self.params.get('speed', 3.0)

    step_interval = max(0.02, 0.2 / speed)
    if now - self.last_step >= step_interval:
      self.last_step = now
      self._step()

    # Color based on cell age
    frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)

    alive = self.grid > 0
    if not np.any(alive):
      return frame

    # Normalize age to 0-1 range
    age_norm = np.clip(self.age.astype(np.float64) / self.MAX_AGE, 0, 1)

    # Young = bright cyan (0.5 hue), Old = dim purple (0.75 hue)
    hue = np.where(alive, 0.5 + age_norm * 0.25, 0.0)
    sat = np.where(alive, 1.0, 0.0)
    val = np.where(alive, np.maximum(0.2, 1.0 - age_norm * 0.7), 0.0)

    colors = _hsv_array(hue, sat, val)
    frame[alive] = colors[alive]

    return frame


# ──────────────────────────────────────────────────────────────────────
#  Mario Runner — simplified World 1-1
# ──────────────────────────────────────────────────────────────────────

class MarioRunner(Effect):
  """Simplified Mario 1-1 auto-scrolling runner. Jump to avoid obstacles."""

  CATEGORY = "game"
  DISPLAY_NAME = "Mario Runner"
  DESCRIPTION = "Run through World 1-1 — tap jump to avoid pipes and goombas"
  PARAMS = [
    _P("Speed", "speed", 0.5, 3.0, 0.1, 1.0),
  ]

  # Colors (NES-inspired)
  SKY = (92, 148, 252)
  GROUND = (192, 112, 0)     # brown ground
  BRICK = (160, 80, 0)       # darker brick
  PIPE_GREEN = (0, 168, 0)
  PIPE_DARK = (0, 120, 0)
  QUESTION = (252, 188, 60)
  QUESTION_HIT = (160, 80, 0)
  MARIO = (228, 0, 0)        # red
  MARIO_SKIN = (252, 188, 116)
  GOOMBA = (172, 80, 48)
  COIN = (252, 216, 0)
  CLOUD = (252, 252, 252)
  FLAG = (0, 168, 0)

  # Level data: list of (y_position, type, params)
  # Types: 'pipe', 'goomba', 'question', 'cloud', 'gap', 'stair', 'flag'
  LEVEL_1_1 = [
    (16, 'question', {}),
    (25, 'goomba', {}),
    (30, 'pipe', {'h': 2}),
    (40, 'question', {}),
    (42, 'question', {}),
    (44, 'question', {}),
    (50, 'goomba', {}),
    (52, 'goomba', {}),
    (58, 'pipe', {'h': 3}),
    (68, 'pipe', {'h': 4}),
    (75, 'goomba', {}),
    (80, 'pipe', {'h': 4}),
    (95, 'gap', {'w': 2}),
    (105, 'question', {}),
    (110, 'goomba', {}),
    (115, 'goomba', {}),
    (120, 'pipe', {'h': 2}),
    (130, 'question', {}),
    (135, 'goomba', {}),
    (140, 'gap', {'w': 3}),
    (150, 'stair', {'h': 4}),
    (158, 'stair', {'h': 4}),
    (165, 'pipe', {'h': 2}),
    (175, 'goomba', {}),
    (180, 'goomba', {}),
    (190, 'stair', {'h': 8}),
    (200, 'flag', {}),
  ]

  def __init__(self, width, height, params=None):
    super().__init__(width, height, params)
    self.auto_play = True
    self.last_input = 0.0
    self._input_queue = []
    self._reset()

  def _reset(self):
    self.scroll = 0.0       # current scroll position (float)
    self._prev_t = None
    self.mario_x = self.width // 2  # column
    self.mario_y = 0.0      # height above ground (0 = on ground)
    self.mario_vy = 0.0     # vertical velocity
    self.on_ground = True
    self.alive = True
    self.score = 0
    self.death_timer = 0.0
    self.coins = 0
    self.level_length = 210
    self.won = False
    self.ground_level = 2   # ground is 2 pixels from bottom

  def get_game_status(self):
    return {'score': self.score, 'game': 'mario', 'coins': self.coins}

  def handle_input(self, action: str):
    self._input_queue.append(action)
    self.last_input = time.monotonic()
    self.auto_play = False

  def render(self, t, state):
    if self._prev_t is None:
      self._prev_t = t
    dt = min(t - self._prev_t, 0.05)
    self._prev_t = t

    speed = self.params.get('speed', 1.0)
    w, h = self.width, self.height

    # Process input
    for action in self._input_queue:
      if action in ('jump', 'rotate', 'up', 'drop', 'shoot') and self.on_ground and self.alive:
        self.mario_vy = 12.0  # jump!
        self.on_ground = False
    self._input_queue.clear()

    # Auto-play: jump over obstacles
    if self.auto_play and self.alive and time.monotonic() - self.last_input > 5.0:
      scroll_y = int(self.scroll)
      for ly, ltype, lparams in self.LEVEL_1_1:
        ahead = ly - scroll_y
        if 2 <= ahead <= 5:
          if ltype in ('pipe', 'goomba', 'gap', 'stair') and self.on_ground:
            self.mario_vy = 12.0
            self.on_ground = False
            break

    if not self.alive:
      self.death_timer -= dt
      if self.death_timer <= 0:
        self._reset()

    if self.won:
      self.death_timer -= dt
      if self.death_timer <= 0:
        self._reset()

    # Physics
    if self.alive:
      self.scroll += speed * 15 * dt

      # Gravity
      self.mario_vy -= 40 * dt
      self.mario_y += self.mario_vy * dt

      # Ground collision
      if self.mario_y <= 0:
        self.mario_y = 0
        self.mario_vy = 0
        self.on_ground = True

      # Check level end
      if self.scroll >= self.level_length:
        self.won = True
        self.death_timer = 3.0
        self.score += 1000

      # Check collisions with obstacles
      scroll_y = int(self.scroll)
      mario_screen_row = h - self.ground_level - 1 - int(self.mario_y)
      for ly, ltype, lparams in self.LEVEL_1_1:
        obj_screen = ly - scroll_y
        if ltype == 'goomba' and abs(obj_screen - mario_screen_row) < 2 and self.mario_y < 1:
          self.alive = False
          self.death_timer = 2.0
        elif ltype == 'pipe':
          ph = lparams.get('h', 2)
          if 0 <= obj_screen - mario_screen_row < ph and self.mario_y < ph:
            if self.mario_vy <= 0 and self.mario_y < ph - 0.5:
              self.alive = False
              self.death_timer = 2.0
        elif ltype == 'gap':
          gw = lparams.get('w', 2)
          if 0 <= mario_screen_row - obj_screen < gw and self.mario_y <= 0:
            self.alive = False
            self.death_timer = 2.0
        elif ltype == 'question' and abs(obj_screen - (mario_screen_row - 4)) < 1 and self.mario_vy > 0:
          self.score += 100
          self.coins += 1

    # Render
    frame = np.zeros((w, h, 3), dtype=np.uint8)
    scroll_y = int(self.scroll)

    # Sky background
    frame[:, :] = self.SKY

    # Ground
    frame[:, -self.ground_level:] = self.GROUND
    # Brick pattern on ground
    for gx in range(w):
      if (gx + scroll_y) % 2 == 0:
        frame[gx, -1] = self.BRICK

    # Draw level objects
    for ly, ltype, lparams in self.LEVEL_1_1:
      screen_y = ly - scroll_y
      if screen_y < -5 or screen_y >= h + 5:
        continue

      row = h - self.ground_level - 1 - max(0, screen_y - (h - self.ground_level - 1))
      actual_row = h - self.ground_level - 1

      if ltype == 'pipe':
        ph = lparams.get('h', 2)
        px = w // 2 - 1
        for py_off in range(ph):
          yr = actual_row - py_off
          if 0 <= (ly - scroll_y) < h and 0 <= yr < h:
            for dx in range(3):
              if 0 <= px + dx < w:
                frame[px + dx, yr] = self.PIPE_DARK if dx == 0 else self.PIPE_GREEN
        # Pipe cap (wider)
        yr = actual_row - ph
        if 0 <= yr < h:
          for dx in range(-1, 4):
            if 0 <= px + dx < w:
              frame[px + dx, yr] = self.PIPE_GREEN

      elif ltype == 'goomba':
        yr = actual_row
        gx = w // 2
        if 0 <= (ly - scroll_y) < h and 0 <= yr < h and 0 <= gx < w:
          frame[gx, yr] = self.GOOMBA
          if gx + 1 < w:
            frame[gx + 1, yr] = self.GOOMBA

      elif ltype == 'question':
        yr = actual_row - 4  # floating above ground
        qx = w // 2
        if 0 <= yr < h and 0 <= qx < w:
          frame[qx, yr] = self.QUESTION

      elif ltype == 'stair':
        sh = lparams.get('h', 4)
        sx = w // 2 - 1
        for step in range(sh):
          yr = actual_row - step
          if 0 <= yr < h:
            for dx in range(step + 1):
              if 0 <= sx + dx < w:
                frame[sx + dx, yr] = self.BRICK

      elif ltype == 'flag':
        fx = w // 2
        for fy in range(8):
          yr = actual_row - fy
          if 0 <= yr < h and 0 <= fx < w:
            frame[fx, yr] = self.FLAG if fy > 0 else self.COIN

    # Draw Mario
    if self.alive:
      mx = self.mario_x
      my = h - self.ground_level - 1 - int(self.mario_y)
      if 0 <= mx < w and 0 <= my < h:
        frame[mx, my] = self.MARIO  # body
      if 0 <= mx < w and 0 <= my - 1 < h:
        frame[mx, my - 1] = self.MARIO_SKIN  # head

    # Score flash on death
    if not self.alive:
      flash = int((self.death_timer * 4) % 2)
      if flash:
        frame[:, :] = (40, 0, 0)

    # Win flash
    if self.won:
      flash = int((self.death_timer * 2) % 2)
      if flash:
        frame[:, :, 1] = np.minimum(frame[:, :, 1].astype(np.uint16) + 60, 255).astype(np.uint8)

    return frame



# ──────────────────────────────────────────────────────────────────────
#  Registry
# ──────────────────────────────────────────────────────────────────────

GAME_EFFECTS = {
  'space_invaders': SpaceInvaders,
  'snake_game': Snake,
  'game_of_life': GameOfLife,
  'mario_runner': MarioRunner,
}
