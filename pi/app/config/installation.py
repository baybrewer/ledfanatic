"""
Installation config — strip-to-channel mapping.

Manages installation.yaml: per-strip channel assignment, direction,
offset, LED count, and color order. Changes apply live.
"""

import logging
import os
import tempfile
from dataclasses import dataclass, field, asdict
from pathlib import Path

import yaml

from ..hardware_constants import (
  STRIPS, LEDS_PER_STRIP, CHANNELS, CONTROLLER_WIRE_ORDER,
  ACTIVE_OUTPUTS,
)

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 3
MAX_LEDS_PER_CHANNEL = 1100

VALID_COLOR_ORDERS = frozenset(["RGB", "RBG", "GRB", "GBR", "BRG", "BGR"])
VALID_DIRECTIONS = frozenset(["bottom_to_top", "top_to_bottom"])


@dataclass
class StripMapping:
  id: int
  channel: int = 0
  offset: int = 0
  direction: str = "bottom_to_top"
  led_count: int = 172
  color_order: str = "BGR"

  def validate(self) -> list[str]:
    errors = []
    if not 0 <= self.channel < 8:
      errors.append(f"Strip {self.id}: channel {self.channel} out of range [0, 7]")
    if self.offset < 0:
      errors.append(f"Strip {self.id}: offset must be >= 0")
    if not 1 <= self.led_count <= MAX_LEDS_PER_CHANNEL:
      errors.append(f"Strip {self.id}: led_count {self.led_count} out of range [1, {MAX_LEDS_PER_CHANNEL}]")
    if self.offset + self.led_count > MAX_LEDS_PER_CHANNEL:
      errors.append(f"Strip {self.id}: offset + led_count ({self.offset + self.led_count}) exceeds {MAX_LEDS_PER_CHANNEL}")
    if self.color_order not in VALID_COLOR_ORDERS:
      errors.append(f"Strip {self.id}: invalid color_order '{self.color_order}'")
    if self.direction not in VALID_DIRECTIONS:
      errors.append(f"Strip {self.id}: invalid direction '{self.direction}'")
    return errors


@dataclass
class StripInstallation:
  schema_version: int = SCHEMA_VERSION
  strips: list[StripMapping] = field(default_factory=list)

  def validate(self) -> list[str]:
    errors = []
    for s in self.strips:
      errors.extend(s.validate())
    by_channel: dict[int, list[StripMapping]] = {}
    for s in self.strips:
      by_channel.setdefault(s.channel, []).append(s)
    for ch, strips in by_channel.items():
      for i, a in enumerate(strips):
        for b in strips[i + 1:]:
          a_end = a.offset + a.led_count
          b_end = b.offset + b.led_count
          if a.offset < b_end and b.offset < a_end:
            errors.append(
              f"Overlap on channel {ch}: strip {a.id} [{a.offset}:{a_end}] "
              f"and strip {b.id} [{b.offset}:{b_end}]"
            )
    return errors

  def to_dict(self) -> dict:
    return {
      'schema_version': self.schema_version,
      'strips': [asdict(s) for s in self.strips],
    }

  def strips_api_list(self) -> list[dict]:
    return [asdict(s) for s in self.strips]

  def next_id(self) -> int:
    if not self.strips:
      return 0
    return max(s.id for s in self.strips) + 1

  def renumber_ids(self):
    for i, s in enumerate(self.strips):
      s.id = i


def synthesize_default_strips() -> StripInstallation:
  strips = []
  for i in range(STRIPS):
    strips.append(StripMapping(
      id=i,
      channel=i // 2,
      offset=(i % 2) * LEDS_PER_STRIP,
      direction='bottom_to_top' if i % 2 == 0 else 'top_to_bottom',
      led_count=LEDS_PER_STRIP,
      color_order=CONTROLLER_WIRE_ORDER,
    ))
  return StripInstallation(strips=strips)


def migrate_v1_to_strips(data: dict) -> StripInstallation:
  strips = []
  for s in data.get('strips', []):
    if not s.get('enabled', True):
      continue
    strips.append(StripMapping(
      id=len(strips),
      channel=s.get('output_channel', 0),
      offset=s.get('output_slot', 0) * LEDS_PER_STRIP,
      direction=s.get('direction', 'bottom_to_top'),
      led_count=s.get('installed_led_count', LEDS_PER_STRIP),
      color_order=s.get('color_order', CONTROLLER_WIRE_ORDER),
    ))
  return StripInstallation(schema_version=SCHEMA_VERSION, strips=strips)


def migrate_v2_to_strips(data: dict) -> StripInstallation:
  strips = []
  for ch in data.get('channels', []):
    led_count = ch.get('led_count', 0)
    if led_count == 0:
      continue
    ch_num = ch.get('channel', 0)
    color_order = ch.get('color_order', CONTROLLER_WIRE_ORDER)
    half = led_count // 2
    strips.append(StripMapping(
      id=len(strips),
      channel=ch_num,
      offset=0,
      direction='bottom_to_top',
      led_count=half,
      color_order=color_order,
    ))
    strips.append(StripMapping(
      id=len(strips),
      channel=ch_num,
      offset=half,
      direction='top_to_bottom',
      led_count=led_count - half,
      color_order=color_order,
    ))
  return StripInstallation(schema_version=SCHEMA_VERSION, strips=strips)


def load_installation(config_dir: Path) -> StripInstallation:
  path = config_dir / "installation.yaml"
  if path.exists():
    with open(path) as f:
      data = yaml.safe_load(f) or {}

    version = data.get('schema_version', 0)
    if version >= 3:
      return _parse_strips(data)
    if version == 2:
      logger.info("Migrating v2 channel installation.yaml to v3 strip format")
      inst = migrate_v2_to_strips(data)
      save_installation(inst, config_dir)
      return inst
    if 'strips' in data:
      logger.info("Migrating v1 strip installation.yaml to v3 format")
      inst = migrate_v1_to_strips(data)
      save_installation(inst, config_dir)
      return inst

  inst = synthesize_default_strips()
  save_installation(inst, config_dir)
  logger.info("Synthesized default strip installation.yaml")
  return inst


def _parse_strips(data: dict) -> StripInstallation:
  strips = []
  for s in data.get('strips', []):
    strips.append(StripMapping(
      id=s.get('id', len(strips)),
      channel=s.get('channel', 0),
      offset=s.get('offset', 0),
      direction=s.get('direction', 'bottom_to_top'),
      led_count=s.get('led_count', LEDS_PER_STRIP),
      color_order=s.get('color_order', CONTROLLER_WIRE_ORDER),
    ))
  return StripInstallation(
    schema_version=data.get('schema_version', SCHEMA_VERSION),
    strips=strips,
  )


def save_installation(config: StripInstallation, config_dir: Path):
  path = config_dir / "installation.yaml"
  config_dir.mkdir(parents=True, exist_ok=True)
  data = config.to_dict()
  fd, tmp_path = tempfile.mkstemp(dir=str(config_dir), suffix='.tmp')
  try:
    with os.fdopen(fd, 'w') as f:
      yaml.dump(data, f, default_flow_style=False, sort_keys=False)
    os.replace(tmp_path, str(path))
    logger.info("Saved installation.yaml")
  except Exception:
    try:
      os.unlink(tmp_path)
    except OSError:
      pass
    raise
