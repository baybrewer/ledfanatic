"""
Audio adapter — compatibility surface for imported sound-reactive effects.

Translates the repo's AudioAnalyzer snapshot into the richer field set
that imported led_sim.py effects expect. Provides aliases, band expansion,
beat timing, and musical-state estimation.
"""

import math
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np


NUM_BANDS = 10


@dataclass
class AudioSnapshot:
  """Extended audio state for imported effects."""
  # Aliases from repo audio
  volume: float = 0.0
  bass: float = 0.0
  mids: float = 0.0
  highs: float = 0.0
  beat: bool = False
  bpm: float = 120.0

  # Band expansion (10-column view for visualizers)
  bands: np.ndarray = field(default_factory=lambda: np.zeros(NUM_BANDS, dtype=np.float32))

  # Beat timing
  beat_energy: float = 0.0
  beat_count: int = 0
  bar_beat: int = 0
  phrase_beat: int = 0
  is_downbeat: bool = False
  is_phrase: bool = False
  beat_phase: float = 0.0

  # Musical state estimation
  buildup: float = 0.0
  breakdown: bool = False
  drop: bool = False
  drop_intensity: float = 0.0

  # Time
  time_s: float = 0.0
  _time: float = 0.0


class AudioCompatAdapter:
  """Adapts the repo's simple audio snapshot into the richer surface
  needed by imported sound-reactive effects."""

  def __init__(self):
    self._beat_count = 0
    self._last_beat_time = 0.0
    self._energy_history: list[float] = []
    self._energy_max_history = 120  # ~2 seconds at 60fps
    self._buildup_acc = 0.0
    self._drop_acc = 0.0

  def adapt(self, repo_snapshot: dict, t: float) -> AudioSnapshot:
    """Convert a repo audio snapshot to the extended format."""
    level = repo_snapshot.get('level', 0.0)
    bass = repo_snapshot.get('bass', 0.0)
    mid = repo_snapshot.get('mid', 0.0)
    high = repo_snapshot.get('high', 0.0)
    beat = repo_snapshot.get('beat', False)
    bpm = repo_snapshot.get('bpm', 0.0) or 120.0

    # Track beats
    if beat:
      self._beat_count += 1
      self._last_beat_time = t

    # Beat energy: recent energy relative to running average
    energy = bass * 0.6 + mid * 0.3 + high * 0.1
    self._energy_history.append(energy)
    if len(self._energy_history) > self._energy_max_history:
      self._energy_history.pop(0)
    avg_energy = sum(self._energy_history) / len(self._energy_history) if self._energy_history else 0.001
    beat_energy = min(energy / max(avg_energy, 0.001), 3.0)

    # Bar/phrase timing (assumes 4/4)
    bar_beat = self._beat_count % 4
    phrase_beat = self._beat_count % 16
    is_downbeat = bar_beat == 0 and beat
    is_phrase = phrase_beat == 0 and beat

    # Beat phase: 0..1 within current beat interval
    if bpm > 0 and self._last_beat_time > 0:
      beat_interval = 60.0 / bpm
      elapsed_since_beat = t - self._last_beat_time
      beat_phase = min(elapsed_since_beat / beat_interval, 1.0)
    else:
      beat_phase = 0.0

    # Musical state estimation (simple heuristic)
    # buildup: rising energy trend
    # breakdown: falling energy with sparse beats
    # drop: high sustained energy after buildup
    if len(self._energy_history) >= 30:
      recent = sum(self._energy_history[-15:]) / 15
      older = sum(self._energy_history[-30:-15]) / 15
      if recent > older * 1.3:
        self._buildup_acc = min(self._buildup_acc + 0.02, 1.0)
        self._drop_acc = max(self._drop_acc - 0.05, 0.0)
      elif recent < older * 0.7:
        self._buildup_acc = max(self._buildup_acc - 0.05, 0.0)
        self._drop_acc = max(self._drop_acc - 0.02, 0.0)
      else:
        if self._buildup_acc > 0.5 and beat_energy > 1.5:
          self._drop_acc = min(self._drop_acc + 0.05, 1.0)
          self._buildup_acc = max(self._buildup_acc - 0.1, 0.0)
        else:
          self._buildup_acc = max(self._buildup_acc - 0.01, 0.0)
          self._drop_acc = max(self._drop_acc - 0.01, 0.0)

    # Boolean states matching source simulator contract
    is_breakdown = level < 0.3 and self._buildup_acc < 0.2
    is_drop = self._drop_acc > 0.5

    # Bands: expand 3-band to 10-band via interpolation
    bands = self._expand_bands(bass, mid, high)

    return AudioSnapshot(
      volume=level,
      bass=bass,
      mids=mid,
      highs=high,
      beat=beat,
      bpm=bpm,
      bands=bands,
      beat_energy=beat_energy,
      beat_count=self._beat_count,
      bar_beat=bar_beat,
      phrase_beat=phrase_beat,
      is_downbeat=is_downbeat,
      is_phrase=is_phrase,
      beat_phase=beat_phase,
      buildup=self._buildup_acc,
      breakdown=is_breakdown,
      drop=is_drop,
      drop_intensity=self._drop_acc,
      time_s=t,
      _time=t,
    )

  def _expand_bands(self, bass: float, mid: float, high: float) -> np.ndarray:
    """Expand 3 bands to 10 via cosine interpolation."""
    # Map: bands 0-2 = bass, 3-6 = mid, 7-9 = high
    bands = np.zeros(NUM_BANDS, dtype=np.float32)
    bands[0] = bass
    bands[1] = bass * 0.8 + mid * 0.2
    bands[2] = bass * 0.4 + mid * 0.6
    bands[3] = mid
    bands[4] = mid * 0.9 + high * 0.1
    bands[5] = mid * 0.7 + high * 0.3
    bands[6] = mid * 0.4 + high * 0.6
    bands[7] = high
    bands[8] = high * 0.8
    bands[9] = high * 0.6
    return bands
