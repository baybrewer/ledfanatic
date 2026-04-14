"""
Setup API routes.

Manages setup sessions, strip inventory, pattern runner, and
camera-assisted wizards. Lives under /api/setup.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)


# --- Request/response schemas ---

class SetupSessionStartResponse(BaseModel):
  session_id: str
  snapshot_scene: Optional[str] = None
  snapshot_blackout: bool = False
  strip_count: int = 0


class SetupSessionStatusResponse(BaseModel):
  active: bool
  session_id: Optional[str] = None
  active_pattern: Optional[dict] = None
  staged_strip_count: int = 0


class StripRowUpdate(BaseModel):
  id: int
  label: Optional[str] = None
  enabled: Optional[bool] = None
  logical_order: Optional[int] = None
  output_channel: Optional[int] = None
  output_slot: Optional[int] = None
  direction: Optional[str] = None
  installed_led_count: Optional[int] = None
  color_order: Optional[str] = None
  chipset: Optional[str] = None


class InstallationUpdateRequest(BaseModel):
  session_id: str
  strips: list[StripRowUpdate]


class SetupPatternRequest(BaseModel):
  session_id: str
  mode: str = "fill_strip"  # fill_strip | fill_leds | clear | anchor
  targets: list[dict] = []
  color: list[int] = [255, 255, 255]
  all_others: str = "black"
  use_compiled_color_order: bool = False


class SetupCommitRequest(BaseModel):
  session_id: str


class AnchorEntry(BaseModel):
  anchor_index: int
  centroid_x: float
  centroid_y: float
  brightness: float = 200.0


class GeometryStripEntry(BaseModel):
  strip_id: int
  anchors: list[AnchorEntry] = []
  installed_led_count: int = 172


class GeometrySolveRequest(BaseModel):
  strips: list[GeometryStripEntry]
  image_width: int = 1280
  image_height: int = 720


class SpatialMapStripEntry(BaseModel):
  id: int
  anchors: list[list[float]] = []
  positions: list[list[float]] = []
  fit_method: str = "anchor_polyline_v1"
  visibility: str = "direct"


class SpatialMapRequest(BaseModel):
  schema_version: int = 1
  profile_id: str = "default"
  coordinate_space: str = "front_projection_uv"
  camera_resolution: list[int] = [1280, 720]
  visible_strips: list[int] = []
  strips: list[SpatialMapStripEntry] = []
  bounds: dict = {"x_min": 0.0, "x_max": 1.0, "y_min": 0.0, "y_max": 1.0}


def create_router(deps, require_auth, broadcast_state) -> APIRouter:
  router = APIRouter(prefix="/api/setup", tags=["setup"])

  def _get_setup_service():
    if not hasattr(deps, 'setup_session_service') or deps.setup_session_service is None:
      raise HTTPException(503, "Setup service not available")
    return deps.setup_session_service

  @router.get("/installation")
  async def get_installation():
    """Return the active installation config."""
    svc = _get_setup_service()
    return svc.installation.to_dict()

  @router.post("/session/start")
  async def start_session(auth=Depends(require_auth)):
    """Start a new setup session, snapshotting live context."""
    svc = _get_setup_service()
    try:
      session = svc.start_session()
    except ValueError as e:
      raise HTTPException(409, str(e))
    return SetupSessionStartResponse(
      session_id=session.session_id,
      snapshot_scene=session.snapshot.current_scene,
      snapshot_blackout=session.snapshot.blackout,
      strip_count=len(session.staged_installation.strips),
    )

  @router.get("/session/status")
  async def get_session_status():
    """Get current setup session status."""
    svc = _get_setup_service()
    session = svc.get_session()
    if session is None:
      return SetupSessionStatusResponse(active=False)
    return SetupSessionStatusResponse(
      active=True,
      session_id=session.session_id,
      active_pattern=session.active_pattern,
      staged_strip_count=len(session.staged_installation.strips),
    )

  @router.put("/session/installation")
  async def update_staged_installation(
    req: InstallationUpdateRequest,
    auth=Depends(require_auth),
  ):
    """Update staged strip rows in the active session."""
    svc = _get_setup_service()
    session = svc.get_session()
    if session is None or session.session_id != req.session_id:
      raise HTTPException(404, "No matching active session")
    updates = [u.model_dump(exclude_none=True) for u in req.strips]
    staged = svc.update_staged_installation(updates)
    return staged.to_dict()

  @router.post("/session/pattern")
  async def run_setup_pattern(
    req: SetupPatternRequest,
    auth=Depends(require_auth),
  ):
    """Run a session-scoped setup pattern."""
    svc = _get_setup_service()
    session = svc.get_session()
    if session is None or session.session_id != req.session_id:
      raise HTTPException(404, "No matching active session")

    from ...setup.patterns import generate_setup_pattern, SetupPatternEffect
    color = tuple(req.color[:3]) if len(req.color) >= 3 else (255, 255, 255)
    frame = generate_setup_pattern(
      mode=req.mode,
      targets=req.targets,
      color=color,
      all_others=req.all_others,
      use_compiled_color_order=req.use_compiled_color_order,
    )
    # Inject a static frame effect into the renderer so LEDs actually show the pattern
    pattern_effect = SetupPatternEffect(frame)
    deps.renderer.current_effect = pattern_effect
    deps.render_state.blackout = False
    svc.run_pattern({'mode': req.mode, 'targets': req.targets})
    return {"status": "pattern_active", "mode": req.mode}

  @router.post("/session/cancel")
  async def cancel_session(auth=Depends(require_auth)):
    """Cancel the active session and restore the prior live context."""
    svc = _get_setup_service()
    try:
      snapshot = svc.cancel()
    except ValueError as e:
      raise HTTPException(404, str(e))
    await broadcast_state()
    return {
      "status": "cancelled",
      "restored_scene": snapshot.current_scene,
      "restored_blackout": snapshot.blackout,
    }

  @router.post("/session/commit")
  async def commit_session(
    req: SetupCommitRequest,
    auth=Depends(require_auth),
  ):
    """Validate, persist, compile, and hot-apply the staged installation."""
    svc = _get_setup_service()
    session = svc.get_session()
    if session is None or session.session_id != req.session_id:
      raise HTTPException(404, "No matching active session")
    try:
      result = svc.commit(broadcast_state=broadcast_state)
    except ValueError as e:
      raise HTTPException(422, str(e))
    await broadcast_state()
    return result

  @router.get("/spatial-map")
  async def get_spatial_map():
    """Return the current spatial map if present."""
    if hasattr(deps, 'spatial_map') and deps.spatial_map is not None:
      return deps.spatial_map.to_dict()
    return {"status": "no_spatial_map"}

  @router.post("/spatial-map")
  async def save_spatial_map_route(req: SpatialMapRequest, auth=Depends(require_auth)):
    """Save a solved spatial map atomically."""
    from ...config.spatial_map import save_spatial_map, _parse_spatial_map
    spatial_map = _parse_spatial_map(req.model_dump())
    svc = _get_setup_service()
    save_spatial_map(spatial_map, svc.config_dir)
    if hasattr(deps, 'spatial_map'):
      deps.spatial_map = spatial_map
    return {"status": "saved", "profile_id": spatial_map.profile_id}

  @router.post("/rgb-order/analyze")
  async def analyze_rgb_order(auth=Depends(require_auth)):
    """Analyze strip capture set for RGB order detection.

    Accepts multipart/form-data with dark_frame, red_frame, green_frame,
    blue_frame images plus session_id and strip_id fields.
    This is a placeholder that will be expanded with file upload handling.
    """
    svc = _get_setup_service()
    if svc.get_session() is None:
      raise HTTPException(409, "No active setup session")
    # Full implementation requires multipart file upload parsing
    # For now, return the expected shape so the API contract exists
    raise HTTPException(501, "RGB order analysis requires image upload — use multipart/form-data")

  @router.post("/geometry/analyze")
  async def analyze_geometry(auth=Depends(require_auth)):
    """Analyze geometry capture batch for strip position detection.

    Accepts multipart/form-data with capture frames and phase metadata.
    """
    svc = _get_setup_service()
    if svc.get_session() is None:
      raise HTTPException(409, "No active setup session")
    raise HTTPException(501, "Geometry analysis requires image upload — use multipart/form-data")

  @router.post("/geometry/solve")
  async def solve_geometry(req: GeometrySolveRequest, auth=Depends(require_auth)):
    """Solve or validate a front-projection geometry fit from anchor observations."""
    svc = _get_setup_service()
    if svc.get_session() is None:
      raise HTTPException(409, "No active setup session")
    if req.image_width < 1 or req.image_height < 1:
      raise HTTPException(422, "image_width and image_height must be >= 1")
    from ...setup.geometry import (
      fit_strip_from_anchors, AnchorObservation,
    )
    fits = []
    for strip_data in req.strips:
      anchors = [
        AnchorObservation(
          strip_id=strip_data.strip_id,
          anchor_index=a.anchor_index,
          centroid_x=a.centroid_x,
          centroid_y=a.centroid_y,
          brightness=a.brightness,
        )
        for a in strip_data.anchors
      ]
      fit = fit_strip_from_anchors(
        strip_data.strip_id,
        anchors,
        strip_data.installed_led_count,
        req.image_width,
        req.image_height,
      )
      fits.append({
        'strip_id': fit.strip_id,
        'passed': fit.passed,
        'fit_method': fit.fit_method,
        'anchor_count': len(fit.anchors),
        'position_count': len(fit.positions),
      })
    return {'fits': fits}

  return router
