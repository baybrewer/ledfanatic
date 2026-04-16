# Animation Switcher Redesign

## Goal

Transform the Animation Switcher into a true "set and forget" feature: configurable interval 5–120s, checkbox selection of effects to include in rotation, with all sound-reactive effects labeled "SR " and grouped together (alphabetical within each group).

## Problem Statement

The existing Animation Switcher is a working effect that cycles through a playlist at a configurable interval with cross-fade. However:
- **No UI for selecting the playlist** — the `playlist` param exists but can only be set via raw API calls
- **Interval capped at 60s** — user wants up to 120s
- **Inconsistent SR labeling** — only the 5 new variants have "SR " prefix; the 10 existing sound.py effects (Spectrum, Bass Fire, Spectrogram, etc.) and the 5 audio_reactive built-ins don't
- **Persistence** — playlist selection currently lives in effect params which get wiped/overridden
- **Discoverability** — no visual grouping; sound-reactive effects are scattered across "sound" category but not clearly marked

## Design

### 1. Relabel All Sound-Reactive Effects with "SR " Prefix

Every sound-reactive effect gets its DISPLAY_NAME prefixed with "SR ":

| Current Name | File | New Name |
|--------------|------|----------|
| Spectrum | sound.py | SR Spectrum |
| VU Meter | sound.py | SR VU Meter |
| Beat Pulse | sound.py | SR Beat Pulse |
| Bass Fire | sound.py | SR Bass Fire |
| Sound Ripples | sound.py | SR Sound Ripples |
| Spectrogram | sound.py | SR Spectrogram |
| Sound Worm | sound.py | SR Sound Worm |
| Particle Burst | sound.py | SR Particle Burst |
| Sound Plasma | sound.py | SR Sound Plasma |
| Strobe Chaos | sound.py | SR Strobe Chaos |
| vu_pulse | audio_reactive.py | SR VU Pulse (explicit map — not just prefix) |
| band_colors | audio_reactive.py | SR Band Colors |
| beat_flash | audio_reactive.py | SR Beat Flash |
| energy_ring | audio_reactive.py | SR Energy Ring |
| spectral_glow | audio_reactive.py | SR Spectral Glow |
| SR Feldstein, SR Lava Lamp, SR Matrix Rain, SR Moire, SR Flow Field | sound_variants.py | Already prefixed — no change |

The catalog label ("Spectrum" vs "SR Spectrum") is what UI shows. The effect `name` (internal ID like `spectrum`) stays the same to preserve state.json compatibility.

**Scope limiter:** Only rename the user-facing label. Internal effect names, class names, file organization stay unchanged.

### 2. Extend Switcher Interval Range

Change max from 60s to 120s in the catalog param registration in `pi/app/main.py`:

```python
{'name': 'interval', 'label': 'Switch Time (s)', 'min': 5, 'max': 120, 'step': 1, 'default': 15, 'type': 'slider'},
```

### 3. Add Effect-Selection UI to Switcher Controls

When Animation Switcher is the active effect, below the interval/fade sliders, render a checkbox list of **every activatable effect** (excluding `animation_switcher` itself and diagnostic effects).

**Two sections, alphabetically sorted within each:**

```
🎵 Sound Reactive
  ☐ SR Bass Fire
  ☐ SR Beat Flash
  ☐ SR Beat Pulse
  ☐ SR Energy Ring
  ☐ SR Feldstein
  ☐ SR Flow Field
  ☐ SR Lava Lamp
  ☐ SR Matrix Rain
  ☐ SR Moire
  ☐ SR Particle Burst
  ☐ SR Sound Plasma
  ☐ SR Sound Ripples
  ☐ SR Sound Worm
  ☐ SR Spectral Glow
  ☐ SR Spectrogram
  ☐ SR Spectrum
  ☐ SR Strobe Chaos
  ☐ SR VU Meter
  ☐ SR VU Pulse
  ☐ SR Band Colors
  [Select All] [Clear]

🎨 Other
  ☐ Aurora Borealis
  ☐ Brett's Favorite
  ☐ Breathing
  ☐ Color Wipe
  ☐ Cylinder Rotate
  ☐ Feldstein Equation
  ☐ Feldstein OG
  ☐ Fire
  ☐ Fireflies
  ☐ Fireplace
  ☐ Flow Field
  ☐ Kaleidoscope
  ☐ Lava Lamp
  ☐ Matrix Rain
  ☐ Moire
  ☐ Nebula
  ☐ Noise Wash
  ☐ Ocean Waves
  ☐ Plasma (generative)
  ☐ Plasma (imported)
  ☐ Rainbow Cycle
  ☐ Rainbow Rotate
  ☐ Scanline
  ☐ Seam Pulse
  ☐ Sine Bands
  ☐ Solid Color
  ☐ Spark
  ☐ Starfield
  ☐ Twinkle
  ☐ Vertical Gradient
  [Select All] [Clear]
```

**Classification rule:** An effect is "Sound Reactive" if its group is `'sound'` OR `'audio'` in the catalog. Everything else goes to "Other" (excluding `animation_switcher` itself and `diagnostic`).

### 4. Checkbox Interaction

Each checkbox toggle debounces 300ms then POSTs to `/api/scenes/activate` with effect=`animation_switcher` and params including the updated `playlist` array **sorted alphabetically by effect label**. This makes rotation order match the display order users see.

When the playlist changes, the switcher effect is already running — `Renderer._set_scene` detects the scene is already active and calls `update_params()` which updates the playlist in place. The switcher handles runtime playlist changes via its `update_params` method.

**Empty playlist behavior:** Empty `playlist` means *truly empty* — switcher renders black. This matches existing semantics and tests.

**First-time default population:** On the *first* activation of `animation_switcher` (no saved `effect_params["animation_switcher"]["playlist"]` yet), the scenes route injects a default playlist of all non-diagnostic effects (sorted alphabetically by label) before passing to the renderer, and saves this explicit list to per-effect params. Thus the UI always reflects reality — if the saved playlist is empty, it's because the user explicitly cleared it.

The "None" button truly clears the selection (renders black frames); the user can rebuild from checkboxes or use "All" to re-populate.

### 5. Switcher Backend Changes

**In `pi/app/effects/switcher.py`:** Extend `update_params` to handle runtime `playlist` changes. When the playlist changes, reset position to index 0. Empty playlist remains empty (renders black — unchanged from current behavior).

```python
def update_params(self, params):
  if 'interval' in params:
    self._interval = params['interval']
  if 'fade_duration' in params:
    self._fade_duration = params['fade_duration']
  if 'shuffle' in params:
    self._shuffle = params['shuffle']
  if 'playlist' in params:
    new_playlist = list(params['playlist'] or [])
    if new_playlist != self._playlist:
      self._playlist = new_playlist
      self._current_idx = 0
      self._phase = 'playing'
      self._phase_timer = 0.0
      self._next_effect = None
      self._activate_current()
  if '_effect_registry' in params and params['_effect_registry']:
    self._effect_registry = params['_effect_registry']
  self.params.update(params)
```

**In `pi/app/api/routes/scenes.py`:** When activating `animation_switcher` for the first time (no saved playlist param), inject a default: all non-diagnostic, non-switcher effects, sorted alphabetically by catalog label.

### 6. Persistence (Automatic via Existing Mechanism)

The per-effect-params persistence from the previous session (state_manager.set_effect_params) already handles this. When the user checks/unchecks boxes, the `playlist` param in `animation_switcher`'s stored params updates and survives restarts.

No new persistence code needed.

### 7. Status Display (Minimal — "Now Playing")

Show the currently playing effect name and time until next switch below the effect checkboxes. Polls `/api/scenes/switcher/status` every 2 seconds while Animation Switcher is the active effect.

Format:
```
Now playing: SR Matrix Rain — switching in 8s
```

Keep it to one line; don't show "next" effect (it's cross-faded already when next=known).

### 8. Files Changed

| File | Change |
|------|--------|
| `pi/app/effects/imported/sound.py` | Add "SR " prefix to 10 DISPLAY_NAMEs |
| `pi/app/effects/audio_reactive.py` | No DISPLAY_NAME on classes (catalog uses label) — add SR labels in main.py |
| `pi/app/main.py` | Bump switcher interval max to 120; override labels for audio_reactive effects with "SR " prefix |
| `pi/app/effects/switcher.py` | Default playlist to all non-diagnostic if empty |
| `pi/app/ui/static/index.html` | Add switcher-controls container below effect-params |
| `pi/app/ui/static/js/app.js` | Render checkbox list when Animation Switcher active; poll status; wire checkboxes to POST activate |
| `pi/app/ui/static/css/app.css` | Styles for switcher section, section headers, checkbox rows, Select All / Clear buttons |

### 9. Removed or Deprecated

Nothing removed. All existing effects keep their internal names. Only user-visible labels change.

## Non-Goals

- No playlist order customization (alphabetical within SR and Other sections, cycled in order)
- No per-effect params override in playlist (each effect uses its own last-known params)
- No drag-and-drop reordering
- No save/load named playlists (the single selected set persists automatically)
- No preview/audition mode separate from activating the switcher
- No UI changes outside the Animation Switcher's controls panel

## Performance

Trivial impact. The checkbox rendering is a one-time list of ~35 effects. Status polling every 2s is negligible. The actual rotation/cross-fade code is unchanged.

## Testing

Tests to add:
1. Switcher with empty playlist activates without crashing (uses default all-non-diag)
2. Playlist param change at runtime (via `update_params`) rebuilds the rotation without reset
3. Status endpoint returns correct shape when switcher is active
4. Catalog labels reflect "SR " prefix on sound-reactive effects
