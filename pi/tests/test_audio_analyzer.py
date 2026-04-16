"""Tests for AudioAnalyzer per-band sensitivity and spectrum extraction."""

import numpy as np
import pytest

from app.audio.analyzer import AudioAnalyzer, SAMPLE_RATE, FFT_SIZE


class FakeRenderState:
  def __init__(self):
    self.last_snapshot = {}

  def update_audio(self, snapshot):
    self.last_snapshot = snapshot


class TestBandSensitivity:
  def _make_analyzer(self):
    rs = FakeRenderState()
    a = AudioAnalyzer(rs)
    return a, rs

  def test_default_band_sensitivities(self):
    a, _ = self._make_analyzer()
    assert a.bass_sensitivity == 1.0
    assert a.mid_sensitivity == 1.0
    assert a.treble_sensitivity == 1.0

  def test_band_sensitivity_affects_output(self):
    a, rs = self._make_analyzer()
    t = np.arange(FFT_SIZE, dtype=np.float32) / SAMPLE_RATE
    tone = (np.sin(2 * np.pi * 100 * t) * 0.5).astype(np.float32)
    indata = tone.reshape(-1, 1)

    a._audio_callback(indata, FFT_SIZE, None, None)
    bass_default = rs.last_snapshot['bass']

    a._bass_smooth = 0.0
    a._mid_smooth = 0.0
    a._high_smooth = 0.0
    a.bass_sensitivity = 0.5
    a._audio_callback(indata, FFT_SIZE, None, None)
    bass_half = rs.last_snapshot['bass']

    assert bass_half < bass_default
    assert bass_half == pytest.approx(bass_default * 0.5, rel=0.05)

  def test_treble_sensitivity_affects_treble(self):
    a, rs = self._make_analyzer()
    t = np.arange(FFT_SIZE, dtype=np.float32) / SAMPLE_RATE
    tone = (np.sin(2 * np.pi * 8000 * t) * 0.5).astype(np.float32)
    indata = tone.reshape(-1, 1)

    a._audio_callback(indata, FFT_SIZE, None, None)
    high_default = rs.last_snapshot['high']

    a._bass_smooth = 0.0
    a._mid_smooth = 0.0
    a._high_smooth = 0.0
    a.treble_sensitivity = 2.0
    a._audio_callback(indata, FFT_SIZE, None, None)
    high_boosted = rs.last_snapshot['high']

    assert high_boosted > high_default
    assert high_boosted == pytest.approx(high_default * 2.0, rel=0.05)


class TestSpectrumBins:
  def _make_analyzer(self):
    rs = FakeRenderState()
    a = AudioAnalyzer(rs)
    return a, rs

  def test_spectrum_in_snapshot(self):
    a, rs = self._make_analyzer()
    t = np.arange(FFT_SIZE, dtype=np.float32) / SAMPLE_RATE
    tone = (np.sin(2 * np.pi * 440 * t) * 0.5).astype(np.float32)
    indata = tone.reshape(-1, 1)

    a._audio_callback(indata, FFT_SIZE, None, None)
    assert 'spectrum' in rs.last_snapshot
    assert len(rs.last_snapshot['spectrum']) == 16
    assert all(isinstance(v, float) for v in rs.last_snapshot['spectrum'])

  def test_spectrum_values_in_range(self):
    a, rs = self._make_analyzer()
    t = np.arange(FFT_SIZE, dtype=np.float32) / SAMPLE_RATE
    tone = (np.sin(2 * np.pi * 440 * t) * 0.5).astype(np.float32)
    indata = tone.reshape(-1, 1)

    a._audio_callback(indata, FFT_SIZE, None, None)
    for v in rs.last_snapshot['spectrum']:
      assert 0.0 <= v <= 1.0

  def test_bass_tone_lights_bass_bins(self):
    a, rs = self._make_analyzer()
    t = np.arange(FFT_SIZE, dtype=np.float32) / SAMPLE_RATE
    tone = (np.sin(2 * np.pi * 100 * t) * 0.8).astype(np.float32)
    indata = tone.reshape(-1, 1)

    a._audio_callback(indata, FFT_SIZE, None, None)
    spectrum = rs.last_snapshot['spectrum']
    bass_energy = sum(spectrum[:4])
    treble_energy = sum(spectrum[10:])
    assert bass_energy > treble_energy * 5
