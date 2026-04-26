"""
Scrolling text effect — renders text bottom-to-top on the LED pillar.

Uses PIL for antialiased text rendering, scrolls vertically.
"""

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from .base import Effect


class _Param:
  def __init__(self, label, attr, lo, hi, step, default):
    self.label, self.attr, self.lo, self.hi = label, attr, lo, hi
    self.step, self.default = step, default


class ScrollingText(Effect):
    """Scrolls text bottom-to-top with antialiasing."""

    CATEGORY = "generative"
    DISPLAY_NAME = "Scrolling Text"
    DESCRIPTION = "Scroll a message up the pillar with smooth antialiased text"
    PALETTE_SUPPORT = False

    PARAMS = [
        _Param("Speed", "speed", 0.1, 5.0, 0.1, 1.0),
    ]

    # Default message — can be overridden via params
    _DEFAULT_TEXT = "LED FANATIC"

    def __init__(self, width, height, params=None):
        super().__init__(width, height, params)
        self._scroll_offset = 0.0
        self._text_image = None
        self._text_height = 0
        self._last_t = None
        self._current_text = None
        self._render_text()

    def _render_text(self):
        """Pre-render the text message into a pixel buffer."""
        text = self.params.get('text', self._DEFAULT_TEXT)
        color = self.params.get('color', '#00FFFF')
        cache_key = f"{text}|{color}"
        if cache_key == self._current_text and self._text_image is not None:
            return
        self._current_text = cache_key

        cols = self.width  # 10 pixels wide

        # Use a pixel font size that fits ~10px wide
        # Each character at size 8 is about 5-6px wide, so ~1-2 chars per row
        # Render rotated: draw text horizontally, then rotate 90 degrees
        # so it reads bottom-to-top on a tall narrow display

        font_size = cols  # font height matches pillar width
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
        except (OSError, IOError):
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", font_size)
            except (OSError, IOError):
                font = ImageFont.load_default()

        # Measure text rendered horizontally
        dummy = Image.new('RGB', (1, 1))
        draw = ImageDraw.Draw(dummy)
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0] + 4
        text_h = bbox[3] - bbox[1] + 4

        # Render text horizontally with antialiasing
        img = Image.new('RGB', (text_w, text_h), (0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Get color from params
        color_hex = self.params.get('color', '#00FFFF')
        try:
            r = int(color_hex[1:3], 16)
            g = int(color_hex[3:5], 16)
            b = int(color_hex[5:7], 16)
        except (ValueError, IndexError):
            r, g, b = 0, 255, 255

        draw.text((2, 2 - bbox[1]), text, font=font, fill=(r, g, b))

        # Rotate 90 degrees CW so text reads with top to the right
        img = img.rotate(-90, expand=True, resample=Image.BICUBIC)

        # Resize width to match pillar columns, keep aspect ratio
        new_w = cols
        new_h = int(img.height * new_w / img.width)
        if new_h < 1:
            new_h = 1
        img = img.resize((new_w, new_h), Image.LANCZOS)

        # Convert to numpy array (width, height, 3)
        arr = np.array(img)  # (new_h, new_w, 3) in PIL order (rows, cols)
        self._text_image = arr.transpose(1, 0, 2)  # (cols, text_height, 3)
        self._text_height = new_h

    def update_params(self, params: dict):
        super().update_params(params)
        self._render_text()

    def render(self, t: float, state) -> np.ndarray:
        if self._last_t is None:
            self._last_t = t
        dt = t - self._last_t
        self._last_t = t

        speed = self.params.get('speed', 1.0)
        pixels_per_sec = speed * 20  # 20 pixels/sec at speed 1.0

        self._scroll_offset += dt * pixels_per_sec

        frame = np.zeros((self.width, self.height, 3), dtype=np.uint8)

        if self._text_image is None or self._text_height == 0:
            return frame

        # Total scroll distance: text scrolls fully through the display
        total_scroll = self.height + self._text_height

        # Current position (wraps)
        offset = int(self._scroll_offset) % total_scroll

        # Text enters from the bottom: y position of text top edge
        text_y = self.height - offset

        # Copy visible portion of text into frame
        for ty in range(self._text_height):
            fy = text_y + ty
            if 0 <= fy < self.height:
                frame[:, fy] = self._text_image[:, ty]

        return frame
