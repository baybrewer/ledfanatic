"""
Shared text rasterization for LED display.

Spine text: glyphs rotated 90 deg clockwise (letter tops face right, like a
US book spine), reading top-to-bottom. Used by the scrolling-text effect and
the pot-menu category overlay.
"""

import numpy as np
from PIL import Image, ImageDraw, ImageFont

_FONT_PATHS = [
  "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
  "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def get_font(size: int):
  for path in _FONT_PATHS:
    try:
      return ImageFont.truetype(path, size)
    except (OSError, IOError):
      continue
  return ImageFont.load_default()


def render_spine_text(text: str, panel_width: int,
                      color: tuple = (255, 255, 255)) -> np.ndarray:
  """Render text rotated 90 deg CW, sized to panel_width.

  Returns uint8 array of shape (panel_width, text_len, 3). Letter tops face
  +x (right side); text reads top-to-bottom along the y axis.
  """
  panel_width = max(1, panel_width)
  font = get_font(panel_width)

  dummy = Image.new('RGB', (1, 1))
  draw = ImageDraw.Draw(dummy)
  bbox = draw.textbbox((0, 0), text, font=font)
  text_w = bbox[2] - bbox[0] + 4
  text_h = bbox[3] - bbox[1] + 4

  img = Image.new('RGB', (text_w, text_h), (0, 0, 0))
  draw = ImageDraw.Draw(img)
  draw.text((2, 2 - bbox[1]), text, font=font, fill=tuple(color))

  # PIL rotate(-90) = 90 deg clockwise: glyph tops end up on the right,
  # reading direction becomes top-to-bottom.
  img = img.rotate(-90, expand=True, resample=Image.BICUBIC)

  new_w = panel_width
  new_h = max(1, int(img.height * new_w / max(img.width, 1)))
  img = img.resize((new_w, new_h), Image.LANCZOS)

  # PIL (rows, cols, 3) -> project convention (width, height, 3)
  return np.array(img).transpose(1, 0, 2)


def composite_spine_overlay(frame: np.ndarray, text_img: np.ndarray,
                            x0: int, y_offset: int, alpha: float) -> np.ndarray:
  """Composite spine text onto frame with a dimmed backing box.

  frame: (width, height, 3) uint8 — modified copy returned.
  text_img: output of render_spine_text.
  x0: left column of the overlay region (region width = text_img.shape[0]).
  y_offset: first text row to show (for vertical scrolling of long names).
  alpha: 0.0-1.0 overall overlay strength (used for fade-out).
  """
  alpha = max(0.0, min(1.0, alpha))
  if alpha == 0.0:
    return frame
  out = frame.astype(np.float32).copy()
  fw, fh = out.shape[0], out.shape[1]
  pw, tlen = text_img.shape[0], text_img.shape[1]
  x1 = min(fw, x0 + pw)
  if x1 <= x0:
    return frame

  # Dim the backing region toward black for legibility
  out[x0:x1, :] *= (1.0 - 0.75 * alpha)

  # Draw visible slice of the text
  y_offset = int(y_offset)
  visible = min(fh, tlen - y_offset)
  if visible > 0 and y_offset >= 0:
    text_slice = text_img[:x1 - x0, y_offset:y_offset + visible].astype(np.float32)
    out[x0:x1, :visible] += text_slice * alpha

  return np.clip(out, 0, 255).astype(np.uint8)
