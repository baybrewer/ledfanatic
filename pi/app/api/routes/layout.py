"""
Layout API routes — get, apply, validate, test-segment.

All mutations: validate -> compile -> send CONFIG to Teensy -> ACK gate -> commit.
"""

import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...layout import (
    load_layout, save_layout, validate_layout, compile_layout,
    output_config_list, pack_frame,
)
from ...layout.schema import parse_layout, BrightnessCal

logger = logging.getLogger(__name__)


class LayoutApplyRequest(BaseModel):
    """Full layout config as JSON (same structure as layout.yaml)."""
    version: int = 1
    matrix: dict
    outputs: list[dict]


def create_router(deps, require_auth) -> APIRouter:
    router = APIRouter(prefix="/api/layout", tags=["layout"])

    @router.get("/")
    async def get_layout():
        """Return current layout config + compiled stats."""
        config = deps.layout_config
        compiled = deps.compiled_layout
        if config is None:
            return {"error": "No layout loaded", "outputs": []}
        return {
            "version": config.version,
            "matrix": {
                "width": config.matrix.width,
                "height": config.matrix.height,
                "origin": config.matrix.origin,
            },
            "outputs": [
                {
                    "id": o.id,
                    "channel": o.channel,
                    "chipset": o.chipset,
                    "color_order": o.color_order,
                    "max_pixels": o.max_pixels,
                    "segments": [_serialize_segment(s) for s in o.segments],
                }
                for o in config.outputs
            ],
            "compiled": {
                "width": compiled.width if compiled else 0,
                "height": compiled.height if compiled else 0,
                "total_mapped": compiled.total_mapped if compiled else 0,
                "output_sizes": output_config_list(compiled) if compiled else [0] * 8,
            },
        }

    @router.post("/apply", dependencies=[Depends(require_auth)])
    async def apply_layout(req: LayoutApplyRequest):
        """Replace entire layout config."""
        try:
            staged = parse_layout(req.model_dump())
        except (ValueError, KeyError) as e:
            raise HTTPException(422, detail=str(e))

        errors = validate_layout(staged)
        if errors:
            raise HTTPException(422, detail=errors)

        compiled = compile_layout(staged)
        oc = output_config_list(compiled)

        config_ok = await deps.transport.send_config(oc)
        if not config_ok:
            raise HTTPException(502, detail="Teensy rejected CONFIG or timed out")

        # ACK received — commit
        deps.layout_config = staged
        deps.compiled_layout = compiled
        deps.renderer.apply_layout(compiled, staged)
        save_layout(staged, deps.config_dir)

        logger.info(f"Layout applied: {compiled.width}x{compiled.height}, {compiled.total_mapped} LEDs")
        return {"status": "ok", "width": compiled.width, "height": compiled.height, "total_mapped": compiled.total_mapped}

    @router.post("/validate")
    async def validate_layout_endpoint(req: LayoutApplyRequest):
        """Validate without applying."""
        try:
            staged = parse_layout(req.model_dump())
        except (ValueError, KeyError) as e:
            return {"valid": False, "errors": [str(e)]}
        errors = validate_layout(staged)
        return {"valid": len(errors) == 0, "errors": errors}

    @router.post("/test-segment/{seg_id}", dependencies=[Depends(require_auth)])
    async def test_segment(seg_id: str):
        """Light a single segment with gradient for identification (5 seconds)."""
        compiled = deps.compiled_layout
        if compiled is None:
            raise HTTPException(404, "No layout loaded")
        if seg_id not in deps.renderer._segment_positions:
            raise HTTPException(404, f"Segment '{seg_id}' not found")
        deps.renderer.set_test_strip(seg_id, duration=5.0)
        return {"status": "ok", "segment": seg_id}

    @router.post("/test-segments", dependencies=[Depends(require_auth)])
    async def test_segments_identify():
        """Light ALL segments, each with a unique color matching the UI swatch."""
        if deps.compiled_layout is None:
            raise HTTPException(404, "No layout loaded")
        deps.renderer.set_test_identify("segment_identify", duration=10.0)
        return {"status": "ok", "mode": "segment_identify"}

    @router.post("/test-strips", dependencies=[Depends(require_auth)])
    async def test_strips_identify():
        """Light each output channel a uniform color (one color per strip)."""
        if deps.compiled_layout is None:
            raise HTTPException(404, "No layout loaded")
        deps.renderer.set_test_identify("strip_identify", duration=10.0)
        return {"status": "ok", "mode": "strip_identify"}

    @router.post("/test-off", dependencies=[Depends(require_auth)])
    async def test_off():
        """Cancel any active test pattern."""
        deps.renderer.set_test_strip(None)
        return {"status": "ok"}

    # --- Config Save/Load ---

    @router.get("/configs")
    async def list_configs():
        """List saved layout configs."""
        import os, json
        saves_dir = deps.config_dir / "layout_saves"
        if not saves_dir.exists():
            return {"configs": []}
        configs = []
        for f in sorted(saves_dir.glob("*.yaml")):
            meta_file = f.with_suffix('.meta.json')
            meta = {}
            if meta_file.exists():
                try:
                    meta = json.loads(meta_file.read_text())
                except Exception:
                    pass
            configs.append({
                "filename": f.name,
                "name": meta.get("name", f.stem),
                "date": meta.get("date", ""),
            })
        configs.sort(key=lambda c: c["date"], reverse=True)
        return {"configs": configs}

    @router.post("/configs/save", dependencies=[Depends(require_auth)])
    async def save_config(req: dict):
        """Save current layout.yaml as a named config."""
        import shutil, json
        from datetime import datetime
        name = (req.get("name") or "").strip()
        if not name:
            raise HTTPException(400, "Name is required")
        saves_dir = deps.config_dir / "layout_saves"
        saves_dir.mkdir(exist_ok=True)
        # Sanitize filename
        safe_name = "".join(c if c.isalnum() or c in '-_ ' else '' for c in name).strip().replace(' ', '_')
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{timestamp}_{safe_name}.yaml"
        # Copy current layout.yaml
        src = deps.config_dir / "layout.yaml"
        if not src.exists():
            raise HTTPException(404, "No layout.yaml to save")
        shutil.copy2(src, saves_dir / filename)
        # Write metadata
        meta = {"name": name, "date": datetime.now().strftime("%Y-%m-%d %H:%M")}
        (saves_dir / f"{filename}.meta.json").write_text(json.dumps(meta))
        logger.info(f"Saved layout config: {filename} ({name})")
        return {"status": "ok", "filename": filename}

    @router.post("/configs/load", dependencies=[Depends(require_auth)])
    async def load_config(req: dict):
        """Load a saved config, replacing current layout.yaml."""
        import shutil
        filename = req.get("filename", "")
        saves_dir = deps.config_dir / "layout_saves"
        src = saves_dir / filename
        if not src.exists():
            raise HTTPException(404, f"Config '{filename}' not found")
        dst = deps.config_dir / "layout.yaml"
        shutil.copy2(src, dst)
        # Reload and apply
        try:
            new_config = load_layout(deps.config_dir)
            errors = validate_layout(new_config)
            if errors:
                raise HTTPException(400, f"Loaded config has errors: {'; '.join(errors)}")
            new_compiled = compile_layout(new_config)
            oc = output_config_list(new_compiled)
            ok = await deps.transport.send_config(oc)
            if not ok:
                logger.warning("CONFIG NAK after loading saved config")
            deps.layout_config = new_config
            deps.compiled_layout = new_compiled
            deps.renderer.apply_layout(new_compiled, new_config)
            logger.info(f"Loaded layout config: {filename}")
            return {"status": "ok"}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, f"Failed to apply loaded config: {e}")

    @router.delete("/configs/{filename}", dependencies=[Depends(require_auth)])
    async def delete_config(filename: str):
        """Delete a saved config."""
        saves_dir = deps.config_dir / "layout_saves"
        target = saves_dir / filename
        meta = saves_dir / f"{filename}.meta.json"
        if not target.exists():
            raise HTTPException(404, f"Config '{filename}' not found")
        target.unlink()
        if meta.exists():
            meta.unlink()
        logger.info(f"Deleted layout config: {filename}")
        return {"status": "ok"}

    @router.post("/probe/{strip}/{led}", dependencies=[Depends(require_auth)])
    async def probe_led(strip: int, led: int):
        """Light a single LED by strip (channel) and wire position. For debugging."""
        compiled = deps.compiled_layout
        if compiled is None:
            raise HTTPException(404, "No layout loaded")
        size = compiled.output_sizes.get(strip, 0)
        if led < 0 or led >= size:
            raise HTTPException(400, f"LED {led} out of range for strip {strip} (0-{size-1})")
        # Set probe mode on renderer
        deps.renderer.set_probe(strip, led)
        mapping = compiled.reverse_lut.get(strip, {}).get(led)
        return {
            "status": "ok",
            "strip": strip,
            "led": led,
            "mapped_to": {"x": mapping[0], "y": mapping[1]} if mapping else None,
        }

    # --- Brightness Calibration ---

    @router.post("/calibrate/preview", dependencies=[Depends(require_auth)])
    async def calibrate_preview(req: dict):
        """Set all segments to a test color at a specific brightness level."""
        color = req.get('color', 'r')
        level = float(req.get('level', 0.5))
        raw_muls = req.get('multipliers', {})
        multipliers = {}
        for k, v in raw_muls.items():
            multipliers[k] = float(v)
        deps.renderer.set_calibrate_preview(color, level, multipliers)
        return {'status': 'ok'}

    @router.get("/calibrate/data")
    async def get_calibration_data():
        """Get current brightness calibration for all segments."""
        result = {}
        for output in deps.layout_config.outputs:
            for seg in output.segments:
                key = f"{output.id}:{seg.id}"
                cal = getattr(seg, 'brightness_cal', None)
                if cal and cal != BrightnessCal():
                    result[key] = {
                        'r': list(cal.r),
                        'g': list(cal.g),
                        'b': list(cal.b),
                    }
                else:
                    result[key] = {
                        'r': [1.0, 1.0, 1.0],
                        'g': [1.0, 1.0, 1.0],
                        'b': [1.0, 1.0, 1.0],
                    }
        return {'calibration': result}

    @router.post("/calibrate/save", dependencies=[Depends(require_auth)])
    async def calibrate_save(req: dict):
        """Save brightness calibration data to layout.yaml."""
        import yaml as _yaml
        cal_data = req.get('calibration', {})
        if not cal_data:
            raise HTTPException(400, "No calibration data")

        # Load current layout yaml
        layout_path = deps.config_dir / "layout.yaml"
        with open(layout_path) as f:
            raw = _yaml.safe_load(f)

        # Update brightness_cal for each segment
        for output_raw in raw.get('outputs', []):
            for seg_raw in output_raw.get('segments', []):
                key = f"{output_raw['id']}:{seg_raw['id']}"
                if key in cal_data:
                    seg_raw['brightness_cal'] = cal_data[key]

        # Write back
        with open(layout_path, 'w') as f:
            _yaml.safe_dump(raw, f, default_flow_style=False, sort_keys=False)

        # Reload and recompile layout
        new_config = load_layout(deps.config_dir)
        new_compiled = compile_layout(new_config)
        deps.layout_config = new_config
        deps.compiled_layout = new_compiled
        deps.renderer.apply_layout(new_compiled, new_config)

        # Clear calibrate preview
        deps.renderer.set_test_strip(None)

        logger.info(f"Saved brightness calibration for {len(cal_data)} segments")
        return {'status': 'ok', 'segments': len(cal_data)}

    return router


def _serialize_segment(seg) -> dict:
    from ...layout.schema import ExplicitSegment
    if isinstance(seg, ExplicitSegment):
        d = {
            "id": seg.id,
            "type": "explicit",
            "points": [{"x": p[0], "y": p[1]} for p in seg.points],
            "physical_offset": seg.physical_offset,
            "enabled": seg.enabled,
        }
        if seg.color_order:
            d["color_order"] = seg.color_order
        return d
    d = {
        "id": seg.id,
        "type": "linear",
        "start": {"x": seg.start[0], "y": seg.start[1]},
        "direction": seg.direction,
        "length": seg.length,
        "physical_offset": seg.physical_offset,
        "enabled": seg.enabled,
    }
    if seg.color_order:
        d["color_order"] = seg.color_order
    return d
