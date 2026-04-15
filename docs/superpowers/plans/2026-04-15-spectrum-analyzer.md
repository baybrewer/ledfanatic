# Spectrum Analyzer & Per-Band Sensitivity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the broken meter bars with a live 16-bar spectrum analyzer and per-band (bass/mid/treble) sensitivity controls.

**Architecture:** AudioAnalyzer gets per-band sensitivity multipliers and a 16-bin spectrum array. RenderState broadcasts spectrum + band levels via WebSocket. Frontend renders a canvas spectrum visualizer with 3 band sensitivity sliders that replace the old global sensitivity slider and static meter bars.

**Tech Stack:** Python (numpy FFT), FastAPI, WebSocket, HTML5 Canvas, vanilla JS

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `pi/app/audio/analyzer.py` | Modify | Per-band sensitivity, 16-bin spectrum extraction |
| `pi/app/core/renderer.py` | Modify | Add spectrum + band levels to `to_dict()` |
| `pi/app/api/schemas.py` | Modify | Add band sensitivity fields to `AudioConfigRequest` |
| `pi/app/api/routes/audio.py` | Modify | Handle band sensitivity config, add GET config endpoint |
| `pi/app/core/state.py` | Modify | Persist band sensitivities |
| `pi/app/main.py` | Modify | Restore band sensitivities on startup |
| `pi/app/ui/static/index.html` | Modify | Replace meter bars with canvas + band sliders |
| `pi/app/ui/static/js/app.js` | Modify | Spectrum renderer, slider wiring, WS handler |
| `pi/app/ui/static/css/app.css` | Modify | Remove meter bar styles, add spectrum styles |
| `pi/tests/test_audio_analyzer.py` | Create | Unit tests for per-band sensitivity and spectrum bins |

---

### Task 1: Per-Band Sensitivity in AudioAnalyzer

**Files:**
- Create: `pi/tests/test_audio_analyzer.py`
- Modify: `pi/app/audio/analyzer.py:26-53` (constructor), `pi/app/audio/analyzer.py:94-153` (callback + snapshot)

- [ ] **Step 1: Write the failing tests**

```python
# pi/tests/test_audio_analyzer.py
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
    # Generate a pure 100 Hz tone (bass range)
    t = np.arange(FFT_SIZE, dtype=np.float32) / SAMPLE_RATE
    tone = (np.sin(2 * np.pi * 100 * t) * 0.5).astype(np.float32)
    indata = tone.reshape(-1, 1)

    # Run with default sensitivity
    a._audio_callback(indata, FFT_SIZE, None, None)
    bass_default = rs.last_snapshot['bass']

    # Reset smoothing, run with bass_sensitivity = 0.5
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
    # Generate a 8000 Hz tone (treble range)
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
    assert bass_energy > treble_energy * 5  # bass should dominate
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd pi && source .venv/bin/activate && PYTHONPATH=. pytest tests/test_audio_analyzer.py -v`
Expected: FAIL — `AudioAnalyzer` has no `bass_sensitivity` attribute, no `spectrum` in snapshot

- [ ] **Step 3: Implement per-band sensitivity and spectrum bins**

In `pi/app/audio/analyzer.py`, add these constants after `HIGH_RANGE`:

```python
# 16 log-spaced bin edges from 20 Hz to 16000 Hz
_SPECTRUM_BINS = 16
_BIN_EDGES = np.geomspace(20, 16000, _SPECTRUM_BINS + 1)
```

In `__init__`, add after `self.gain = 1.0`:

```python
    # Per-band sensitivity
    self.bass_sensitivity = 1.0
    self.mid_sensitivity = 1.0
    self.treble_sensitivity = 1.0
```

Add a new method for computing spectrum bins:

```python
  def _compute_spectrum_bins(self, spectrum: np.ndarray, freqs: np.ndarray) -> list[float]:
    """Extract 16 log-spaced bins from FFT, apply per-band sensitivity."""
    bins = []
    for i in range(_SPECTRUM_BINS):
      lo, hi = _BIN_EDGES[i], _BIN_EDGES[i + 1]
      mask = (freqs >= lo) & (freqs < hi)
      if np.any(mask):
        val = float(np.sqrt(np.mean(spectrum[mask] ** 2))) * 0.01
      else:
        val = 0.0
      # Apply per-band sensitivity
      if hi <= 250:
        val *= self.bass_sensitivity
      elif lo >= 4000:
        val *= self.treble_sensitivity
      else:
        val *= self.mid_sensitivity
      bins.append(min(1.0, val))
    return bins
```

In `_audio_callback`, after computing `bass`, `mid`, `high` (line 117), apply per-band sensitivity before smoothing:

```python
    bass *= self.bass_sensitivity
    mid *= self.mid_sensitivity
    high *= self.treble_sensitivity
```

After the smoothing lines (line 121), compute spectrum bins:

```python
    spectrum_bins = self._compute_spectrum_bins(spectrum, freqs)
```

In the snapshot dict (line 139), add the spectrum and remove the old global `sensitivity` multiplier from band values (it's now per-band):

```python
    snapshot = {
      'level': min(1.0, self._level_smooth),
      'bass': min(1.0, self._bass_smooth),
      'mid': min(1.0, self._mid_smooth),
      'high': min(1.0, self._high_smooth),
      'beat': beat,
      'bpm': 0.0,
      'spectrum': spectrum_bins,
    }
```

Note: the old code multiplied `self._bass_smooth * self.sensitivity` in the snapshot. Remove that — per-band sensitivity is already applied before smoothing.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd pi && PYTHONPATH=. pytest tests/test_audio_analyzer.py -v`
Expected: all 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add pi/app/audio/analyzer.py pi/tests/test_audio_analyzer.py
git commit -m "feat: per-band audio sensitivity and 16-bin spectrum extraction"
```

---

### Task 2: Broadcast Spectrum via WebSocket

**Files:**
- Modify: `pi/app/core/renderer.py:32-87` (RenderState)

- [ ] **Step 1: Add spectrum and band levels to RenderState**

In `pi/app/core/renderer.py`, modify `RenderState.__init__` to include spectrum in the audio dict (line 33-36):

```python
    self._audio_lock_free: dict = {
      'level': 0.0, 'bass': 0.0, 'mid': 0.0, 'high': 0.0,
      'beat': False, 'bpm': 0.0, 'spectrum': [0.0] * 16,
    }
```

Add a property for spectrum after `audio_bpm` (line 72):

```python
  @property
  def audio_spectrum(self) -> list:
    return self._audio_lock_free.get('spectrum', [0.0] * 16)
```

Modify `to_dict()` to include the band levels and spectrum (line 74-87):

```python
  def to_dict(self) -> dict:
    return {
      'target_fps': self.target_fps,
      'actual_fps': round(self.actual_fps, 1),
      'current_scene': self.current_scene,
      'blackout': self.blackout,
      'frames_rendered': self.frames_rendered,
      'frames_sent': self.frames_sent,
      'frames_dropped': self.frames_dropped,
      'last_frame_time_ms': round(self.last_frame_time_ms, 2),
      'render_cost_ms': round(self.render_cost_ms, 2),
      'audio_level': round(self.audio_level, 3),
      'audio_bass': round(self.audio_bass, 3),
      'audio_mid': round(self.audio_mid, 3),
      'audio_high': round(self.audio_high, 3),
      'audio_beat': self.audio_beat,
      'audio_spectrum': [round(v, 3) for v in self.audio_spectrum],
    }
```

- [ ] **Step 2: Run existing tests**

Run: `cd pi && PYTHONPATH=. pytest tests/ -x -q --ignore=tests/test_matrix_rain_perf.py --ignore=tests/test_migrations.py`
Expected: all pass (no existing tests depend on the exact shape of `to_dict()`)

- [ ] **Step 3: Commit**

```bash
git add pi/app/core/renderer.py
git commit -m "feat: broadcast audio spectrum and band levels via WebSocket"
```

---

### Task 3: Audio Config API — Per-Band Sensitivity + Persistence

**Files:**
- Modify: `pi/app/api/schemas.py:37-40`
- Modify: `pi/app/api/routes/audio.py`
- Modify: `pi/app/core/state.py`
- Modify: `pi/app/main.py:193-194`

- [ ] **Step 1: Extend AudioConfigRequest schema**

In `pi/app/api/schemas.py`, replace the `AudioConfigRequest` class:

```python
class AudioConfigRequest(BaseModel):
    device_index: Optional[int] = None
    sensitivity: Optional[float] = None  # kept for backward compat
    gain: Optional[float] = None
    bass_sensitivity: Optional[float] = None
    mid_sensitivity: Optional[float] = None
    treble_sensitivity: Optional[float] = None
```

- [ ] **Step 2: Add band sensitivity properties to StateManager**

In `pi/app/core/state.py`, add after the `target_fps` property (after line 138):

```python
  @property
  def audio_bass_sensitivity(self) -> Optional[float]:
    return self._state.get('audio_bass_sensitivity')

  @audio_bass_sensitivity.setter
  def audio_bass_sensitivity(self, value: float):
    self._state['audio_bass_sensitivity'] = max(0.1, min(3.0, value))
    self.mark_dirty()

  @property
  def audio_mid_sensitivity(self) -> Optional[float]:
    return self._state.get('audio_mid_sensitivity')

  @audio_mid_sensitivity.setter
  def audio_mid_sensitivity(self, value: float):
    self._state['audio_mid_sensitivity'] = max(0.1, min(3.0, value))
    self.mark_dirty()

  @property
  def audio_treble_sensitivity(self) -> Optional[float]:
    return self._state.get('audio_treble_sensitivity')

  @audio_treble_sensitivity.setter
  def audio_treble_sensitivity(self, value: float):
    self._state['audio_treble_sensitivity'] = max(0.1, min(3.0, value))
    self.mark_dirty()
```

- [ ] **Step 3: Extend audio routes — handle band sensitivity + add GET config**

Replace `pi/app/api/routes/audio.py`:

```python
"""Audio routes — devices, config, start/stop."""

from fastapi import APIRouter, Depends

from ..schemas import AudioConfigRequest


def create_router(deps, require_auth) -> APIRouter:
    router = APIRouter(prefix="/api/audio", tags=["audio"])

    @router.get("/devices")
    async def list_audio_devices():
        return {"devices": deps.audio_analyzer.list_devices()}

    @router.get("/config")
    async def get_audio_config():
        a = deps.audio_analyzer
        return {
            "gain": a.gain,
            "bass_sensitivity": a.bass_sensitivity,
            "mid_sensitivity": a.mid_sensitivity,
            "treble_sensitivity": a.treble_sensitivity,
        }

    @router.post("/config", dependencies=[Depends(require_auth)])
    async def configure_audio(req: AudioConfigRequest):
        a = deps.audio_analyzer
        if req.gain is not None:
            a.gain = req.gain
        if req.bass_sensitivity is not None:
            a.bass_sensitivity = req.bass_sensitivity
            deps.state_manager.audio_bass_sensitivity = req.bass_sensitivity
        if req.mid_sensitivity is not None:
            a.mid_sensitivity = req.mid_sensitivity
            deps.state_manager.audio_mid_sensitivity = req.mid_sensitivity
        if req.treble_sensitivity is not None:
            a.treble_sensitivity = req.treble_sensitivity
            deps.state_manager.audio_treble_sensitivity = req.treble_sensitivity
        if req.sensitivity is not None:
            # Legacy: apply global sensitivity to all bands equally
            a.bass_sensitivity = req.sensitivity
            a.mid_sensitivity = req.sensitivity
            a.treble_sensitivity = req.sensitivity
        if req.device_index is not None:
            a.set_device(req.device_index)
        return {"status": "ok"}

    @router.post("/start", dependencies=[Depends(require_auth)])
    async def start_audio():
        deps.audio_analyzer.start()
        return {"status": "started"}

    @router.post("/stop", dependencies=[Depends(require_auth)])
    async def stop_audio():
        deps.audio_analyzer.stop()
        return {"status": "stopped"}

    return router
```

- [ ] **Step 4: Restore band sensitivities on startup**

In `pi/app/main.py`, after `audio_analyzer = AudioAnalyzer(render_state)` (line 194), add:

```python
  # Restore saved band sensitivities
  if state_manager.audio_bass_sensitivity is not None:
    audio_analyzer.bass_sensitivity = state_manager.audio_bass_sensitivity
  if state_manager.audio_mid_sensitivity is not None:
    audio_analyzer.mid_sensitivity = state_manager.audio_mid_sensitivity
  if state_manager.audio_treble_sensitivity is not None:
    audio_analyzer.treble_sensitivity = state_manager.audio_treble_sensitivity
```

- [ ] **Step 5: Run all tests**

Run: `cd pi && PYTHONPATH=. pytest tests/ -x -q --ignore=tests/test_matrix_rain_perf.py --ignore=tests/test_migrations.py`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add pi/app/api/schemas.py pi/app/api/routes/audio.py pi/app/core/state.py pi/app/main.py
git commit -m "feat: per-band sensitivity API, persistence, and startup restore"
```

---

### Task 4: Frontend — Replace Meter Bars with Spectrum Canvas + Band Sliders

**Files:**
- Modify: `pi/app/ui/static/index.html:127-161`
- Modify: `pi/app/ui/static/css/app.css:307-372`
- Modify: `pi/app/ui/static/js/app.js:54-88` (updateState), `480-517` (audio init)

- [ ] **Step 1: Replace meter bar HTML with spectrum canvas and band sliders**

In `pi/app/ui/static/index.html`, replace the audio section (lines 127-161):

```html
      <!-- AUDIO -->
      <section id="panel-audio" class="panel" role="tabpanel" aria-labelledby="tab-audio">
        <div class="help-panel collapsed" data-tab="audio">
          <button class="help-toggle" aria-expanded="false">
            <span class="help-icon">?</span> How to use this page
          </button>
          <div class="help-content" hidden>
            <p>Configure the microphone for sound-reactive effects.</p>
            <p>Select your audio input device from the dropdown. Tap "Start Audio" to begin listening.</p>
            <p>Adjust bass, mid, and treble sensitivity to tune how effects respond to each frequency range.</p>
          </div>
        </div>
        <div class="audio-controls">
          <div class="control-group">
            <label>Input Device</label>
            <select id="audio-device-select"></select>
          </div>
          <div class="control-group">
            <label>Gain</label>
            <input type="range" id="audio-gain" min="0" max="300" value="100">
            <span id="audio-gain-value" class="slider-value">100%</span>
          </div>
          <button id="audio-start-btn" class="action-btn">Start Audio</button>
          <button id="audio-stop-btn" class="action-btn secondary">Stop Audio</button>
        </div>
        <div id="spectrum-container">
          <canvas id="spectrum-canvas" width="400" height="140"></canvas>
          <div class="spectrum-labels">
            <span>20 Hz</span><span>250</span><span>4k</span><span>16k Hz</span>
          </div>
          <div id="beat-indicator"></div>
        </div>
        <div id="band-sensitivity">
          <div class="band-control band-bass">
            <label>Bass</label>
            <input type="range" id="sens-bass" min="10" max="300" value="100" step="5">
            <span id="sens-bass-value" class="slider-value">100%</span>
          </div>
          <div class="band-control band-mid">
            <label>Mid</label>
            <input type="range" id="sens-mid" min="10" max="300" value="100" step="5">
            <span id="sens-mid-value" class="slider-value">100%</span>
          </div>
          <div class="band-control band-treble">
            <label>Treble</label>
            <input type="range" id="sens-treble" min="10" max="300" value="100" step="5">
            <span id="sens-treble-value" class="slider-value">100%</span>
          </div>
        </div>
      </section>
```

- [ ] **Step 2: Replace meter bar CSS with spectrum styles**

In `pi/app/ui/static/css/app.css`, replace the `#audio-meter` and `.meter-bar` styles (lines 333-372) with:

```css
#spectrum-container {
  margin-top: 16px;
  position: relative;
}

#spectrum-canvas {
  width: 100%;
  height: 140px;
  background: var(--surface2);
  border-radius: 8px;
  display: block;
}

.spectrum-labels {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
  color: var(--text-dim);
  padding: 4px 4px 0;
}

#beat-indicator {
  position: absolute;
  top: 8px;
  right: 8px;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--text-dim);
  transition: background 0.1s;
}

#beat-indicator.active {
  background: #fff;
  box-shadow: 0 0 8px #fff;
}

#band-sensitivity {
  display: flex;
  gap: 8px;
  margin-top: 12px;
}

.band-control {
  flex: 1;
  text-align: center;
  padding: 8px;
  background: var(--surface2);
  border-radius: 8px;
}

.band-control label {
  display: block;
  font-size: 12px;
  font-weight: bold;
  margin-bottom: 6px;
}

.band-control input[type="range"] {
  width: 100%;
}

.band-control .slider-value {
  display: block;
  font-size: 11px;
  color: var(--text-dim);
  margin-top: 4px;
}

.band-bass label { color: #c0392b; }
.band-mid label { color: #2ecc71; }
.band-treble label { color: #9b59b6; }
```

- [ ] **Step 3: Add spectrum renderer and band slider wiring to JavaScript**

In `pi/app/ui/static/js/app.js`, replace the `updateState` function (lines 54-88) to handle audio data:

```javascript
function updateState(data) {
  state = { ...state, ...data };

  if (data.actual_fps !== undefined) {
    document.getElementById('fps-display').textContent = `${data.actual_fps} FPS`;
  }

  if (data.current_scene) {
    document.getElementById('current-scene-name').textContent = data.current_scene.replace(/_/g, ' ');
  }

  if (data.blackout !== undefined) {
    document.getElementById('blackout-on-btn').classList.toggle('active', data.blackout);
  }

  // Brightness from WebSocket
  if (data.brightness) {
    const b = data.brightness;
    if (b.manual_cap !== undefined) {
      const slider = document.getElementById('brightness-slider');
      slider.value = Math.round(b.manual_cap * 100);
      document.getElementById('brightness-value').textContent = `${slider.value}%`;
    }
    if (b.auto_enabled !== undefined) {
      document.getElementById('brightness-auto-toggle').checked = b.auto_enabled;
    }
    if (b.solar_phase) {
      document.getElementById('brightness-phase').textContent = b.solar_phase;
    }
    if (b.effective_brightness !== undefined) {
      document.getElementById('brightness-effective').textContent =
        `Effective: ${Math.round(b.effective_brightness * 100)}%`;
    }
  }

  // Audio spectrum + beat
  if (data.audio_spectrum) {
    spectrumTarget = data.audio_spectrum;
  }
  const beatEl = document.getElementById('beat-indicator');
  if (beatEl) {
    beatEl.classList.toggle('active', !!data.audio_beat);
  }
}
```

Add the spectrum rendering code. Replace the entire `// --- Audio ---` section (lines 480-517):

```javascript
// --- Audio ---

let spectrumTarget = new Array(16).fill(0);
let spectrumCurrent = new Array(16).fill(0);
let spectrumAnimId = null;

const BAND_COLORS = [
  '#c0392b','#c0392b','#c0392b','#d35400',  // bass (bins 0-3)
  '#e67e22','#f1c40f','#2ecc71','#2ecc71','#27ae60','#2ecc71',  // mid (bins 4-9)
  '#3498db','#2980b9','#8e44ad','#9b59b6','#8e44ad','#9b59b6',  // treble (bins 10-15)
];

function renderSpectrum() {
  const canvas = document.getElementById('spectrum-canvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;

  // Size canvas to actual display size
  const rect = canvas.getBoundingClientRect();
  canvas.width = rect.width * dpr;
  canvas.height = rect.height * dpr;
  ctx.scale(dpr, dpr);

  const w = rect.width;
  const h = rect.height;
  const barCount = 16;
  const gap = 3;
  const barWidth = (w - gap * (barCount + 1)) / barCount;

  ctx.clearRect(0, 0, w, h);

  // Band region labels
  ctx.font = '10px system-ui, sans-serif';
  ctx.fillStyle = '#c0392b88';
  ctx.fillText('BASS', gap, 12);
  ctx.fillStyle = '#2ecc7188';
  ctx.fillText('MID', gap + (barWidth + gap) * 4, 12);
  ctx.fillStyle = '#9b59b688';
  ctx.fillText('TREBLE', gap + (barWidth + gap) * 10, 12);

  // Lerp current toward target
  for (let i = 0; i < barCount; i++) {
    spectrumCurrent[i] += (spectrumTarget[i] - spectrumCurrent[i]) * 0.3;
  }

  // Draw bars
  for (let i = 0; i < barCount; i++) {
    const x = gap + i * (barWidth + gap);
    const barH = Math.max(1, spectrumCurrent[i] * (h - 20));
    const y = h - barH;

    const grad = ctx.createLinearGradient(x, h, x, y);
    grad.addColorStop(0, BAND_COLORS[i] + '44');
    grad.addColorStop(1, BAND_COLORS[i]);
    ctx.fillStyle = grad;

    // Rounded top
    const r = Math.min(3, barWidth / 2);
    ctx.beginPath();
    ctx.moveTo(x, h);
    ctx.lineTo(x, y + r);
    ctx.quadraticCurveTo(x, y, x + r, y);
    ctx.lineTo(x + barWidth - r, y);
    ctx.quadraticCurveTo(x + barWidth, y, x + barWidth, y + r);
    ctx.lineTo(x + barWidth, h);
    ctx.fill();
  }

  spectrumAnimId = requestAnimationFrame(renderSpectrum);
}

function startSpectrum() {
  if (!spectrumAnimId) renderSpectrum();
}

function stopSpectrum() {
  if (spectrumAnimId) {
    cancelAnimationFrame(spectrumAnimId);
    spectrumAnimId = null;
  }
}

async function loadAudioDevices() {
  const data = await api('GET', '/api/audio/devices');
  if (!data) return;

  const select = document.getElementById('audio-device-select');
  select.innerHTML = '<option value="">Default</option>';
  for (const dev of data.devices) {
    const opt = document.createElement('option');
    opt.value = dev.index;
    opt.textContent = dev.name;
    select.appendChild(opt);
  }
}

async function loadAudioConfig() {
  const data = await api('GET', '/api/audio/config');
  if (!data) return;
  const setSlider = (id, val) => {
    const el = document.getElementById(id);
    if (el && val != null) {
      el.value = Math.round(val * 100);
      const valEl = document.getElementById(id + '-value');
      if (valEl) valEl.textContent = Math.round(val * 100) + '%';
    }
  };
  setSlider('audio-gain', data.gain);
  setSlider('sens-bass', data.bass_sensitivity);
  setSlider('sens-mid', data.mid_sensitivity);
  setSlider('sens-treble', data.treble_sensitivity);
}

function initAudio() {
  document.getElementById('audio-device-select').addEventListener('change', (e) => {
    const idx = e.target.value ? parseInt(e.target.value) : null;
    api('POST', '/api/audio/config', { device_index: idx });
  });

  document.getElementById('audio-start-btn').addEventListener('click', () => {
    api('POST', '/api/audio/start');
    startSpectrum();
  });

  document.getElementById('audio-stop-btn').addEventListener('click', () => {
    api('POST', '/api/audio/stop');
    stopSpectrum();
  });

  document.getElementById('audio-gain').addEventListener('input', (e) => {
    const val = e.target.value / 100;
    document.getElementById('audio-gain-value').textContent = Math.round(val * 100) + '%';
    api('POST', '/api/audio/config', { gain: val });
  });

  // Per-band sensitivity sliders
  const bandSliders = [
    { id: 'sens-bass', param: 'bass_sensitivity' },
    { id: 'sens-mid', param: 'mid_sensitivity' },
    { id: 'sens-treble', param: 'treble_sensitivity' },
  ];
  for (const { id, param } of bandSliders) {
    let debounce = null;
    document.getElementById(id).addEventListener('input', (e) => {
      const val = e.target.value / 100;
      document.getElementById(id + '-value').textContent = Math.round(val * 100) + '%';
      clearTimeout(debounce);
      debounce = setTimeout(() => {
        api('POST', '/api/audio/config', { [param]: val });
      }, 100);
    });
  }

  // Start spectrum animation when audio tab is shown
  startSpectrum();
}
```

- [ ] **Step 4: Wire loadAudioConfig into page init**

Find the section in `app.js` where `loadAudioDevices()` is called during init (search for `loadAudioDevices`). Add `loadAudioConfig()` right after it. It's likely in a `DOMContentLoaded` or tab-init block. The call should be:

```javascript
loadAudioDevices();
loadAudioConfig();
```

- [ ] **Step 5: Run full test suite**

Run: `cd pi && PYTHONPATH=. pytest tests/ -x -q --ignore=tests/test_matrix_rain_perf.py --ignore=tests/test_migrations.py`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add pi/app/ui/static/index.html pi/app/ui/static/css/app.css pi/app/ui/static/js/app.js
git commit -m "feat: spectrum analyzer UI with per-band sensitivity sliders"
```

---

### Task 5: Deploy and Verify

**Files:** None (deployment only)

- [ ] **Step 1: Deploy to Pi**

```bash
bash pi/scripts/deploy.sh ledfanatic.local
```

- [ ] **Step 2: Verify backend audio config API**

```bash
ssh jim@ledfanatic.local "curl -s http://localhost:80/api/audio/config | python3 -m json.tool"
```

Expected: `{ "gain": 1.0, "bass_sensitivity": 1.0, "mid_sensitivity": 1.0, "treble_sensitivity": 1.0 }`

- [ ] **Step 3: Verify spectrum data in WebSocket broadcast**

Start audio, then check the state endpoint:
```bash
ssh jim@ledfanatic.local "curl -s -X POST http://localhost:80/api/audio/start && sleep 2 && curl -s http://localhost:80/api/effects/catalog | python3 -c \"import sys,json; print('done')\""
```

Open the UI in a browser and verify:
- Spectrum canvas shows 16 color-coded bars responding to audio
- Band sensitivity sliders appear below spectrum
- Beat indicator pulses on beats
- Adjusting bass sensitivity slider reduces/boosts bass response in both spectrum and effects

- [ ] **Step 4: Test persistence**

```bash
ssh jim@ledfanatic.local "curl -s -X POST http://localhost:80/api/audio/config -H 'Content-Type: application/json' -d '{\"bass_sensitivity\": 0.4, \"treble_sensitivity\": 1.5}'"
sleep 2
ssh jim@ledfanatic.local "sudo cat /opt/pillar/config/state.json | python3 -c \"import sys,json; d=json.load(sys.stdin); print('bass:', d.get('audio_bass_sensitivity')); print('treble:', d.get('audio_treble_sensitivity'))\""
```

Expected: `bass: 0.4` and `treble: 1.5`
