"""
Tetris effect — endless falling blocks on the LED pillar.

10-wide grid (matches pillar columns), 83 rows tall.
Auto-plays when no input received for 5 seconds.
Accepts controls via renderer.tetris_input() method.
"""

import time
import random
import numpy as np
from .base import Effect


class _Param:
  def __init__(self, label, attr, lo, hi, step, default):
    self.label, self.attr, self.lo, self.hi = label, attr, lo, hi
    self.step, self.default = step, default

# Standard Tetris pieces (rotations as relative coords from pivot)
PIECES = {
    'I': [[(0,0),(1,0),(2,0),(3,0)], [(0,0),(0,1),(0,2),(0,3)]],
    'O': [[(0,0),(1,0),(0,1),(1,1)]],
    'T': [[(0,0),(1,0),(2,0),(1,1)], [(0,0),(0,1),(0,2),(1,1)],
           [(0,1),(1,1),(2,1),(1,0)], [(1,0),(1,1),(1,2),(0,1)]],
    'S': [[(1,0),(2,0),(0,1),(1,1)], [(0,0),(0,1),(1,1),(1,2)]],
    'Z': [[(0,0),(1,0),(1,1),(2,1)], [(1,0),(1,1),(0,1),(0,2)]],
    'L': [[(0,0),(1,0),(2,0),(0,1)], [(0,0),(0,1),(0,2),(1,2)],
           [(2,1),(0,1),(1,1),(2,0)], [(0,0),(1,0),(1,1),(1,2)]],
    'J': [[(0,0),(1,0),(2,0),(2,1)], [(0,0),(0,1),(0,2),(1,0)],
           [(0,0),(0,1),(1,1),(2,1)], [(1,0),(1,1),(1,2),(0,2)]],
}

PIECE_COLORS = {
    'I': (0, 255, 255),
    'O': (255, 255, 0),
    'T': (160, 0, 255),
    'S': (0, 255, 0),
    'Z': (255, 0, 0),
    'L': (255, 165, 0),
    'J': (0, 0, 255),
}

PIECE_NAMES = list(PIECES.keys())


class TetrisAutoplay(Effect):
    """Autoplay Tetris as a visual effect — pro-level AI plays endlessly."""

    CATEGORY = "generative"
    DISPLAY_NAME = "Tetris (Auto)"
    DESCRIPTION = "Watch an AI play Tetris endlessly at high speed"
    PALETTE_SUPPORT = False

    PARAMS = [
        _Param("Speed", "speed", 0.1, 2.0, 0.1, 1.0),
    ]

    def __init__(self, width, height, params=None):
        super().__init__(width, height, params)
        self._game = Tetris(width, height, params)
        self._game.auto_play = True
        self._update_speed()

    def _update_speed(self):
        speed = self.params.get('speed', 1.0)
        # 0.1 = 0.13s/row (slow visible), 1.0 = 0.07s/row, 2.0 = 0.005s/row (lightning)
        interval = max(0.005, 0.14 - speed * 0.067)
        self._game._speed_override = interval
        self._game.drop_interval = interval

    def update_params(self, params: dict):
        super().update_params(params)
        self._update_speed()

    def render(self, t: float, state) -> np.ndarray:
        return self._game.render(t, state)


class Tetris(Effect):
    """Endless Tetris game. Auto-plays when idle."""

    CATEGORY = "game"
    DISPLAY_NAME = "Tetris"
    DESCRIPTION = "Endless Tetris — play from your phone or watch it auto-play"

    def __init__(self, width, height, params=None):
        super().__init__(width, height, params)
        self.board = np.zeros((width, height, 3), dtype=np.uint8)
        self.grid = [[None] * width for _ in range(height)]  # None or color tuple
        self.current_piece = None
        self.current_type = None
        self.current_rot = 0
        self.piece_x = 0
        self.piece_y = 0
        self.last_drop = 0
        self.last_input = 0
        self.drop_interval = 0.5  # seconds between drops
        self._speed_override = None  # set by TetrisAutoplay to lock speed
        self.auto_play = True
        self.auto_move_time = 0
        self.game_over_time = 0
        self.lines_cleared = 0
        self._input_queue = []
        self._spawn_piece()

    def _spawn_piece(self):
        self.current_type = random.choice(PIECE_NAMES)
        self.current_rot = 0
        rotations = PIECES[self.current_type]
        cells = rotations[0]
        piece_width = max(c[0] for c in cells) + 1
        self.piece_x = (self.width - piece_width) // 2
        self.piece_y = 0
        if self._collides(self.piece_x, self.piece_y, self.current_rot):
            # Game over — clear board and restart
            self.grid = [[None] * self.width for _ in range(self.height)]
            self.game_over_time = time.monotonic()
            self.lines_cleared = 0

    def _get_cells(self, rot=None):
        if rot is None:
            rot = self.current_rot
        rotations = PIECES[self.current_type]
        return rotations[rot % len(rotations)]

    def _collides(self, px, py, rot):
        cells = PIECES[self.current_type][rot % len(PIECES[self.current_type])]
        for cx, cy in cells:
            x = px + cx
            y = py + cy
            if x < 0 or x >= self.width or y >= self.height:
                return True
            if y >= 0 and self.grid[y][x] is not None:
                return True
        return False

    def _lock_piece(self):
        color = PIECE_COLORS[self.current_type]
        cells = self._get_cells()
        for cx, cy in cells:
            x = self.piece_x + cx
            y = self.piece_y + cy
            if 0 <= x < self.width and 0 <= y < self.height:
                self.grid[y][x] = color
        self._clear_lines()
        self._spawn_piece()

    def _clear_lines(self):
        new_grid = [row for row in self.grid if any(c is None for c in row)]
        cleared = self.height - len(new_grid)
        self.lines_cleared += cleared
        # Add empty rows at top
        for _ in range(cleared):
            new_grid.insert(0, [None] * self.width)
        self.grid = new_grid
        # Speed up slightly with lines cleared
        if self._speed_override is None:
            self.drop_interval = max(0.15, 0.5 - self.lines_cleared * 0.005)

    def handle_input(self, action: str):
        """Queue a player input: left, right, rotate, down, drop, fast."""
        self._input_queue.append(action)
        self.last_input = time.monotonic()
        self.auto_play = False

    def _process_input(self, action):
        if action == 'left':
            if not self._collides(self.piece_x - 1, self.piece_y, self.current_rot):
                self.piece_x -= 1
        elif action == 'right':
            if not self._collides(self.piece_x + 1, self.piece_y, self.current_rot):
                self.piece_x += 1
        elif action == 'rotate':
            new_rot = (self.current_rot + 1) % len(PIECES[self.current_type])
            if not self._collides(self.piece_x, self.piece_y, new_rot):
                self.current_rot = new_rot
            # Wall kick: try shifting left or right
            elif not self._collides(self.piece_x - 1, self.piece_y, new_rot):
                self.piece_x -= 1
                self.current_rot = new_rot
            elif not self._collides(self.piece_x + 1, self.piece_y, new_rot):
                self.piece_x += 1
                self.current_rot = new_rot
        elif action == 'down':
            if not self._collides(self.piece_x, self.piece_y + 1, self.current_rot):
                self.piece_y += 1
        elif action == 'fast':
            # Phase 1: fast drop (3x speed) — piece falls quickly but doesn't lock
            self.drop_interval = 0.08
        elif action == 'drop':
            # Phase 2: instant drop — locks immediately
            while not self._collides(self.piece_x, self.piece_y + 1, self.current_rot):
                self.piece_y += 1
            self._lock_piece()
            self.last_drop = time.monotonic()
            # Reset speed after hard drop
            if self._speed_override is None:
            self.drop_interval = max(0.15, 0.5 - self.lines_cleared * 0.005)

    def _auto_move(self, t):
        """Pro-level AI: evaluates all placements, picks the best, moves fast."""
        if t - self.auto_move_time < self.drop_interval * 0.3:  # scales with drop speed
            return
        self.auto_move_time = t

        # Compute best placement once per piece
        if not hasattr(self, '_auto_target_x') or self._auto_target_x is None:
            best_score = -999999
            best_x = self.piece_x
            best_rot = self.current_rot
            rotations = PIECES[self.current_type]

            for rot in range(len(rotations)):
                cells = rotations[rot]
                min_cx = min(c[0] for c in cells)
                max_cx = max(c[0] for c in cells)
                for px in range(-min_cx, self.width - max_cx):
                    # Simulate drop
                    py = self.piece_y
                    while not self._collides_at(px, py + 1, rot):
                        py += 1
                    score = self._evaluate_placement(px, py, rot)
                    if score > best_score:
                        best_score = score
                        best_x = px
                        best_rot = rot

            self._auto_target_x = best_x
            self._auto_target_rot = best_rot

        # Execute moves toward target
        # First rotate
        if self.current_rot != self._auto_target_rot:
            self._process_input('rotate')
        # Then move horizontally
        elif self.piece_x < self._auto_target_x:
            self._process_input('right')
        elif self.piece_x > self._auto_target_x:
            self._process_input('left')
        # In position — let gravity do the rest (don't hard drop)

    def _collides_at(self, px, py, rot):
        """Check collision at arbitrary position/rotation."""
        cells = PIECES[self.current_type][rot % len(PIECES[self.current_type])]
        for cx, cy in cells:
            x = px + cx
            y = py + cy
            if x < 0 or x >= self.width or y >= self.height:
                return True
            if y >= 0 and self.grid[y][x] is not None:
                return True
        return False

    def _evaluate_placement(self, px, py, rot):
        """Score a placement. Higher = better. Rewards filled rows, flat surface, no holes."""
        cells = PIECES[self.current_type][rot % len(PIECES[self.current_type])]
        # Simulate placing the piece
        test_grid = [row[:] for row in self.grid]
        color = (1, 1, 1)  # dummy
        for cx, cy in cells:
            x = px + cx
            y = py + cy
            if 0 <= x < self.width and 0 <= y < self.height:
                test_grid[y][x] = color

        # Score components
        score = 0

        # Reward: lines cleared (heavily weighted)
        lines = sum(1 for row in test_grid if all(c is not None for c in row))
        score += lines * 1000

        # Reward: piece placed low (maximize y)
        score += py * 5

        # Penalty: holes (empty cells with filled cells above)
        holes = 0
        for x in range(self.width):
            found_block = False
            for y in range(self.height):
                if test_grid[y][x] is not None:
                    found_block = True
                elif found_block:
                    holes += 1
        score -= holes * 80

        # Penalty: height variance (bumpiness)
        heights = []
        for x in range(self.width):
            h = 0
            for y in range(self.height):
                if test_grid[y][x] is not None:
                    h = self.height - y
                    break
            heights.append(h)
        bumpiness = sum(abs(heights[i] - heights[i+1]) for i in range(len(heights)-1))
        score -= bumpiness * 15

        # Penalty: max height
        max_h = max(heights) if heights else 0
        score -= max_h * 3

        return score

    def render(self, t: float, state) -> np.ndarray:
        now = time.monotonic()

        # Re-enable auto-play after 5 seconds of no input
        if not self.auto_play and now - self.last_input > 5.0:
            self.auto_play = True
            self.drop_interval = 0.15  # auto-play speed
            self._auto_target_x = None

        # Process queued inputs
        while self._input_queue:
            self._process_input(self._input_queue.pop(0))

        # Auto-play
        if self.auto_play:
            self._auto_move(now)

        # Gravity drop
        if now - self.last_drop >= self.drop_interval:
            self.last_drop = now
            if not self._collides(self.piece_x, self.piece_y + 1, self.current_rot):
                self.piece_y += 1
            else:
                self._lock_piece()
                self._auto_target_x = None
                # Reset speed after lock
                if self._speed_override is None:
            self.drop_interval = max(0.15, 0.5 - self.lines_cleared * 0.005)

        # Render board
        frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)

        # Draw locked pieces
        for y in range(self.height):
            for x in range(self.width):
                if self.grid[y][x] is not None:
                    frame[x, y] = self.grid[y][x]

        # Draw current piece
        if self.current_type:
            color = PIECE_COLORS[self.current_type]
            cells = self._get_cells()
            for cx, cy in cells:
                x = self.piece_x + cx
                y = self.piece_y + cy
                if 0 <= x < self.width and 0 <= y < self.height:
                    frame[x, y] = color

        return frame
