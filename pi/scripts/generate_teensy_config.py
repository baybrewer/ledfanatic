#!/usr/bin/env python3
"""
Generate teensy/firmware/include/config.h from pi/config/hardware.yaml.

This ensures the Teensy compile-time constants stay in sync with
the Python-side SSOT in hardware.yaml. Run after changing hardware.yaml:

    python3 pi/scripts/generate_teensy_config.py

The generated config.h preserves all non-geometry sections (protocol,
timing, color order, firmware info) and only updates the LED geometry
defines to match hardware.yaml.
"""

import sys
from pathlib import Path

import yaml


def main():
    repo_root = Path(__file__).resolve().parent.parent.parent
    hw_path = repo_root / "pi" / "config" / "hardware.yaml"
    config_h_path = repo_root / "teensy" / "firmware" / "include" / "config.h"

    if not hw_path.exists():
        print(f"ERROR: {hw_path} not found", file=sys.stderr)
        sys.exit(1)

    with open(hw_path) as f:
        hw = yaml.safe_load(f)

    pillar = hw['pillar']
    channels = pillar['channels']

    strips = pillar['strips']
    leds_per_strip = pillar['leds_per_strip']
    channel_count = channels['count']
    leds_per_channel = channels['leds_per_channel']
    total_outputs = 8  # OctoWS2811 always addresses 8

    if not config_h_path.exists():
        print(f"ERROR: {config_h_path} not found", file=sys.stderr)
        sys.exit(1)

    content = config_h_path.read_text()

    # Replace geometry defines (defaults — runtime config is dynamic via CONFIG packet)
    import re
    replacements = {
        'DEFAULT_LEDS_PER_STRIP': str(leds_per_channel),
        'DEFAULT_ACTIVE_OUTPUTS': str(channel_count),
        'LEDS_PER_PHYSICAL': str(leds_per_strip),
        'PHYSICAL_STRIPS': str(strips),
    }

    for define_name, value in replacements.items():
        pattern = rf'(#define\s+{define_name}\s+)\d+'
        if not re.search(pattern, content):
            print(f"WARNING: #define {define_name} not found in config.h",
                  file=sys.stderr)
        content = re.sub(pattern, rf'\g<1>{value}', content)

    config_h_path.write_text(content)
    print(f"Updated {config_h_path}")
    print(f"  DEFAULT_LEDS_PER_STRIP = {leds_per_channel}")
    print(f"  DEFAULT_ACTIVE_OUTPUTS = {channel_count}")
    print(f"  LEDS_PER_PHYSICAL      = {leds_per_strip}")
    print(f"  PHYSICAL_STRIPS        = {strips}")


if __name__ == "__main__":
    main()
