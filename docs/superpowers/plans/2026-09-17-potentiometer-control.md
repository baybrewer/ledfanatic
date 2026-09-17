# Potentiometer Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Three physical pots on the Teensy control brightness, effect category, and effect selection, with last-writer-wins vs the app, an LED spine-text category overlay, per-knob software disable, and server-side favorites.

**Architecture:** Teensy reads 3 ADC pins and pushes raw values upstream in a new `PKT_POTS` (0x31) packet at 20 Hz. The Pi drains incoming packets in a background task; a pure `PotController` (in `pi/app/core/pots.py`) turns raw values into events (`brightness` / `overlay` / `activate`) using deadband + hysteresis + settle-debounce; an async dispatcher in `main.py` applies them via the existing brightness engine and `renderer.activate_scene()`. The renderer composites a book-spine category overlay onto the left panel. Favorites and per-knob enable flags live in `state.json` (schema v3) with new API routes and UI controls.

**Tech Stack:** Python 3 / FastAPI / NumPy / PIL (Pi), Arduino C++ / PlatformIO (Teensy 4.1), vanilla JS UI.

**Spec:** `docs/superpowers/specs/2026-09-17-potentiometer-control-design.md`

## Global Constraints

- 2-space indentation in Python (project style); the UI JS/HTML follows existing file style.
- Firmware version bumps to `1.2.0`. Protocol version stays `1` (additive packet type).
- `PKT_POTS = 0x31`, payload exactly 6 bytes: 3 × uint16 LE, ADC range 0–1023. Pot order: brightness, menu, pattern.
- Pot pins: A14 (pin 38) = brightness, A15 (pin 39) = menu, A16 (pin 40) = pattern. OctoWS2811 uses pins [2, 14, 7, 8, 6, 20, 21, 5] — do not touch those.
- STATS payload stays exactly 28 bytes. No changes to PING/FRAME/CONFIG handling.
- Deadband 2% of travel; selector hysteresis guard 15% of zone width; effect activation settles 300 ms after the knob stops; overlay lingers 1.5 s then fades 300 ms.
- All effect activation goes through `renderer.activate_scene()`.
- Never hardcode grid geometry — overlay region defaults to the left half (`width // 2`) and is overridable via `system.yaml` `pots.overlay`.
- Tests run with: `cd pi && source .venv/bin/activate && PYTHONPATH=. pytest tests/ -v`
- Commit after every task. Deploy to the Pi at the end (`bash pi/scripts/deploy.sh ledfanatic.local`) — never claim done without deploying. Do NOT edit config files on the Pi itself.

---

### Task 1: Protocol — PKT_POTS on the Pi side

**Files:**
- Modify: `pi/app/models/protocol.py`
- Test: `pi/tests/test_protocol.py`

**Interfaces:**
- Produces: `PacketType.POTS` (0x31), `POTS_PAYLOAD_SIZE = 6`, `parse_pots_payload(payload: bytes) -> Optional[tuple[int, int, int]]`

- [ ] **Step 1: Write the failing tests** — append to `pi/tests/test_protocol.py` (import `parse_pots_payload` and `POTS_PAYLOAD_SIZE` alongside the existing imports at the top of the file):

```python
class TestPotsPayload:
  def test_parse_valid(self):
    payload = struct.pack('<HHH', 0, 512, 1023)
    assert parse_pots_payload(payload) == (0, 512, 1023)

  def test_too_short_rejected(self):
    assert parse_pots_payload(b'\x00' * 5) is None

  def test_extra_bytes_ok(self):
    payload = struct.pack('<HHH', 1, 2, 3) + b'\xAA\xBB'
    assert parse_pots_payload(payload) == (1, 2, 3)

  def test_packet_round_trip(self):
    pkt = build_packet(PacketType.POTS, struct.pack('<HHH', 10, 20, 30))
    framed = frame_packet(pkt)
    assert framed.endswith(b'\x00')
    decoded = cobs_decode(framed[:-1])
    header, payload = verify_packet(decoded)
    assert header.packet_type == PacketType.POTS
    assert len(payload) == POTS_PAYLOAD_SIZE
    assert parse_pots_payload(payload) == (10, 20, 30)
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=. pytest tests/test_protocol.py::TestPotsPayload -v`
Expected: FAIL with `ImportError` (cannot import `parse_pots_payload`)

- [ ] **Step 3: Implement** in `pi/app/models/protocol.py`. Add to `PacketType`:

```python
  POTS = 0x31
```

Add near the other canonical payload sizes:

```python
POTS_PAYLOAD_SIZE = 6  # 3 x uint16 LE (brightness, menu, pattern), ADC range 0-1023
POTS_STRUCT_FMT = '<HHH'
```

Add after `parse_stats_payload`:

```python
def parse_pots_payload(payload: bytes) -> Optional[tuple[int, int, int]]:
  """Parse POTS payload from Teensy: (brightness, menu, pattern) raw ADC values."""
  if len(payload) < POTS_PAYLOAD_SIZE:
    return None
  return struct.unpack(POTS_STRUCT_FMT, payload[:POTS_PAYLOAD_SIZE])
```

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=. pytest tests/test_protocol.py -v`
Expected: all PASS (new + existing)

- [ ] **Step 5: Commit**

```bash
git add pi/app/models/protocol.py pi/tests/test_protocol.py
git commit -m "feat: add PKT_POTS (0x31) packet type and parser"
```

---

### Task 2: Spine-text rendering helper (shared with scrolltext)

**Files:**
- Create: `pi/app/effects/textrender.py`
- Modify: `pi/app/effects/scrolltext.py` (vertical branch of `_render_text` delegates to helper)
- Test: `pi/tests/test_textrender.py`

**Interfaces:**
- Produces: `render_spine_text(text: str, panel_width: int, color: tuple[int, int, int] = (255, 255, 255)) -> np.ndarray` — shape `(panel_width, text_len, 3)` uint8, glyphs rotated 90° CW (letter tops face +x / right), reading top-to-bottom along y.
- Produces: `composite_spine_overlay(frame: np.ndarray, text_img: np.ndarray, x0: int, y_offset: int, alpha: float) -> np.ndarray` — pure compositing used by the renderer overlay (Task 6) and its tests.

- [ ] **Step 1: Write the failing tests** — create `pi/tests/test_textrender.py`:

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=. pytest tests/test_textrender.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.effects.textrender'`

- [ ] **Step 3: Implement** — create `pi/app/effects/textrender.py`:

```python
"""
Shared text rasterization for LED display.

Spine text: glyphs rotated 90 deg clockwise (letter tops face right, like a
US book spine), reading top-to-bottom. Used by the scrolling-text effect and
the pot-menu category overlay.
"""

import numpy as np
from PIL import Image, ImageDraw, ImageFont

_FONT_PATHS = [
  "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
  "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def get_font(size: int):
  for path in _FONT_PATHS:
    try:
      return ImageFont.truetype(path, size)
    except (OSError, IOError):
      continue
  return ImageFont.load_default()


def render_spine_text(text: str, panel_width: int,
                      color: tuple = (255, 255, 255)) -> np.ndarray:
  """Render text rotated 90 deg CW, sized to panel_width.

  Returns uint8 array of shape (panel_width, text_len, 3). Letter tops face
  +x (right side); text reads top-to-bottom along the y axis.
  """
  panel_width = max(1, panel_width)
  font = get_font(panel_width)

  dummy = Image.new('RGB', (1, 1))
  draw = ImageDraw.Draw(dummy)
  bbox = draw.textbbox((0, 0), text, font=font)
  text_w = bbox[2] - bbox[0] + 4
  text_h = bbox[3] - bbox[1] + 4

  img = Image.new('RGB', (text_w, text_h), (0, 0, 0))
  draw = ImageDraw.Draw(img)
  draw.text((2, 2 - bbox[1]), text, font=font, fill=tuple(color))

  # PIL rotate(-90) = 90 deg clockwise: glyph tops end up on the right,
  # reading direction becomes top-to-bottom.
  img = img.rotate(-90, expand=True, resample=Image.BICUBIC)

  new_w = panel_width
  new_h = max(1, int(img.height * new_w / max(img.width, 1)))
  img = img.resize((new_w, new_h), Image.LANCZOS)

  # PIL (rows, cols, 3) -> project convention (width, height, 3)
  return np.array(img).transpose(1, 0, 2)


def composite_spine_overlay(frame: np.ndarray, text_img: np.ndarray,
                            x0: int, y_offset: int, alpha: float) -> np.ndarray:
  """Composite spine text onto frame with a dimmed backing box.

  frame: (width, height, 3) uint8 — modified copy returned.
  text_img: output of render_spine_text.
  x0: left column of the overlay region (region width = text_img.shape[0]).
  y_offset: first text row to show (for vertical scrolling of long names).
  alpha: 0.0-1.0 overall overlay strength (used for fade-out).
  """
  alpha = max(0.0, min(1.0, alpha))
  if alpha == 0.0:
    return frame
  out = frame.astype(np.float32).copy()
  fw, fh = out.shape[0], out.shape[1]
  pw, tlen = text_img.shape[0], text_img.shape[1]
  x1 = min(fw, x0 + pw)
  if x1 <= x0:
    return frame

  # Dim the backing region toward black for legibility
  out[x0:x1, :] *= (1.0 - 0.75 * alpha)

  # Draw visible slice of the text
  y_offset = int(y_offset)
  visible = min(fh, tlen - y_offset)
  if visible > 0 and y_offset >= 0:
    text_slice = text_img[:x1 - x0, y_offset:y_offset + visible].astype(np.float32)
    out[x0:x1, :visible] += text_slice * alpha

  return np.clip(out, 0, 255).astype(np.uint8)
```

- [ ] **Step 4: Refactor `pi/app/effects/scrolltext.py`** to use the shared font helper (DRY on font paths). In `scrolltext.py`, add `from .textrender import get_font` to the imports and replace the body of `_get_font` with:

```python
    def _get_font(self, size):
        return get_font(size)
```

Do NOT restructure the rest of `_render_text` — its vertical branch has its own scroll-oriented sizing; the shared piece is font loading and the spine rasterizer for new callers.

- [ ] **Step 5: Run tests**

Run: `PYTHONPATH=. pytest tests/test_textrender.py tests/test_imported_effects.py -v && python -c "from app.effects.scrolltext import ScrollingText; ScrollingText(10, 100)._render_text(); print('scrolltext ok')"`
Expected: all PASS, `scrolltext ok`

- [ ] **Step 6: Commit**

```bash
git add pi/app/effects/textrender.py pi/app/effects/scrolltext.py pi/tests/test_textrender.py
git commit -m "feat: shared spine-text renderer + overlay compositor"
```

---

### Task 3: Catalog display categories (server-side SSOT for pot menu)

**Files:**
- Modify: `pi/app/effects/catalog.py`
- Test: `pi/tests/test_effect_catalog.py`

**Interfaces:**
- Produces: `DISPLAY_CATEGORY_MAP`, `DISPLAY_CATEGORY_ORDER`, `display_category(group: str) -> str`, and `EffectCatalogService.get_display_categories() -> list[tuple[str, list[str]]]` (ordered `(category_label, [effect_name, ...])`, effect names sorted by label, `diag_*` excluded).
- Consumes: existing `EffectCatalogService.get_catalog()`.

- [ ] **Step 1: Write the failing test** — append to `pi/tests/test_effect_catalog.py` (match its existing import/service-construction style; it constructs `EffectCatalogService()`):

```python
class TestDisplayCategories:
  def test_ordered_nonempty_categories(self):
    svc = EffectCatalogService()
    cats = svc.get_display_categories()
    labels = [label for label, _ in cats]
    assert 'Built-in' in labels
    assert 'Sound Reactive' in labels
    # Every category non-empty, no diagnostics anywhere
    for label, names in cats:
      assert names, f"category {label} is empty"
      assert not any(n.startswith('diag_') for n in names)

  def test_names_sorted_by_label(self):
    svc = EffectCatalogService()
    catalog = svc.get_catalog()
    for label, names in svc.get_display_categories():
      labels = [catalog[n].label for n in names]
      assert labels == sorted(labels)
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=. pytest tests/test_effect_catalog.py -v -k Display`
Expected: FAIL with `AttributeError: ... 'get_display_categories'`

- [ ] **Step 3: Implement** in `pi/app/effects/catalog.py`. Add module-level constants (mirror of the UI's `CATEGORY_MAP` in `app.js` — this becomes the canonical copy):

```python
# Canonical group -> display category mapping (UI mirrors this; keep in sync
# with CATEGORY_MAP in pi/app/ui/static/js/app.js)
DISPLAY_CATEGORY_MAP = {
  'imported_sound': 'Sound Reactive',
  'sound': 'Sound Reactive',
  'audio': 'Sound Reactive',
  'classic': 'Classic',
  'imported_classic': 'Classic',
  'ambient': 'Ambient',
  'imported_ambient': 'Ambient',
  'generative': 'Built-in',
  'special': 'Special',
  'simulation': 'Simulation',
  'game': 'Game',
}

DISPLAY_CATEGORY_ORDER = [
  'Ambient', 'Sound Reactive', 'Simulation', 'Built-in',
  'Classic', 'Game', 'Special', 'Other',
]


def display_category(group: str) -> str:
  return DISPLAY_CATEGORY_MAP.get(group, 'Other')
```

Add a method to `EffectCatalogService`:

```python
  def get_display_categories(self) -> list:
    """Ordered (category_label, [effect names sorted by label]) for pot menus.

    Excludes diagnostic effects and empty categories.
    """
    catalog = self.get_catalog()
    buckets: dict = {}
    for name, meta in catalog.items():
      if name.startswith('diag_'):
        continue
      buckets.setdefault(display_category(meta.group), []).append((meta.label, name))
    result = []
    for cat in DISPLAY_CATEGORY_ORDER:
      if cat in buckets:
        result.append((cat, [n for _, n in sorted(buckets[cat])]))
    return result
```

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=. pytest tests/test_effect_catalog.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add pi/app/effects/catalog.py pi/tests/test_effect_catalog.py
git commit -m "feat: server-side display-category mapping for pot menu"
```

---

### Task 4: State schema v3 — favorites + pots_enabled

**Files:**
- Modify: `pi/app/core/state.py`
- Test: `pi/tests/test_migrations.py`

**Interfaces:**
- Produces: `StateManager.favorites` (property, `list[str]`, setter marks dirty), `StateManager.pots_enabled` (property, `dict` with keys `brightness`/`menu`/`pattern` → bool, setter marks dirty), `STATE_SCHEMA_VERSION = 3`, v2→v3 migration.

- [ ] **Step 1: Write the failing tests** — append to `pi/tests/test_migrations.py` (follow its existing fixture style for constructing a `StateManager` with a tmp dir):

```python
class TestV3Migration:
  def test_v2_to_v3_adds_favorites_and_pots(self, tmp_path):
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({
      'schema_version': 2, 'current_scene': 'fire', 'current_layers': [],
      'render_mode': 'single',
    }))
    mgr = StateManager(tmp_path)
    mgr.load()
    assert mgr._state['schema_version'] == 3
    assert mgr.favorites == []
    assert mgr.pots_enabled == {'brightness': True, 'menu': True, 'pattern': True}

  def test_favorites_persist(self, tmp_path):
    mgr = StateManager(tmp_path)
    mgr.load()
    mgr.favorites = ['fire', 'rainbow_rotate']
    mgr.force_save()
    mgr2 = StateManager(tmp_path)
    mgr2.load()
    assert mgr2.favorites == ['fire', 'rainbow_rotate']

  def test_pots_enabled_persist(self, tmp_path):
    mgr = StateManager(tmp_path)
    mgr.load()
    mgr.pots_enabled = {'brightness': True, 'menu': True, 'pattern': False}
    mgr.force_save()
    mgr2 = StateManager(tmp_path)
    mgr2.load()
    assert mgr2.pots_enabled['pattern'] is False
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=. pytest tests/test_migrations.py -v -k V3`
Expected: FAIL (no `favorites` attribute / schema stays 2)

- [ ] **Step 3: Implement** in `pi/app/core/state.py`:

Change `STATE_SCHEMA_VERSION = 2` → `STATE_SCHEMA_VERSION = 3`.

Add to the `self._state` defaults dict:

```python
      'favorites': [],
      'pots_enabled': {'brightness': True, 'menu': True, 'pattern': True},
```

Append to `_migrate`:

```python
    if version < 3:
      # v2 -> v3: favorites list + per-knob enable flags
      data['favorites'] = []
      data['pots_enabled'] = {'brightness': True, 'menu': True, 'pattern': True}
      data['schema_version'] = 3
      logger.info("Migrated state.json from v2 to v3")
```

Add properties (near `current_layers`):

```python
  @property
  def favorites(self) -> list[str]:
    return self._state.get('favorites', [])

  @favorites.setter
  def favorites(self, value: list[str]):
    self._state['favorites'] = list(value)
    self.mark_dirty()

  @property
  def pots_enabled(self) -> dict:
    return dict(self._state.get(
      'pots_enabled', {'brightness': True, 'menu': True, 'pattern': True}))

  @pots_enabled.setter
  def pots_enabled(self, value: dict):
    current = self.pots_enabled
    current.update({k: bool(v) for k, v in value.items()
                    if k in ('brightness', 'menu', 'pattern')})
    self._state['pots_enabled'] = current
    self.mark_dirty()
```

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=. pytest tests/test_migrations.py tests/test_state.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add pi/app/core/state.py pi/tests/test_migrations.py
git commit -m "feat: state schema v3 - favorites + pots_enabled flags"
```

---

### Task 5: PotController — pure mapping logic

**Files:**
- Create: `pi/app/core/pots.py`
- Test: `pi/tests/test_pots.py`

**Interfaces:**
- Produces: `normalize_raw(raw: int) -> float`; `select_index(value: float, count: int, current: Optional[int], guard: float = 0.15) -> Optional[int]`; `PotController(menu_provider, enabled_provider, deadband=0.02, settle_s=0.3)` with `handle_raw(raw: tuple[int, int, int], now: float) -> list[tuple[str, object]]` returning events `('brightness', float)`, `('overlay', str)`, `('activate', str)`; attribute `last_values: tuple[float, float, float]`.
- Consumes: `menu_provider() -> list[tuple[str, list[str]]]` (Task 3 shape, favorites prepended by caller); `enabled_provider() -> dict` (Task 4 shape).
- Note: `pots_poll_loop` / transport glue is Task 7, NOT here — this task is pure logic with injected time.

- [ ] **Step 1: Write the failing tests** — create `pi/tests/test_pots.py`:

```python
"""Tests for pot mapping logic: deadband, hysteresis, settle-debounce, disable."""

from app.core.pots import PotController, normalize_raw, select_index


MENU = [
  ('Favorites', ['fav_a', 'fav_b']),
  ('Ambient', ['amb_a', 'amb_b', 'amb_c']),
  ('Game', ['game_a']),
]

ALL_ON = {'brightness': True, 'menu': True, 'pattern': True}


def make_controller(enabled=None, menu=None):
  flags = dict(enabled or ALL_ON)
  return PotController(
    menu_provider=lambda: list(menu if menu is not None else MENU),
    enabled_provider=lambda: dict(flags),
  ), flags


def raw(b=0.0, m=0.0, p=0.0):
  return (int(b * 1023), int(m * 1023), int(p * 1023))


class TestHelpers:
  def test_normalize(self):
    assert normalize_raw(0) == 0.0
    assert normalize_raw(1023) == 1.0
    assert abs(normalize_raw(512) - 0.5005) < 0.001
    assert normalize_raw(2000) == 1.0  # clamped

  def test_select_index_basic(self):
    assert select_index(0.0, 4, None) == 0
    assert select_index(0.99, 4, None) == 3
    assert select_index(0.5, 0, None) is None

  def test_select_index_hysteresis_holds_at_boundary(self):
    # value just past the 0/1 boundary stays at current=0 within guard
    assert select_index(0.26, 4, current=0) == 0
    # well past the guard, switches
    assert select_index(0.35, 4, current=0) == 1


class TestBaseline:
  def test_first_packet_is_silent_baseline(self):
    ctl, _ = make_controller()
    events = ctl.handle_raw(raw(b=0.9, m=0.9, p=0.9), now=0.0)
    assert events == []  # boot position must not take over

  def test_static_pots_stay_silent(self):
    ctl, _ = make_controller()
    ctl.handle_raw(raw(b=0.5), now=0.0)
    for i in range(20):
      assert ctl.handle_raw(raw(b=0.5), now=0.05 * (i + 1)) == []


class TestBrightness:
  def test_movement_emits_brightness(self):
    ctl, _ = make_controller()
    ctl.handle_raw(raw(b=0.5), now=0.0)
    events = ctl.handle_raw(raw(b=0.6), now=0.05)
    assert ('brightness' in [k for k, _ in events])
    val = dict(events)['brightness']
    assert abs(val - 0.6) < 0.01

  def test_tiny_jitter_ignored(self):
    ctl, _ = make_controller()
    ctl.handle_raw(raw(b=0.5), now=0.0)
    events = ctl.handle_raw(raw(b=0.505), now=0.05)  # within 2% deadband
    assert events == []


class TestMenuAndPattern:
  def test_menu_movement_emits_overlay(self):
    ctl, _ = make_controller()
    ctl.handle_raw(raw(m=0.1), now=0.0)
    events = ctl.handle_raw(raw(m=0.5), now=0.05)  # into 'Ambient' zone
    assert ('overlay', 'Ambient') in events
    assert not any(k == 'activate' for k, _ in events)  # not settled yet

  def test_activation_after_settle(self):
    ctl, _ = make_controller()
    ctl.handle_raw(raw(m=0.1, p=0.1), now=0.0)
    ctl.handle_raw(raw(m=0.5, p=0.1), now=0.05)   # menu -> Ambient
    ctl.handle_raw(raw(m=0.5, p=0.1), now=0.1)    # stopped moving
    events = ctl.handle_raw(raw(m=0.5, p=0.1), now=0.5)  # 300ms after stop
    activates = [v for k, v in events if k == 'activate']
    assert activates == ['amb_a']  # pattern pot at 0.1 -> index 0 of Ambient

  def test_sweep_activates_once(self):
    ctl, _ = make_controller()
    ctl.handle_raw(raw(m=0.5, p=0.0), now=0.0)
    # Sweep pattern pot through all 3 Ambient zones quickly
    ctl.handle_raw(raw(m=0.5, p=0.3), now=0.05)
    ctl.handle_raw(raw(m=0.5, p=0.6), now=0.10)
    ctl.handle_raw(raw(m=0.5, p=0.95), now=0.15)
    mid = ctl.handle_raw(raw(m=0.5, p=0.95), now=0.2)
    assert not any(k == 'activate' for k, _ in mid)
    done = ctl.handle_raw(raw(m=0.5, p=0.95), now=0.6)
    activates = [v for k, v in done if k == 'activate']
    assert activates == ['amb_c']

  def test_pattern_pot_no_overlay(self):
    ctl, _ = make_controller()
    ctl.handle_raw(raw(p=0.1), now=0.0)
    events = ctl.handle_raw(raw(p=0.8), now=0.05)
    assert not any(k == 'overlay' for k, _ in events)

  def test_empty_favorites_omitted_by_provider_contract(self):
    # Provider contract: caller omits empty favorites. Menu without favorites:
    ctl, _ = make_controller(menu=[('Ambient', ['amb_a']), ('Game', ['game_a'])])
    ctl.handle_raw(raw(m=0.1, p=0.1), now=0.0)
    ctl.handle_raw(raw(m=0.9, p=0.1), now=0.05)
    events = ctl.handle_raw(raw(m=0.9, p=0.1), now=0.5)
    assert ('activate', 'game_a') in events


class TestDisable:
  def test_disabled_pattern_pot_inert(self):
    ctl, _ = make_controller(enabled={'brightness': True, 'menu': True, 'pattern': False})
    ctl.handle_raw(raw(m=0.5, p=0.1), now=0.0)
    ctl.handle_raw(raw(m=0.5, p=0.9), now=0.05)
    events = ctl.handle_raw(raw(m=0.5, p=0.9), now=0.5)
    assert not any(k == 'activate' for k, _ in events)

  def test_reenable_requires_new_movement(self):
    ctl, flags = make_controller(enabled={'brightness': False, 'menu': True, 'pattern': True})
    ctl.handle_raw(raw(b=0.2), now=0.0)
    ctl.handle_raw(raw(b=0.9), now=0.05)  # moved while disabled — ignored
    flags['brightness'] = True
    # Re-enabled at resting 0.9: must NOT apply until it moves again
    assert ctl.handle_raw(raw(b=0.9), now=0.1) == []
    events = ctl.handle_raw(raw(b=0.7), now=0.15)
    assert ('brightness' in [k for k, _ in events])

  def test_disabled_menu_pot_no_overlay(self):
    ctl, _ = make_controller(enabled={'brightness': True, 'menu': False, 'pattern': True})
    ctl.handle_raw(raw(m=0.1), now=0.0)
    events = ctl.handle_raw(raw(m=0.9), now=0.05)
    assert not any(k == 'overlay' for k, _ in events)
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=. pytest tests/test_pots.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.core.pots'`

- [ ] **Step 3: Implement** — create `pi/app/core/pots.py`:

```python
"""
Pot input mapping: raw ADC values -> control events.

Pure logic with injected time so every behavior is unit-testable.
Events returned by handle_raw:
  ('brightness', float 0-1)  -> apply as manual brightness cap
  ('overlay', str)           -> show category name on the LED overlay
  ('activate', str)          -> activate this effect via the renderer

Last-writer-wins: a pot is inert until it physically moves beyond the
deadband from its last settled position. The first packet after startup
(or after re-enable) only establishes a baseline.
"""

import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)

ADC_MAX = 1023
DEADBAND = 0.02       # 2% of travel to count as movement
ZONE_GUARD = 0.15     # hysteresis: fraction of zone width past the boundary
SETTLE_S = 0.3        # selector pots activate this long after motion stops

POT_BRIGHTNESS, POT_MENU, POT_PATTERN = 0, 1, 2
_POT_KEYS = ('brightness', 'menu', 'pattern')


def normalize_raw(raw: int) -> float:
  return max(0.0, min(1.0, raw / ADC_MAX))


def select_index(value: float, count: int, current: Optional[int],
                 guard: float = ZONE_GUARD) -> Optional[int]:
  """Quantize value into one of `count` zones with hysteresis.

  Keeps `current` while value stays within the current zone expanded by
  guard * zone_width on both sides, so a knob resting on a boundary never
  flickers between two selections.
  """
  if count <= 0:
    return None
  zone_width = 1.0 / count
  naive = min(int(value / zone_width), count - 1)
  if current is None or not (0 <= current < count):
    return naive
  lo = (current - guard) * zone_width
  hi = (current + 1 + guard) * zone_width
  if lo <= value < hi:
    return current
  return naive


class PotController:
  def __init__(self, menu_provider: Callable, enabled_provider: Callable,
               deadband: float = DEADBAND, settle_s: float = SETTLE_S):
    self._menu_provider = menu_provider
    self._enabled_provider = enabled_provider
    self._deadband = deadband
    self._settle_s = settle_s
    self._settled: list = [None, None, None]     # last settled value per pot
    self._was_enabled: list = [True, True, True]
    self._last_move: list = [None, None]          # menu, pattern move times
    self._menu_idx: Optional[int] = None
    self._pattern_idx: Optional[int] = None
    self._pending_selection = False
    self.last_values: tuple = (0.0, 0.0, 0.0)

  def handle_raw(self, raw: tuple, now: float) -> list:
    values = tuple(normalize_raw(r) for r in raw)
    self.last_values = values
    enabled = self._enabled_provider()
    events: list = []

    for i in range(3):
      pot_on = bool(enabled.get(_POT_KEYS[i], True))
      just_reenabled = pot_on and not self._was_enabled[i]
      self._was_enabled[i] = pot_on

      if not pot_on or just_reenabled or self._settled[i] is None:
        # Disabled, freshly re-enabled, or first packet: silently rebaseline.
        self._settled[i] = values[i]
        continue

      if abs(values[i] - self._settled[i]) <= self._deadband:
        continue  # no movement

      self._settled[i] = values[i]
      if i == POT_BRIGHTNESS:
        events.append(('brightness', values[i]))
      elif i == POT_MENU:
        self._last_move[0] = now
        self._pending_selection = True
        menu = self._menu_provider()
        new_idx = select_index(values[i], len(menu), self._menu_idx)
        if new_idx is not None and new_idx != self._menu_idx:
          self._menu_idx = new_idx
          self._pattern_idx = None  # remap pattern within new category
        if self._menu_idx is not None and menu:
          events.append(('overlay', menu[self._menu_idx][0]))
      elif i == POT_PATTERN:
        self._last_move[1] = now
        self._pending_selection = True

    if self._pending_selection:
      moves = [t for t in self._last_move if t is not None]
      if moves and now - max(moves) >= self._settle_s:
        effect = self._resolve_selection()
        self._pending_selection = False
        if effect:
          events.append(('activate', effect))

    return events

  def _resolve_selection(self) -> Optional[str]:
    menu = self._menu_provider()
    if not menu:
      return None
    menu_idx = self._menu_idx
    if menu_idx is None:
      # Menu knob never moved: fall back to its absolute position.
      menu_idx = select_index(self.last_values[POT_MENU], len(menu), None)
    menu_idx = min(menu_idx, len(menu) - 1)
    self._menu_idx = menu_idx
    label, effect_names = menu[menu_idx]
    if not effect_names:
      return None
    self._pattern_idx = select_index(
      self.last_values[POT_PATTERN], len(effect_names), self._pattern_idx)
    return effect_names[self._pattern_idx]

  def get_status(self) -> dict:
    menu = self._menu_provider()
    category = menu[self._menu_idx][0] if (self._menu_idx is not None and menu) else None
    return {
      'values': {k: round(v, 3) for k, v in zip(_POT_KEYS, self.last_values)},
      'enabled': self._enabled_provider(),
      'category': category,
    }
```

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=. pytest tests/test_pots.py -v`
Expected: all PASS. If `test_activation_after_settle` fails, check that the settle branch runs even on packets with no movement (it must — the knob has stopped).

- [ ] **Step 5: Commit**

```bash
git add pi/app/core/pots.py pi/tests/test_pots.py
git commit -m "feat: PotController - deadband, hysteresis, settle-debounce, disable"
```

---

### Task 6: Renderer category overlay

**Files:**
- Modify: `pi/app/core/renderer.py`
- Test: `pi/tests/test_pots.py` (overlay timing tests appended)

**Interfaces:**
- Produces: `Renderer.set_overlay_text(text: str, linger: float = 1.5)`; `Renderer.overlay_region: Optional[tuple[int, int]]` attribute (`(x0, x1)`, defaults to `(0, width // 2)` when None).
- Consumes: `render_spine_text`, `composite_spine_overlay` from Task 2.

- [ ] **Step 1: Write the failing test** — append to `pi/tests/test_pots.py`:

```python
import numpy as np

from app.core.renderer import Renderer


class TestOverlayState:
  def _bare_renderer(self):
    # Renderer without transport dependency — we only exercise overlay helpers
    r = Renderer.__new__(Renderer)
    r._overlay_text = None
    r._overlay_until = 0.0
    r._overlay_img = None
    r._overlay_img_key = None
    r.overlay_region = None
    return r

  def test_set_overlay_text_arms_timer(self):
    r = self._bare_renderer()
    r.set_overlay_text('Ambient')
    assert r._overlay_text == 'Ambient'
    assert r._overlay_until > 0

  def test_apply_overlay_writes_pixels(self):
    r = self._bare_renderer()
    r.set_overlay_text('AB')
    frame = np.zeros((20, 40, 3), dtype=np.uint8)
    out = r._apply_overlay(frame, 20, 40)
    assert out.sum() > 0
    assert (out[10:] == 0).all()  # right half untouched (default region = left half)

  def test_expired_overlay_is_identity(self):
    import time as _time
    r = self._bare_renderer()
    r.set_overlay_text('AB', linger=0.0)
    r._overlay_until = _time.monotonic() - 1.0
    frame = np.full((20, 40, 3), 7, dtype=np.uint8)
    out = r._apply_overlay(frame, 20, 40)
    assert (out == frame).all()
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=. pytest tests/test_pots.py -v -k Overlay`
Expected: FAIL with `AttributeError: ... 'set_overlay_text'`

- [ ] **Step 3: Implement** in `pi/app/core/renderer.py`:

Add import at top: `from ..effects.textrender import render_spine_text, composite_spine_overlay`

In `Renderer.__init__`, after `self._segment_positions ... = {}` add:

```python
    # Pot-menu category overlay (book-spine text on the left panel)
    self._overlay_text: Optional[str] = None
    self._overlay_until: float = 0.0
    self._overlay_img = None
    self._overlay_img_key = None
    self.overlay_region: Optional[tuple] = None  # (x0, x1); None = left half
```

Add methods:

```python
  OVERLAY_FADE_S = 0.3
  OVERLAY_SCROLL_PX_S = 8.0

  def set_overlay_text(self, text: str, linger: float = 1.5):
    """Show category text on the overlay region; call repeatedly to extend."""
    self._overlay_text = text
    self._overlay_until = time.monotonic() + linger

  def _apply_overlay(self, logical_frame, w: int, h: int):
    now = time.monotonic()
    if not self._overlay_text or now >= self._overlay_until:
      return logical_frame
    x0, x1 = self.overlay_region or (0, max(1, w // 2))
    x0 = max(0, min(x0, w - 1))
    x1 = max(x0 + 1, min(x1, w))

    key = (self._overlay_text, x1 - x0)
    if key != self._overlay_img_key:
      self._overlay_img = render_spine_text(self._overlay_text, x1 - x0)
      self._overlay_img_key = key

    # Fade out over the last OVERLAY_FADE_S
    remaining = self._overlay_until - now
    alpha = min(1.0, remaining / self.OVERLAY_FADE_S)

    # Scroll long names top-to-bottom, holding briefly at the start
    text_len = self._overlay_img.shape[1]
    y_offset = 0
    if text_len > h:
      overflow = text_len - h
      y_offset = int(min(overflow, max(0.0, (now % (overflow / self.OVERLAY_SCROLL_PX_S
                    + 2.0)) - 1.0) * self.OVERLAY_SCROLL_PX_S))

    return composite_spine_overlay(logical_frame, self._overlay_img, x0, y_offset, alpha)
```

In `_render_frame`, inside the post-processing branch (`if not self.state.blackout and (...)`), insert the overlay BEFORE the brightness line (`effective = self.brightness_engine...`) so the brightness cap and gamma also apply to the overlay:

```python
      # Pot-menu category overlay (before brightness so caps apply to it too)
      logical_frame = self._apply_overlay(logical_frame, w, h)
```

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=. pytest tests/test_pots.py tests/test_error_isolation.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add pi/app/core/renderer.py pi/tests/test_pots.py
git commit -m "feat: renderer spine-text category overlay with linger + fade"
```

---

### Task 7: Transport drain + poll loop + main.py wiring

**Files:**
- Modify: `pi/app/transport/usb.py` (add `drain_incoming`)
- Modify: `pi/app/core/pots.py` (add `pots_poll_loop`)
- Modify: `pi/app/main.py` (build controller, dispatcher, background task, overlay region config)
- Modify: `pi/app/api/deps.py` (add `pot_controller` field)
- Modify: `pi/app/api/server.py` (accept and pass `pot_controller`)
- Modify: `pi/config/system.yaml.example` (document `pots.overlay`)

**Interfaces:**
- Produces: `TeensyTransport.drain_incoming() -> list[tuple[PacketHeader, bytes]]` (async, non-blocking drain of buffered packets); `pots_poll_loop(transport, controller, dispatch, interval=0.05)` (async task; `dispatch` is `async (events: list) -> None`); `AppDeps.pot_controller`.
- Consumes: `PacketType.POTS` + `parse_pots_payload` (Task 1), `PotController` (Task 5), `renderer.set_overlay_text` (Task 6), `EffectCatalogService.get_display_categories` (Task 3), `state_manager.favorites` / `pots_enabled` (Task 4).

- [ ] **Step 1: Add `drain_incoming`** to `TeensyTransport` in `pi/app/transport/usb.py` (after `request_stats`):

```python
  async def drain_incoming(self) -> list:
    """Read and return all fully-buffered incoming packets without blocking.

    Used by the pot poll loop to receive Teensy-pushed packets (PKT_POTS).
    Holds the lock briefly so it can't interleave with request/response
    exchanges (stats, config) that expect to consume replies themselves.

    Guarded on self.caps: connected flips True BEFORE the handshake, and the
    handshake's CAPS read does not hold the lock — draining during that
    window would steal the CAPS packet and break every reconnect.
    """
    if not self.connected or not self.serial or self.caps is None:
      return []
    packets = []
    async with self._lock:
      try:
        while True:
          result = self._read_packet()
          if result is None:
            break
          packets.append(result)
      except (serial.SerialException, OSError) as e:
        logger.error(f"drain_incoming failed: {e}")
        self.connected = False
    return packets
```

- [ ] **Step 2: Add `pots_poll_loop`** to `pi/app/core/pots.py` (bottom of file; add `import asyncio` and `import time` to its imports, plus `from ..models.protocol import PacketType, parse_pots_payload`):

```python
async def pots_poll_loop(transport, controller: PotController, dispatch,
                         interval: float = 0.05):
  """Background task: drain Teensy packets, feed POTS to the controller,
  dispatch resulting events. Never dies on a single bad iteration."""
  while True:
    try:
      packets = await transport.drain_incoming()
      for header, payload in packets:
        if header.packet_type != PacketType.POTS:
          continue
        raw = parse_pots_payload(payload)
        if raw is None:
          continue
        events = controller.handle_raw(raw, time.monotonic())
        if events:
          await dispatch(events)
      await asyncio.sleep(interval)
    except asyncio.CancelledError:
      break
    except Exception as e:
      logger.error(f"pots_poll_loop error: {e}", exc_info=True)
      await asyncio.sleep(1.0)
```

(The try/except-with-logging inside the loop is mandatory — see the CONFIG-startup-scope incident: background tasks must never die silently.)

- [ ] **Step 3: Wire in `pi/app/main.py`.** After `preview_service = PreviewService(renderer)` and before `create_app(...)`, add:

```python
  # --- Pot controller (physical knobs via Teensy PKT_POTS) ---
  from datetime import datetime, timezone
  from .core.pots import PotController, pots_poll_loop

  def _pot_menu():
    menu = []
    known = set(renderer.effect_registry.keys())
    favorites = [n for n in state_manager.favorites if n in known]
    if favorites:
      fav_sorted = sorted(favorites)
      menu.append(('Favorites', fav_sorted))
    menu.extend(effect_catalog.get_display_categories())
    return menu

  pot_controller = PotController(
    menu_provider=_pot_menu,
    enabled_provider=lambda: state_manager.pots_enabled,
  )

  async def _dispatch_pot_events(events):
    for kind, value in events:
      if kind == 'brightness':
        brightness_engine.manual_cap = value
        state_manager.brightness_manual_cap = value
        effective = brightness_engine.get_effective_brightness(
          datetime.now(timezone.utc))
        await transport.send_brightness(effective)
      elif kind == 'overlay':
        renderer.set_overlay_text(value)
      elif kind == 'activate':
        params = state_manager.get_effect_params(value)
        if renderer.activate_scene(value, params, media_manager=media_manager):
          state_manager.current_scene = value
          state_manager.current_params = params
          logger.info(f"Pot-activated effect: {value}")

  # Overlay region from system.yaml (pots.overlay.x0/x1); default = left half
  pots_conf = sys_conf.get('pots', {}) or {}
  overlay_conf = pots_conf.get('overlay', {}) or {}
  if 'x0' in overlay_conf and 'x1' in overlay_conf:
    renderer.overlay_region = (int(overlay_conf['x0']), int(overlay_conf['x1']))
```

Pass `pot_controller=pot_controller` in the `create_app(...)` call. In the `startup_tasks` handler, add alongside the other background tasks:

```python
    _background_tasks.append(asyncio.create_task(
      pots_poll_loop(transport, pot_controller, _dispatch_pot_events)))
```

- [ ] **Step 4: Plumb deps.** In `pi/app/api/deps.py` add to `AppDeps`:

```python
    pot_controller: Optional[object] = None
```

In `pi/app/api/server.py`, add `pot_controller=None` to the `create_app` signature and `pot_controller=pot_controller` to the `AppDeps(...)` construction.

- [ ] **Step 5: Document config** — append to `pi/config/system.yaml.example`:

```yaml
# Physical pot control (optional). Overlay region for the category name;
# defaults to the left half of the grid when omitted.
# pots:
#   overlay:
#     x0: 0
#     x1: 10
```

- [ ] **Step 6: Verify app boots and tests pass**

Run: `PYTHONPATH=. pytest tests/ -v -x -q 2>&1 | tail -5 && python -c "import app.main; print('main imports ok')"`
Expected: all tests PASS; `main imports ok`

- [ ] **Step 7: Commit**

```bash
git add pi/app/transport/usb.py pi/app/core/pots.py pi/app/main.py pi/app/api/deps.py pi/app/api/server.py pi/config/system.yaml.example
git commit -m "feat: pot poll loop, event dispatch, main wiring"
```

---

### Task 8: API — /api/pots + favorites endpoints

**Files:**
- Create: `pi/app/api/routes/pots.py`
- Modify: `pi/app/api/schemas.py`, `pi/app/api/routes/effects.py`, `pi/app/api/server.py`
- Test: `pi/tests/test_api_contract.py` (follow its existing TestClient fixture pattern)

**Interfaces:**
- Produces: `GET /api/pots` (public) → `{values, enabled, category}`; `POST /api/pots/config` (auth) body `{brightness?, menu?, pattern?}` → updated enabled dict; `GET /api/effects/favorites` (public) → `{favorites: [...]}`; `POST /api/effects/favorites` (auth) body `{favorites: [...]}` → 400 on unknown effect names.
- Consumes: `deps.pot_controller.get_status()` (Task 5), `deps.state_manager.favorites` / `pots_enabled` (Task 4).
- NOTE: `effects.create_router` gains a `require_auth` parameter — update the call in `server.py`.

- [ ] **Step 1: Write the failing tests** — append to `pi/tests/test_api_contract.py`, following its existing client/auth-header fixture conventions:

```python
class TestPotsApi:
  def test_get_pots_public(self, client):
    res = client.get('/api/pots')
    assert res.status_code == 200
    body = res.json()
    assert set(body['enabled'].keys()) == {'brightness', 'menu', 'pattern'}

  def test_post_config_requires_auth(self, client):
    res = client.post('/api/pots/config', json={'pattern': False})
    assert res.status_code == 401

  def test_post_config_updates(self, client, auth_headers):
    res = client.post('/api/pots/config', json={'pattern': False},
                      headers=auth_headers)
    assert res.status_code == 200
    assert res.json()['enabled']['pattern'] is False
    assert client.get('/api/pots').json()['enabled']['pattern'] is False


class TestFavoritesApi:
  def test_get_favorites_public(self, client):
    res = client.get('/api/effects/favorites')
    assert res.status_code == 200
    assert isinstance(res.json()['favorites'], list)

  def test_post_requires_auth(self, client):
    res = client.post('/api/effects/favorites', json={'favorites': []})
    assert res.status_code == 401

  def test_post_rejects_unknown_effect(self, client, auth_headers):
    res = client.post('/api/effects/favorites',
                      json={'favorites': ['definitely_not_an_effect']},
                      headers=auth_headers)
    assert res.status_code == 400

  def test_post_roundtrip(self, client, auth_headers):
    catalog = client.get('/api/effects/catalog').json()['effects']
    name = next(iter(catalog))
    res = client.post('/api/effects/favorites', json={'favorites': [name]},
                      headers=auth_headers)
    assert res.status_code == 200
    assert client.get('/api/effects/favorites').json()['favorites'] == [name]
```

If the existing contract-test fixtures don't construct a `pot_controller`, build a real one in the fixture: `PotController(menu_provider=lambda: [], enabled_provider=lambda: state_manager.pots_enabled)` and pass it to `create_app`.

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=. pytest tests/test_api_contract.py -v -k "Pots or Favorites"`
Expected: FAIL with 404s

- [ ] **Step 3: Implement.** Add to `pi/app/api/schemas.py`:

```python
class PotsConfigRequest(BaseModel):
    brightness: Optional[bool] = None
    menu: Optional[bool] = None
    pattern: Optional[bool] = None


class FavoritesRequest(BaseModel):
    favorites: list[str]
```

Create `pi/app/api/routes/pots.py`:

```python
"""Physical pot control routes — status and per-knob enable flags."""

from fastapi import APIRouter, Depends

from ..schemas import PotsConfigRequest


def create_router(deps, require_auth) -> APIRouter:
    router = APIRouter(prefix="/api/pots", tags=["pots"])

    @router.get("")
    async def get_pots():
        if deps.pot_controller is not None:
            status = deps.pot_controller.get_status()
        else:
            status = {'values': None, 'category': None,
                      'enabled': deps.state_manager.pots_enabled}
        return status

    @router.post("/config", dependencies=[Depends(require_auth)])
    async def update_pots_config(req: PotsConfigRequest):
        update = {k: v for k, v in
                  (('brightness', req.brightness), ('menu', req.menu),
                   ('pattern', req.pattern)) if v is not None}
        if update:
            deps.state_manager.pots_enabled = update
        return {'enabled': deps.state_manager.pots_enabled}

    return router
```

In `pi/app/api/routes/effects.py`, change the factory signature to `def create_router(deps, require_auth) -> APIRouter:` and add before `return router` (imports: `from fastapi import APIRouter, HTTPException, Depends` and `from ..schemas import FavoritesRequest`). Note: these MUST be registered before the existing `@router.get("/{name}")` catch-all route, so place them above it in the file:

```python
  @router.get("/favorites")
  async def get_favorites():
    return {'favorites': deps.state_manager.favorites}

  @router.post("/favorites", dependencies=[Depends(require_auth)])
  async def set_favorites(req: FavoritesRequest):
    if hasattr(deps, 'effect_catalog') and deps.effect_catalog:
      catalog = deps.effect_catalog.get_catalog()
    else:
      catalog = EffectCatalogService().get_catalog()
    unknown = [n for n in req.favorites if n not in catalog]
    if unknown:
      raise HTTPException(400, f"Unknown effects: {unknown}")
    deps.state_manager.favorites = req.favorites
    return {'favorites': deps.state_manager.favorites}
```

In `pi/app/api/server.py`: change `app.include_router(effects.create_router(deps))` → `app.include_router(effects.create_router(deps, require_auth))` and add `from .routes import pots as pots_routes` + `app.include_router(pots_routes.create_router(deps, require_auth))`.

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=. pytest tests/test_api_contract.py tests/test_auth.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add pi/app/api/
git commit -m "feat: /api/pots + /api/effects/favorites endpoints"
```

---

### Task 9: UI — knob toggles, favorites star, Favorites pill

**Files:**
- Modify: `pi/app/ui/static/index.html` (Knobs block in System > Status)
- Modify: `pi/app/ui/static/js/app.js` (knob toggles, favorites state, star button, Favorites filter)
- Modify: `pi/app/ui/static/css/app.css` (toggle switch + star styles)

**Interfaces:**
- Consumes: `GET/POST /api/pots[.../config]`, `GET/POST /api/effects/favorites` (Task 8), existing `api()` helper (adds auth headers automatically).

- [ ] **Step 1: HTML** — in `pi/app/ui/static/index.html`, inside `<div id="system-stats">` after the `Frames Sent` line, add:

```html
              <h3>Control Knobs</h3>
              <div id="knob-toggles" class="knob-toggles">
                <label class="knob-toggle-row">
                  <span>Brightness knob</span>
                  <span class="toggle-switch"><input type="checkbox" id="knob-brightness" checked><span class="toggle-slider"></span></span>
                </label>
                <label class="knob-toggle-row">
                  <span>Menu knob</span>
                  <span class="toggle-switch"><input type="checkbox" id="knob-menu" checked><span class="toggle-slider"></span></span>
                </label>
                <label class="knob-toggle-row">
                  <span>Pattern knob</span>
                  <span class="toggle-switch"><input type="checkbox" id="knob-pattern" checked><span class="toggle-slider"></span></span>
                </label>
              </div>
```

- [ ] **Step 2: CSS** — append to `pi/app/ui/static/css/app.css` (match the glassmorphism theme's existing accent variables; inspect nearby rules and reuse their color tokens):

```css
/* --- Knob enable toggles (System tab) --- */
.knob-toggles { display: flex; flex-direction: column; gap: 10px; max-width: 320px; }
.knob-toggle-row { display: flex; justify-content: space-between; align-items: center; cursor: pointer; }
.toggle-switch { position: relative; width: 44px; height: 24px; flex: 0 0 auto; }
.toggle-switch input { opacity: 0; width: 0; height: 0; }
.toggle-slider {
  position: absolute; inset: 0; border-radius: 24px;
  background: rgba(255, 255, 255, 0.15); transition: background 0.2s;
}
.toggle-slider::before {
  content: ''; position: absolute; width: 18px; height: 18px; border-radius: 50%;
  left: 3px; top: 3px; background: #fff; transition: transform 0.2s;
}
.toggle-switch input:checked + .toggle-slider { background: #00b894; }
.toggle-switch input:checked + .toggle-slider::before { transform: translateX(20px); }

/* --- Favorites star on effect cards --- */
.effect-card-fav {
  position: absolute; top: 4px; left: 8px; font-size: 14px;
  color: rgba(255, 255, 255, 0.25); cursor: pointer; z-index: 2;
}
.effect-card-fav.faved { color: #fdcb6e; }
```

- [ ] **Step 3: JS — knob toggles.** In `pi/app/ui/static/js/app.js`, add near `loadSystemStatus` and call `loadKnobToggles()` from inside `loadSystemStatus`:

```javascript
async function loadKnobToggles() {
  const data = await api('GET', '/api/pots');
  if (!data || !data.enabled) return;
  for (const key of ['brightness', 'menu', 'pattern']) {
    const box = document.getElementById(`knob-${key}`);
    if (box) box.checked = !!data.enabled[key];
  }
}

function initKnobToggles() {
  for (const key of ['brightness', 'menu', 'pattern']) {
    const box = document.getElementById(`knob-${key}`);
    if (!box) continue;
    box.addEventListener('change', async () => {
      const res = await api('POST', '/api/pots/config', { [key]: box.checked });
      if (!res) box.checked = !box.checked; // revert on auth failure
    });
  }
}
```

Call `initKnobToggles()` from the same init block that calls `initSystem()`.

- [ ] **Step 4: JS — favorites.** Add module state near `currentFilterCategory`:

```javascript
let favoriteEffects = [];
```

In `loadEffects()`, after fetching the catalog add:

```javascript
  const favData = await api('GET', '/api/effects/favorites');
  favoriteEffects = (favData && favData.favorites) || [];
```

Add `'Favorites'` to the category pills: in the `categoryOrder` array used for the FILTER BAR loop only, insert `'Favorites'` right after `'All'`, count it via `counts['Favorites'] = favoriteEffects.length`, and give it a color in `CATEGORY_COLORS`: `'Favorites': '#fdcb6e'`. Skip rendering the pill when `favoriteEffects.length === 0`.

In `applyEffectsFilter()`, extend the category match:

```javascript
    const matchesCategory =
      currentFilterCategory === 'All' ||
      (currentFilterCategory === 'Favorites'
        ? favoriteEffects.includes(btn.dataset.effect)
        : btn.dataset.category === currentFilterCategory);
```

In the effect-card build loop (next to the `hideBtn` code), add a star:

```javascript
    const favBtn = document.createElement('span');
    favBtn.className = 'effect-card-fav' + (favoriteEffects.includes(eff.name) ? ' faved' : '');
    favBtn.textContent = '★';
    favBtn.title = 'Toggle favorite';
    favBtn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const idx = favoriteEffects.indexOf(eff.name);
      if (idx >= 0) favoriteEffects.splice(idx, 1);
      else favoriteEffects.push(eff.name);
      favBtn.classList.toggle('faved', idx < 0);
      const res = await api('POST', '/api/effects/favorites', { favorites: favoriteEffects });
      if (!res) { // auth failure — revert
        if (idx >= 0) favoriteEffects.push(eff.name);
        else favoriteEffects.splice(favoriteEffects.indexOf(eff.name), 1);
        favBtn.classList.toggle('faved', idx >= 0);
      }
      applyEffectsFilter();
    });
    btn.appendChild(favBtn);
```

(`.effect-card` is a button with `position: relative` context — if the star lands wrong, add `position: relative` to `.effect-card` in CSS.)

- [ ] **Step 5: Verify locally**

Run: `LEDFANATIC_DEV=1 python -m app.main` (from `pi/` with venv active), open `http://localhost:8000`, check: System tab shows three working toggles; Effects tab shows stars; starring an effect creates the Favorites pill; POST without a token shows console auth warning and reverts. Stop the server.

- [ ] **Step 6: Run the frontend-design-audit skill** on the changed UI (mandatory per user's global CLAUDE.md after any UI work). Fix severity 3-4 findings.

- [ ] **Step 7: Commit**

```bash
git add pi/app/ui/static/
git commit -m "feat: UI knob enable toggles, favorites star + filter pill"
```

---

### Task 10: Teensy firmware — read pots, push PKT_POTS

**Files:**
- Modify: `teensy/firmware/include/config.h`
- Modify: `teensy/firmware/src/main.cpp`

**Interfaces:**
- Produces: `PKT_POTS` (0x31) sent every 50 ms, payload 3 × uint16 LE filtered ADC values (0–1023), order: brightness (A14), menu (A15), pattern (A16).
- Consumes: existing `sendPacket(type, payload, len)`.

- [ ] **Step 1: config.h** — add to the packet types block and timing block, and bump the version:

```cpp
#define PKT_POTS               0x31
```

```cpp
// --- Potentiometer inputs ---
#define POT_PIN_BRIGHTNESS  38   // A14
#define POT_PIN_MENU        39   // A15
#define POT_PIN_PATTERN     40   // A16
#define POTS_INTERVAL_MS    50
```

Change `#define FIRMWARE_VERSION "1.1.0"` → `#define FIRMWARE_VERSION "1.2.0"`.

- [ ] **Step 2: main.cpp** — add state near the other statics:

```cpp
// --- Pot inputs (raw 10-bit, exponentially smoothed) ---
static int32_t potFiltered[3] = {-1, -1, -1};
static uint32_t lastPotsSend = 0;
static const uint8_t POT_PINS[3] = {POT_PIN_BRIGHTNESS, POT_PIN_MENU, POT_PIN_PATTERN};
```

Add forward declarations `void readPots();` and `void sendPots();`, then implementations (near `sendStats`):

```cpp
void readPots() {
  for (int i = 0; i < 3; i++) {
    int32_t raw = analogRead(POT_PINS[i]);  // 10-bit default: 0-1023
    if (potFiltered[i] < 0) {
      potFiltered[i] = raw;
    } else {
      potFiltered[i] += (raw - potFiltered[i]) >> 3;  // EMA, alpha = 1/8
    }
  }
}

void sendPots() {
  uint8_t payload[6];
  for (int i = 0; i < 3; i++) {
    uint16_t v = (uint16_t)potFiltered[i];
    payload[i * 2] = v & 0xFF;
    payload[i * 2 + 1] = (v >> 8) & 0xFF;
  }
  sendPacket(PKT_POTS, payload, sizeof(payload));
}
```

In `loop()`, after the FPS counter block (which already computes `uint32_t now = millis();`), add:

```cpp
  // --- Pot inputs ---
  readPots();
  if (now - lastPotsSend >= POTS_INTERVAL_MS) {
    sendPots();
    lastPotsSend = now;
  }
```

- [ ] **Step 3: Build**

Run: `cd teensy/firmware && pio run`
Expected: `SUCCESS`. If `pio` is not installed locally, run `pip install platformio` in a throwaway venv or flag the build to the user — do NOT skip the compile check silently.

- [ ] **Step 4: Commit**

```bash
git add teensy/firmware/
git commit -m "feat(firmware): read 3 pots, push PKT_POTS at 20Hz (v1.2.0)"
```

---

### Task 11: Full test run, deploy, live verification

**Files:** none new.

- [ ] **Step 1: Full test suite**

Run: `cd pi && source .venv/bin/activate && PYTHONPATH=. pytest tests/ -v 2>&1 | tail -15`
Expected: all PASS, zero failures.

- [ ] **Step 2: Deploy Pi code**

Run: `bash pi/scripts/deploy.sh ledfanatic.local`
Expected: rsync + pip install + service restart succeed.

- [ ] **Step 3: Flash firmware.** The Teensy is attached to the Pi, not this Mac. Tell the user the firmware needs flashing and how (e.g. `pio run` hex + their usual loader), and confirm the plan for their setup before doing anything. After flashing, `GET /api/system/status` (or the System tab) should report firmware `1.2.0`.

- [ ] **Step 4: Live verification with the user** (needs pots wired: 3.3V–wiper–GND to pins 38/39/40 — **never 5V**):
  - Brightness knob changes brightness; app slider still works after knob stops (last-writer-wins both directions).
  - Menu knob shows spine text on the left panel (letter tops face right, reads top-to-bottom), lingers ~1.5 s, fades.
  - Pattern knob changes effects only ~300 ms after it stops moving; sweeping doesn't machine-gun activate.
  - Disabling "Pattern knob" in System tab makes that knob inert; re-enabling doesn't jump until it moves.
  - Starred favorites appear as the first knob category.
  - With no pots wired yet, floating ADC pins will jitter: verify the smoothing + deadband keep the system stable (no spurious activations); if floating pins still trigger movement, raise the deadband for unwired pots or have the user disable those knobs until wired.

- [ ] **Step 5: Commit any tuning changes and push**

```bash
git push
```
