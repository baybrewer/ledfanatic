"""Tests for spine-text rendering (book-spine orientation, top of letters faces right)."""

import numpy as np

from app.effects.textrender import render_spine_text, composite_spine_overlay


class TestRenderSpineText:
  def test_shape_and_ink(self):
    img = render_spine_text("AMBIENT", panel_width=10)
    assert img.dtype == np.uint8
    assert img.shape[0] == 10
    assert img.shape[2] == 3
    assert img.sum() > 0  # has ink

  def test_longer_text_is_longer(self):
    short = render_spine_text("AB", panel_width=10)
    long = render_spine_text("ABABABAB", panel_width=10)
    assert long.shape[1] > short.shape[1]

  def test_letter_tops_face_right(self):
    # 'T' has its bar at the glyph top; rotated 90 deg CW the bar lands at high x.
    img = render_spine_text("T", panel_width=20).astype(np.float64)
    half = img.shape[0] // 2
    left_ink = img[:half].sum()
    right_ink = img[half:].sum()
    assert right_ink > left_ink

  def test_color_applied(self):
    img = render_spine_text("X", panel_width=10, color=(255, 0, 0))
    assert img[:, :, 0].sum() > 0
    assert img[:, :, 2].sum() == 0  # no blue ink


class TestCompositeSpineOverlay:
  def test_dims_background_and_draws_text(self):
    frame = np.full((20, 30, 3), 200, dtype=np.uint8)
    text_img = np.zeros((10, 8, 3), dtype=np.uint8)
    text_img[5, 4] = [255, 255, 255]
    out = composite_spine_overlay(frame, text_img, x0=0, y_offset=0, alpha=1.0)
    assert out.shape == frame.shape
    # Overlay region background dimmed
    assert out[2, 20].max() < 200
    # Right half untouched
    assert (out[10:] == 200).all()
    # Text pixel bright
    assert out[5, 4].max() > 200

  def test_alpha_zero_is_identity(self):
    frame = np.full((20, 30, 3), 100, dtype=np.uint8)
    text_img = np.full((10, 8, 3), 255, dtype=np.uint8)
    out = composite_spine_overlay(frame, text_img, x0=0, y_offset=0, alpha=0.0)
    assert (out == frame).all()

  def test_y_offset_scrolls(self):
    frame = np.zeros((20, 30, 3), dtype=np.uint8)
    text_img = np.full((10, 60, 3), 255, dtype=np.uint8)  # taller than frame
    out0 = composite_spine_overlay(frame, text_img, x0=0, y_offset=0, alpha=1.0)
    out_neg = composite_spine_overlay(frame, text_img, x0=0, y_offset=40, alpha=1.0)
    # Both draw something; offset 40 leaves only 20 rows of text (60-40)
    assert out0[:10, :, :].sum() > 0
    assert out_neg[:10, 19].sum() > 0
