# Animation Switcher Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the Animation Switcher as a true "set and forget" feature — checkbox UI for selecting effects, 5–120s interval, all sound-reactive effects labeled "SR " and grouped together.

**Architecture:** Backend adds "SR " prefix to sound-reactive effect labels and supports runtime `playlist` updates. Frontend adds a checkbox list below the Animation Switcher's existing interval/fade sliders, split into "Sound Reactive" and "Other" sections (alphabetical within each). Checkbox changes POST updated playlist via the standard scene activate endpoint. Persistence piggybacks on the per-effect params store added in the previous session.

**Tech Stack:** Python (FastAPI, numpy), HTML/CSS/JS (vanilla).

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `pi/app/effects/imported/sound.py` | Modify | Add "SR " prefix to 10 DISPLAY_NAMEs |
| `pi/app/main.py` | Modify | Bump switcher interval max to 120 |
| `pi/app/effects/catalog.py` | Modify | Explicit SR-prefixed labels for audio_reactive effects |
| `pi/app/api/routes/scenes.py` | Modify | Inject default playlist on first animation_switcher activation |
| `pi/app/effects/switcher.py` | Modify | `update_params` handles runtime playlist changes |
| `pi/app/ui/static/index.html` | Modify | Add switcher-controls container |
| `pi/app/ui/static/js/app.js` | Modify | Render checkbox list; wire changes; status polling |
| `pi/app/ui/static/css/app.css` | Modify | Section headers, checkbox rows, Select All buttons |
| `pi/tests/test_switcher.py` | Create | Tests for playlist updates + empty-playlist default |

---

### Task 1: Relabel Sound Effects with "SR " Prefix

**Files:**
- Modify: `pi/app/effects/imported/sound.py` (10 DISPLAY_NAME fields)

- [ ] **Step 1: Update all 10 DISPLAY_NAMEs**

In `pi/app/effects/imported/sound.py`, find and replace each DISPLAY_NAME line:

```python
# Spectrum class
DISPLAY_NAME = "Spectrum"  →  DISPLAY_NAME = "SR Spectrum"

# VUMeter class
DISPLAY_NAME = "VU Meter"  →  DISPLAY_NAME = "SR VU Meter"

# BeatPulse class
DISPLAY_NAME = "Beat Pulse"  →  DISPLAY_NAME = "SR Beat Pulse"

# BassFire class
DISPLAY_NAME = "Bass Fire"  →  DISPLAY_NAME = "SR Bass Fire"

# SoundRipples class
DISPLAY_NAME = "Sound Ripples"  →  DISPLAY_NAME = "SR Sound Ripples"

# Spectrogram class
DISPLAY_NAME = "Spectrogram"  →  DISPLAY_NAME = "SR Spectrogram"

# SoundWorm class
DISPLAY_NAME = "Sound Worm"  →  DISPLAY_NAME = "SR Sound Worm"

# ParticleBurst class
DISPLAY_NAME = "Particle Burst"  →  DISPLAY_NAME = "SR Particle Burst"

# SoundPlasma class
DISPLAY_NAME = "Sound Plasma"  →  DISPLAY_NAME = "SR Sound Plasma"

# StrobeChaos class
DISPLAY_NAME = "Strobe Chaos"  →  DISPLAY_NAME = "SR Strobe Chaos"
```

- [ ] **Step 2: Run tests**

Run: `cd /Users/jim/ai/pillar-controller/pi && source .venv/bin/activate && PYTHONPATH=. pytest tests/ -x -q --ignore=tests/test_matrix_rain_perf.py --ignore=tests/test_migrations.py`

Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add pi/app/effects/imported/sound.py
git commit -m "feat: add SR prefix to sound-reactive effect display names"
```

---

### Task 2: Relabel audio_reactive Effects via Explicit Label Map + Bump Interval Max

**Files:**
- Modify: `pi/app/main.py` (switcher interval max)
- Modify: `pi/app/effects/catalog.py` (audio group labels in _build_catalog)

- [ ] **Step 1: Add explicit label map**

`_name_to_label('vu_pulse')` produces "Vu Pulse", which makes "SR Vu Pulse" awkward. Use an explicit map for acronym-heavy effects.

In `pi/app/effects/catalog.py`, near the top of the `EffectCatalogService` class (next to `_EFFECT_PARAMS`), add:

```python
  # Explicit display labels for audio_reactive effects — all SR-prefixed,
  # with proper acronym casing (VU not Vu).
  _AUDIO_LABELS = {
    'vu_pulse': 'SR VU Pulse',
    'band_colors': 'SR Band Colors',
    'beat_flash': 'SR Beat Flash',
    'energy_ring': 'SR Energy Ring',
    'spectral_glow': 'SR Spectral Glow',
  }
```

- [ ] **Step 2: Use the map in AUDIO_EFFECTS loop**

In `pi/app/effects/catalog.py`, find the AUDIO_EFFECTS loop inside `_build_catalog`:

```python
for name, cls in AUDIO_EFFECTS.items():
  params = self._EFFECT_PARAMS.get(name, ())
  self._catalog[name] = EffectMeta(
    name=name,
    label=_name_to_label(name),
    group='audio',
    description=_get_description(name, cls),
    params=params,
    audio_requires=('level', 'bass', 'mid', 'high', 'beat'),
  )
```

Replace the `label=_name_to_label(name)` line with:

```python
    label=self._AUDIO_LABELS.get(name, f"SR {_name_to_label(name)}"),
```

Result: SR VU Pulse, SR Band Colors, SR Beat Flash, SR Energy Ring, SR Spectral Glow.

- [ ] **Step 2: Bump switcher interval max**

In `pi/app/main.py`, find the Animation Switcher registration (around line 163–173):

```python
effect_catalog.register_imported('animation_switcher', EffectMeta(
  ...
  params=(
    {'name': 'interval', 'label': 'Switch Time (s)', 'min': 5, 'max': 60, 'step': 1, 'default': 15, 'type': 'slider'},
    {'name': 'fade_duration', 'label': 'Fade Duration (s)', 'min': 0.5, 'max': 5.0, 'step': 0.5, 'default': 2.0, 'type': 'slider'},
  ),
))
```

Change `'max': 60` to `'max': 120` on the interval slider.

- [ ] **Step 3: Run tests**

Run: `cd /Users/jim/ai/pillar-controller/pi && source .venv/bin/activate && PYTHONPATH=. pytest tests/ -x -q --ignore=tests/test_matrix_rain_perf.py --ignore=tests/test_migrations.py`

- [ ] **Step 4: Commit**

```bash
git add pi/app/effects/catalog.py pi/app/main.py
git commit -m "feat: SR prefix for audio_reactive effects; switcher interval max 120s"
```

---

### Task 3: Switcher Handles Runtime Playlist Updates

**Files:**
- Modify: `pi/app/effects/switcher.py`

Empty playlist stays empty (existing behavior — renders black). The "default all effects on first activation" logic lives in the scenes route (Task 4), not here.

- [ ] **Step 1: Update `update_params` to handle playlist**

In `pi/app/effects/switcher.py`, find the `update_params` method (around line 123). Replace the entire method with:

```python
  def _sanitize_playlist(self, raw):
    """Drop any entries not present in the current effect registry.

    Prevents stale/renamed/removed effect names from producing silent black
    frames or confusing status output.
    """
    if not raw:
      return []
    if not self._effect_registry:
      return list(raw)
    return [name for name in raw if name in self._effect_registry]

  def update_params(self, params):
    """Update switcher params. Playlist changes reset position to 0."""
    if 'interval' in params:
      self._interval = params['interval']
    if 'fade_duration' in params:
      self._fade_duration = params['fade_duration']
    if 'shuffle' in params:
      self._shuffle = params['shuffle']
    if '_effect_registry' in params and params['_effect_registry']:
      self._effect_registry = params['_effect_registry']
    if 'playlist' in params:
      new_playlist = self._sanitize_playlist(list(params['playlist'] or []))
      if new_playlist != self._playlist:
        self._playlist = new_playlist
        self._current_idx = 0
        self._phase = 'playing'
        self._phase_timer = 0.0
        self._current_effect = None
        self._next_effect = None
        self._activate_current()
    self.params.update(params)
```

Also update `__init__` to sanitize on first activation. Find:

```python
    self._playlist = self.params.get('playlist', [])
```

Replace with:

```python
    self._playlist = []  # assigned after _effect_registry is set
```

Then find (still in `__init__`, before `self._activate_current()`):

```python
    if self._shuffle and len(self._playlist) > 1:
      random.shuffle(self._playlist)

    self._activate_current()
```

Replace with:

```python
    self._playlist = self._sanitize_playlist(self.params.get('playlist', []))
    if self._shuffle and len(self._playlist) > 1:
      random.shuffle(self._playlist)

    self._activate_current()
```

- [ ] **Step 2: Write tests**

Create `pi/tests/test_switcher.py`:

```python
"""Tests for Animation Switcher runtime playlist updates."""

import numpy as np
import pytest

from app.effects.switcher import AnimationSwitcher


class FakeEffect:
  """Minimal effect stub for switcher tests."""
  def __init__(self, width=10, height=172, params=None):
    self.width = width
    self.height = height
    self.params = params or {}

  def render(self, t, state):
    return np.zeros((self.width, self.height, 3), dtype=np.uint8)


REGISTRY = {
  'twinkle': FakeEffect,
  'fire': FakeEffect,
  'plasma': FakeEffect,
  'animation_switcher': FakeEffect,
  'diag_sweep': FakeEffect,
  'diag_strip_identify': FakeEffect,
}


def _make_switcher(playlist=None):
  return AnimationSwitcher(
    width=10,
    height=172,
    params={
      'interval': 15,
      'fade_duration': 2.0,
      '_effect_registry': REGISTRY,
      'playlist': playlist if playlist is not None else [],
    },
  )


class TestRuntimePlaylistUpdate:
  def test_update_playlist_changes_rotation(self):
    s = _make_switcher(playlist=['twinkle', 'fire'])
    s.update_params({'playlist': ['plasma']})
    assert s._playlist == ['plasma']

  def test_update_playlist_resets_index(self):
    s = _make_switcher(playlist=['twinkle', 'fire', 'plasma'])
    s._current_idx = 2
    s.update_params({'playlist': ['fire', 'plasma']})
    assert s._current_idx == 0

  def test_update_interval_no_playlist_reset(self):
    s = _make_switcher(playlist=['twinkle', 'fire'])
    s._current_idx = 1
    s.update_params({'interval': 30})
    assert s._current_idx == 1  # unchanged
    assert s._interval == 30

  def test_update_empty_playlist_clears(self):
    s = _make_switcher(playlist=['twinkle'])
    s.update_params({'playlist': []})
    # Empty stays empty — no magic default
    assert s._playlist == []

  def test_update_same_playlist_does_not_reset_index(self):
    s = _make_switcher(playlist=['twinkle', 'fire', 'plasma'])
    s._current_idx = 2
    s.update_params({'playlist': ['twinkle', 'fire', 'plasma']})
    assert s._current_idx == 2  # no-op when list matches


class TestEmptyPlaylist:
  def test_empty_playlist_renders_black(self):
    s = _make_switcher(playlist=[])
    frame = s.render(0.0, None)
    assert frame.shape == (10, 172, 3)
    assert frame.sum() == 0  # all black


class TestSanitization:
  def test_init_strips_unknown_effect_names(self):
    s = _make_switcher(playlist=['twinkle', 'nonexistent', 'fire'])
    assert s._playlist == ['twinkle', 'fire']

  def test_update_strips_unknown_effect_names(self):
    s = _make_switcher(playlist=['twinkle'])
    s.update_params({'playlist': ['fire', 'does_not_exist', 'plasma']})
    assert s._playlist == ['fire', 'plasma']

  def test_all_unknown_becomes_empty(self):
    s = _make_switcher(playlist=['twinkle'])
    s.update_params({'playlist': ['unknown_a', 'unknown_b']})
    assert s._playlist == []


class TestStatus:
  def test_get_switcher_status_shape(self):
    s = _make_switcher(playlist=['twinkle', 'fire'])
    status = s.get_switcher_status()
    assert status['active'] is True
    assert 'current' in status
    assert 'playlist' in status
    assert 'interval' in status
    assert 'time_remaining' in status
```

- [ ] **Step 3: Run tests**

Run: `cd /Users/jim/ai/pillar-controller/pi && source .venv/bin/activate && PYTHONPATH=. pytest tests/test_switcher.py -v`

Expected: all 10 tests pass.

Then full suite:
`PYTHONPATH=. pytest tests/ -x -q --ignore=tests/test_matrix_rain_perf.py --ignore=tests/test_migrations.py`

The existing `test_render_empty_playlist` in `tests/test_imported_animations.py` continues to pass since empty = black stays.

- [ ] **Step 4: Commit**

```bash
git add pi/app/effects/switcher.py pi/tests/test_switcher.py
git commit -m "feat: switcher supports runtime playlist updates"
```

---

### Task 4: Scenes Route — Inject Default Playlist on First Activation

**Files:**
- Modify: `pi/app/api/routes/scenes.py`

When the user activates `animation_switcher` for the first time (no saved playlist), inject a default: all non-diagnostic, non-switcher effect names sorted alphabetically by catalog label. Save this explicit list via `set_effect_params` so the UI reflects reality on next load.

- [ ] **Step 1: Add default helper at the top of `create_router` body**

In `pi/app/api/routes/scenes.py`, inside `create_router(deps, require_auth, broadcast_state)`, add this helper function before the first `@router` decorator:

```python
    def _default_switcher_playlist():
      """All non-diagnostic, non-switcher effects sorted alphabetically by label."""
      catalog = (
        deps.effect_catalog.get_catalog()
        if hasattr(deps, 'effect_catalog') and deps.effect_catalog
        else _catalog.get_catalog()
      )
      entries = [
        (name, meta.label or name)
        for name, meta in catalog.items()
        if name != 'animation_switcher'
        and meta.group != 'diagnostic'
        and not name.startswith('diag_')
      ]
      entries.sort(key=lambda e: e[1].lower())
      return [name for name, _ in entries]
```

- [ ] **Step 2: Modify the activate handler**

Find the activate handler:

```python
    @router.post("/activate", dependencies=[Depends(require_auth)])
    async def activate_scene(req: SceneRequest):
        # If no params provided, restore this effect's last-known params
        if req.params is None:
            params_to_apply = deps.state_manager.get_effect_params(req.effect) or None
        else:
            params_to_apply = req.params
```

Add default-playlist injection AFTER the existing param resolution but BEFORE `renderer.activate_scene` is called. Insert:

```python
        # Animation Switcher: inject default playlist on first activation
        if req.effect == 'animation_switcher':
          if params_to_apply is None or 'playlist' not in (params_to_apply or {}):
            base = dict(params_to_apply or {})
            base['playlist'] = _default_switcher_playlist()
            params_to_apply = base
```

- [ ] **Step 3: Add route-level tests**

Append to `pi/tests/test_switcher.py`:

```python
# --- Scenes route default-injection tests ---

from fastapi.testclient import TestClient
from app.api.server import create_app


class StubRenderer:
  def __init__(self):
    self.current_effect = None
    self.effect_registry = {}
    self.activated = []

  def activate_scene(self, name, params=None, media_manager=None):
    self.activated.append((name, dict(params or {})))
    self.current_effect = type('E', (), {'params': dict(params or {})})()
    return True

  def apply_output_plan(self, plan):
    pass


class StubRenderState:
  current_scene = None
  target_fps = 60
  blackout = False
  gamma = 2.2
  actual_fps = 0.0
  def to_dict(self):
    return {}


class StubTransport:
  async def send_frame(self, b): return True
  async def send_blackout(self, v): pass


class StubStateManager:
  def __init__(self):
    self._effect_params = {}
    self.current_scene = None
    self.current_params = {}

  def get_effect_params(self, name):
    return dict(self._effect_params.get(name, {}))

  def set_effect_params(self, name, params):
    self._effect_params[name] = dict(params)

  def get_full_state(self): return {}
  def list_scenes(self): return {}


# Minimal integration-style test: call the route handler directly
import pytest as _pt


@_pt.mark.asyncio
async def test_first_activation_injects_default_playlist():
  """First activation of animation_switcher without params should populate a default playlist."""
  from app.api.routes.scenes import create_router
  from app.effects.catalog import EffectCatalogService, EffectMeta

  catalog = EffectCatalogService()
  catalog._catalog['twinkle'] = EffectMeta(name='twinkle', label='Twinkle', group='generative', description='')
  catalog._catalog['fire'] = EffectMeta(name='fire', label='Fire', group='generative', description='')
  catalog._catalog['animation_switcher'] = EffectMeta(
    name='animation_switcher', label='Animation Switcher', group='special', description=''
  )

  class Deps:
    renderer = StubRenderer()
    render_state = StubRenderState()
    state_manager = StubStateManager()
    effect_catalog = catalog
    transport = StubTransport()

  deps = Deps()
  async def broadcast(): pass
  def require_auth(): return None

  router = create_router(deps, require_auth, broadcast)
  # Find the activate handler
  for route in router.routes:
    if route.path == '/api/scenes/activate':
      handler = route.endpoint
      break

  from app.api.schemas import SceneRequest
  req = SceneRequest(effect='animation_switcher', params=None)
  result = await handler(req)

  assert result['status'] == 'ok'
  injected = result['params'].get('playlist', [])
  assert 'twinkle' in injected
  assert 'fire' in injected
  assert 'animation_switcher' not in injected
  # Persistence check
  saved = deps.state_manager.get_effect_params('animation_switcher')
  assert saved.get('playlist') == injected


@_pt.mark.asyncio
async def test_explicit_empty_playlist_saves_empty():
  """If caller explicitly sends playlist=[], it should NOT be re-injected with defaults."""
  from app.api.routes.scenes import create_router
  from app.effects.catalog import EffectCatalogService, EffectMeta

  catalog = EffectCatalogService()
  catalog._catalog['twinkle'] = EffectMeta(name='twinkle', label='Twinkle', group='generative', description='')
  catalog._catalog['animation_switcher'] = EffectMeta(
    name='animation_switcher', label='Animation Switcher', group='special', description=''
  )

  class Deps:
    renderer = StubRenderer()
    render_state = StubRenderState()
    state_manager = StubStateManager()
    effect_catalog = catalog
    transport = StubTransport()

  deps = Deps()
  async def broadcast(): pass
  def require_auth(): return None

  router = create_router(deps, require_auth, broadcast)
  for route in router.routes:
    if route.path == '/api/scenes/activate':
      handler = route.endpoint
      break

  from app.api.schemas import SceneRequest
  req = SceneRequest(effect='animation_switcher', params={'playlist': [], 'interval': 10})
  result = await handler(req)

  assert result['params']['playlist'] == []
  saved = deps.state_manager.get_effect_params('animation_switcher')
  assert saved.get('playlist') == []
```

If `pytest-asyncio` isn't installed, skip these tests with a `@pytest.mark.skipif(not _has_asyncio(), ...)` guard or convert to synchronous using `asyncio.run`. To keep it simple, use `asyncio.run`:

Replace the two test function headers:
```python
@_pt.mark.asyncio
async def test_first_activation_injects_default_playlist():
```
with:
```python
def test_first_activation_injects_default_playlist():
  import asyncio
  asyncio.run(_first_activation_injects_default_playlist())

async def _first_activation_injects_default_playlist():
```
(and similarly for the second test). Adjust indentation accordingly.

- [ ] **Step 4: Run tests**

Run: `cd /Users/jim/ai/pillar-controller/pi && source .venv/bin/activate && PYTHONPATH=. pytest tests/test_switcher.py -v`

Expected: all 12 tests pass (10 unit + 2 route).

Full suite: `PYTHONPATH=. pytest tests/ -x -q --ignore=tests/test_matrix_rain_perf.py --ignore=tests/test_migrations.py`

- [ ] **Step 5: Commit**

```bash
git add pi/app/api/routes/scenes.py pi/tests/test_switcher.py
git commit -m "feat: inject default playlist on first animation_switcher activation"
```

---

### Task 5: Frontend HTML — Switcher Controls Container

**Files:**
- Modify: `pi/app/ui/static/index.html`

- [ ] **Step 1: Find active-effect-controls section**

In `pi/app/ui/static/index.html`, find the `<div id="active-effect-controls">` block. The section currently contains:
- `#active-effect-name` heading
- `#effect-palette-wrap` (palette selector)
- `#effect-params` (slider list)

- [ ] **Step 2: Add switcher-controls block after #effect-params**

After the `<div id="effect-params">` line (and its closing tag), add:

```html
          <div id="switcher-controls" class="hidden">
            <div id="switcher-status" class="switcher-status"></div>
            <div class="switcher-section">
              <div class="switcher-section-header">
                <span class="switcher-section-title">Sound Reactive</span>
                <span class="switcher-section-actions">
                  <button type="button" class="switcher-select-all" data-section="sr">All</button>
                  <button type="button" class="switcher-clear" data-section="sr">None</button>
                </span>
              </div>
              <div id="switcher-sr-list" class="switcher-checklist"></div>
            </div>
            <div class="switcher-section">
              <div class="switcher-section-header">
                <span class="switcher-section-title">Other</span>
                <span class="switcher-section-actions">
                  <button type="button" class="switcher-select-all" data-section="other">All</button>
                  <button type="button" class="switcher-clear" data-section="other">None</button>
                </span>
              </div>
              <div id="switcher-other-list" class="switcher-checklist"></div>
            </div>
          </div>
```

- [ ] **Step 3: Commit (HTML only — CSS/JS come next)**

```bash
git add pi/app/ui/static/index.html
git commit -m "feat: switcher controls container in effect panel"
```

---

### Task 6: CSS Styles for Switcher

**Files:**
- Modify: `pi/app/ui/static/css/app.css`

- [ ] **Step 1: Add styles at end of file**

Append to `pi/app/ui/static/css/app.css`:

```css
/* Animation Switcher controls */
#switcher-controls {
  margin-top: 14px;
}

.switcher-status {
  font-size: 12px;
  color: var(--text-dim);
  padding: 6px 10px;
  background: var(--surface2);
  border-radius: 6px;
  margin-bottom: 10px;
  min-height: 20px;
}

.switcher-section {
  margin-bottom: 12px;
}

.switcher-section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 0;
  border-bottom: 1px solid var(--border);
  margin-bottom: 6px;
}

.switcher-section-title {
  font-weight: bold;
  font-size: 13px;
  color: var(--text);
}

.switcher-section-actions {
  display: flex;
  gap: 4px;
}

.switcher-section-actions button {
  padding: 3px 10px;
  font-size: 11px;
  border: 1px solid var(--border);
  border-radius: 4px;
  background: var(--surface2);
  color: var(--text-dim);
  cursor: pointer;
}

.switcher-section-actions button:hover {
  color: var(--text);
  border-color: var(--accent);
}

.switcher-checklist {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 2px 12px;
}

.switcher-check-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 6px;
  font-size: 13px;
  cursor: pointer;
  user-select: none;
  border-radius: 4px;
}

.switcher-check-row:hover {
  background: var(--surface2);
}

.switcher-check-row input[type="checkbox"] {
  margin: 0;
  cursor: pointer;
}

.switcher-check-row.checked {
  color: var(--accent);
}
```

- [ ] **Step 2: Commit**

```bash
git add pi/app/ui/static/css/app.css
git commit -m "style: switcher controls checklist styles"
```

---

### Task 7: Frontend JS — Build Checkboxes and Wire Up

**Files:**
- Modify: `pi/app/ui/static/js/app.js`

- [ ] **Step 1: Add switcher state at top of file**

Near the other module state declarations (search for `let spectrumTarget`), add:

```javascript
let switcherStatusInterval = null;
let switcherSelectedEffects = new Set();
```

- [ ] **Step 2: Add renderSwitcherControls() function**

Before the `activateEffect()` function (search `async function activateEffect`), add:

```javascript
function classifyEffectForSwitcher(name, meta) {
  // Exclude effects that can't be in a rotation
  if (name === 'animation_switcher') return null;
  if (name.startsWith('diag_')) return null;
  if (meta.group === 'diagnostic') return null;
  // SR section = group is 'sound' or 'audio'
  if (meta.group === 'sound' || meta.group === 'audio') return 'sr';
  return 'other';
}

// Deterministic, shared comparator used for both display and persistence ordering
const SWITCHER_COLLATOR = new Intl.Collator(undefined, { sensitivity: 'base' });

function compareByLabel(aName, bName) {
  const la = (effectsCatalog[aName]?.label || aName);
  const lb = (effectsCatalog[bName]?.label || bName);
  const cmp = SWITCHER_COLLATOR.compare(la, lb);
  if (cmp !== 0) return cmp;
  return aName.localeCompare(bName);  // stable tiebreaker on internal name
}

function renderSwitcherControls() {
  const wrap = document.getElementById('switcher-controls');
  if (!wrap || !effectsCatalog) return;

  // Partition and sort alphabetically by label (same comparator used for saving)
  const srEntries = [];
  const otherEntries = [];
  for (const [name, meta] of Object.entries(effectsCatalog)) {
    const section = classifyEffectForSwitcher(name, meta);
    if (section === 'sr') srEntries.push([name, meta]);
    else if (section === 'other') otherEntries.push([name, meta]);
  }
  const byName = (a, b) => compareByLabel(a[0], b[0]);
  srEntries.sort(byName);
  otherEntries.sort(byName);

  const build = (container, entries) => {
    container.innerHTML = '';
    for (const [name, meta] of entries) {
      const row = document.createElement('label');
      row.className = 'switcher-check-row';
      row.dataset.name = name;
      const checked = switcherSelectedEffects.has(name);
      if (checked) row.classList.add('checked');
      row.innerHTML = `
        <input type="checkbox" ${checked ? 'checked' : ''} data-name="${name}">
        <span>${meta.label || name}</span>
      `;
      container.appendChild(row);
    }
  };

  build(document.getElementById('switcher-sr-list'), srEntries);
  build(document.getElementById('switcher-other-list'), otherEntries);

  // Wire individual checkboxes
  wrap.querySelectorAll('.switcher-check-row input[type="checkbox"]').forEach(cb => {
    cb.addEventListener('change', () => {
      const name = cb.dataset.name;
      if (cb.checked) switcherSelectedEffects.add(name);
      else switcherSelectedEffects.delete(name);
      cb.closest('.switcher-check-row').classList.toggle('checked', cb.checked);
      scheduleSwitcherSave();
    });
  });

  // Section Select All / Clear
  wrap.querySelectorAll('.switcher-select-all').forEach(btn => {
    btn.addEventListener('click', () => {
      const section = btn.dataset.section;
      const entries = section === 'sr' ? srEntries : otherEntries;
      entries.forEach(([name]) => switcherSelectedEffects.add(name));
      renderSwitcherControls();
      scheduleSwitcherSave();
    });
  });
  wrap.querySelectorAll('.switcher-clear').forEach(btn => {
    btn.addEventListener('click', () => {
      const section = btn.dataset.section;
      const entries = section === 'sr' ? srEntries : otherEntries;
      entries.forEach(([name]) => switcherSelectedEffects.delete(name));
      renderSwitcherControls();
      scheduleSwitcherSave();
    });
  });
}

let switcherSaveDebounce = null;
function scheduleSwitcherSave() {
  clearTimeout(switcherSaveDebounce);
  switcherSaveDebounce = setTimeout(() => {
    if (activeEffectName !== 'animation_switcher') return;
    // Use the same deterministic comparator used for display order
    const playlist = Array.from(switcherSelectedEffects).sort(compareByLabel);
    const params = { ...currentEffectParams, playlist };
    currentEffectParams = params;
    api('POST', '/api/scenes/activate', { effect: 'animation_switcher', params });
  }, 300);
}

async function pollSwitcherStatus() {
  if (activeEffectName !== 'animation_switcher') return;
  const status = await api('GET', '/api/scenes/switcher/status');
  if (!status || !status.active) return;
  const el = document.getElementById('switcher-status');
  if (!el) return;
  const current = status.current;
  const currentLabel = (effectsCatalog && effectsCatalog[current])
    ? effectsCatalog[current].label : current;
  const remaining = Math.round(status.time_remaining || 0);
  // Single-line format matching spec: "Now playing: X — switching in Ns"
  el.textContent = `Now playing: ${currentLabel || '(none)'} — switching in ${remaining}s`;
}

function startSwitcherStatusPolling() {
  stopSwitcherStatusPolling();
  pollSwitcherStatus();
  switcherStatusInterval = setInterval(pollSwitcherStatus, 2000);
}

function stopSwitcherStatusPolling() {
  if (switcherStatusInterval) {
    clearInterval(switcherStatusInterval);
    switcherStatusInterval = null;
  }
}
```

- [ ] **Step 3: Show/hide switcher controls in showEffectControls + suppress speed slider for switcher**

Find the `showEffectControls(name, meta)` function. The current code auto-injects a Speed slider if `meta.params` lacks one — this is wrong for `animation_switcher` which has no `speed` concept.

Locate this block near the top of `showEffectControls`:

```javascript
  // Build params list, ensuring speed is always present
  const params = meta.params ? [...meta.params] : [];
  const hasSpeed = params.some(p => p.name === 'speed');
  if (!hasSpeed) {
    params.unshift({
      name: 'speed',
      label: 'Speed',
      ...
```

Replace with:

```javascript
  // Build params list, ensuring speed is always present (except for special effects)
  const params = meta.params ? [...meta.params] : [];
  const hasSpeed = params.some(p => p.name === 'speed');
  if (!hasSpeed && name !== 'animation_switcher') {
    params.unshift({
      name: 'speed',
      label: 'Speed',
      ...
```

Then after `paramsDiv.innerHTML = '';` (or at the end of the function, wherever is cleanest), add:

```javascript
  // Switcher-specific UI
  const switcherWrap = document.getElementById('switcher-controls');
  if (switcherWrap) {
    if (name === 'animation_switcher') {
      // Initialize selected set from current params
      const saved = currentEffectParams.playlist;
      switcherSelectedEffects = new Set(Array.isArray(saved) ? saved : []);
      switcherWrap.classList.remove('hidden');
      renderSwitcherControls();
      startSwitcherStatusPolling();
    } else {
      switcherWrap.classList.add('hidden');
      stopSwitcherStatusPolling();
    }
  }
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/jim/ai/pillar-controller/pi && source .venv/bin/activate && PYTHONPATH=. pytest tests/ -x -q --ignore=tests/test_matrix_rain_perf.py --ignore=tests/test_migrations.py`

- [ ] **Step 5: Commit**

```bash
git add pi/app/ui/static/js/app.js
git commit -m "feat: switcher checkbox UI with SR/Other sections and status polling"
```

---

### Task 8: Deploy and Verify

- [ ] **Step 1: Deploy**

```bash
cd /Users/jim/ai/pillar-controller && bash pi/scripts/deploy.sh ledfanatic.local
```

- [ ] **Step 2: Verify SR labels in catalog**

```bash
ssh jim@ledfanatic.local "sleep 3 && curl -s http://localhost:80/api/effects/catalog | python3 -c \"
import sys, json
d = json.load(sys.stdin)
sr_labels = sorted(e['label'] for e in d['effects'].values() if e['label'].startswith('SR '))
print('SR labels:', sr_labels)
print('count:', len(sr_labels))
\""
```

Expected: ~20 effects prefixed with "SR ".

- [ ] **Step 3: Verify switcher interval max is 120**

```bash
ssh jim@ledfanatic.local "curl -s http://localhost:80/api/effects/animation_switcher | python3 -c \"
import sys, json
d = json.load(sys.stdin)
for p in d.get('params', []):
  if p['name'] == 'interval':
    print(f'interval max: {p[\"max\"]}')
\""
```

Expected: `interval max: 120`.

- [ ] **Step 4: Activate switcher with custom playlist**

```bash
ssh jim@ledfanatic.local "curl -s -X POST http://localhost:80/api/scenes/activate -H 'Content-Type: application/json' -d '{\"effect\":\"animation_switcher\",\"params\":{\"interval\":10,\"playlist\":[\"twinkle\",\"fire\",\"plasma\"]}}' | head -c 200"
echo
sleep 2
ssh jim@ledfanatic.local "curl -s http://localhost:80/api/scenes/switcher/status | python3 -m json.tool"
```

Expected: status shows active=true, current in the playlist, playlist=[twinkle,fire,plasma], interval=10.

- [ ] **Step 5: Verify playlist update at runtime**

```bash
ssh jim@ledfanatic.local "curl -s -X POST http://localhost:80/api/scenes/activate -H 'Content-Type: application/json' -d '{\"effect\":\"animation_switcher\",\"params\":{\"playlist\":[\"spark\"]}}' > /dev/null; sleep 1; curl -s http://localhost:80/api/scenes/switcher/status | python3 -c \"import sys,json; d=json.load(sys.stdin); print('playlist:', d['playlist'])\""
```

Expected: `playlist: ['spark']`.

- [ ] **Step 6: Open UI and verify the checkbox UI**

Open the UI in a browser. Click Effects → Animation Switcher. Verify:
- Sliders appear (interval + fade_duration)
- Two sections: Sound Reactive (with SR-prefixed effects, sorted A-Z) and Other (regular effects, sorted A-Z)
- Checking/unchecking updates the rotation; status line updates every 2s
- "All" / "None" buttons per section work

- [ ] **Step 7: Full regression test**

Activate a few non-switcher effects and verify they still work:

```bash
for e in twinkle fire spectrum sr_matrix_rain; do
  ssh jim@ledfanatic.local "curl -s -X POST http://localhost:80/api/scenes/activate -H 'Content-Type: application/json' -d '{\"effect\":\"$e\"}' > /dev/null; sleep 1; sudo journalctl -u pillar --no-pager --since '3 seconds ago' | grep -ciE 'error|traceback' || true"
  echo "$e OK"
done
```

Expected: all zero errors.
