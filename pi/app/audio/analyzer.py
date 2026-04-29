"""
Audio analysis worker.

Runs FFT, beat detection, and band-level extraction on a background thread.
Updates RenderState via thread-safe snapshot dict.
"""

import logging
import threading
import time
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

SAMPLE_RATE = 44100
CHUNK_SIZE = 512
FFT_SIZE = 2048

BASS_RANGE = (20, 250)
MID_RANGE = (250, 4000)
HIGH_RANGE = (4000, 16000)

_SPECTRUM_BINS = 16
_BIN_EDGES = np.geomspace(20, 16000, _SPECTRUM_BINS + 1)


class AudioAnalyzer:
  def __init__(self, render_state, device_index: Optional[int] = None):
    self._render_state = render_state
    self.device_index = device_index
    self._running = False
    self._thread: Optional[threading.Thread] = None

    # Thread-safe snapshot
    self._lock = threading.Lock()
    self._snapshot: dict = {
      'level': 0.0, 'bass': 0.0, 'mid': 0.0, 'high': 0.0,
      'beat': False, 'bpm': 0.0,
    }

    # Internal smoothed values
    self._level_smooth = 0.0
    self._bass_smooth = 0.0
    self._mid_smooth = 0.0
    self._high_smooth = 0.0
    self._smoothing = 0.65  # lower = more responsive (was 0.85)

    # Beat detection
    self._energy_history: list[float] = []
    self._beat_cooldown = 0
    self._beat_frame_id = 0  # increments on each beat, render compares to detect

    # Config
    self.sensitivity = 1.0
    self.gain = 1.0
    self.bass_sensitivity = 1.0
    self.mid_sensitivity = 1.0
    self.treble_sensitivity = 1.0

  def start(self):
    if self._running:
      return

    self._running = True
    self._thread = threading.Thread(target=self._run, daemon=True)
    self._thread.start()
    logger.info("Audio analyzer started")

  def stop(self):
    self._running = False
    if self._thread:
      self._thread.join(timeout=2.0)
    self._thread = None
    logger.info("Audio analyzer stopped")

  def _run(self):
    try:
      import sounddevice as sd
    except ImportError:
      logger.warning("sounddevice not available — audio analysis disabled")
      self._running = False
      return

    try:
      with sd.InputStream(
        samplerate=SAMPLE_RATE,
        blocksize=CHUNK_SIZE,
        channels=1,
        dtype='float32',
        device=self.device_index,
        callback=self._audio_callback,
      ):
        while self._running:
          time.sleep(0.1)
    except Exception as e:
      logger.error(f"Audio stream error: {e}")
      self._running = False

  def _audio_callback(self, indata, frames, time_info, status):
    if status:
      logger.debug(f"Audio status: {status}")

    audio = indata[:, 0] * self.gain

    # RMS level
    rms = float(np.sqrt(np.mean(audio ** 2))) * self.sensitivity
    self._level_smooth = self._level_smooth * self._smoothing + rms * (1 - self._smoothing)

    # FFT
    if len(audio) >= FFT_SIZE:
      windowed = audio[:FFT_SIZE] * np.hanning(FFT_SIZE)
    else:
      padded = np.zeros(FFT_SIZE)
      padded[:len(audio)] = audio * np.hanning(len(audio))
      windowed = padded

    spectrum = np.abs(np.fft.rfft(windowed))
    freqs = np.fft.rfftfreq(FFT_SIZE, 1.0 / SAMPLE_RATE)

    bass = self._band_energy(spectrum, freqs, *BASS_RANGE)
    mid = self._band_energy(spectrum, freqs, *MID_RANGE)
    high = self._band_energy(spectrum, freqs, *HIGH_RANGE)

    bass *= self.bass_sensitivity
    mid *= self.mid_sensitivity
    high *= self.treble_sensitivity

    self._bass_smooth = self._bass_smooth * self._smoothing + bass * (1 - self._smoothing)
    self._mid_smooth = self._mid_smooth * self._smoothing + mid * (1 - self._smoothing)
    self._high_smooth = self._high_smooth * self._smoothing + high * (1 - self._smoothing)

    spectrum_bins = self._compute_spectrum_bins(spectrum, freqs)

    # Beat detection — tuned for responsiveness
    energy = float(np.sum(spectrum[:len(spectrum) // 4]))
    self._energy_history.append(energy)
    if len(self._energy_history) > 86:  # ~1 second at 86Hz (512 chunk)
      self._energy_history.pop(0)

    beat = False
    if self._beat_cooldown > 0:
      self._beat_cooldown -= 1
    elif len(self._energy_history) > 8:
      avg_energy = np.mean(self._energy_history)
      if energy > avg_energy * 1.4:  # slightly lower threshold for sensitivity
        beat = True
        self._beat_cooldown = 4  # ~46ms cooldown (was 8=184ms) → ~21 beats/sec max
        self._beat_frame_id += 1

    # Build snapshot under lock and push to render state
    snapshot = {
      'level': min(1.0, self._level_smooth),
      'bass': min(1.0, self._bass_smooth),
      'mid': min(1.0, self._mid_smooth),
      'high': min(1.0, self._high_smooth),
      'beat': beat,
      'beat_frame_id': self._beat_frame_id,
      'bpm': 0.0,
      'spectrum': spectrum_bins,
    }

    with self._lock:
      self._snapshot = snapshot

    # Push to render state (dict assignment is atomic in CPython)
    self._render_state.update_audio(snapshot)

  # Per-bin normalization — higher frequencies have less energy, need more gain
  _BIN_GAIN = [
    0.01, 0.01, 0.01, 0.012,           # bass bins 0-3
    0.015, 0.025, 0.05, 0.08, 0.12, 0.18,  # mid bins 4-9
    0.3, 0.5, 0.8, 1.2, 1.8, 2.5,      # treble bins 10-15
  ]

  def _compute_spectrum_bins(self, spectrum: np.ndarray, freqs: np.ndarray) -> list[float]:
    bins = []
    for i in range(_SPECTRUM_BINS):
      lo, hi = _BIN_EDGES[i], _BIN_EDGES[i + 1]
      mask = (freqs >= lo) & (freqs < hi)
      if np.any(mask):
        val = float(np.sqrt(np.mean(spectrum[mask] ** 2))) * self._BIN_GAIN[i]
      else:
        val = 0.0
      # Apply per-band sensitivity by bin index (4 bass / 6 mid / 6 treble)
      if i < 4:
        val *= self.bass_sensitivity
      elif i < 10:
        val *= self.mid_sensitivity
      else:
        val *= self.treble_sensitivity
      bins.append(min(1.0, val))
    return bins

  def _band_energy(self, spectrum: np.ndarray, freqs: np.ndarray,
                   low_hz: float, high_hz: float) -> float:
    mask = (freqs >= low_hz) & (freqs <= high_hz)
    if not np.any(mask):
      return 0.0
    band = spectrum[mask]
    return float(np.sqrt(np.mean(band ** 2))) * 0.01

  def list_devices(self) -> list[dict]:
    try:
      import sounddevice as sd
      devices = sd.query_devices()
      inputs = []
      for i, dev in enumerate(devices):
        if dev['max_input_channels'] > 0:
          inputs.append({
            'index': i,
            'name': dev['name'],
            'channels': dev['max_input_channels'],
            'sample_rate': dev['default_samplerate'],
          })
      return inputs
    except ImportError:
      return []

  def set_device(self, device_index: Optional[int]):
    was_running = self._running
    if was_running:
      self.stop()
    self.device_index = device_index
    if was_running:
      self.start()
