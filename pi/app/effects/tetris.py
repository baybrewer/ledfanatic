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
        self.drop_interval = max(0.15, 0.5 - self.lines_cleared * 0.005)

    def handle_input(self, action: str):
        """Queue a player input: left, right, rotate, down, drop."""
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
        elif action == 'drop':
            while not self._collides(self.piece_x, self.piece_y + 1, self.current_rot):
                self.piece_y += 1
            self._lock_piece()
            self.last_drop = time.monotonic()

    def _auto_move(self, t):
        """Simple AI: try to fill from left to right, rotate randomly."""
        if t - self.auto_move_time < 0.1:
            return
        self.auto_move_time = t

        # Simple strategy: move toward a target column
        cells = self._get_cells()
        piece_center = self.piece_x + max(c[0] for c in cells) / 2

        # Pick a random target (changes per piece)
        if not hasattr(self, '_auto_target') or self._auto_target is None:
            self._auto_target = random.randint(0, self.width - 1)
            if random.random() < 0.4:
                self._process_input('rotate')

        if piece_center < self._auto_target - 0.5:
            self._process_input('right')
        elif piece_center > self._auto_target + 0.5:
            self._process_input('left')

    def render(self, t: float, state) -> np.ndarray:
        now = time.monotonic()

        # Re-enable auto-play after 5 seconds of no input
        if not self.auto_play and now - self.last_input > 5.0:
            self.auto_play = True

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
                self._auto_target = None

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
