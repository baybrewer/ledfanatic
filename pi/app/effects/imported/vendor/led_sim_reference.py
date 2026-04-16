#!/usr/bin/env python3
"""
LED Matrix Animation Simulator — 10x172 WS2812 matrix
23 animations: 3 classic, 10 ambient, 10 sound-reactive

Controls:
  TAB        Switch category (Classic / Ambient / Sound)
  X          Next animation in category
  UP/DOWN    Select parameter
  LEFT/RIGHT Adjust parameter
  P          Cycle color palette
  R          Reset params to defaults
  Q/ESC      Quit
"""

import pygame
import math
import random
import sys
import threading

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

try:
    import sounddevice as sd
    HAS_AUDIO = True
except ImportError:
    HAS_AUDIO = False

# ─── Matrix layout ────────────────────────────────────────────────
from ....hardware_constants import LEDS_PER_STRIP as _HW_ROWS
COLS = 10
ROWS = _HW_ROWS
TOTAL_LEDS = COLS * ROWS

# ─── Display ──────────────────────────────────────────────────────
LED_R = 2
PITCH = 5
MARGIN = 12
PANEL_W = 280
WIN_W = MARGIN + COLS * PITCH + MARGIN + PANEL_W
WIN_H = MARGIN + ROWS * PITCH + MARGIN
BG = (8, 8, 12)
TARGET_FPS = 60

# ─── LED buffer ───────────────────────────────────────────────────
leds = [[0, 0, 0] for _ in range(TOTAL_LEDS + 1)]


def xy(x, y):
    """Map matrix (col, row) to LED index — serpentine layout."""
    if x < 0 or x >= COLS or y < 0 or y >= ROWS:
        return -1
    if x % 2 == 0:
        return x * ROWS + y
    else:
        return x * ROWS + (ROWS - 1 - y)


def clear_leds():
    for i in range(TOTAL_LEDS):
        leds[i] = [0, 0, 0]


def set_led(x, y, r, g, b):
    x = int(x) % COLS  # cylinder wrap
    idx = xy(x, y)
    if idx >= 0:
        leds[idx] = [clamp(r), clamp(g), clamp(b)]


def add_led(x, y, r, g, b):
    x = int(x) % COLS  # cylinder wrap
    idx = xy(x, y)
    if idx >= 0:
        leds[idx][0] = min(255, leds[idx][0] + int(r))
        leds[idx][1] = min(255, leds[idx][1] + int(g))
        leds[idx][2] = min(255, leds[idx][2] + int(b))


# ─── Math helpers ─────────────────────────────────────────────────

def clamp(v, lo=0, hi=255):
    return int(max(lo, min(hi, v)))


def clampf(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))


def qsub8(a, b):
    return max(0, a - b)


def qadd8(a, b):
    return min(255, a + b)


def scale8(a, b):
    return (a * b) >> 8


def hsv2rgb(h, s, v):
    if v == 0:
        return (0, 0, 0)
    if s == 0:
        return (v, v, v)
    region = (h * 6) >> 8
    frac = (h * 6) & 0xFF
    p = (v * (255 - s)) >> 8
    q = (v * (255 - ((s * frac) >> 8))) >> 8
    t = (v * (255 - ((s * (255 - frac)) >> 8))) >> 8
    lut = [(v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q)]
    return lut[min(region, 5)]


# ─── Perlin noise ────────────────────────────────────────────────

_rng = random.Random(42)
_p = list(range(256))
_rng.shuffle(_p)
_p += _p


def _fade(t):
    return t * t * t * (t * (t * 6 - 15) + 10)


def _lerp(t, a, b):
    return a + t * (b - a)


def _grad(h, x, y, z):
    h &= 15
    u = x if h < 8 else y
    if h < 4:
        v = y
    elif h == 12 or h == 14:
        v = x
    else:
        v = z
    return (u if (h & 1) == 0 else -u) + (v if (h & 2) == 0 else -v)


def _perlin(x, y, z):
    fx = math.floor(x); fy = math.floor(y); fz = math.floor(z)
    X = int(fx) & 255; Y = int(fy) & 255; Z = int(fz) & 255
    x -= fx; y -= fy; z -= fz
    u = _fade(x); v = _fade(y); w = _fade(z)
    A = _p[X]+Y; AA = _p[A]+Z; AB = _p[A+1]+Z
    B = _p[X+1]+Y; BA = _p[B]+Z; BB = _p[B+1]+Z
    return _lerp(w,
        _lerp(v, _lerp(u, _grad(_p[AA],x,y,z), _grad(_p[BA],x-1,y,z)),
                 _lerp(u, _grad(_p[AB],x,y-1,z), _grad(_p[BB],x-1,y-1,z))),
        _lerp(v, _lerp(u, _grad(_p[AA+1],x,y,z-1), _grad(_p[BA+1],x-1,y,z-1)),
                 _lerp(u, _grad(_p[AB+1],x,y-1,z-1), _grad(_p[BB+1],x-1,y-1,z-1))))


def _fbm(x, y, z, octaves=2, lacunarity=2.0, gain=0.5):
    val = 0.0; amp = 1.0; freq = 1.0
    for _ in range(octaves):
        val += _perlin(x * freq, y * freq, z * freq) * amp
        freq *= lacunarity; amp *= gain
    return val / (1.0 + gain + gain * gain)


def noise01(x, y=0.0, z=0.0):
    """Perlin noise normalised to 0-1."""
    return (_perlin(x, y, z) + 1.0) * 0.5


def cyl_noise(x, y, t, x_scale=1.0, y_scale=0.01):
    """Perlin noise that wraps seamlessly around x-axis (cylinder mapping).
    Maps x to a circle in 2D noise space so column 0 and COLS-1 are adjacent."""
    angle = x / COLS * 6.2832
    r = COLS * x_scale / 6.2832
    return _perlin(math.cos(angle) * r, math.sin(angle) * r, y * y_scale + t)


def cyl_fbm(x, y, t, octaves=2, x_scale=1.0, y_scale=0.01):
    """Fractal noise with seamless cylinder wrapping."""
    angle = x / COLS * 6.2832
    r = COLS * x_scale / 6.2832
    return _fbm(math.cos(angle) * r, math.sin(angle) * r, y * y_scale + t, octaves)


# ─── Color palettes ──────────────────────────────────────────────

def _make_pal(stops):
    """Build 256-entry palette from gradient stops [(pos, r, g, b), ...]."""
    pal = [(0, 0, 0)] * 256
    for i in range(256):
        t = i / 255.0
        for j in range(len(stops) - 1):
            if stops[j][0] <= t <= stops[j + 1][0]:
                s0, s1 = stops[j], stops[j + 1]
                f = (t - s0[0]) / max(0.001, s1[0] - s0[0])
                pal[i] = (clamp(s0[1] + f * (s1[1] - s0[1])),
                          clamp(s0[2] + f * (s1[2] - s0[2])),
                          clamp(s0[3] + f * (s1[3] - s0[3])))
                break
    return pal


PALETTES = [
    ("Rainbow", _make_pal([(0,255,0,0),(0.16,255,160,0),(0.33,255,255,0),(0.5,0,255,0),(0.66,0,0,255),(0.83,160,0,255),(1,255,0,0)])),
    ("Ocean", _make_pal([(0,0,0,30),(0.25,0,40,150),(0.5,0,120,200),(0.75,60,200,255),(1,180,255,255)])),
    ("Sunset", _make_pal([(0,30,0,50),(0.2,150,0,80),(0.4,255,40,0),(0.6,255,150,0),(0.8,255,220,50),(1,255,255,150)])),
    ("Forest", _make_pal([(0,0,15,0),(0.3,0,80,20),(0.6,40,180,40),(0.8,120,220,60),(1,200,255,120)])),
    ("Lava", _make_pal([(0,15,0,0),(0.2,120,0,0),(0.4,255,60,0),(0.65,255,180,0),(0.85,255,255,80),(1,255,255,220)])),
    ("Ice", _make_pal([(0,0,0,30),(0.25,20,60,160),(0.5,80,160,255),(0.75,180,220,255),(1,255,255,255)])),
    ("Neon", _make_pal([(0,255,0,80),(0.25,0,255,180),(0.5,255,0,255),(0.75,0,180,255),(1,255,255,0)])),
    ("Cyberpunk", _make_pal([(0,0,0,0),(0.2,80,0,180),(0.4,255,0,80),(0.6,0,180,255),(0.8,255,0,255),(1,0,255,180)])),
    ("Pastel", _make_pal([(0,255,180,180),(0.25,180,220,255),(0.5,200,255,200),(0.75,255,230,180),(1,255,180,220)])),
    ("Vapor", _make_pal([(0,20,0,40),(0.2,80,0,120),(0.4,255,80,180),(0.6,80,200,255),(0.8,255,150,255),(1,180,255,255)])),
]


def pal_color(pal_idx, t):
    """Get (r,g,b) from palette at position t (0.0-1.0)."""
    p = PALETTES[pal_idx % len(PALETTES)][1]
    return p[clamp(int(t * 255))]


# ─── Fire palette (separate — tuned to campfire) ──────────────────

def _build_fire_palette():
    pal = []
    for i in range(256):
        t = i / 255.0
        if t < 0.08:
            f = t / 0.08; r, g, b = f*50, 0, 0
        elif t < 0.22:
            f = (t-0.08)/0.14; r, g, b = 50+f*180, f*15, 0
        elif t < 0.40:
            f = (t-0.22)/0.18; r, g, b = 230+f*25, 15+f*110, 0
        elif t < 0.60:
            f = (t-0.40)/0.20; r, g, b = 255, 125+f*130, f*10
        elif t < 0.78:
            f = (t-0.60)/0.18; r, g, b = 255, 255, 10+f*70
        else:
            f = min(1.0, (t-0.78)/0.22); r, g, b = 255, 255, 80+f*120
        pal.append((clamp(r), clamp(g), clamp(b)))
    return pal

_FIRE_PALETTE = _build_fire_palette()

def fire_color(h01):
    return _FIRE_PALETTE[clamp(int(h01 * 255))]


# ─── Audio capture ────────────────────────────────────────────────

class AudioCapture:
    """Advanced audio capture with BPM-locked beat tracking.

    Beyond simple volume/FFT, this provides musical structure awareness
    optimized for house/techno (4-on-the-floor, 120-150 BPM):

      .beat          - True on detected kick onset
      .beat_count    - Total beats counted since start
      .bar_beat      - Position within bar (0-3, 0=downbeat)
      .phrase_beat   - Position within 16-beat phrase (0-15)
      .is_downbeat   - True on beat 0 of a bar (every 4th beat)
      .is_phrase     - True on beat 0 of a phrase (every 16th beat)
      .beat_phase    - 0.0-1.0 fractional position within current beat
      .bpm           - Estimated BPM (auto-detected from kick pattern)
      .bass          - Low frequency energy (kick)
      .mids          - Mid frequency energy (snare/vocal)
      .highs         - High frequency energy (hi-hat)
      .drop          - True when energy surge detected (build→drop)
    """

    def __init__(self):
        self.volume = 0.0
        self.bands = [0.0] * COLS
        self.beat = False
        self.beat_energy = 0.0
        self.raw_fft = None
        # Beat tracking
        self.beat_count = 0
        self.bar_beat = 0       # 0-3
        self.phrase_beat = 0    # 0-15
        self.is_downbeat = False
        self.is_phrase = False
        self.beat_phase = 0.0   # 0.0-1.0 within current beat
        self.bpm = 128.0        # house/techno default
        self.bass = 0.0
        self.mids = 0.0
        self.highs = 0.0
        self.drop = False
        self.drop_intensity = 0.0   # how hard the drop hit (0-1+)
        self.buildup = 0.0         # 0-1 buildup intensity (for gradual effects)
        self.breakdown = False     # True during quiet before drop
        self.drop_timer = 0.0      # seconds remaining of drop effect
        # Drop state machine
        self._drop_state = 'NORMAL'  # NORMAL → BUILDUP → BREAKDOWN → DROP → NORMAL
        self._buildup_counter = 0
        self._buildup_peak_bass = 0.0
        self._buildup_peak_energy = 0.0
        self._breakdown_start = 0.0
        self._beats_in_window = 0
        self._beat_window_start = 0.0
        self._energy_short_avg = 0.0
        self._energy_med_avg = 0.0
        # Internal
        self._active = False
        self._buffer = None
        self._peak_vol = 0.01
        self._time = 0.0
        # Beat tracker state
        self._last_beat_time = 0.0
        self._beat_intervals = []
        self._bass_history = []
        self._bass_avg = 0.01
        self._bass_peak = 0.01
        self._beat_cooldown = 0.0
        self._energy_history = []
        self._energy_long_avg = 0.01
        # Frequency band tracking
        self._bass_smooth = 0.0
        self._mids_smooth = 0.0
        self._highs_smooth = 0.0
        # ── Auto-gain (15-second sampling window) ─────────────────
        self._agc_period = 15.0          # seconds between recalibrations
        self._agc_timer = 0.0
        self._agc_bass_samples = []      # raw bass values over window
        self._agc_mids_samples = []
        self._agc_highs_samples = []
        self._agc_vol_samples = []
        self._agc_band_samples = [[] for _ in range(COLS)]
        self.gain_bass = 0.03            # current gain multipliers
        self.gain_mids = 0.02
        self.gain_highs = 0.015
        self.gain_bands = 0.02
        self.gain_vol = 1.0              # volume normalization

        if HAS_NUMPY and HAS_AUDIO:
            try:
                self._buffer = np.zeros(2048)
                self._stream = sd.InputStream(
                    samplerate=44100, channels=1, blocksize=2048,
                    callback=self._callback)
                self._stream.start()
                self._active = True
            except Exception:
                pass

    def _callback(self, indata, frames, time_info, status):
        self._buffer = indata[:, 0].copy()

    def update(self):
        self._time += 1.0 / 60.0  # approximate frame time

        if not self._active or self._buffer is None:
            self._sim_update()
            return

        buf = self._buffer

        # ── Volume (AGC) ─────────────────────────────────────────
        raw_vol = float(np.sqrt(np.mean(buf ** 2)))
        self._agc_vol_samples.append(raw_vol)
        self.volume = clampf(raw_vol * self.gain_vol)

        # ── FFT ──────────────────────────────────────────────────
        fft = np.abs(np.fft.rfft(buf))
        self.raw_fft = fft
        n = len(fft)

        # Perceptual frequency bands for display (auto-gained)
        self.bands = []
        for i in range(COLS):
            lo = int(n ** (i / COLS))
            hi = max(lo + 1, int(n ** ((i + 1) / COLS)))
            raw_band = float(np.mean(fft[lo:hi]))
            self._agc_band_samples[i].append(raw_band)
            self.bands.append(clampf(raw_band * self.gain_bands))

        # ── Isolated frequency bands (for house/techno) ──────────
        # Bass: 20-200 Hz, Mids: 200-2000 Hz, Highs: 2-16 kHz
        hz_per_bin = 44100.0 / 2048
        bass_lo, bass_hi = int(20/hz_per_bin), int(200/hz_per_bin)
        mids_lo, mids_hi = int(200/hz_per_bin), int(2000/hz_per_bin)
        highs_lo, highs_hi = int(2000/hz_per_bin), min(int(16000/hz_per_bin), n)
        raw_bass = float(np.mean(fft[max(1,bass_lo):bass_hi+1])) if bass_hi > bass_lo else 0
        raw_mids = float(np.mean(fft[mids_lo:mids_hi+1])) if mids_hi > mids_lo else 0
        raw_highs = float(np.mean(fft[highs_lo:highs_hi+1])) if highs_hi > highs_lo else 0
        # Collect samples for auto-gain
        self._agc_bass_samples.append(raw_bass)
        self._agc_mids_samples.append(raw_mids)
        self._agc_highs_samples.append(raw_highs)
        # Smooth with attack/release envelope (fast attack, slow release)
        self._bass_smooth = max(raw_bass * self.gain_bass, self._bass_smooth * 0.85)
        self._mids_smooth = max(raw_mids * self.gain_mids, self._mids_smooth * 0.88)
        self._highs_smooth = max(raw_highs * self.gain_highs, self._highs_smooth * 0.90)
        self.bass = clampf(self._bass_smooth)
        self.mids = clampf(self._mids_smooth)
        self.highs = clampf(self._highs_smooth)

        # ── Kick detection + BPM tracking ────────────────────────
        # Use bass energy for kick detection (house/techno = bass drum)
        bass_energy = float(np.sum(fft[max(1,bass_lo):bass_hi+1] ** 2))
        self._bass_peak = max(self._bass_peak * 0.999, bass_energy, 0.001)
        bass_norm = bass_energy / self._bass_peak

        self._bass_history.append(bass_norm)
        if len(self._bass_history) > 90:  # ~1.5 sec history
            self._bass_history.pop(0)
        self._bass_avg = sum(self._bass_history) / len(self._bass_history)

        self.beat = False
        self.is_downbeat = False
        self.is_phrase = False
        self._beat_cooldown = max(0.0, self._beat_cooldown - 1.0/60)

        # Onset detection: bass energy significantly above running average
        if bass_norm > self._bass_avg * 2.0 + 0.1 and self._beat_cooldown <= 0:
            interval = self._time - self._last_beat_time
            # Plausible beat interval: 60-200 BPM → 0.3-1.0 sec
            if 0.25 <= interval <= 1.2:
                self._beat_intervals.append(interval)
                if len(self._beat_intervals) > 24:
                    self._beat_intervals.pop(0)
                # Estimate BPM from median of recent intervals
                if len(self._beat_intervals) >= 4:
                    sorted_iv = sorted(self._beat_intervals)
                    median_iv = sorted_iv[len(sorted_iv) // 2]
                    self.bpm = max(60, min(200, 60.0 / median_iv))

            self._last_beat_time = self._time
            self.beat = True
            self.beat_count += 1
            self.bar_beat = self.beat_count % 4
            self.phrase_beat = self.beat_count % 16
            self.is_downbeat = (self.bar_beat == 0)
            self.is_phrase = (self.phrase_beat == 0)
            # Cooldown: ~60% of one beat period (prevents double triggers)
            self._beat_cooldown = 60.0 / self.bpm * 0.6

        # Beat energy (how hard this beat hit)
        self.beat_energy = clampf(bass_norm / max(0.01, self._bass_avg) * 0.3)

        # ── Beat phase (fractional position within current beat) ──
        beat_dur = 60.0 / self.bpm
        elapsed = self._time - self._last_beat_time
        self.beat_phase = clampf(elapsed / beat_dur)

        # ── Auto-gain recalibration (every 15 seconds) ───────────
        # Samples the 90th percentile of each band over the window,
        # then sets gain so that p90 maps to ~0.7 (leaves headroom
        # for peaks to hit 1.0 without constant clipping).
        self._agc_timer += 1.0 / 60
        if self._agc_timer >= self._agc_period:
            self._agc_timer = 0.0
            target = 0.7  # desired p90 output level

            def _calc_gain(samples, current_gain, min_gain=0.001, max_gain=1.0):
                if len(samples) < 30:
                    return current_gain
                s = sorted(samples)
                p90 = s[int(len(s) * 0.9)]
                if p90 < 0.0001:
                    return current_gain  # silence, don't adjust
                ideal = target / p90
                # Smooth transition: blend 70% new, 30% old
                return max(min_gain, min(max_gain, ideal * 0.7 + current_gain * 0.3))

            self.gain_bass = _calc_gain(self._agc_bass_samples, self.gain_bass, 0.001, 0.5)
            self.gain_mids = _calc_gain(self._agc_mids_samples, self.gain_mids, 0.001, 0.3)
            self.gain_highs = _calc_gain(self._agc_highs_samples, self.gain_highs, 0.001, 0.2)
            self.gain_vol = _calc_gain(self._agc_vol_samples, self.gain_vol, 0.5, 50.0)

            # Auto-gain for display bands (use average across all bands)
            all_band_vals = []
            for bs in self._agc_band_samples:
                all_band_vals.extend(bs)
            self.gain_bands = _calc_gain(all_band_vals, self.gain_bands, 0.001, 0.5)

            # Clear sample buffers for next window
            self._agc_bass_samples.clear()
            self._agc_mids_samples.clear()
            self._agc_highs_samples.clear()
            self._agc_vol_samples.clear()
            for bs in self._agc_band_samples:
                bs.clear()

        # ── Drop detection state machine ────────────────────────
        # Tracks the house/techno pattern: BUILDUP → BREAKDOWN → DROP
        # BUILDUP:   consistent beats + rising energy over several bars
        # BREAKDOWN: bass/kick drops out (the tension before the drop)
        # DROP:      bass comes back hard — the payoff moment
        total_energy = float(np.sum(buf ** 2) / len(buf))
        self._energy_history.append(total_energy)
        if len(self._energy_history) > 300:
            self._energy_history.pop(0)
        self._energy_long_avg = max(0.0001, sum(self._energy_history) / len(self._energy_history))
        # Short-term (~0.5s) and medium-term (~4s) energy averages
        self._energy_short_avg = self._energy_short_avg * 0.7 + total_energy * 0.3
        self._energy_med_avg = self._energy_med_avg * 0.97 + total_energy * 0.03
        # Count beats in rolling window
        if self.beat:
            self._beats_in_window += 1
        if self._time - self._beat_window_start > 4.0:
            self._beat_window_start = self._time
            self._beats_in_window = 0

        self.drop = False
        self.drop_timer = max(0, self.drop_timer - 1.0/60)
        dt_frame = 1.0 / 60

        if self._drop_state == 'NORMAL':
            self.breakdown = False
            self.buildup = max(0, self.buildup - 0.005)
            # Enter BUILDUP: consistent beats (8+ in 4 sec) and energy above average
            if (self._beats_in_window >= 8 and
                    self._energy_short_avg > self._energy_long_avg * 0.8):
                self._buildup_counter += 1
                if self._buildup_counter > 120:  # ~2 sec of consistent activity
                    self._drop_state = 'BUILDUP'
                    self._buildup_peak_bass = self.bass
                    self._buildup_peak_energy = self._energy_short_avg
            else:
                self._buildup_counter = max(0, self._buildup_counter - 2)

        elif self._drop_state == 'BUILDUP':
            self.breakdown = False
            self.buildup = min(1.0, self.buildup + 0.008)
            self._buildup_peak_bass = max(self._buildup_peak_bass, self.bass)
            self._buildup_peak_energy = max(self._buildup_peak_energy, self._energy_short_avg)
            # Enter BREAKDOWN: bass drops below 30% of buildup peak
            # (kick dropped out — the tension moment)
            if self.bass < self._buildup_peak_bass * 0.25 and self.buildup > 0.3:
                self._drop_state = 'BREAKDOWN'
                self._breakdown_start = self._time

        elif self._drop_state == 'BREAKDOWN':
            self.breakdown = True
            # Buildup holds/slowly rises during breakdown (anticipation)
            self.buildup = min(1.0, self.buildup + 0.003)
            # Timeout: if breakdown lasts > 16 sec, reset
            if self._time - self._breakdown_start > 16:
                self._drop_state = 'NORMAL'
                self.buildup = 0
                self.breakdown = False
            # Enter DROP: bass comes back above 60% of buildup peak
            elif self.bass > self._buildup_peak_bass * 0.5 and self.beat:
                self._drop_state = 'DROP'
                self.drop = True
                self.drop_intensity = clampf(
                    self._energy_short_avg / max(0.0001, self._energy_long_avg))
                self.drop_timer = 3.0  # 3 seconds of drop effects
                self.breakdown = False

        elif self._drop_state == 'DROP':
            self.breakdown = False
            self.buildup = max(0, self.buildup - 0.02)  # wind down
            if self.drop_timer <= 0:
                self._drop_state = 'NORMAL'
                self.buildup = 0
                self._buildup_counter = 0

    def _sim_update(self):
        """Simulated beat tracking with periodic drop simulation."""
        self._time += 1.0 / 60
        beat_dur = 60.0 / self.bpm
        self.beat_phase = (self._time % beat_dur) / beat_dur
        # Simulate a 32-bar cycle: 24 bars normal, 4 bars buildup, 2 bars breakdown, 2 bars drop
        cycle_beats = 128  # 32 bars * 4 beats
        cycle_pos = self.beat_count % cycle_beats
        in_buildup = 96 <= cycle_pos < 112
        in_breakdown = 112 <= cycle_pos < 120
        in_drop = 120 <= cycle_pos < 128

        self.volume = 0.2 + random.random() * 0.1
        self.bass = 0.0 if in_breakdown else (0.2 + random.random() * 0.1)
        self.mids = 0.15 + random.random() * 0.1
        self.highs = 0.1 + random.random() * 0.1
        if in_buildup:
            self.buildup = min(1.0, self.buildup + 0.01)
            self.highs *= 1.5
        elif in_breakdown:
            self.breakdown = True
            self.buildup = min(1.0, self.buildup + 0.005)
        else:
            self.breakdown = False
            self.buildup = max(0, self.buildup - 0.01)
        self.bands = [random.random() * 0.25 for _ in range(COLS)]
        # Simulate beats at BPM
        old_phase = ((self._time - 1/60) % beat_dur) / beat_dur
        self.beat = self.beat_phase < old_phase
        if self.beat:
            self.beat_count += 1
            self.bar_beat = self.beat_count % 4
            self.phrase_beat = self.beat_count % 16
            self.is_downbeat = (self.bar_beat == 0)
            self.is_phrase = (self.phrase_beat == 0)
            self.beat_energy = random.uniform(0.5, 1.0)
        else:
            self.is_downbeat = False
            self.is_phrase = False
        self.drop = (in_drop and cycle_pos == 120 and self.beat)
        self.drop_timer = max(0, self.drop_timer - 1/60)
        if self.drop:
            self.drop_intensity = 1.0
            self.drop_timer = 3.0

    def close(self):
        if self._active:
            try:
                self._stream.stop()
            except Exception:
                pass


# ─── Param descriptor + AnimBase ──────────────────────────────────

class Param:
    __slots__ = ('label', 'attr', 'lo', 'hi', 'step', 'fmt')
    def __init__(self, label, attr, lo, hi, step, fmt=".2f"):
        self.label = label; self.attr = attr
        self.lo = lo; self.hi = hi; self.step = step; self.fmt = fmt


class AnimBase:
    """Mixin providing parameter adjustment, palette cycling, defaults."""
    category = "Classic"
    has_palette = False
    palette_idx = 0
    selected_param = 0
    PARAMS = []

    def _init_defaults(self):
        self._defaults = {p.attr: getattr(self, p.attr) for p in self.PARAMS}

    def reset_params(self):
        for a, v in self._defaults.items():
            setattr(self, a, v)

    def adjust_param(self, direction):
        if not self.PARAMS:
            return
        p = self.PARAMS[self.selected_param]
        val = getattr(self, p.attr) + p.step * direction
        val = max(p.lo, min(p.hi, val))
        if p.fmt == "d":
            val = int(round(val))
        else:
            d = int(p.fmt[-2]) if len(p.fmt) > 1 and p.fmt[-2].isdigit() else 2
            val = round(val, d)
        setattr(self, p.attr, val)

    def cycle_palette(self):
        if self.has_palette:
            self.palette_idx = (self.palette_idx + 1) % len(PALETTES)

    def pal(self, t):
        return pal_color(self.palette_idx, t)


# ═════════════════════════════════════════════════════════════════
#  CLASSIC ANIMATIONS
# ═════════════════════════════════════════════════════════════════

class RainbowCycle(AnimBase):
    name = "Rainbow Cycle"
    category = "Classic"
    has_palette = True
    SPEED = 1.0
    PARAMS = [Param("Speed", "SPEED", 0.1, 5.0, 0.1, ".1f")]

    def __init__(self):
        self.hue = 0; self.timer = 0.0; self._init_defaults()

    def update(self, dt_ms, audio=None):
        self.timer += dt_ms * self.SPEED
        if self.timer >= 100:
            self.hue = (self.hue + 1) % 256; self.timer -= 100
        c = list(self.pal(self.hue / 255.0))
        for i in range(TOTAL_LEDS):
            leds[i] = c[:]


class FeldsteinEquation(AnimBase):
    """Cylinder-wrapped noise with alternating up/down traveling bars.
    Even columns scroll noise downward, odd columns scroll upward,
    creating a weaving barber-pole effect around the cylinder."""
    name = "Feldstein Equation"
    category = "Classic"
    SPEED = 1.0; BAR_SPEED = 1.0
    PARAMS = [Param("Speed", "SPEED", 0.2, 3.0, 0.1, ".1f"),
              Param("Bar Speed", "BAR_SPEED", 0.2, 4.0, 0.1, ".1f")]

    def __init__(self):
        self.xo = _rng.randint(0, 65535)
        self.yo = _rng.randint(0, 65535)
        self.zo = _rng.randint(0, 65535)
        self.hue = 0; self.hue_accum = 0.0; self.t0 = None
        self._init_defaults()

    def update(self, dt_ms, audio=None):
        now = pygame.time.get_ticks()
        if self.t0 is None:
            self.t0 = now
        t = (now - self.t0) * self.SPEED * 0.001
        self.hue_accum += dt_ms
        if self.hue_accum >= 1000:
            self.hue = (self.hue + 1) % 256; self.hue_accum -= 1000
        h = self.hue
        bar_t = t * self.BAR_SPEED * 40  # bar scroll offset in rows
        for col in range(COLS):
            direction = 1.0 if col % 2 == 0 else -1.0
            y_scroll = bar_t * direction
            for row in range(ROWS):
                i = xy(col, row)
                if i < 0: continue
                led = leds[i]
                led[0] -= led[0] * 48 >> 8
                led[1] -= led[1] * 48 >> 8
                led[2] -= led[2] * 48 >> 8
                scrolled_row = row + y_scroll
                n1 = cyl_noise(col, scrolled_row, t * 1.0, 0.8, 0.015)
                v1 = clamp(max(0, n1) * 300)
                c1 = hsv2rgb(h & 255, 255, v1)
                n2 = cyl_noise(col + 20, scrolled_row, t * 0.7 + 50, 1.2, 0.012)
                v2 = clamp(max(0, n2) * 300)
                c2 = hsv2rgb((h + 96) & 255, 255, v2)
                n3 = cyl_noise(col + 50, scrolled_row, t * 0.4 + 100, 0.6, 0.008)
                v3 = clamp(max(0, n3) * 250)
                c3 = hsv2rgb((h + 160) & 255, 255, v3)
                led[0] = min(255, led[0] + c1[0] + c2[0] + c3[0])
                led[1] = min(255, led[1] + c1[1] + c2[1] + c3[1])
                led[2] = min(255, led[2] + c1[2] + c2[2] + c3[2])


def inoise8_sub(x, y, z):
    v = clamp((_perlin(x / 256.0, y / 256.0, z / 256.0) + 1) * 127.5)
    v = qsub8(v, 128)
    return qadd8(v, scale8(v, 128))


_FELD_PALETTES = [
    # (name, [(hue_offset, saturation, _), ...])  — 3 CHSV layers
    # sat=255 = fully saturated color, sat=0 = white
    ("Original",   [(0,255,0),(96,255,0),(160,255,0)]),        # R/G/B tricolor, vivid
    ("Rainbow",    [(0,255,0),(85,255,0),(170,255,0)]),         # evenly spaced R/G/B rainbow
    ("Ocean",      [(140,255,0),(170,255,0),(200,240,0)]),      # deep teals + blues
    ("Fire",       [(0,255,0),(15,255,0),(35,240,0)]),          # saturated reds + oranges
    ("Acid",       [(75,255,0),(120,255,0),(200,255,0)]),       # toxic greens + purples
    ("Pastel",     [(0,100,0),(96,100,0),(160,100,0)]),         # soft pastels
    ("Monochrome", [(0,0,0),(0,0,0),(0,0,0)]),                  # pure white
    ("Sunset",     [(250,255,0),(10,255,0),(30,230,0)]),        # warm reds → gold
    ("Aurora",     [(85,255,0),(105,240,0),(170,255,0)]),       # greens + violet
    ("Cyberpunk",  [(200,255,0),(230,255,0),(170,240,0)]),      # magenta + pink + purple
    ("Deep Sea",   [(150,255,0),(165,255,0),(140,220,0)]),      # dark blues + cyan
    ("Ember",      [(0,255,0),(8,240,0),(16,220,0)]),           # tight warm reds
    ("Neon",       [(55,255,0),(160,255,0),(220,255,0)]),       # yellow + blue + pink
    ("Forest",     [(70,255,0),(85,255,0),(105,240,0)]),        # vivid greens
    ("Vapor",      [(180,230,0),(210,200,0),(240,240,0)]),      # vaporwave pinks + blues
    ("Blood Moon", [(250,255,0),(5,240,0),(0,255,0)]),          # all reds
    ("Ice Storm",  [(130,200,0),(155,220,0),(180,180,0)]),      # cold blues
]


class Feldstein2(AnimBase):
    """Faithful port of the original Noise2DAnimation with scale=500,
    three CHSV layers, fadeToBlackBy, and the exact divisors from C++.
    Adds palette selection and dark/light ratio control."""
    name = "Feldstein OG"
    category = "Classic"
    SPEED = 1.0; FADE = 48; PALETTE = 0
    PARAMS = [Param("Speed", "SPEED", 0.2, 3.0, 0.1, ".1f"),
              Param("Fade/Dark", "FADE", 10, 200, 5, "d"),
              Param("Palette", "PALETTE", 0, 16, 1, "d")]

    def __init__(self):
        self.xo = _rng.randint(0, 65535)
        self.yo = _rng.randint(0, 65535)
        self.zo = _rng.randint(0, 65535)
        self.hue = 0; self.hue_accum = 0.0; self.t0 = None
        self._init_defaults()

    def update(self, dt_ms, audio=None):
        now = pygame.time.get_ticks()
        if self.t0 is None:
            self.t0 = now
        time = int((now - self.t0) * self.SPEED) // 7 + self.zo
        # Scale reduced from original 500 to fill 172 rows without
        # repeating horizontal bands (original was tuned for 61 rows)
        SCALE = 180
        fade = int(self.FADE)

        self.hue_accum += dt_ms
        if self.hue_accum >= 1000:
            self.hue = (self.hue + 1) % 256; self.hue_accum -= 1000
        h = self.hue

        # Get palette hue/sat offsets for each layer
        pi = int(self.PALETTE) % len(_FELD_PALETTES)
        pname, layers = _FELD_PALETTES[pi]
        h1_off, s1, _ = layers[0]
        h2_off, s2, _ = layers[1]
        h3_off, s3, _ = layers[2]

        for x in range(COLS):
            xS = x * SCALE + self.xo
            for y in range(ROWS):
                yS = y * SCALE + self.yo
                i = xy(x, y)
                if i < 0:
                    continue
                led = leds[i]
                led[0] -= led[0] * fade >> 8
                led[1] -= led[1] * fade >> 8
                led[2] -= led[2] * fade >> 8
                # Layer 1
                n1 = inoise8_sub(xS // 10, yS // 50 + time // 2, time)
                c1 = hsv2rgb((h + h1_off) & 255, s1, n1)
                # Layer 2
                n2 = inoise8_sub(xS // 10, yS // 50 + time // 2, time + 100 * SCALE)
                c2 = hsv2rgb((h + h2_off) & 255, s2, n2)
                # Layer 3
                n3 = inoise8_sub(xS // 100, yS // 40, time // 10 + 300 * SCALE)
                c3 = hsv2rgb((h + h3_off) & 255, s3, n3)
                led[0] = min(255, led[0] + c1[0] + c2[0] + c3[0])
                led[1] = min(255, led[1] + c1[1] + c2[1] + c3[1])
                led[2] = min(255, led[2] + c1[2] + c2[2] + c3[2])


class BrettsFavorite(AnimBase):
    """Port of BrettsFavoriteAnimation — sine-wave bands with drifting
    positions and speeds. Each horizontal band (ROWS/BANDS) has its own
    phase and velocity, creating flowing wave interference patterns."""
    name = "Brett's Favorite"
    category = "Classic"
    has_palette = True
    SPEED = 1.0; BANDS = 16; DAMPING = 0.95
    PARAMS = [Param("Speed", "SPEED", 0.2, 3.0, 0.1, ".1f"),
              Param("Bands", "BANDS", 4, 32, 1, "d"),
              Param("Damping", "DAMPING", 0.8, 0.99, 0.01, ".2f")]

    def __init__(self):
        self._init_defaults()
        self.hue = random.randint(0, 255)
        self.hue_accum = 0.0
        self.pos = [random.randint(0, 255) for _ in range(32)]
        self.spd = [random.choice([-1, 1]) for _ in range(32)]
        self.spd_accum = 0.0

    def update(self, dt_ms, audio=None):
        # Hue drifts slowly
        self.hue_accum += dt_ms
        if self.hue_accum >= 100:
            self.hue = (self.hue + 1) % 256
            self.hue_accum -= 100

        # Speeds decay toward ±1
        self.spd_accum += dt_ms
        if self.spd_accum >= 30:
            self.spd_accum -= 30
            for i in range(int(self.BANDS)):
                if self.spd[i] > 1:
                    self.spd[i] -= 1
                elif self.spd[i] < -1:
                    self.spd[i] += 1

        # Random kicks (simulates stomp/jerk from original)
        if random.random() < 0.03 * self.SPEED:
            for i in range(int(self.BANDS)):
                self.spd[i] += random.randint(-5, 5)

        # Update positions
        bands = int(self.BANDS)
        for i in range(bands):
            self.pos[i] = (self.pos[i] + int(self.spd[i] * self.SPEED)) & 255

        # Render
        band_h = max(1, ROWS // bands)
        for y in range(ROWS):
            band_idx = min(y // band_h, bands - 1)
            p = self.pos[band_idx]
            base_hue = (self.hue + self.spd[band_idx]) & 255
            base_sat = max(0, 255 - abs(self.spd[band_idx]) * 3)
            for x in range(COLS):
                # sin8 equivalent: sin wave across width
                phase = (p + x * 256 // COLS) & 255
                val = max(0, int((math.sin(phase / 255.0 * 6.2832) + 1) * 127.5) - 20)
                c = hsv2rgb(base_hue, base_sat, val)
                idx = xy(x, y)
                if idx >= 0:
                    leds[idx] = list(c)


# ─── Ember + Fireplace ───────────────────────────────────────────

class Ember:
    __slots__ = ('x','y','vx','vy','brightness','life','max_life',
                 'flicker_phase','flicker_speed')
    def __init__(self, x, y, brightness, spread=0.0):
        self.x = x + random.uniform(-0.4, 0.4)
        self.y = float(y)
        speed = random.uniform(30.0, 100.0)
        angle = random.gauss(0, spread)
        self.vx = speed * math.sin(angle)
        self.vy = -speed * math.cos(angle)
        self.brightness = random.uniform(0.3, 1.0) ** 0.7
        self.max_life = random.uniform(1.0, 4.5)
        self.life = self.max_life
        self.flicker_phase = random.uniform(0, 6.28)
        self.flicker_speed = random.uniform(8.0, 25.0)


class Fireplace(AnimBase):
    name = "Fireplace"
    category = "Classic"
    FUEL = 0.6  # master "how roaring is this fire" — 0=dying embers, 1=raging inferno
    SPARK_ZONE = 35; SPARK_PROB = 0.85; SPARK_MIN = 0.55; SPARK_MAX = 1.0
    FLARE_PROB = 0.025; COOL_BASE = 0.012; COOL_HEIGHT = 0.045
    COOL_NOISE = 0.50; DIFFUSE_CENTER = 0.74; DIFFUSE_SIDE = 0.13
    TURB_X_SCALE = 1.8; TURB_Y_BIAS = 2.0; TURB_Y_RANGE = 3.0
    BUOYANCY = 2.5; NOISE_OCTAVES = 2
    EMBER_RATE = 0.20; EMBER_BURST = 6; EMBER_SPREAD = 0.65
    MAX_EMBERS = 150

    PARAMS = [
        Param("** FUEL **",  "FUEL",           0.0, 1.0,  0.05,  ".2f"),
        Param("Spark Zone",   "SPARK_ZONE",    1,   60,   2,     "d"),
        Param("Spark Prob",   "SPARK_PROB",     0.0, 1.0,  0.05,  ".2f"),
        Param("Cool Base",    "COOL_BASE",      0.0, 0.10, 0.002, ".3f"),
        Param("Cool Height",  "COOL_HEIGHT",    0.0, 0.20, 0.005, ".3f"),
        Param("Cool Noise",   "COOL_NOISE",     0.0, 1.0,  0.05,  ".2f"),
        Param("Diffuse Ctr",  "DIFFUSE_CENTER", 0.50,1.0,  0.02,  ".2f"),
        Param("Diffuse Side", "DIFFUSE_SIDE",   0.0, 0.25, 0.01,  ".2f"),
        Param("Turb X",       "TURB_X_SCALE",   0.0, 3.0,  0.1,   ".1f"),
        Param("Turb Y Bias",  "TURB_Y_BIAS",    0.0, 5.0,  0.1,   ".1f"),
        Param("Turb Y Range", "TURB_Y_RANGE",   0.0, 5.0,  0.1,   ".1f"),
        Param("Buoyancy",     "BUOYANCY",       0.0, 5.0,  0.1,   ".1f"),
        Param("Noise Detail", "NOISE_OCTAVES",  1,   3,    1,     "d"),
        Param("Ember Rate",   "EMBER_RATE",     0.0, 1.0,  0.05,  ".2f"),
        Param("Ember Burst",  "EMBER_BURST",    1,   15,   1,     "d"),
        Param("Ember Spread", "EMBER_SPREAD",   0.0, 1.2,  0.05,  ".2f"),
    ]

    def __init__(self):
        self.heat = [[0.0]*ROWS for _ in range(COLS)]
        self.time = 0.0; self.embers = []
        self._init_defaults()
        for x in range(COLS):
            for y in range(ROWS - self.SPARK_ZONE, ROWS):
                self.heat[x][y] = random.uniform(0.4, 0.9)

    def update(self, dt_ms, audio=None):
        self.time += dt_ms * 0.001
        dt = min(dt_ms / 16.67, 2.5)
        t = self.time
        center = (COLS - 1) / 2.0
        # FUEL governs everything — 0=dying embers, 1=raging inferno
        fuel = clampf(self.FUEL)
        fuel_sq = fuel * fuel  # quadratic response for dramatic range
        sz = max(3, int(self.SPARK_ZONE * (0.2 + fuel * 0.8)))

        # Sparks — noise-modulated intensity across the bottom half
        # Creates hot spots that wander, not uniform fuel injection
        for x in range(COLS):
            cw = 1.0 - abs(x - center) / (center + 0.5) * 0.25
            # Noise-driven hotspot: some regions get more fuel than others
            hotspot = (cyl_noise(x * 2, t * 3, t * 0.5, 1.0, 1.0) + 1) * 0.5
            for yo in range(sz):
                y = ROWS - 1 - yo
                if y < 0: break
                # More sparks near bottom, fewer at top of zone
                depth_factor = 1.0 - yo / sz * 0.6
                prob = self.SPARK_PROB * fuel * cw * depth_factor * (0.5 + hotspot * 0.7)
                if random.random() < prob:
                    intensity = random.uniform(self.SPARK_MIN, self.SPARK_MAX) * (0.3 + fuel * 0.7)
                    self.heat[x][y] = min(1.0, self.heat[x][y] + intensity * cw * dt)
        # Flares — bigger and more dramatic, scaled by fuel
        if random.random() < self.FLARE_PROB * fuel_sq:
            fc = random.randint(0, COLS - 1)
            flare_height = int(random.randint(15, min(ROWS // 2, 60)) * (0.3 + fuel * 0.7))
            for dx in range(-2, 3):
                fx = (fc + dx) % COLS
                for yo in range(flare_height):
                    y = ROWS - 1 - yo
                    fade = 1.0 - yo / flare_height
                    self.heat[fx][y] = min(1.0, self.heat[fx][y] + 0.6 * fade * dt)

        # Convection — process every other row for performance
        new_heat = [[0.0]*ROWS for _ in range(COLS)]
        turb_x = self.TURB_X_SCALE * (0.5 + fuel * 0.5)
        ty_bias = self.TURB_Y_BIAS * (0.3 + fuel * 0.7)
        ty_range = self.TURB_Y_RANGE * (0.3 + fuel * 0.7)
        buoy = self.BUOYANCY * (0.2 + fuel * 0.8)
        octs = max(1, int(self.NOISE_OCTAVES))
        for x in range(COLS):
            for y in range(ROWS):
                nx = cyl_fbm(x, y, t*8.0, octs, 0.5, 0.015)
                ny = cyl_fbm(x+5, y, t*7.0, octs, 0.5, 0.015)
                lh = self.heat[x][y]
                sx = x + nx * turb_x
                sy = y + ty_bias + lh * buoy + abs(ny) * ty_range
                sx = max(0.0, min(COLS-1.001, sx))
                sy = max(0.0, min(ROWS-1.001, sy))
                ix = int(sx); iy = int(sy)
                fx = sx - ix; fy = sy - iy
                ix2 = min(ix+1, COLS-1); iy2 = min(iy+1, ROWS-1)
                new_heat[x][y] = (self.heat[ix][iy]*(1-fx)*(1-fy) +
                    self.heat[ix2][iy]*fx*(1-fy) +
                    self.heat[ix][iy2]*(1-fx)*fy +
                    self.heat[ix2][iy2]*fx*fy)

        # Lateral diffusion (wraps around cylinder)
        dc = self.DIFFUSE_CENTER; ds = self.DIFFUSE_SIDE
        for y in range(ROWS):
            snap = [new_heat[x][y] for x in range(COLS)]
            for x in range(COLS):
                new_heat[x][y] = snap[x]*dc + snap[(x-1)%COLS]*ds + snap[(x+1)%COLS]*ds

        # Cooling — gentle in bottom 50%, aggressive above
        # This ensures the bottom half stays dynamic and filled with fire
        # Less fuel = more cooling (fire dies down)
        fuel_cool = 1.5 - fuel  # 0.5 at max fuel, 1.5 at no fuel
        cb = self.COOL_BASE * fuel_cool
        ch = self.COOL_HEIGHT * fuel_cool
        cn_amt = self.COOL_NOISE
        half = ROWS // 2
        for x in range(COLS):
            for y in range(ROWS):
                hf = (ROWS-1-y) / float(ROWS)  # 0=bottom, 1=top
                cn = (cyl_noise(x, y, t*10.0, 0.8, 0.03)+1)*0.5
                if hf < 0.5:
                    # Bottom half: very gentle cooling, mostly noise-driven
                    # This keeps fire alive and dynamic here
                    cool = (cb * 0.3 + cn * cn_amt * 0.15 + random.random()*0.003) * dt
                else:
                    # Top half: normal cooling ramps up to kill flames at tips
                    top_frac = (hf - 0.5) * 2  # 0 at midpoint, 1 at top
                    cool = (cb + top_frac * top_frac * ch +
                            cn * cn_amt * top_frac + random.random()*0.005) * dt
                new_heat[x][y] = max(0.0, new_heat[x][y] - cool)
        self.heat = new_heat

        # Ember bed — glowing coals across bottom 10 rows
        for x in range(COLS):
            for yo in range(12):
                y = ROWS-1-yo
                glow = 0.30 - yo * 0.02
                shimmer = (cyl_noise(x*2+500, yo*0.5, t*1.5, 1.0, 1.0)+1)*0.05
                self.heat[x][y] = max(self.heat[x][y], glow + shimmer)

        # Ember particles
        dt_s = dt_ms * 0.001
        if self.EMBER_RATE > 0 and fuel > 0.1:
            spread = self.EMBER_SPREAD; avg_b = max(1, int(self.EMBER_BURST))
            ember_rate = self.EMBER_RATE * (0.1 + fuel_sq * 0.9)
            for x in range(COLS):
                for y in range(0, ROWS, 3):
                    h = self.heat[x][y]
                    if 0.15 < h < 0.65 and random.random() < ember_rate*h*dt_s*1.5:
                        burst = max(1, int(random.gauss(avg_b, avg_b*0.5)))
                        for _ in range(burst):
                            if len(self.embers) < self.MAX_EMBERS:
                                self.embers.append(Ember(x, y, h, spread))
        alive = []
        for e in self.embers:
            e.x += e.vx * dt_s; e.y += e.vy * dt_s
            e.vx += random.uniform(-2.5, 2.5) * dt_s
            e.vy *= 0.98 ** (dt_s * 60)
            e.brightness *= 0.998 ** (dt_s * 60)
            e.flicker_phase += e.flicker_speed * dt_s
            e.life -= dt_s
            if e.life > 0 and -2 < e.y < ROWS: alive.append(e)
        self.embers = alive

        # Render heat
        for x in range(COLS):
            for y in range(ROWS):
                idx = xy(x, y)
                if idx >= 0: leds[idx] = list(fire_color(self.heat[x][y]))
        # Render embers
        for e in self.embers:
            col = int(round(e.x)); row = int(round(e.y))
            if 0 <= col < COLS and 0 <= row < ROWS:
                idx = xy(col, row)
                if idx >= 0:
                    af = max(0, e.life / e.max_life)
                    fl = 0.65 + 0.35 * math.sin(e.flicker_phase)
                    b = e.brightness * af * fl
                    ec = fire_color(min(1.0, af * 0.45 + 0.15))
                    leds[idx][0] = min(255, leds[idx][0] + int(ec[0]*b*1.4))
                    leds[idx][1] = min(255, leds[idx][1] + int(ec[1]*b*1.1))
                    leds[idx][2] = min(255, leds[idx][2] + int(ec[2]*b*0.4))


# ═════════════════════════════════════════════════════════════════
#  AMBIENT ANIMATIONS
# ═════════════════════════════════════════════════════════════════

class Plasma(AnimBase):
    name = "Plasma"
    category = "Ambient"
    has_palette = True
    SPEED = 1.0; SCALE = 1.0
    PARAMS = [Param("Speed","SPEED",0.1,5.0,0.1,".1f"), Param("Scale","SCALE",0.2,3.0,0.1,".1f")]
    def __init__(self): self.t = 0.0; self._init_defaults()
    def update(self, dt_ms, audio=None):
        self.t += dt_ms * 0.001 * self.SPEED
        s = self.SCALE; t = self.t
        for x in range(COLS):
            ax = x / COLS * 6.2832  # angle for cylinder wrap
            for y in range(ROWS):
                v = (math.sin(ax*2*s + t*1.3) +
                     math.sin(y*s*0.035 + t*0.7) +
                     math.sin(ax*3*s + y*s*0.02 + t*1.1) +
                     math.sin(math.sqrt(abs(math.sin(ax)*4 + y*y*0.001))*s*2 + t*0.9) +
                     cyl_noise(x, y, t*0.5, s, 0.01) * 1.5) / 5.0
                idx = xy(x, y)
                if idx >= 0: leds[idx] = list(self.pal((v+1)*0.5))


class Aurora(AnimBase):
    name = "Aurora Borealis"
    category = "Ambient"
    has_palette = True
    SPEED = 0.4; WAVE = 1.0; BRIGHT = 0.9
    PARAMS = [Param("Speed","SPEED",0.05,2.0,0.05,".2f"),
              Param("Wave","WAVE",0.2,3.0,0.1,".1f"),
              Param("Bright","BRIGHT",0.2,1.0,0.05,".2f")]
    def __init__(self): self.t = 0.0; self._init_defaults()
    def update(self, dt_ms, audio=None):
        self.t += dt_ms * 0.001 * self.SPEED
        t = self.t; w = self.WAVE; br = self.BRIGHT
        for x in range(COLS):
            for y in range(ROWS):
                curtain = noise01(x*0.3, y*0.008*w, t*0.5)
                wave = (math.sin(y*0.02*w + t*2 + x*0.8) + 1) * 0.5
                shimmer = noise01(x*0.5+100, y*0.02, t*3) * 0.4
                v = clampf(curtain * wave * br + shimmer * curtain * br * 0.5)
                idx = xy(x, y)
                if idx >= 0: leds[idx] = list(self.pal(curtain * 0.8 + 0.1))
                if idx >= 0:
                    c = leds[idx]
                    c[0] = int(c[0] * v); c[1] = int(c[1] * v); c[2] = int(c[2] * v)


class LavaLamp(AnimBase):
    name = "Lava Lamp"
    category = "Ambient"
    has_palette = True
    SPEED = 0.3; BLOBS = 5; SIZE = 1.0
    PARAMS = [Param("Speed","SPEED",0.05,2.0,0.05,".2f"),
              Param("Blobs","BLOBS",2,12,1,"d"),
              Param("Size","SIZE",0.3,3.0,0.1,".1f")]
    def __init__(self):
        self.t = 0.0; self._init_defaults()
        self._blob_seeds = [(random.random()*100, random.random()*100) for _ in range(12)]
    def update(self, dt_ms, audio=None):
        self.t += dt_ms * 0.001 * self.SPEED
        t = self.t; nb = int(self.BLOBS); sz = self.SIZE
        clear_leds()
        for x in range(COLS):
            for y in range(ROWS):
                val = 0.0
                for bi in range(nb):
                    sx, sy = self._blob_seeds[bi]
                    bx = (COLS/2) + math.sin(t*0.7 + sx*6.28) * COLS * 0.4
                    by = (ROWS/2) + math.sin(t*0.3 + sy*6.28) * ROWS * 0.4
                    dx = (x - bx) / max(1, sz * 2)
                    dy = (y - by) / max(1, sz * 25)
                    dist_sq = dx*dx + dy*dy
                    val += 1.0 / (1.0 + dist_sq * 3)
                val = clampf(val)
                idx = xy(x, y)
                if idx >= 0:
                    c = self.pal(val * 0.8 + 0.1)
                    leds[idx] = [int(c[0]*val), int(c[1]*val), int(c[2]*val)]


class OceanWaves(AnimBase):
    name = "Ocean Waves"
    category = "Ambient"
    has_palette = True
    SPEED = 0.6; DEPTH = 1.0; LAYERS = 3
    PARAMS = [Param("Speed","SPEED",0.1,3.0,0.1,".1f"),
              Param("Depth","DEPTH",0.3,3.0,0.1,".1f"),
              Param("Layers","LAYERS",1,5,1,"d")]
    def __init__(self): self.t = 0.0; self.palette_idx = 1; self._init_defaults()
    def update(self, dt_ms, audio=None):
        self.t += dt_ms * 0.001 * self.SPEED
        t = self.t; d = self.DEPTH; nl = int(self.LAYERS)
        for x in range(COLS):
            for y in range(ROWS):
                v = 0.0
                for layer in range(nl):
                    freq = (layer + 1) * 0.5
                    phase = layer * 1.7
                    v += math.sin(y * 0.02 * d * freq + t * (1 + layer * 0.4) + phase +
                                  x * 0.3 * freq) / nl
                v = (v + 1) * 0.5
                depth_fade = 1.0 - (ROWS - 1 - y) / ROWS * 0.3
                v *= depth_fade
                idx = xy(x, y)
                if idx >= 0: leds[idx] = list(self.pal(v))


class Starfield(AnimBase):
    name = "Starfield"
    category = "Ambient"
    has_palette = True
    DENSITY = 0.03; TWINKLE = 1.0; SPEED = 1.0
    PARAMS = [Param("Density","DENSITY",0.005,0.1,0.005,".3f"),
              Param("Twinkle","TWINKLE",0.2,3.0,0.1,".1f"),
              Param("Speed","SPEED",0.2,3.0,0.1,".1f")]
    def __init__(self):
        self._init_defaults()
        self.stars = []
        for _ in range(int(COLS * ROWS * 0.03)):
            self.stars.append([random.randint(0,COLS-1), random.randint(0,ROWS-1),
                              random.uniform(0,6.28), random.uniform(0.5,3.0),
                              random.uniform(0.3,1.0)])
    def update(self, dt_ms, audio=None):
        dt = dt_ms * 0.001
        clear_leds()
        target = int(COLS * ROWS * self.DENSITY)
        while len(self.stars) < target:
            self.stars.append([random.randint(0,COLS-1), random.randint(0,ROWS-1),
                              random.uniform(0,6.28), random.uniform(0.5,3.0),
                              random.uniform(0.3,1.0)])
        while len(self.stars) > target:
            self.stars.pop()
        for s in self.stars:
            s[2] += s[3] * self.SPEED * dt
            b = (math.sin(s[2] * self.TWINKLE) + 1) * 0.5 * s[4]
            c = self.pal(s[4])
            add_led(s[0], s[1], c[0]*b, c[1]*b, c[2]*b)


class MatrixRain(AnimBase):
    name = "Matrix Rain"
    category = "Ambient"
    has_palette = True
    SPEED = 1.0; DENSITY = 0.4; TRAIL = 25
    PARAMS = [Param("Speed","SPEED",0.2,4.0,0.1,".1f"),
              Param("Density","DENSITY",0.1,1.0,0.05,".2f"),
              Param("Trail","TRAIL",5,60,1,"d")]
    def __init__(self): self.drops = []; self.t = 0; self.palette_idx = 3; self._init_defaults()
    def update(self, dt_ms, audio=None):
        dt = dt_ms * 0.001; clear_leds()
        for x in range(COLS):
            if random.random() < self.DENSITY * dt * 3:
                # Mix of speeds: many slow, some medium, few fast
                r = random.random()
                if r < 0.5:
                    spd = random.uniform(6, 20)    # slow drips
                elif r < 0.85:
                    spd = random.uniform(20, 50)   # medium
                else:
                    spd = random.uniform(50, 90)   # fast streaks
                self.drops.append([x, -1.0, spd * self.SPEED, random.uniform(0.5, 1.0)])
        trail = int(self.TRAIL)
        alive = []
        for d in self.drops:
            d[1] += d[2] * dt
            head = int(d[1])
            if head - trail < ROWS:
                alive.append(d)
                for ty in range(trail):
                    py = head - ty
                    if 0 <= py < ROWS:
                        fade = (1.0 - ty / trail) ** 1.5
                        c = self.pal(fade)
                        b = fade * d[3]
                        add_led(d[0], py, c[0]*b, c[1]*b, c[2]*b)
        self.drops = alive


class Breathing(AnimBase):
    name = "Breathing"
    category = "Ambient"
    has_palette = True
    SPEED = 0.3; WAVE = 1.0
    PARAMS = [Param("Speed","SPEED",0.05,2.0,0.05,".2f"),
              Param("Wave","WAVE",0.0,3.0,0.1,".1f")]
    def __init__(self): self.t = 0.0; self._init_defaults()
    def update(self, dt_ms, audio=None):
        self.t += dt_ms * 0.001 * self.SPEED
        t = self.t; w = self.WAVE
        breath = (math.sin(t * 2) + 1) * 0.5
        for x in range(COLS):
            for y in range(ROWS):
                hue_shift = y / ROWS + math.sin(y * 0.01 * w + t) * 0.1
                b = breath * (0.7 + 0.3 * math.sin(y * 0.02 * w + x * 0.5 + t * 0.5))
                c = self.pal(hue_shift % 1.0)
                idx = xy(x, y)
                if idx >= 0:
                    leds[idx] = [int(c[0]*b), int(c[1]*b), int(c[2]*b)]


class Fireflies(AnimBase):
    name = "Fireflies"
    category = "Ambient"
    has_palette = True
    COUNT = 20; SPEED = 0.5; GLOW = 1.0
    PARAMS = [Param("Count","COUNT",3,60,1,"d"),
              Param("Speed","SPEED",0.1,2.0,0.1,".1f"),
              Param("Glow","GLOW",0.3,3.0,0.1,".1f")]
    def __init__(self):
        self._init_defaults()
        self.flies = []
        for _ in range(60):
            self.flies.append({'x': random.uniform(0,COLS-1), 'y': random.uniform(0,ROWS-1),
                'vx': random.uniform(-1,1), 'vy': random.uniform(-3,3),
                'phase': random.uniform(0,6.28), 'freq': random.uniform(0.3,1.5),
                'hue': random.random()})
    def update(self, dt_ms, audio=None):
        dt = dt_ms * 0.001; clear_leds()
        count = int(self.COUNT); glow = self.GLOW
        for i, f in enumerate(self.flies[:count]):
            f['x'] += f['vx'] * self.SPEED * dt
            f['y'] += f['vy'] * self.SPEED * dt
            if f['x'] < 0 or f['x'] >= COLS: f['vx'] *= -1; f['x'] = clampf(f['x'], 0, COLS-1)
            if f['y'] < 0 or f['y'] >= ROWS: f['vy'] *= -1; f['y'] = clampf(f['y'], 0, ROWS-1)
            f['vx'] += random.uniform(-0.5, 0.5) * dt
            f['vy'] += random.uniform(-1, 1) * dt
            f['phase'] += f['freq'] * dt
            b = max(0, math.sin(f['phase'] * 3)) ** 2 * glow
            c = self.pal(f['hue'])
            cx, cy = int(round(f['x'])), int(round(f['y']))
            # Glow radius
            for dx in range(-1, 2):
                for dy in range(-2, 3):
                    dist = abs(dx) + abs(dy) * 0.5
                    fade = max(0, 1.0 - dist * 0.5) * b
                    if fade > 0.01:
                        add_led(cx+dx, cy+dy, c[0]*fade, c[1]*fade, c[2]*fade)


class Nebula(AnimBase):
    name = "Nebula"
    category = "Ambient"
    has_palette = True
    SPEED = 0.2; SCALE = 1.0; LAYERS = 2
    PARAMS = [Param("Speed","SPEED",0.05,1.5,0.05,".2f"),
              Param("Scale","SCALE",0.3,3.0,0.1,".1f"),
              Param("Layers","LAYERS",1,3,1,"d")]
    def __init__(self): self.t = 0.0; self.palette_idx = 9; self._init_defaults()
    def update(self, dt_ms, audio=None):
        self.t += dt_ms * 0.001 * self.SPEED
        t = self.t; sc = self.SCALE; nl = int(self.LAYERS)
        for x in range(COLS):
            for y in range(ROWS):
                v = _fbm(x*0.15*sc+10, y*0.008*sc, t*0.5, nl)
                v2 = _fbm(x*0.2*sc+50, y*0.006*sc, t*0.3+100, nl)
                hue = (v + 1) * 0.5
                bright = clampf((v2 + 0.8) * 0.7)
                c = self.pal(hue)
                idx = xy(x, y)
                if idx >= 0:
                    leds[idx] = [int(c[0]*bright), int(c[1]*bright), int(c[2]*bright)]


class Kaleidoscope(AnimBase):
    name = "Kaleidoscope"
    category = "Ambient"
    has_palette = True
    SPEED = 0.5; SEGMENTS = 6; ZOOM = 1.0
    PARAMS = [Param("Speed","SPEED",0.1,3.0,0.1,".1f"),
              Param("Segments","SEGMENTS",3,12,1,"d"),
              Param("Zoom","ZOOM",0.3,3.0,0.1,".1f")]
    def __init__(self): self.t = 0.0; self._init_defaults()
    def update(self, dt_ms, audio=None):
        self.t += dt_ms * 0.001 * self.SPEED
        t = self.t; seg = int(self.SEGMENTS); zm = self.ZOOM
        cx, cy = COLS / 2.0, ROWS / 2.0
        for x in range(COLS):
            for y in range(ROWS):
                dx = (x - cx) / max(1, cx)
                dy = (y - cy) / max(1, cy) * 0.3
                angle = math.atan2(dy, dx)
                dist = math.sqrt(dx*dx + dy*dy) * zm
                # Mirror into segments
                angle = abs(((angle + t) % (6.28 / seg)) - 3.14 / seg)
                v = math.sin(dist * 5 + t * 2) * 0.5 + math.sin(angle * seg + t) * 0.5
                hue = (v + 1) * 0.25 + dist * 0.3
                bright = clampf(0.3 + (math.sin(dist * 3 - t * 2) + 1) * 0.35)
                c = self.pal(hue % 1.0)
                idx = xy(x, y)
                if idx >= 0:
                    leds[idx] = [int(c[0]*bright), int(c[1]*bright), int(c[2]*bright)]



class FlowField(AnimBase):
    """Perlin noise flow field with particle trails — Fidenza-style generative art."""
    name = "Flow Field"
    category = "Ambient"
    has_palette = True
    SPEED = 0.3; PARTICLES = 80; FADE = 0.92; NOISE_SCALE = 1.0
    PARAMS = [Param("Speed","SPEED",0.05,2.0,0.05,".2f"),
              Param("Particles","PARTICLES",10,200,10,"d"),
              Param("Fade","FADE",0.8,0.99,0.01,".2f"),
              Param("Noise Scale","NOISE_SCALE",0.3,3.0,0.1,".1f")]
    def __init__(self):
        self.t = 0.0; self._init_defaults()
        self._pts = []
        for _ in range(200):
            self._pts.append([random.uniform(0, COLS), random.uniform(0, ROWS),
                             random.random()])  # x, y, hue
    def update(self, dt_ms, audio=None):
        self.t += dt_ms * 0.001 * self.SPEED
        dt = dt_ms * 0.001
        # Fade existing pixels
        fade = self.FADE
        for i in range(TOTAL_LEDS):
            leds[i][0] = int(leds[i][0] * fade)
            leds[i][1] = int(leds[i][1] * fade)
            leds[i][2] = int(leds[i][2] * fade)
        ns = self.NOISE_SCALE
        count = int(self.PARTICLES)
        for p in self._pts[:count]:
            # Get flow angle from cylinder-wrapped noise
            angle = cyl_noise(p[0], p[1], self.t * 0.5, ns, 0.008 * ns) * 6.28
            # Move particle along flow
            p[0] += math.cos(angle) * 30 * dt * self.SPEED
            p[1] += math.sin(angle) * 30 * dt * self.SPEED
            # Cylinder wrap x
            p[0] = p[0] % COLS
            # Respawn if off grid vertically
            if p[1] < 0 or p[1] >= ROWS:
                p[0] = random.uniform(0, COLS)
                p[1] = random.uniform(0, ROWS)
                p[2] = random.random()
            # Draw
            c = self.pal(p[2])
            add_led(int(p[0]), int(p[1]), c[0]*0.8, c[1]*0.8, c[2]*0.8)


class Moire(AnimBase):
    """Hypnotic moiré interference — overlapping rings create depth illusion."""
    name = "Moire"
    category = "Ambient"
    has_palette = True
    SPEED = 0.4; SCALE = 1.0; CENTERS = 3
    PARAMS = [Param("Speed","SPEED",0.05,2.0,0.05,".2f"),
              Param("Scale","SCALE",0.3,3.0,0.1,".1f"),
              Param("Centers","CENTERS",2,5,1,"d")]
    def __init__(self): self.t = 0.0; self._init_defaults()
    def update(self, dt_ms, audio=None):
        self.t += dt_ms * 0.001 * self.SPEED
        t = self.t; sc = self.SCALE; nc = int(self.CENTERS)
        # Center points orbit on Lissajous curves (cylinder-aware)
        centers = []
        for i in range(nc):
            phase = i * 6.28 / nc
            # Use angle-based x so centers wrap around
            cx = (math.sin(t * 0.7 + phase) * 0.5 + 0.5) * COLS
            cy = ROWS / 2 + math.sin(t * 0.3 + phase * 1.7) * ROWS * 0.35
            centers.append((cx, cy))
        for x in range(COLS):
            for y in range(ROWS):
                val = 0.0
                for cx, cy in centers:
                    # Cylinder-aware distance: shortest path around
                    dx = x - cx
                    if abs(dx) > COLS / 2:
                        dx = dx - COLS if dx > 0 else dx + COLS
                    dy = (y - cy) * (COLS / ROWS) * 5  # aspect correction
                    dist = math.sqrt(dx * dx + dy * dy)
                    val += math.sin(dist * sc * 3 + t * 2)
                val /= nc
                hue = (val + 1) * 0.5
                bright = clampf((abs(val) ** 0.5) * 0.9 + 0.1)
                c = self.pal(hue)
                idx = xy(x, y)
                if idx >= 0:
                    leds[idx] = [int(c[0]*bright), int(c[1]*bright), int(c[2]*bright)]


# ═════════════════════════════════════════════════════════════════
#  SOUND-REACTIVE ANIMATIONS
# ═════════════════════════════════════════════════════════════════

class Spectrum(AnimBase):
    name = "Spectrum"
    category = "Sound"
    has_palette = True
    GAIN = 1.0; DECAY = 0.85
    PARAMS = [Param("Gain","GAIN",0.2,5.0,0.1,".1f"),
              Param("Decay","DECAY",0.5,0.99,0.01,".2f")]
    def __init__(self):
        self._init_defaults()
        self._heights = [0.0] * COLS
        self._peaks = [0.0] * COLS
        self._peak_age = [0] * COLS
        self._drop_flash = 0.0
    def update(self, dt_ms, audio=None):
        clear_leds()
        if not audio: return
        # DROP: all bars slam to max, rainbow cascade
        if audio.drop:
            self._drop_flash = 1.0
            for i in range(COLS): self._heights[i] = ROWS; self._peaks[i] = ROWS
        self._drop_flash *= 0.96
        gain = self.GAIN * (1 + audio.buildup * 0.5)  # buildup intensifies
        for i in range(COLS):
            target = audio.bands[i] * ROWS * gain
            self._heights[i] = max(target, self._heights[i] * self.DECAY)
            h = int(self._heights[i])
            if h > self._peaks[i]:
                self._peaks[i] = h; self._peak_age[i] = 0
            self._peak_age[i] += 1
            if self._peak_age[i] > 30:
                self._peaks[i] = max(0, self._peaks[i] - 1)
            for y in range(min(h, ROWS)):
                row = ROWS - 1 - y
                if self._drop_flash > 0.1:
                    c = pal_color(0, (y/ROWS + i/COLS) % 1.0)  # rainbow on drop
                    b = self._drop_flash
                    set_led(i, row, int(c[0]*b), int(c[1]*b), int(c[2]*b))
                else:
                    set_led(i, row, *self.pal(y / ROWS))
            pk = int(self._peaks[i])
            if 0 < pk < ROWS:
                set_led(i, ROWS - 1 - pk, 255, 255, 255)


class VUMeter(AnimBase):
    name = "VU Meter"
    category = "Sound"
    has_palette = True
    GAIN = 1.5; DECAY = 0.9
    PARAMS = [Param("Gain","GAIN",0.2,5.0,0.1,".1f"),
              Param("Decay","DECAY",0.5,0.99,0.01,".2f")]
    def __init__(self): self._h = 0.0; self._drop_hue = 0.0; self._drop_flash = 0.0; self._init_defaults()
    def update(self, dt_ms, audio=None):
        clear_leds()
        if not audio: return
        if audio.drop:
            self._drop_flash = 1.0; self._drop_hue = random.random()
        self._drop_flash *= 0.95
        # During breakdown, dim and pulse slowly (anticipation)
        if audio.breakdown:
            breath = (math.sin(audio._time * 4) + 1) * 0.15
            for i in range(TOTAL_LEDS):
                c = self.pal(0.5)
                leds[i] = [int(c[0]*breath), int(c[1]*breath), int(c[2]*breath)]
            return
        gain = self.GAIN * (1 + audio.buildup * 0.8)
        target = audio.volume * ROWS * gain
        self._h = max(target, self._h * self.DECAY)
        h = int(self._h)
        for x in range(COLS):
            for y in range(min(h, ROWS)):
                row = ROWS - 1 - y
                if self._drop_flash > 0.1:
                    c = pal_color(0, (y/ROWS + self._drop_hue) % 1.0)
                    set_led(x, row, int(c[0]*self._drop_flash), int(c[1]*self._drop_flash), int(c[2]*self._drop_flash))
                else:
                    set_led(x, row, *self.pal(y / ROWS))


class BeatPulse(AnimBase):
    name = "Beat Pulse"
    category = "Sound"
    has_palette = True
    DECAY = 0.92; FLASH = 1.0
    PARAMS = [Param("Decay","DECAY",0.8,0.99,0.01,".2f"),
              Param("Flash","FLASH",0.3,2.0,0.1,".1f")]
    def __init__(self):
        self._energy = 0.0; self._hue = 0.0; self._strobe_timer = 0.0
        self._init_defaults()
    def update(self, dt_ms, audio=None):
        dt = dt_ms * 0.001
        if audio and audio.beat:
            self._energy = self.FLASH * (1 + audio.buildup)
            self._hue = (self._hue + 0.08) % 1.0
        # DROP: rapid strobe burst for 2 seconds
        if audio and audio.drop:
            self._strobe_timer = 2.0
        self._strobe_timer = max(0, self._strobe_timer - dt)
        if self._strobe_timer > 0:
            # Fast strobe: alternate full bright / dark
            strobe_on = int(self._strobe_timer * 20) % 2 == 0
            if strobe_on:
                hue = (self._hue + random.random() * 0.3) % 1.0
                c = pal_color(0, hue)  # rainbow strobe
                for i in range(TOTAL_LEDS):
                    leds[i] = [c[0], c[1], c[2]]
            else:
                clear_leds()
            return
        # Breakdown: dim pulsing
        if audio and audio.breakdown:
            b = (math.sin(audio._time * 6) + 1) * 0.1
            c = self.pal(self._hue)
            for i in range(TOTAL_LEDS):
                leds[i] = [int(c[0]*b), int(c[1]*b), int(c[2]*b)]
            return
        self._energy *= self.DECAY
        c = self.pal(self._hue)
        e = self._energy
        for i in range(TOTAL_LEDS):
            leds[i] = [int(c[0]*e), int(c[1]*e), int(c[2]*e)]


class BassReactiveFire(AnimBase):
    """Beat-tracked fire: bass drives flames, bars trigger flares,
    phrases trigger rainbow explosions. Optimized for house/techno."""
    name = "Bass Fire"
    category = "Sound"
    GAIN = 3.0; BASE_SPARK = 0.3
    PARAMS = [Param("Gain","GAIN",0.5,8.0,0.5,".1f"),
              Param("Base Spark","BASE_SPARK",0.1,0.8,0.05,".2f")]
    def __init__(self):
        self._fire = Fireplace()
        self._rainbow_timer = 0.0
        self._flash_bright = 0.0
        self._flash_hue = 0.0
        self._init_defaults()

    def update(self, dt_ms, audio=None):
        dt_s = dt_ms * 0.001
        bass = self.BASE_SPARK
        if audio:
            bass = clampf(self.BASE_SPARK + audio.bass * self.GAIN * 2 +
                         audio.bands[0] * self.GAIN * 0.5)

        self._fire.SPARK_PROB = bass
        self._fire.SPARK_MAX = clampf(bass * 1.5)
        self._fire.FLARE_PROB = 0.01

        if audio:
            # ── Every beat: fire surges ───────────────────────────
            if audio.beat:
                self._fire.FLARE_PROB = 0.6
                self._fire.SPARK_MAX = 1.0
                # Inject heat directly into bottom rows
                for x in range(COLS):
                    for yo in range(8):
                        y = ROWS - 1 - yo
                        self._fire.heat[x][y] = min(1.0,
                            self._fire.heat[x][y] + audio.beat_energy * 0.4)

            # ── Every 4th beat (downbeat): color-shifted flare ────
            if audio.is_downbeat:
                self._fire.FLARE_PROB = 0.95
                self._flash_bright = 0.6
                self._flash_hue = (self._flash_hue + 0.25) % 1.0
                # Big heat injection across all columns
                for x in range(COLS):
                    for yo in range(15):
                        y = ROWS - 1 - yo
                        self._fire.heat[x][y] = min(1.0,
                            self._fire.heat[x][y] + 0.6)

            # ── Every 16th beat (phrase): RAINBOW EXPLOSION ───────
            if audio.is_phrase:
                self._rainbow_timer = 1.5  # seconds of rainbow
                # Max out the entire bottom half
                for x in range(COLS):
                    for yo in range(ROWS // 3):
                        y = ROWS - 1 - yo
                        self._fire.heat[x][y] = 1.0
                # Spawn a massive burst of embers
                for _ in range(40):
                    if len(self._fire.embers) < self._fire.MAX_EMBERS:
                        ex = random.uniform(0, COLS - 1)
                        ey = ROWS - random.randint(1, 20)
                        self._fire.embers.append(
                            Ember(ex, ey, 1.0, 0.8))

            # ── Drop detection: everything goes white-hot ─────────
            if audio.drop:
                self._rainbow_timer = 2.5
                for x in range(COLS):
                    for y in range(ROWS):
                        self._fire.heat[x][y] = min(1.0,
                            self._fire.heat[x][y] + 0.8)

        # Update the fire simulation
        self._fire.update(dt_ms, audio)

        # ── Rainbow explosion overlay ─────────────────────────────
        # During phrase beats, overlay rainbow colors on the embers
        # and add rainbow sparkle across the fire
        self._rainbow_timer = max(0, self._rainbow_timer - dt_s)
        if self._rainbow_timer > 0:
            intensity = min(1.0, self._rainbow_timer / 0.5)
            t = self._rainbow_timer * 5  # fast cycling
            for x in range(COLS):
                for y in range(ROWS):
                    idx = xy(x, y)
                    if idx < 0:
                        continue
                    h = self._fire.heat[x][y]
                    if h > 0.1:
                        # Rainbow hue based on position + time
                        hue = ((y / ROWS + x / COLS * 0.3 + t) % 1.0)
                        rc = hsv2rgb(int(hue * 255), 200, int(h * 255 * intensity))
                        led = leds[idx]
                        # Blend rainbow over fire
                        blend = intensity * 0.7
                        led[0] = int(led[0] * (1-blend) + rc[0] * blend)
                        led[1] = int(led[1] * (1-blend) + rc[1] * blend)
                        led[2] = int(led[2] * (1-blend) + rc[2] * blend)

        # ── Downbeat color flash overlay ──────────────────────────
        self._flash_bright *= 0.9
        if self._flash_bright > 0.05:
            fc = hsv2rgb(int(self._flash_hue * 255), 180, int(self._flash_bright * 255))
            for x in range(COLS):
                for yo in range(5):
                    y = ROWS - 1 - yo
                    add_led(x, y, fc[0], fc[1], fc[2])


class SoundRipples(AnimBase):
    """Beat-tracked ripples — kicks spawn from bottom, snares from center,
    hi-hats from top. Phrase beats spawn full-matrix rainbow rings."""
    name = "Sound Ripples"
    category = "Sound"
    has_palette = True
    GAIN = 2.0; SPEED = 1.5; DECAY = 0.93; SENSITIVITY = 0.15
    PARAMS = [Param("Gain","GAIN",0.2,5.0,0.1,".1f"),
              Param("Speed","SPEED",0.3,4.0,0.1,".1f"),
              Param("Decay","DECAY",0.85,0.99,0.01,".2f"),
              Param("Sensitivity","SENSITIVITY",0.02,0.5,0.02,".2f")]
    def __init__(self):
        self._ripples = []; self._bass_prev = 0; self._mids_prev = 0
        self._highs_prev = 0; self._init_defaults()

    def update(self, dt_ms, audio=None):
        dt = dt_ms * 0.001
        # Fade existing instead of clear — trails look better
        for i in range(TOTAL_LEDS):
            leds[i][0] = int(leds[i][0] * 0.85)
            leds[i][1] = int(leds[i][1] * 0.85)
            leds[i][2] = int(leds[i][2] * 0.85)

        if audio:
            sens = self.SENSITIVITY
            # ── Kick (bass onset) → ripple from bottom ────────────
            bass_delta = audio.bass - self._bass_prev
            if bass_delta > sens or audio.beat:
                intensity = clampf(max(bass_delta, audio.beat_energy * 0.3) * 2)
                self._ripples.append([COLS/2.0, ROWS*0.85, 0.0,
                    random.uniform(0, 0.15), intensity, 5.0])  # warm hue, wide ring
            self._bass_prev = audio.bass

            # ── Snare/clap (mids onset) → ripple from center ─────
            mids_delta = audio.mids - self._mids_prev
            if mids_delta > sens * 1.5:
                self._ripples.append([random.uniform(1,COLS-2), ROWS*0.5, 0.0,
                    random.uniform(0.2, 0.5), clampf(mids_delta * 3), 3.0])
            self._mids_prev = audio.mids

            # ── Hi-hat (highs onset) → small ripple from top ─────
            highs_delta = audio.highs - self._highs_prev
            if highs_delta > sens * 0.8:
                self._ripples.append([random.uniform(0,COLS-1), ROWS*0.15, 0.0,
                    random.uniform(0.5, 0.8), clampf(highs_delta * 2), 2.0])
            self._highs_prev = audio.highs

            # ── Phrase beat → massive rainbow ring from center ────
            if audio.is_phrase:
                self._ripples.append([COLS/2.0, ROWS/2.0, 0.0,
                    -1.0, 1.5, 8.0])  # -1 hue = rainbow mode

            # ── Downbeat → bright ring ────────────────────────────
            elif audio.is_downbeat:
                self._ripples.append([COLS/2.0, ROWS*0.7, 0.0,
                    random.random(), 1.0, 6.0])

        alive = []
        for r in self._ripples:
            # r = [cx, cy, radius, hue, intensity, ring_width]
            r[2] += self.SPEED * 80 * dt
            r[4] *= self.DECAY ** (dt * 60)
            if r[4] > 0.015 and r[2] < ROWS * 1.5:
                alive.append(r)
                rw = r[5]
                for x in range(COLS):
                    for y in range(ROWS):
                        # Cylinder-aware distance
                        dx = x - r[0]
                        if abs(dx) > COLS / 2:
                            dx = dx - COLS if dx > 0 else dx + COLS
                        dx *= (ROWS / COLS)  # aspect correction
                        dy = y - r[1]
                        dist = math.sqrt(dx*dx + dy*dy)
                        ring = abs(dist - r[2])
                        if ring < rw:
                            b = (1.0 - ring / rw) * r[4] * self.GAIN
                            if r[3] < 0:  # rainbow mode
                                hue = (dist * 0.02 + r[2] * 0.01) % 1.0
                                c = pal_color(0, hue)  # rainbow palette
                            else:
                                c = self.pal(r[3])
                            add_led(x, y, c[0]*b, c[1]*b, c[2]*b)
        self._ripples = alive


class Spectrogram(AnimBase):
    name = "Spectrogram"
    category = "Sound"
    has_palette = True
    GAIN = 2.0; SCROLL = 1.0
    PARAMS = [Param("Gain","GAIN",0.5,8.0,0.5,".1f"),
              Param("Scroll","SCROLL",0.3,3.0,0.1,".1f")]
    def __init__(self):
        self._grid = [[0.0]*COLS for _ in range(ROWS)]
        self._accum = 0.0; self._drop_flash = 0.0; self._init_defaults()
    def update(self, dt_ms, audio=None):
        if audio and audio.drop:
            self._drop_flash = 1.0
        self._drop_flash *= 0.95
        gain = self.GAIN * (1 + (audio.buildup if audio else 0) * 0.5)
        self._accum += dt_ms * self.SCROLL * 0.06
        while self._accum >= 1.0:
            self._accum -= 1.0
            self._grid.pop(0)
            row = [0.0] * COLS
            if audio:
                if self._drop_flash > 0.3:
                    row = [1.0] * COLS  # white flash line on drop
                else:
                    for i in range(COLS):
                        row[i] = clampf(audio.bands[i] * gain)
            self._grid.append(row)
        for y in range(ROWS):
            for x in range(COLS):
                v = self._grid[y][x]
                if self._drop_flash > 0.1 and v > 0.8:
                    c = pal_color(0, (y/ROWS) % 1.0)  # rainbow on drop lines
                else:
                    c = self.pal(v)
                b = v
                set_led(x, y, int(c[0]*b), int(c[1]*b), int(c[2]*b))


class SoundWorm(AnimBase):
    name = "Sound Worm"
    category = "Sound"
    has_palette = True
    GAIN = 1.0; SPEED = 1.0; WIDTH = 2
    PARAMS = [Param("Gain","GAIN",0.2,5.0,0.1,".1f"),
              Param("Speed","SPEED",0.3,3.0,0.1,".1f"),
              Param("Width","WIDTH",1,5,1,"d")]
    def __init__(self): self.t = 0.0; self._drop_split = 0.0; self._init_defaults()
    def update(self, dt_ms, audio=None):
        self.t += dt_ms * 0.001 * self.SPEED
        # Fade instead of clear for trails
        for i in range(TOTAL_LEDS):
            leds[i][0] = int(leds[i][0] * 0.8)
            leds[i][1] = int(leds[i][1] * 0.8)
            leds[i][2] = int(leds[i][2] * 0.8)
        vol = audio.volume if audio else 0.3
        buildup = audio.buildup if audio else 0
        if audio and audio.drop:
            self._drop_split = 2.0
        self._drop_split *= 0.97
        w = int(self.WIDTH)
        # During drop: split into multiple worms with rainbow
        num_worms = 1 + int(self._drop_split * 2)
        for worm in range(num_worms):
            phase_offset = worm * 6.28 / max(1, num_worms)
            for y in range(ROWS):
                amp = (vol + buildup * 0.5 + self._drop_split * 0.3) * self.GAIN * (COLS / 2)
                wave_x = COLS / 2 + math.sin(y * 0.03 + self.t * 3 + phase_offset) * amp
                for dx in range(-w, w + 1):
                    px = int(round(wave_x)) + dx
                    fade = 1.0 - abs(dx) / (w + 1)
                    if self._drop_split > 0.5:
                        hue = (y/ROWS + worm/num_worms + self.t*0.3) % 1.0
                        c = pal_color(0, hue)
                    else:
                        hue = (y / ROWS + self.t * 0.1) % 1.0
                        c = self.pal(hue)
                    add_led(px, y, c[0]*fade, c[1]*fade, c[2]*fade)


class ParticleBurst(AnimBase):
    """Beat-triggered particles. DROP = FIREWORKS: multiple simultaneous
    rainbow explosions from different launch points with trailing sparks."""
    name = "Particle Burst"
    category = "Sound"
    has_palette = True
    GRAVITY = 0.5; SPEED = 1.0; COUNT = 30
    PARAMS = [Param("Gravity","GRAVITY",0.0,2.0,0.1,".1f"),
              Param("Speed","SPEED",0.3,3.0,0.1,".1f"),
              Param("Count","COUNT",5,60,5,"d")]
    def __init__(self): self._particles = []; self._init_defaults()

    def _spawn_burst(self, cx, cy, count, hue, speed_mult=1.0, rainbow=False):
        for _ in range(count):
            angle = random.uniform(0, 6.28)
            speed = random.uniform(15, 60) * self.SPEED * speed_mult
            h = (random.random() if rainbow else hue + random.uniform(-0.1, 0.1))
            self._particles.append([cx, cy,
                math.cos(angle)*speed, math.sin(angle)*speed*2.5,
                h, 1.0,
                1 if random.random() < 0.3 else 0])  # [6]=trail flag

    def update(self, dt_ms, audio=None):
        dt = dt_ms * 0.001
        # Fade instead of clear — trails persist
        for i in range(TOTAL_LEDS):
            leds[i][0] = int(leds[i][0] * 0.82)
            leds[i][1] = int(leds[i][1] * 0.82)
            leds[i][2] = int(leds[i][2] * 0.82)

        if audio:
            # Normal beat: single burst
            if audio.beat and not audio.drop:
                cx = random.uniform(1, COLS - 2)
                cy = random.uniform(ROWS * 0.3, ROWS * 0.7)
                count = int(self.COUNT * (1 + audio.buildup))
                self._spawn_burst(cx, cy, count, random.random())

            # DROP: FIREWORKS — 5-8 simultaneous rainbow explosions
            if audio.drop:
                num_fireworks = random.randint(5, 8)
                for _ in range(num_fireworks):
                    cx = random.uniform(0, COLS)
                    cy = random.uniform(ROWS * 0.15, ROWS * 0.75)
                    count = int(self.COUNT * 2.5)
                    self._spawn_burst(cx, cy, count, 0, 1.5, rainbow=True)

            # Breakdown: occasional slow sparkle (anticipation)
            if audio.breakdown and random.random() < 0.05:
                cx = random.uniform(0, COLS)
                cy = random.uniform(0, ROWS)
                self._spawn_burst(cx, cy, 3, random.random(), 0.3)

        alive = []
        for p in self._particles:
            p[0] += p[2] * dt; p[1] += p[3] * dt
            p[2] *= 0.99  # air drag
            p[3] += self.GRAVITY * 60 * dt
            p[5] -= dt * 0.4
            # Trail particles: spawn child sparks
            if len(p) > 6 and p[6] and p[5] > 0.5 and random.random() < 0.3:
                self._particles.append([p[0], p[1],
                    p[2]*0.1+random.uniform(-3,3), p[3]*0.1+random.uniform(-3,3),
                    p[4]+random.uniform(-0.05,0.05), 0.4, 0])
            if p[5] > 0 and -5 < p[1] < ROWS + 5:
                alive.append(p)
                px, py = int(round(p[0])) % COLS, int(round(p[1]))
                if 0 <= py < ROWS:
                    c = self.pal(p[4] % 1.0)
                    b = p[5]
                    add_led(px, py, c[0]*b, c[1]*b, c[2]*b)
        self._particles = alive[:500]  # cap for performance


class SoundPlasma(AnimBase):
    name = "Sound Plasma"
    category = "Sound"
    has_palette = True
    GAIN = 1.5; BASE_SPEED = 0.5
    PARAMS = [Param("Gain","GAIN",0.2,5.0,0.1,".1f"),
              Param("Base Speed","BASE_SPEED",0.1,3.0,0.1,".1f")]
    def __init__(self): self.t = 0.0; self._drop_boost = 0.0; self._init_defaults()
    def update(self, dt_ms, audio=None):
        vol = audio.volume * self.GAIN if audio else 0.3
        buildup = audio.buildup if audio else 0
        if audio and audio.drop:
            self._drop_boost = 3.0  # speed explosion on drop
        self._drop_boost *= 0.97
        speed = self.BASE_SPEED + vol * 2 + buildup * 2 + self._drop_boost
        self.t += dt_ms * 0.001 * speed
        t = self.t
        scale = 1.0 + vol + self._drop_boost * 0.5
        # During breakdown, go dark and slow
        if audio and audio.breakdown:
            scale *= 0.3; vol *= 0.2
        for x in range(COLS):
            ax = x / COLS * 6.2832
            for y in range(ROWS):
                v = (math.sin(ax*2 * scale + t * 1.5) +
                     math.sin(y * scale * 0.035 + t * 0.8) +
                     math.sin(ax*3 + y * 0.02 * scale + t * 1.2)) / 3.0
                bright = clampf((v + 1) * 0.5 * (0.4 + vol * 0.8))
                if self._drop_boost > 0.5:  # rainbow during drop
                    c = pal_color(0, ((v+1)*0.5 + t*0.2) % 1.0)
                else:
                    c = self.pal((v + 1) * 0.5)
                idx = xy(x, y)
                if idx >= 0:
                    leds[idx] = [int(c[0]*bright), int(c[1]*bright), int(c[2]*bright)]


class StrobeChaos(AnimBase):
    name = "Strobe Chaos"
    category = "Sound"
    has_palette = True
    INTENSITY = 0.8; SEGMENTS = 4
    PARAMS = [Param("Intensity","INTENSITY",0.1,1.0,0.05,".2f"),
              Param("Segments","SEGMENTS",1,10,1,"d")]
    def __init__(self):
        self._flash = 0.0; self._hue = 0.0; self._drop_strobe = 0.0
        self._init_defaults()
    def update(self, dt_ms, audio=None):
        clear_leds()
        dt = dt_ms * 0.001
        if audio and audio.drop:
            self._drop_strobe = 3.0
        self._drop_strobe = max(0, self._drop_strobe - dt)
        # DROP: full-matrix rapid rainbow strobe
        if self._drop_strobe > 0:
            frame = int(self._drop_strobe * 30)
            if frame % 2 == 0:
                hue = (frame * 0.07) % 1.0
                c = pal_color(0, hue)
                b = min(1.0, self._drop_strobe / 1.5)
                for i in range(TOTAL_LEDS):
                    leds[i] = [int(c[0]*b), int(c[1]*b), int(c[2]*b)]
            return
        # Breakdown: dark with occasional dim flicker
        if audio and audio.breakdown:
            if random.random() < 0.05:
                c = self.pal(random.random())
                for i in range(TOTAL_LEDS):
                    leds[i] = [int(c[0]*0.08), int(c[1]*0.08), int(c[2]*0.08)]
            return
        if audio and audio.beat:
            self._flash = self.INTENSITY * (1 + audio.buildup)
            self._hue = random.random()
        self._flash *= 0.88
        if self._flash > 0.05:
            seg_h = max(1, ROWS // int(self.SEGMENTS))
            for seg in range(int(self.SEGMENTS)):
                if random.random() < 0.6:
                    hue = (self._hue + seg * 0.15) % 1.0
                    c = self.pal(hue)
                    y0 = seg * seg_h; y1 = min(y0 + seg_h, ROWS)
                    b = self._flash * random.uniform(0.5, 1.0)
                    for x in range(COLS):
                        for y in range(y0, y1):
                            set_led(x, y, int(c[0]*b), int(c[1]*b), int(c[2]*b))


# ═════════════════════════════════════════════════════════════════
#  RENDERER
# ═════════════════════════════════════════════════════════════════

def draw_leds(screen):
    for col in range(COLS):
        for row in range(ROWS):
            i = xy(col, row)
            if i < 0: continue
            r, g, b = int(leds[i][0]) & 255, int(leds[i][1]) & 255, int(leds[i][2]) & 255
            cx = MARGIN + col * PITCH + PITCH // 2
            cy = MARGIN + row * PITCH + PITCH // 2
            bright = max(r, g, b)
            if bright > 20:
                m = bright / 255.0
                pygame.draw.circle(screen,
                    (int(r*0.2*m), int(g*0.2*m), int(b*0.2*m)),
                    (cx, cy), LED_R + 2)
            pygame.draw.circle(screen, (r, g, b), (cx, cy), LED_R)


# ═════════════════════════════════════════════════════════════════
#  MAIN
# ═════════════════════════════════════════════════════════════════

def main():
    pygame.init()
    screen = pygame.display.set_mode((WIN_W, WIN_H))
    pygame.display.set_caption("LED Matrix Simulator — 10x172")
    clock = pygame.time.Clock()

    try:
        font = pygame.font.SysFont("menlo", 13)
        font_sm = pygame.font.SysFont("menlo", 10)
    except Exception:
        font = pygame.font.SysFont(None, 15)
        font_sm = pygame.font.SysFont(None, 12)

    audio = AudioCapture()

    classics = [RainbowCycle(), FeldsteinEquation(), Feldstein2(), BrettsFavorite(), Fireplace()]
    ambients = [Plasma(), Aurora(), LavaLamp(), OceanWaves(), Starfield(),
                MatrixRain(), Breathing(), Fireflies(), Nebula(), Kaleidoscope(),
                FlowField(), Moire()]
    sounds = [Spectrum(), VUMeter(), BeatPulse(), BassReactiveFire(),
              SoundRipples(), Spectrogram(), SoundWorm(), ParticleBurst(),
              SoundPlasma(), StrobeChaos()]

    categories = [("Classic", classics), ("Ambient", ambients), ("Sound", sounds)]
    cat_idx = 0
    anim_idx = 0
    running = True

    held_key = None; held_time = 0; repeat_accum = 0

    def cur_anim():
        return categories[cat_idx][1][anim_idx]

    while running:
        dt = clock.tick(TARGET_FPS)
        audio.update()

        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_TAB:
                    cat_idx = (cat_idx + 1) % len(categories)
                    anim_idx = 0; clear_leds()
                elif ev.key == pygame.K_x:
                    anim_list = categories[cat_idx][1]
                    anim_idx = (anim_idx + 1) % len(anim_list); clear_leds()
                    a = cur_anim()
                    if hasattr(a, 't0'): a.t0 = None
                elif ev.key == pygame.K_p:
                    cur_anim().cycle_palette()
                elif ev.key == pygame.K_r:
                    cur_anim().reset_params()
                elif ev.key in (pygame.K_q, pygame.K_ESCAPE):
                    running = False
                elif ev.key == pygame.K_UP:
                    a = cur_anim()
                    if a.PARAMS: a.selected_param = (a.selected_param - 1) % len(a.PARAMS)
                elif ev.key == pygame.K_DOWN:
                    a = cur_anim()
                    if a.PARAMS: a.selected_param = (a.selected_param + 1) % len(a.PARAMS)
                elif ev.key in (pygame.K_LEFT, pygame.K_RIGHT):
                    d = -1 if ev.key == pygame.K_LEFT else 1
                    cur_anim().adjust_param(d)
                    held_key = ev.key; held_time = 0; repeat_accum = 0
            elif ev.type == pygame.KEYUP:
                if ev.key in (pygame.K_LEFT, pygame.K_RIGHT):
                    held_key = None

        if held_key is not None:
            held_time += dt
            if held_time > 300:
                repeat_accum += dt
                while repeat_accum >= 80:
                    repeat_accum -= 80
                    d = -1 if held_key == pygame.K_LEFT else 1
                    cur_anim().adjust_param(d)

        anim = cur_anim()
        anim.update(dt, audio)
        screen.fill(BG)
        draw_leds(screen)

        # ── Info panel ────────────────────────────────────────────
        ix = MARGIN + COLS * PITCH + MARGIN + 4
        iy = MARGIN

        screen.blit(font.render("LED Simulator 10x172", True, (180,180,200)), (ix, iy))
        iy += 22

        # Category tabs
        cat_name = categories[cat_idx][0]
        cat_color = {'Classic':(200,200,120),'Ambient':(120,200,180),'Sound':(200,120,150)}
        screen.blit(font.render(f"[{cat_name}]", True, cat_color.get(cat_name,(180,180,180))), (ix, iy))
        iy += 20

        screen.blit(font.render(f"> {anim.name}", True, (120,220,120)), (ix, iy))
        iy += 24

        # Palette
        if anim.has_palette:
            pname = PALETTES[anim.palette_idx % len(PALETTES)][0]
            screen.blit(font_sm.render(f"Palette: {pname}", True, (140,140,160)), (ix, iy))
            iy += 16

        # Params
        if anim.PARAMS:
            iy += 4
            for pi, p in enumerate(anim.PARAMS):
                sel = pi == anim.selected_param
                val = getattr(anim, p.attr)
                default_val = anim._defaults.get(p.attr, val)
                if p.attr == "PALETTE" and isinstance(anim, Feldstein2):
                    vs = _FELD_PALETTES[int(val) % len(_FELD_PALETTES)][0]
                elif p.fmt == "d":
                    vs = str(int(val))
                else:
                    vs = f"{val:{p.fmt}}"
                ind = ">" if sel else " "
                is_fuel = p.attr == "FUEL"
                txt = f"{ind} {p.label:<14s} {vs:>6s}"
                if sel:
                    color = (255, 220, 100)
                elif is_fuel:
                    # FUEL always stands out — color reflects fire intensity
                    fv = float(val)
                    color = (clamp(int(180 + fv*75)), clamp(int(100 + fv*100)), clamp(int(50 - fv*50)))
                elif val != default_val:
                    color = (180, 180, 200)
                else:
                    color = (100, 100, 120)
                render_font = font if is_fuel else font_sm
                screen.blit(render_font.render(txt, True, color), (ix, iy))
                iy += 18 if is_fuel else 14

        # Controls
        iy += 10
        hints = ["[TAB]  Category", "[X]    Next anim",
                 "[P]    Palette" if anim.has_palette else "",
                 "[R]    Reset", "[Q]    Quit"]
        for h in hints:
            if h:
                screen.blit(font_sm.render(h, True, (80,80,100)), (ix, iy))
                iy += 14

        # Status
        iy = WIN_H - MARGIN - 100
        screen.blit(font_sm.render(f"FPS: {clock.get_fps():.0f}", True, (70,70,90)), (ix, iy))
        iy += 14
        if audio._active:
            screen.blit(font_sm.render(f"Mic: active", True, (80,160,80)), (ix, iy))
        else:
            screen.blit(font_sm.render(f"Mic: simulated", True, (160,120,80)), (ix, iy))
        iy += 14
        # Beat tracking display
        bpm_color = (200,180,80) if audio.beat else (70,70,90)
        screen.blit(font_sm.render(f"BPM: {audio.bpm:.0f}", True, bpm_color), (ix, iy))
        iy += 14
        bar_str = "".join(["X" if i == audio.bar_beat else "." for i in range(4)])
        phrase_str = "".join(["X" if i == audio.phrase_beat else "." for i in range(16)])
        screen.blit(font_sm.render(f"Bar:    [{bar_str}]", True, (100,100,130)), (ix, iy))
        iy += 14
        screen.blit(font_sm.render(f"Phrase: [{phrase_str}]", True, (100,100,130)), (ix, iy))
        iy += 14
        state = audio._drop_state if hasattr(audio, '_drop_state') else 'N/A'
        state_colors = {'NORMAL':(80,80,100),'BUILDUP':(200,200,80),'BREAKDOWN':(200,80,80),'DROP':(80,255,80)}
        sc = state_colors.get(state, (80,80,100))
        buildup_bar = "=" * int(audio.buildup * 10) + "-" * (10 - int(audio.buildup * 10))
        screen.blit(font_sm.render(f"{state} [{buildup_bar}]", True, sc), (ix, iy))
        iy += 14
        agc_remaining = max(0, audio._agc_period - audio._agc_timer)
        screen.blit(font_sm.render(f"AGC: {agc_remaining:.0f}s", True, (70,70,90)), (ix, iy))
        iy += 14
        screen.blit(font_sm.render(f"LEDs: {TOTAL_LEDS}  {COLS}x{ROWS}", True, (70,70,90)), (ix, iy))

        pygame.display.flip()

    audio.close()
    pygame.quit()


if __name__ == "__main__":
    main()
