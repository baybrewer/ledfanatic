"""
Scrolling text effect — renders text with configurable direction and panels.

Uses PIL for antialiased text rendering. Supports vertical (bottom→top)
and horizontal (right→left) scrolling, with 1-4 panel repeats.
"""

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from .base import Effect


class _Param:
  def __init__(self, label, attr, lo, hi, step, default):
    self.label, self.attr, self.lo, self.hi = label, attr, lo, hi
    self.step, self.default = step, default


class ScrollingText(Effect):
    """Scrolls text with configurable direction and panel count."""

    CATEGORY = "generative"
    DISPLAY_NAME = "Scrolling Text"
    DESCRIPTION = "Scroll a message across the display with smooth antialiased text"
    PALETTE_SUPPORT = False

    PARAMS = [
        _Param("Speed", "speed", 0.1, 5.0, 0.1, 1.0),
    ]

    _DEFAULT_TEXT = "LED FANATIC"

    def __init__(self, width, height, params=None):
        super().__init__(width, height, params)
        self._scroll_offset = 0.0
        self._text_image = None
        self._text_len = 0  # length along scroll axis
        self._last_t = None
        self._cache_key = None
        self._render_text()

    def _get_font(self, size):
        for path in [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]:
            try:
                return ImageFont.truetype(path, size)
            except (OSError, IOError):
                continue
        return ImageFont.load_default()

    def _parse_color(self):
        color_hex = self.params.get('color', '#00FFFF')
        try:
            return int(color_hex[1:3], 16), int(color_hex[3:5], 16), int(color_hex[5:7], 16)
        except (ValueError, IndexError):
            return 0, 255, 255

    def _render_text(self):
        """Pre-render text into a pixel buffer based on direction and panels."""
        text = self.params.get('text', self._DEFAULT_TEXT)
        color = self.params.get('color', '#00FFFF')
        direction = self.params.get('direction', 'vertical')
        panels = max(1, int(self.params.get('panels', 1)))

        cache_key = f"{text}|{color}|{direction}|{panels}|{self.width}|{self.height}"
        if cache_key == self._cache_key and self._text_image is not None:
            return
        self._cache_key = cache_key

        r, g, b = self._parse_color()
        panel_width = max(1, self.width // panels)

        if direction == 'horizontal':
            # Horizontal scroll: text rendered at full grid height, scrolls left
            font_size = self.height
            font = self._get_font(font_size)

            dummy = Image.new('RGB', (1, 1))
            draw = ImageDraw.Draw(dummy)
            bbox = draw.textbbox((0, 0), text, font=font)
            text_w = bbox[2] - bbox[0] + 4
            text_h = bbox[3] - bbox[1] + 4

            img = Image.new('RGB', (text_w, text_h), (0, 0, 0))
            draw = ImageDraw.Draw(img)
            draw.text((2, 2 - bbox[1]), text, font=font, fill=(r, g, b))

            # Resize height to match grid height
            new_h = self.height
            new_w = int(img.width * new_h / max(img.height, 1))
            if new_w < 1:
                new_w = 1
            img = img.resize((new_w, new_h), Image.LANCZOS)

            # PIL (rows, cols, 3) → numpy (width, height, 3)
            arr = np.array(img).transpose(1, 0, 2)  # (new_w, height, 3)
            self._text_image = arr
            self._text_len = new_w  # scroll along x axis

        else:
            # Vertical scroll: text rotated 90° CW, scrolls bottom→top
            font_size = panel_width
            font = self._get_font(font_size)

            dummy = Image.new('RGB', (1, 1))
            draw = ImageDraw.Draw(dummy)
            bbox = draw.textbbox((0, 0), text, font=font)
            text_w = bbox[2] - bbox[0] + 4
            text_h = bbox[3] - bbox[1] + 4

            img = Image.new('RGB', (text_w, text_h), (0, 0, 0))
            draw = ImageDraw.Draw(img)
            draw.text((2, 2 - bbox[1]), text, font=font, fill=(r, g, b))

            # Rotate 90° CW so text reads bottom-to-top
            img = img.rotate(-90, expand=True, resample=Image.BICUBIC)

            # Resize width to match panel width
            new_w = panel_width
            new_h = int(img.height * new_w / max(img.width, 1))
            if new_h < 1:
                new_h = 1
            img = img.resize((new_w, new_h), Image.LANCZOS)

            arr = np.array(img).transpose(1, 0, 2)  # (panel_width, text_height, 3)
            self._text_image = arr
            self._text_len = new_h  # scroll along y axis

    def update_params(self, params: dict):
        super().update_params(params)
        self._render_text()

    def render(self, t: float, state) -> np.ndarray:
        if self._last_t is None:
            self._last_t = t
        dt = t - self._last_t
        self._last_t = t

        speed = self.params.get('speed', 1.0)
        direction = self.params.get('direction', 'vertical')
        panels = max(1, int(self.params.get('panels', 1)))
        pixels_per_sec = speed * 20

        self._scroll_offset += dt * pixels_per_sec

        frame = np.zeros((self.width, self.height, 3), dtype=np.float32)

        if self._text_image is None or self._text_len == 0:
            return frame.astype(np.uint8)

        text_f = self._text_image.astype(np.float32)

        if direction == 'horizontal':
            # Scroll right→left along x axis
            total_scroll = self.width + self._text_len
            raw_offset = self._scroll_offset % total_scroll
            offset_int = int(raw_offset)
            frac = raw_offset - offset_int

            text_x = self.width - offset_int
            tw = self._text_len
            th = min(text_f.shape[1], self.height)

            for tx in range(tw):
                fx = text_x + tx
                if 0 <= fx < self.width:
                    frame[fx, :th] += text_f[tx, :th] * (1.0 - frac)
                fx_left = fx - 1
                if 0 <= fx_left < self.width:
                    frame[fx_left, :th] += text_f[tx, :th] * frac

        else:
            # Scroll bottom→top along y axis
            panel_width = max(1, self.width // panels)
            total_scroll = self.height + self._text_len
            raw_offset = self._scroll_offset % total_scroll
            offset_int = int(raw_offset)
            frac = raw_offset - offset_int

            text_y = self.height - offset_int
            pw = min(text_f.shape[0], panel_width)

            for ty in range(self._text_len):
                fy = text_y + ty
                if 0 <= fy < self.height:
                    for p in range(panels):
                        x_off = p * panel_width
                        frame[x_off:x_off + pw, fy] += text_f[:pw, ty] * (1.0 - frac)
                fy_up = fy - 1
                if 0 <= fy_up < self.height:
                    for p in range(panels):
                        x_off = p * panel_width
                        frame[x_off:x_off + pw, fy_up] += text_f[:pw, ty] * frac

        return np.clip(frame, 0, 255).astype(np.uint8)
