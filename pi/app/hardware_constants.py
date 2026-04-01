"""
Hardware constants — single source of truth.

Reads hardware.yaml at import time. All geometry constants used across
the Python codebase should be imported from this module.
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
  # Fallback to hardcoded defaults matching the spec
  return {
    'pillar': {
      'strips': 10,
      'leds_per_strip': 172,
      'total_leds': 1720,
      'channels': {'count': 5, 'leds_per_channel': 344},
      'color_order': 'GRB',
    }
  }


_hw = _load_hardware_config()
_pillar = _hw.get('pillar', {})
_channels = _pillar.get('channels', {})

# --- Exported constants ---
STRIPS = _pillar.get('strips', 10)
LEDS_PER_STRIP = _pillar.get('leds_per_strip', 172)
TOTAL_LEDS = _pillar.get('total_leds', STRIPS * LEDS_PER_STRIP)
CHANNELS = _channels.get('count', 5)
LEDS_PER_CHANNEL = _channels.get('leds_per_channel', LEDS_PER_STRIP * 2)
COLOR_ORDER = _pillar.get('color_order', 'GRB')

# Render dimensions
OUTPUT_WIDTH = STRIPS          # 10 columns
HEIGHT = LEDS_PER_STRIP        # 172 rows
INTERNAL_WIDTH = 40            # supersampled render width (config-overridable)
