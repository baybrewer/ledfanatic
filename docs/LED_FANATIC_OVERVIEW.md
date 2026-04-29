# LED Fanatic — Technical Overview

## What Is It?

LED Fanatic is a real-time visual performance engine for LED sculptures. It drives 830+ individually addressable LEDs at 60 frames per second, rendering effects that range from fluid dynamics simulations to interactive games — all controlled from a phone or tablet.

## Architecture

The system is a split-brain design: a **Raspberry Pi 4** handles all rendering, audio analysis, and UI serving, while a **Teensy 4.1 microcontroller** with an OctoWS2811 adapter handles the time-critical LED signal output via DMA.

**Pi → Teensy communication** uses USB CDC serial with COBS-framed binary packets and CRC32 checksums. The Pi renders each frame into a logical pixel grid, applies brightness calibration and gamma correction, packs the pixel data with precomputed NumPy index arrays (0.24ms per frame), and sends it to the Teensy. The Teensy outputs the signal to 8 parallel LED channels simultaneously using direct memory access — no CPU involvement during output.

## Rendering Engine

Effects render into a canonical NumPy frame buffer (width × height × RGB). A compositor supports multiple effect layers with blend modes (normal, add, screen, multiply, max). The render loop runs at 60 FPS with per-component profiling: effect render time, pack time, and transport time are all measured and exposed via API.

The layout is fully schema-driven — a YAML config defines every LED segment's position, direction, color order, and brightness calibration. Changing the physical layout requires editing one config file; no code changes needed.

## Effects Library

The engine ships with 90+ effects across categories:

- **Generative** — fire, plasma, rainbow, twin torches with fluid dynamics, fire bubbles
- **Sound Reactive** — spectrum analyzer, fluid jets, negative-space effects driven by bass amplitude
- **Simulation** — Navier-Stokes fluid dynamics, reaction-diffusion, wave equation, boids flocking, vortex-particle smoke rings
- **Fractals** — Mandelbrot zoom, Julia set explorer, burning ship, fractal flames
- **Fluids** — ink drop, Kelvin-Helmholtz instability, convection cells, Lorenz attractor, plasma globe, lattice Boltzmann
- **Games** — Tetris, Space Invaders, Snake, Mario Runner, Conway's Game of Life

All effects are written in Python with NumPy vectorization — no per-pixel Python loops. Even the Navier-Stokes solver runs in under 15ms on the Pi.

## Audio Processing

A USB microphone feeds a real-time FFT analyzer running at 86 Hz (512-sample chunks at 44.1 kHz). Bass, mid, and high frequency bands are extracted and smoothed. Sound-reactive effects respond directly to bass amplitude — no beat detection needed. The audio snapshot is delivered lock-free to the render thread via atomic dict assignment.

## Control Interface

A FastAPI web server serves a responsive single-page app with glassmorphism dark theme. The UI adapts to phone, tablet, and desktop layouts. Features include:

- Effect cards with category color coding and archive/hide
- Per-effect parameter sliders generated from schema
- Named playlist save/load with cross-fade transitions
- Per-segment brightness calibration tool
- Layout config management
- Live WebSocket preview
- Game controls with per-game touch layouts

## Performance

The entire pipeline — effect render, brightness calibration LUT application, pixel packing, and USB transport — completes in under 16.67ms (60 FPS budget). The vectorized NumPy packer uses precomputed source/destination index arrays for a single fancy-index operation per frame. Per-segment brightness correction is applied via 256-entry lookup tables compiled from 3-point calibration curves — zero float math in the hot path.

## Hardware

- Raspberry Pi 4 (4GB) — rendering, UI, audio
- Teensy 4.1 — OctoWS2811 DMA LED output
- WS2812/WS2815 LED strips — 830 pixels across 20 segments on 8 channels
- USB microphone — real-time audio input
- WiFi hotspot — portable operation without infrastructure
