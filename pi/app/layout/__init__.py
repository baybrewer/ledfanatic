"""LED layout mapping — declarative config, compiled lookup tables, fast packer."""

import logging
import os
import tempfile
from pathlib import Path

import yaml

from .schema import LayoutConfig, MatrixConfig, LinearSegment, ExplicitSegment, BrightnessCal, parse_layout
from .compiler import validate_layout, compile_layout, CompiledLayout, _expand_segment
from .packer import pack_frame, output_config_list

logger = logging.getLogger(__name__)


def load_layout(config_dir: Path) -> LayoutConfig:
    """Load layout.yaml from config directory."""
    path = config_dir / "layout.yaml"
    if not path.exists():
        logger.warning(f"No layout.yaml at {path} — using empty config")
        return LayoutConfig(version=1, matrix=MatrixConfig(width=0, height=0))
    try:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        logger.error(f"Failed to parse {path}: {e}")
        return LayoutConfig(version=1, matrix=MatrixConfig(width=0, height=0))
    return parse_layout(data)


def save_layout(config: LayoutConfig, config_dir: Path) -> None:
    """Atomically save layout config to layout.yaml."""
    path = config_dir / "layout.yaml"
    config_dir.mkdir(parents=True, exist_ok=True)

    data = {
        "version": config.version,
        "matrix": {
            "width": config.matrix.width,
            "height": config.matrix.height,
            "origin": config.matrix.origin,
        },
        "outputs": [],
    }
    for output in config.outputs:
        out_data = {
            "id": output.id,
            "channel": output.channel,
            "chipset": output.chipset,
            "color_order": output.color_order,
            "segments": [],
        }
        for seg in output.segments:
            if isinstance(seg, ExplicitSegment):
                seg_dict = {
                    "id": seg.id,
                    "type": "explicit",
                    "points": [{"x": p[0], "y": p[1]} for p in seg.points],
                    "physical_offset": seg.physical_offset,
                    "enabled": seg.enabled,
                }
                if seg.brightness_cal != BrightnessCal():
                    seg_dict['brightness_cal'] = {
                        'r': list(seg.brightness_cal.r),
                        'g': list(seg.brightness_cal.g),
                        'b': list(seg.brightness_cal.b),
                    }
                out_data["segments"].append(seg_dict)
            else:
                seg_data = {
                    "id": seg.id,
                    "start": {"x": seg.start[0], "y": seg.start[1]},
                    "direction": seg.direction,
                    "length": seg.length,
                    "physical_offset": seg.physical_offset,
                }
                if not seg.enabled:
                    seg_data["enabled"] = False
                if seg.color_order:
                    seg_data["color_order"] = seg.color_order
                if seg.brightness_cal != BrightnessCal():
                    seg_data['brightness_cal'] = {
                        'r': list(seg.brightness_cal.r),
                        'g': list(seg.brightness_cal.g),
                        'b': list(seg.brightness_cal.b),
                    }
                out_data["segments"].append(seg_data)
        data["outputs"].append(out_data)

    fd, tmp_path = tempfile.mkstemp(dir=str(config_dir), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)
        os.replace(tmp_path, str(path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
