"""Tests for preview service — isolation, lifecycle, frame format."""

import struct
from unittest.mock import MagicMock

from app.preview.service import PreviewService, FRAME_HEADER_FORMAT, MSG_TYPE_FRAME


def _make_renderer():
  renderer = MagicMock()
  renderer.internal_width = 10
  renderer.effects_config = {}
  return renderer


def _make_render_state():
  state = MagicMock()
  state.current_scene = 'fire'
  state.blackout = False
  state._audio_lock_free = {'level': 0, 'bass': 0, 'mid': 0, 'high': 0, 'beat': False, 'bpm': 0}
  return state


class TestPreviewLifecycle:
  def test_initially_inactive(self):
    svc = PreviewService(_make_renderer())
    assert not svc.active
    assert svc.effect_name is None

  def test_start_activates(self):
    svc = PreviewService(_make_renderer())
    svc.start('fire')
    assert svc.active
    assert svc.effect_name == 'fire'

  def test_stop_deactivates(self):
    svc = PreviewService(_make_renderer())
    svc.start('fire')
    svc.stop()
    assert not svc.active
    assert svc.effect_name is None

  def test_unknown_effect_raises(self):
    svc = PreviewService(_make_renderer())
    try:
      svc.start('nonexistent_effect_xyz')
      assert False, "Should have raised"
    except ValueError:
      pass

  def test_status_dict(self):
    svc = PreviewService(_make_renderer())
    status = svc.get_status()
    assert 'active' in status
    assert 'effect' in status
    assert 'fps' in status


class TestPreviewIsolation:
  """Preview must not affect live effect state."""

  def test_preview_has_own_effect_instance(self):
    svc = PreviewService(_make_renderer())
    svc.start('fire')
    # The preview effect is a separate instance, not the renderer's live effect
    assert svc._effect is not None
    assert svc._effect is not svc._renderer.current_effect

  def test_live_scene_unchanged_after_preview(self):
    renderer = _make_renderer()
    renderer.current_effect = MagicMock()
    original_effect = renderer.current_effect

    svc = PreviewService(renderer)
    svc.start('solid_color')
    svc.stop()

    # Live effect should not be touched
    assert renderer.current_effect is original_effect


class TestFrameFormat:
  def test_render_produces_binary_with_header(self):
    svc = PreviewService(_make_renderer())
    svc.start('solid_color', params={'color': '#ff0000'})
    state = _make_render_state()
    payload = svc.render_frame(state)
    assert payload is not None
    assert len(payload) > struct.calcsize(FRAME_HEADER_FORMAT)

  def test_frame_header_format(self):
    svc = PreviewService(_make_renderer())
    svc.start('solid_color')
    state = _make_render_state()
    payload = svc.render_frame(state)
    assert payload is not None

    # Parse header
    header_size = struct.calcsize(FRAME_HEADER_FORMAT)
    msg_type, frame_id, width, height, encoding = struct.unpack(
      FRAME_HEADER_FORMAT, payload[:header_size],
    )
    assert msg_type == MSG_TYPE_FRAME
    assert frame_id == 1
    assert width > 0
    assert height > 0
    assert encoding == 0  # RGB

    # Payload size should match dimensions
    expected_payload = width * height * 3
    assert len(payload) == header_size + expected_payload

  def test_frame_id_increments(self):
    svc = PreviewService(_make_renderer())
    svc.start('solid_color')
    state = _make_render_state()
    p1 = svc.render_frame(state)
    p2 = svc.render_frame(state)

    header_size = struct.calcsize(FRAME_HEADER_FORMAT)
    _, id1, _, _, _ = struct.unpack(FRAME_HEADER_FORMAT, p1[:header_size])
    _, id2, _, _, _ = struct.unpack(FRAME_HEADER_FORMAT, p2[:header_size])
    assert id2 == id1 + 1

  def test_no_frame_when_stopped(self):
    svc = PreviewService(_make_renderer())
    state = _make_render_state()
    assert svc.render_frame(state) is None
