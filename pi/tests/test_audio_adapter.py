"""Tests for audio adapter — aliases, band shape, musical state."""

import numpy as np
from app.audio.adapter import AudioCompatAdapter, AudioSnapshot, NUM_BANDS


class TestAudioAliases:
  def test_volume_alias(self):
    adapter = AudioCompatAdapter()
    snap = adapter.adapt({'level': 0.75, 'bass': 0.5, 'mid': 0.3, 'high': 0.1, 'beat': False, 'bpm': 120}, 0.0)
    assert snap.volume == 0.75

  def test_mids_alias(self):
    adapter = AudioCompatAdapter()
    snap = adapter.adapt({'level': 0.5, 'bass': 0.5, 'mid': 0.8, 'high': 0.1, 'beat': False, 'bpm': 120}, 0.0)
    assert snap.mids == 0.8

  def test_highs_alias(self):
    adapter = AudioCompatAdapter()
    snap = adapter.adapt({'level': 0.5, 'bass': 0.5, 'mid': 0.3, 'high': 0.9, 'beat': False, 'bpm': 120}, 0.0)
    assert snap.highs == 0.9

  def test_time_s(self):
    adapter = AudioCompatAdapter()
    snap = adapter.adapt({'level': 0, 'bass': 0, 'mid': 0, 'high': 0, 'beat': False, 'bpm': 0}, 42.5)
    assert snap.time_s == 42.5


class TestBandShape:
  def test_band_length_is_10(self):
    adapter = AudioCompatAdapter()
    snap = adapter.adapt({'level': 0.5, 'bass': 0.8, 'mid': 0.5, 'high': 0.3, 'beat': False, 'bpm': 120}, 0.0)
    assert len(snap.bands) == NUM_BANDS
    assert snap.bands.shape == (NUM_BANDS,)

  def test_bands_dtype_float32(self):
    adapter = AudioCompatAdapter()
    snap = adapter.adapt({'level': 0.5, 'bass': 0.8, 'mid': 0.5, 'high': 0.3, 'beat': False, 'bpm': 120}, 0.0)
    assert snap.bands.dtype == np.float32

  def test_bands_not_all_zero_with_input(self):
    adapter = AudioCompatAdapter()
    snap = adapter.adapt({'level': 0.5, 'bass': 0.8, 'mid': 0.5, 'high': 0.3, 'beat': False, 'bpm': 120}, 0.0)
    assert np.sum(snap.bands) > 0

  def test_bands_all_zero_with_silence(self):
    adapter = AudioCompatAdapter()
    snap = adapter.adapt({'level': 0, 'bass': 0, 'mid': 0, 'high': 0, 'beat': False, 'bpm': 0}, 0.0)
    assert np.all(snap.bands == 0)


class TestBeatTiming:
  def test_beat_count_increments(self):
    adapter = AudioCompatAdapter()
    snap1 = adapter.adapt({'level': 0.5, 'bass': 0.5, 'mid': 0.3, 'high': 0.1, 'beat': True, 'bpm': 120}, 0.0)
    assert snap1.beat_count == 1
    snap2 = adapter.adapt({'level': 0.5, 'bass': 0.5, 'mid': 0.3, 'high': 0.1, 'beat': False, 'bpm': 120}, 0.5)
    assert snap2.beat_count == 1
    snap3 = adapter.adapt({'level': 0.5, 'bass': 0.5, 'mid': 0.3, 'high': 0.1, 'beat': True, 'bpm': 120}, 1.0)
    assert snap3.beat_count == 2

  def test_bar_beat_wraps_at_4(self):
    adapter = AudioCompatAdapter()
    for i in range(8):
      snap = adapter.adapt({'level': 0.5, 'bass': 0.5, 'mid': 0.3, 'high': 0.1, 'beat': True, 'bpm': 120}, float(i))
    assert snap.bar_beat == 0  # 8 % 4 == 0

  def test_phrase_beat_wraps_at_16(self):
    adapter = AudioCompatAdapter()
    for i in range(16):
      snap = adapter.adapt({'level': 0.5, 'bass': 0.5, 'mid': 0.3, 'high': 0.1, 'beat': True, 'bpm': 120}, float(i))
    assert snap.phrase_beat == 0

  def test_is_downbeat_on_bar_boundary(self):
    adapter = AudioCompatAdapter()
    # Beats 1-3 are not downbeats
    for i in range(3):
      snap = adapter.adapt({'level': 0.5, 'bass': 0.5, 'mid': 0.3, 'high': 0.1, 'beat': True, 'bpm': 120}, float(i))
      if i > 0:
        assert snap.is_downbeat is False
    # Beat 4 wraps bar_beat to 0 + beat=True → downbeat
    snap = adapter.adapt({'level': 0.5, 'bass': 0.5, 'mid': 0.3, 'high': 0.1, 'beat': True, 'bpm': 120}, 4.0)
    assert snap.is_downbeat is True

  def test_beat_phase_bounded_0_1(self):
    adapter = AudioCompatAdapter()
    snap = adapter.adapt({'level': 0.5, 'bass': 0.5, 'mid': 0.3, 'high': 0.1, 'beat': True, 'bpm': 120}, 0.0)
    assert 0 <= snap.beat_phase <= 1.0
    snap = adapter.adapt({'level': 0.5, 'bass': 0.5, 'mid': 0.3, 'high': 0.1, 'beat': False, 'bpm': 120}, 10.0)
    assert 0 <= snap.beat_phase <= 1.0


class TestMusicalState:
  def test_buildup_bounded(self):
    adapter = AudioCompatAdapter()
    for i in range(200):
      level = (i % 20) / 20.0
      snap = adapter.adapt(
        {'level': level, 'bass': level, 'mid': level * 0.5, 'high': level * 0.2, 'beat': i % 10 == 0, 'bpm': 120},
        float(i) / 60.0,
      )
    assert 0 <= snap.buildup <= 1.0

  def test_drop_is_bool(self):
    adapter = AudioCompatAdapter()
    snap = adapter.adapt({'level': 0.8, 'bass': 0.9, 'mid': 0.5, 'high': 0.3, 'beat': True, 'bpm': 120}, 0.0)
    assert isinstance(snap.drop, bool)

  def test_breakdown_is_bool(self):
    adapter = AudioCompatAdapter()
    snap = adapter.adapt({'level': 0.1, 'bass': 0.05, 'mid': 0.05, 'high': 0.02, 'beat': False, 'bpm': 120}, 0.0)
    assert isinstance(snap.breakdown, bool)

  def test_drop_intensity_is_float(self):
    adapter = AudioCompatAdapter()
    snap = adapter.adapt({'level': 0.8, 'bass': 0.9, 'mid': 0.5, 'high': 0.3, 'beat': True, 'bpm': 120}, 0.0)
    assert isinstance(snap.drop_intensity, float)
    assert 0 <= snap.drop_intensity <= 1.0

  def test_beat_energy_positive(self):
    adapter = AudioCompatAdapter()
    snap = adapter.adapt({'level': 0.8, 'bass': 0.9, 'mid': 0.5, 'high': 0.3, 'beat': True, 'bpm': 120}, 0.0)
    assert snap.beat_energy >= 0


class TestAudioSnapshotSchema:
  def test_all_fields_present(self):
    snap = AudioSnapshot()
    assert hasattr(snap, 'volume')
    assert hasattr(snap, 'bass')
    assert hasattr(snap, 'mids')
    assert hasattr(snap, 'highs')
    assert hasattr(snap, 'bands')
    assert hasattr(snap, 'beat_energy')
    assert hasattr(snap, 'beat_count')
    assert hasattr(snap, 'bar_beat')
    assert hasattr(snap, 'phrase_beat')
    assert hasattr(snap, 'is_downbeat')
    assert hasattr(snap, 'is_phrase')
    assert hasattr(snap, 'beat_phase')
    assert hasattr(snap, 'buildup')
    assert hasattr(snap, 'breakdown')
    assert hasattr(snap, 'drop')
    assert hasattr(snap, 'drop_intensity')
    assert hasattr(snap, 'time_s')
    assert hasattr(snap, '_time')
