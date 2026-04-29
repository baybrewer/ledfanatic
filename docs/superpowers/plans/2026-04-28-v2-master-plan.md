# LED Fanatic v2.0 — Master Upgrade Plan

> **Decomposition:** 7 independent sub-projects, each producing working software on its own.

**Vision:** Transform LED Fanatic from a functional controller into a premium visual performance instrument with a beautiful UI, organized effect library, playlists, new showcase effects, and expanded content.

---

## Sub-Project Decomposition

| Phase | Sub-Project | Effort | Dependencies |
|-------|------------|--------|-------------|
| **A** | Quick Wins & Settings | 1 day | None |
| **B** | UI Overhaul — Responsive + Beautiful | 3-5 days | Phase A |
| **C** | Effect Organization — Categories, Archive, Thumbnails | 2-3 days | Phase B |
| **D** | Playlists — Named, Editable, Persistent | 1-2 days | Phase C |
| **E** | New Effects — Fractals + 10 Fluid Animations | 3-4 days | None (parallel) |
| **F** | Games — Space Invaders + More | 2-3 days | None (parallel) |
| **G** | Media — iPhone Video Upload + Camera | 2-3 days | Phase B |

---

## Phase A: Quick Wins & Settings

**Plan:** `2026-04-28-v2-phase-a-settings.md`

Deliverables:
1. Night brightness adjustable from control panel (currently hardcoded)
2. Live preview defaults to OFF
3. Commit + deploy

---

## Phase B: UI Overhaul — Responsive + Beautiful

**Plan:** `2026-04-28-v2-phase-b-ui-overhaul.md` (TBD)

Deliverables:
1. Full-width responsive layout (computer, iPad, phone)
2. Futuristic dark theme with glassmorphism, gradients, glow effects
3. Proper grid layout for effect cards
4. Status bar redesign with live metrics
5. Brightness controls integrated into header
6. Touch-optimized for iPad

Key decisions:
- No framework (stay vanilla JS — Pi serves static files, no build step)
- CSS-only visual upgrade (no new dependencies)
- Progressive enhancement: works on all screen sizes

---

## Phase C: Effect Organization — Categories, Archive, Thumbnails

**Plan:** `2026-04-28-v2-phase-c-organization.md` (TBD)

Deliverables:
1. Logical category sorting (Ambient, Sound Reactive, Simulation, Games, etc.)
2. Sort by: category, name, recently used
3. Archive/hide effects (with restore)
4. Algorithmic preview thumbnails (canvas-rendered, not GIFs)
5. Hide confusing/duplicate effects by default

---

## Phase D: Playlists — Named, Editable, Persistent

**Plan:** `2026-04-28-v2-phase-d-playlists.md` (TBD)

Deliverables:
1. Multiple named playlists (CRUD)
2. Drag-to-reorder effects in playlist
3. Per-effect duration and transition settings
4. Playlist playback with cross-fade
5. Playlist save/load from state.json
6. Playlist UI panel

---

## Phase E: New Effects — Fractals + 10 Fluid Animations

**Plan:** `2026-04-28-v2-phase-e-new-effects.md` (TBD)

Deliverables:
1. Fractal effects: Mandelbrot zoom, Julia set explorer, Burning Ship
2. Electric Sheep-inspired: evolving fractal flames
3. 10 new fluid animations (non-SR):
   - Ink drop diffusion
   - Kelvin-Helmholtz instability
   - Rayleigh-Bénard convection cells
   - Lava lamp (improved physics)
   - Oil/water separation
   - Magnetic ferrofluid
   - Double pendulum trace
   - Strange attractor (Lorenz, Rössler)
   - Cellular automata fluid (lattice Boltzmann)
   - Turbulent jet mixing
4. All registered in catalog with params

---

## Phase F: Games — Space Invaders + More

**Plan:** `2026-04-28-v2-phase-f-games.md` (TBD)

Deliverables:
1. Space Invaders (10-wide, phone controls)
2. Snake (classic, auto-play mode)
3. Pong (auto-play, 2-player via two phones?)
4. Conway's Game of Life (interactive seed placement)

---

## Phase G: Media — iPhone Video + Camera

**Plan:** `2026-04-28-v2-phase-g-media.md` (TBD)

Deliverables:
1. iPhone video upload (MOV/HEVC transcoding via ffmpeg)
2. Live camera feed via RTSP or WebRTC
3. Improved media library UI

---

## Execution Order

```
Phase A (settings) ──→ Phase B (UI) ──→ Phase C (organization) ──→ Phase D (playlists)
                                                                         ↑
Phase E (new effects) ──────────────────────────────────────────────────┘
Phase F (games) ─────────────────────────────────────────────────────────┘
Phase G (media) ─────────────── Phase B (needs responsive UI first) ────┘
```

Phases E and F can run in parallel with B/C/D since they don't touch UI code.
