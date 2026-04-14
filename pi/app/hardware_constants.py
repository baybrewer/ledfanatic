"""
Hardware constants — single source of truth.

Reads hardware.yaml at import time. All geometry constants used across
the Python codebase should be imported from this module.

The live color order is BGR. All fallback defaults reflect this.
"""

from pathlib import Path
import yaml


def _load_hardware_config() -> dict:
  """Load hardware.yaml from the config directory."""
  # Try /opt/pillar first (production), then relative to this file (dev)
  for config_dir in [
    Path("/opt/pillar/config"),
    Path(__file__).parent.parent / "config",
  ]:
    path = config_dir / "hardware.yaml"
    if path.exists():
      with open(path) as f:
        return yaml.safe_load(f)
  # Fallback to hardcoded defaults matching current live hardware
  return {
    'pillar': {
      'strips': 10,
      'leds_per_strip': 172,
      'total_leds': 1720,
      'channels': {'count': 5, 'leds_per_channel': 344},
      'color_order': 'BGR',
    },
    'controller': {
      'output_backend': 'octows2811',
      'signal_family': 'ws281x_800khz',
      'controller_wire_order': 'BGR',
      'active_outputs': 5,
      'total_outputs': 8,
      'electrical_leds_per_output': 344,
      'physical_leds_per_strip': 172,
    },
  }


_hw = _load_hardware_config()
_pillar = _hw.get('pillar', {})
_channels = _pillar.get('channels', {})
_controller = _hw.get('controller', {})

# --- Exported pillar geometry constants ---
STRIPS = _pillar.get('strips', 10)
LEDS_PER_STRIP = _pillar.get('leds_per_strip', 172)
TOTAL_LEDS = _pillar.get('total_leds', STRIPS * LEDS_PER_STRIP)
CHANNELS = _channels.get('count', 5)
LEDS_PER_CHANNEL = _channels.get('leds_per_channel', LEDS_PER_STRIP * 2)

# --- Exported controller envelope constants ---
CONTROLLER_WIRE_ORDER = _controller.get('controller_wire_order', 'BGR')
# Legacy alias — CONTROLLER_WIRE_ORDER is the SSOT for color order
COLOR_ORDER = CONTROLLER_WIRE_ORDER
ACTIVE_OUTPUTS = _controller.get('active_outputs', 5)
TOTAL_OUTPUTS = _controller.get('total_outputs', 8)
ELECTRICAL_LEDS_PER_OUTPUT = _controller.get('electrical_leds_per_output', 344)
PHYSICAL_LEDS_PER_STRIP = _controller.get('physical_leds_per_strip', 172)

# Render dimensions
OUTPUT_WIDTH = STRIPS          # 10 columns
HEIGHT = LEDS_PER_STRIP        # 172 rows
INTERNAL_WIDTH = 40            # supersampled render width (config-overridable)
