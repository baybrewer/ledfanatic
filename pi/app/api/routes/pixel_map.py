"""
Pixel Map API routes — CRUD for strips, scanlines, segments, origin, validation.

All mutations use a staged-edit pattern:
  1. Deep-copy the live config
  2. Apply edits to the copy
  3. Validate + compile
  4. Send CONFIG to Teensy and await ACK
  5. Only on ACK: commit to deps, apply to renderer, save to disk
"""

import copy
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ...config.pixel_map import (
  PixelMapConfig,
  StripConfig,
  SegmentConfig,
  ScanlineConfig,
  CompiledPixelMap,
  validate_pixel_map,
  compile_pixel_map,
  save_pixel_map,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class ScanlineRequest(BaseModel):
  start: list[int]  # [x, y]
  end: list[int]    # [x, y]


class SegmentRequest(BaseModel):
  range_start: int
  range_end: int
  color_order: str = "BGR"


class StripRequest(BaseModel):
  id: int
  output: int = 0
  output_offset: int = 0
  total_leds: int = 0
  segments: list[SegmentRequest] = []
  scanlines: list[ScanlineRequest] = []
  pixel_overrides: dict[str, list[int]] = {}  # {"led_index": [x, y]}


class OriginRequest(BaseModel):
  origin: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strip_from_request(req: StripRequest) -> StripConfig:
  """Convert a StripRequest into a StripConfig dataclass."""
  return StripConfig(
    id=req.id,
    output=req.output,
    output_offset=req.output_offset,
    total_leds=req.total_leds,
    segments=[
      SegmentConfig(
        range_start=s.range_start,
        range_end=s.range_end,
        color_order=s.color_order,
      )
      for s in req.segments
    ],
    scanlines=[
      ScanlineConfig(
        start=tuple(s.start),
        end=tuple(s.end),
      )
      for s in req.scanlines
    ],
    pixel_overrides={int(k): tuple(v) for k, v in req.pixel_overrides.items()},
  )


def _config_to_response(config: PixelMapConfig, compiled: CompiledPixelMap) -> dict:
  """Build the full GET response from config + compiled map."""
  strips = []
  for s in config.strips:
    strips.append({
      "id": s.id,
      "output": s.output,
      "output_offset": s.output_offset,
      "total_leds": s.total_leds,
      "segments": [
        {
          "range_start": seg.range_start,
          "range_end": seg.range_end,
          "color_order": seg.color_order,
        }
        for seg in s.segments
      ],
      "scanlines": [
        {
          "start": list(sc.start),
          "end": list(sc.end),
        }
        for sc in s.scanlines
      ],
      "pixel_overrides": [
        {"led_index": idx, "position": list(pos)}
        for idx, pos in s.pixel_overrides.items()
      ] if s.pixel_overrides else [],
    })

  return {
    "origin": config.origin,
    "teensy": {
      "outputs": config.teensy_outputs,
      "max_leds_per_output": config.teensy_max_leds_per_output,
      "wire_order": config.teensy_wire_order,
      "signal_family": config.teensy_signal_family,
    },
    "grid": {
      "width": compiled.width if compiled else 0,
      "height": compiled.height if compiled else 0,
      "total_mapped_leds": compiled.total_mapped_leds if compiled else 0,
    },
    "output_config": {
      str(k): [
        {"strip_id": sid, "offset": off, "count": cnt}
        for sid, off, cnt in entries
      ]
      for k, entries in (compiled.output_config.items() if compiled else {})
    },
    "strips": strips,
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
    f"{compiled.total_mapped_leds} LEDs, {len(staged_config.strips)} strips"
  )


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
      return {"error": "No pixel map loaded", "strips": []}
    return _config_to_response(config, compiled)

  # --- POST /strips — add a new strip ---

  @router.post("/strips", dependencies=[Depends(require_auth)])
  async def add_strip(req: StripRequest):
    if deps.pixel_map_config is None:
      raise HTTPException(404, "No pixel map loaded")

    staged = copy.deepcopy(deps.pixel_map_config)

    # Reject duplicate strip ID
    if any(s.id == req.id for s in staged.strips):
      raise HTTPException(409, f"Strip {req.id} already exists")

    staged.strips.append(_strip_from_request(req))
    await _recompile_and_apply(staged, deps)

    return {"status": "ok", "strip_id": req.id}

  # --- POST /strips/{strip_id} — update an existing strip ---

  @router.post("/strips/{strip_id}", dependencies=[Depends(require_auth)])
  async def update_strip(strip_id: int, req: StripRequest):
    if deps.pixel_map_config is None:
      raise HTTPException(404, "No pixel map loaded")

    # Strip ID changes not supported via update — delete + re-add instead
    if req.id != strip_id:
      raise HTTPException(
        422,
        f"Strip ID in body ({req.id}) does not match URL ({strip_id}). "
        f"To change a strip ID, delete and re-add."
      )

    staged = copy.deepcopy(deps.pixel_map_config)

    idx = next((i for i, s in enumerate(staged.strips) if s.id == strip_id), None)
    if idx is None:
      raise HTTPException(404, f"Strip {strip_id} not found")

    # Preserve existing pixel_overrides if none provided in request
    old_strip = staged.strips[idx]
    new_strip = _strip_from_request(req)
    if not new_strip.pixel_overrides and old_strip.pixel_overrides:
      new_strip.pixel_overrides = old_strip.pixel_overrides

    staged.strips[idx] = new_strip
    await _recompile_and_apply(staged, deps)

    return {"status": "ok", "strip_id": strip_id}

  # --- DELETE /strips/{strip_id} — delete a strip ---

  @router.delete("/strips/{strip_id}", dependencies=[Depends(require_auth)])
  async def delete_strip(strip_id: int):
    if deps.pixel_map_config is None:
      raise HTTPException(404, "No pixel map loaded")

    staged = copy.deepcopy(deps.pixel_map_config)

    idx = next((i for i, s in enumerate(staged.strips) if s.id == strip_id), None)
    if idx is None:
      raise HTTPException(404, f"Strip {strip_id} not found")

    staged.strips.pop(idx)
    await _recompile_and_apply(staged, deps)

    return {"status": "ok", "strip_id": strip_id}

  # --- POST /origin — set grid origin ---

  @router.post("/origin", dependencies=[Depends(require_auth)])
  async def set_origin(req: OriginRequest):
    if deps.pixel_map_config is None:
      raise HTTPException(404, "No pixel map loaded")

    valid_origins = {"bottom-left", "top-left"}
    if req.origin not in valid_origins:
      raise HTTPException(
        422,
        f"Invalid origin '{req.origin}'. Must be one of: {sorted(valid_origins)}"
      )

    staged = copy.deepcopy(deps.pixel_map_config)
    staged.origin = req.origin
    await _recompile_and_apply(staged, deps)

    return {"status": "ok", "origin": req.origin}

  # --- POST /pixel/{strip_id}/{led_index} — single-pixel override ---

  @router.post("/pixel/{strip_id}/{led_index}", dependencies=[Depends(require_auth)])
  async def set_pixel_override(
    strip_id: int,
    led_index: int,
    x: int = Query(..., ge=0),
    y: int = Query(..., ge=0),
  ):
    if deps.pixel_map_config is None:
      raise HTTPException(404, "No pixel map loaded")

    staged = copy.deepcopy(deps.pixel_map_config)

    strip = next((s for s in staged.strips if s.id == strip_id), None)
    if strip is None:
      raise HTTPException(404, f"Strip {strip_id} not found")

    if led_index < 0 or led_index >= strip.total_leds:
      raise HTTPException(
        422,
        f"LED index {led_index} out of range [0, {strip.total_leds - 1}]"
      )

    strip.pixel_overrides[led_index] = (x, y)
    await _recompile_and_apply(staged, deps)

    return {"status": "ok", "strip_id": strip_id, "led_index": led_index, "position": [x, y]}

  # --- POST /validate — validate current config without applying ---

  @router.post("/validate")
  async def validate_config():
    if deps.pixel_map_config is None:
      raise HTTPException(404, "No pixel map loaded")

    errors = validate_pixel_map(deps.pixel_map_config)
    return {
      "valid": len(errors) == 0,
      "errors": errors,
    }

  # --- GET /teensy-status — Teensy config status ---

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
      result["output_config"] = {
        str(k): [
          {"strip_id": sid, "offset": off, "count": cnt}
          for sid, off, cnt in entries
        ]
        for k, entries in compiled.output_config.items()
      }

    return result

  return router
