# Phase 4 — Animation Switcher

## Goal

A meta-effect that lets the user pick N animations and automatically cross-fades between them on an adjustable timer (5–60 seconds).

## Design

### AnimationSwitcher effect class

```python
class AnimationSwitcher(Effect):
    """Meta-effect: cycles through a playlist of effects with cross-fade transitions."""
    DISPLAY_NAME = "Animation Switcher"
    CATEGORY = "special"
    PARAMS = [
        {"name": "interval", "label": "Switch Time (s)", "min": 5, "max": 60, "step": 1, "default": 15, "type": "slider"},
        {"name": "fade_duration", "label": "Fade Duration (s)", "min": 0.5, "max": 5.0, "step": 0.5, "default": 2.0, "type": "slider"},
        {"name": "shuffle", "label": "Shuffle", "min": 0, "max": 1, "step": 1, "default": 0, "type": "toggle"},
    ]
```

### State machine

```
PLAYING (current effect) ──[timer expires]──→ FADING ──[fade complete]──→ PLAYING (next effect)
```

### Cross-fade

During the fade window:
1. Render both current and next effect to separate buffers
2. Blend: `output = current * (1 - t) + next * t` where `t` goes 0→1 over `fade_duration`
3. Once t=1, discard current, promote next

### Playlist management

```python
# Internal state
self.playlist: list[str] = []        # effect names
self.playlist_params: dict = {}       # per-effect param overrides
self.current_idx: int = 0
self.current_effect: Effect = None
self.next_effect: Effect = None
self.phase: str = "playing"           # "playing" | "fading"
self.phase_timer: float = 0.0
```

## API

### Set playlist

```
POST /api/scenes/activate
{
  "effect": "animation_switcher",
  "params": {
    "interval": 15,
    "fade_duration": 2.0,
    "shuffle": false,
    "playlist": ["aurora_borealis", "fireplace", "plasma", "lava_lamp"],
    "playlist_params": {
      "aurora_borealis": {"speed": 2.0, "palette": "Ice"},
      "fireplace": {"fuel": 0.8}
    }
  }
}
```

### Get current state

The switcher exposes its state through `render_state` or a dedicated endpoint:

```
GET /api/scenes/switcher/status
{
  "active": true,
  "current": "aurora_borealis",
  "next": "fireplace",
  "phase": "fading",
  "progress": 0.65,
  "time_remaining": 4.2,
  "playlist": ["aurora_borealis", "fireplace", "plasma", "lava_lamp"]
}
```

## UI — Switcher panel

When "Animation Switcher" is selected in the Effects tab, show a special panel:

```
┌─────────────────────────────────────────┐
│ Animation Switcher                       │
│                                          │
│ Switch Time  [══════●════] 15s           │
│ Fade Duration [══●═══════] 2.0s          │
│ Shuffle      [✓]                         │
│                                          │
│ Playlist:                                │
│  ✓ Aurora Borealis                       │
│  ✓ Fireplace                             │
│  ✓ Plasma                                │
│  ✓ Lava Lamp                             │
│  ☐ Ocean Waves                           │
│  ☐ Starfield                             │
│  ... (all effects as checkboxes)         │
│                                          │
│ Now: Aurora Borealis → Fireplace (4s)    │
│ [▓▓▓▓▓▓▓░░░] 65% fade                   │
│                                          │
└─────────────────────────────────────────┘
```

### JS implementation

- Checkbox list of all non-diagnostic effects
- At least 2 must be selected to activate
- Live status via WebSocket or polling
- Interval/fade sliders send re-activate with updated params

## Tests

- Switcher renders correct dimensions
- Cross-fade blending produces intermediate values
- Playlist wraps around
- Shuffle produces different order
- Empty/single playlist handled gracefully
- Interval change takes effect on next cycle

## Gate

- Switcher works with any combination of existing + imported effects
- Cross-fade is visually smooth (no flicker)
- UI shows current/next/progress
