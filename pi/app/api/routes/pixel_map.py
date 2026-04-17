"""
Pixel Map API routes — full-replacement apply, validate, and status.

All mutations use a staged-edit pattern:
  1. Build a PixelMapConfig from the request (preserving teensy_* from current)
  2. Validate
  3. Compile
  4. Send CONFIG to Teensy and await ACK
  5. Only on ACK: commit to deps, apply to renderer, save to disk
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...config.pixel_map import (
  PixelMapConfig,
  SegmentConfig,
  compile_pixel_map,
  validate_pixel_map,
  save_pixel_map,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class SegmentRequest(BaseModel):
  start: list[int]      # [x, y]
  end: list[int]        # [x, y]
  output: int           # 0-7
  color_order: str = 'BGR'


class PixelMapApplyRequest(BaseModel):
  origin: str = 'bottom-left'
  segments: list[SegmentRequest]


# ---------------------------------------------------------------------------
# Response builder
# ---------------------------------------------------------------------------

def _build_get_response(config: PixelMapConfig, compiled) -> dict:
  """Build the full GET response from config + compiled map."""
  segments = []
  for idx, seg in enumerate(config.segments):
    segments.append({
      'start': list(seg.start),
      'end': list(seg.end),
      'output': seg.output,
      'color_order': seg.color_order,
      'led_count': seg.led_count(),
      'offset': compiled.segment_offsets[idx] if compiled else 0,
    })

  return {
    'origin': config.origin,
    'grid': {
      'width': compiled.width if compiled else 0,
      'height': compiled.height if compiled else 0,
      'total_mapped_leds': compiled.total_mapped_leds if compiled else 0,
    },
    'output_config': compiled.output_config if compiled else [0] * 8,
    'segments': segments,
  }


# ---------------------------------------------------------------------------
# Staged recompile + Teensy ACK gate
# ---------------------------------------------------------------------------

async def _recompile_and_apply(staged_config: PixelMapConfig, deps):
  """Validate, compile, send CONFIG to Teensy, commit on ACK."""
  errors = validate_pixel_map(staged_config)
  if errors:
    raise HTTPException(status_code=422, detail=errors)

  compiled = compile_pixel_map(staged_config)

  config_ok = await deps.transport.send_config(compiled.output_config)
  if not config_ok:
    raise HTTPException(status_code=502, detail="Teensy rejected CONFIG or timed out")

  # ACK received — commit changes
  deps.pixel_map_config = staged_config
  deps.compiled_pixel_map = compiled
  deps.renderer.apply_pixel_map(compiled)
  save_pixel_map(staged_config, deps.config_dir)

  logger.info(
    f"Pixel map applied: {compiled.width}x{compiled.height}, "
    f"{compiled.total_mapped_leds} LEDs, {len(staged_config.segments)} segments"
  )

  return compiled


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------

def create_router(deps, require_auth) -> APIRouter:
  router = APIRouter(prefix="/api/pixel-map", tags=["pixel-map"])

  # --- GET / — full pixel map config + grid dimensions + output config ---

  @router.get("/")
  async def get_pixel_map():
    config = deps.pixel_map_config
    compiled = deps.compiled_pixel_map
    if config is None:
      return {"error": "No pixel map loaded", "segments": []}
    return _build_get_response(config, compiled)

  # --- POST /apply — replace entire pixel map ---

  @router.post("/apply", dependencies=[Depends(require_auth)])
  async def apply_pixel_map(req: PixelMapApplyRequest):
    current = deps.pixel_map_config

    # Build new config, preserving teensy_* settings from current config
    staged = PixelMapConfig(
      origin=req.origin,
      teensy_outputs=current.teensy_outputs if current else 8,
      teensy_max_leds_per_output=current.teensy_max_leds_per_output if current else 1200,
      teensy_wire_order=current.teensy_wire_order if current else "BGR",
      teensy_signal_family=current.teensy_signal_family if current else "ws281x_800khz",
      teensy_octo_pins=current.teensy_octo_pins if current else [2, 14, 7, 8, 6, 20, 21, 5],
      segments=[
        SegmentConfig(
          start=tuple(seg.start),
          end=tuple(seg.end),
          output=seg.output,
          color_order=seg.color_order,
        )
        for seg in req.segments
      ],
    )

    compiled = await _recompile_and_apply(staged, deps)
    return _build_get_response(staged, compiled)

  # --- POST /validate — validate without applying ---

  @router.post("/validate")
  async def validate_map(req: PixelMapApplyRequest):
    staged = PixelMapConfig(
      origin=req.origin,
      segments=[
        SegmentConfig(
          start=tuple(seg.start),
          end=tuple(seg.end),
          output=seg.output,
          color_order=seg.color_order,
        )
        for seg in req.segments
      ],
    )
    errors = validate_pixel_map(staged)
    return {"valid": len(errors) == 0, "errors": errors}

  # --- POST /test-segment/{seg_index} — light one segment for identification ---

  @router.post("/test-segment/{seg_index}", dependencies=[Depends(require_auth)])
  async def test_segment(seg_index: int):
    """Light a single segment for identification (5 seconds)."""
    compiled = deps.compiled_pixel_map
    if compiled is None or seg_index < 0 or seg_index >= len(compiled.segments):
      raise HTTPException(404, f"Segment {seg_index} not found")
    deps.renderer.set_test_strip(seg_index, duration=5.0)
    return {'status': 'ok', 'segment': seg_index}

  # --- GET /teensy-status — Teensy connection + config status ---

  @router.get("/teensy-status")
  async def teensy_status():
    transport_status = deps.transport.get_status()
    config = deps.pixel_map_config
    compiled = deps.compiled_pixel_map

    result = {
      "connected": transport_status.get("connected", False),
      "caps": transport_status.get("caps"),
      "last_config_ack": deps.transport._last_config_ack,
    }

    if config:
      result["teensy_config"] = {
        "outputs": config.teensy_outputs,
        "max_leds_per_output": config.teensy_max_leds_per_output,
        "wire_order": config.teensy_wire_order,
        "signal_family": config.teensy_signal_family,
      }

    if compiled:
      result["output_config"] = compiled.output_config

    return result

  return router
