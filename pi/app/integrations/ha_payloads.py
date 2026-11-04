"""
Home Assistant MQTT light (JSON schema) payloads — pure functions.

Translates between OverheadService's model (brightness 0-1, effect names,
rgb tuples, kelvin) and HA's wire format (brightness 0-255, effect labels,
{r,g,b}, color_temp in kelvin). No I/O so it can be tested without a broker.

effects: list of (name, label) from OverheadService.effects().
"""

import json
from collections import namedtuple
from importlib import metadata

from ..core.overhead import OverheadCommand

BRIGHTNESS_SCALE = 255
MIN_KELVIN = 2200
MAX_KELVIN = 6500

DEFAULT_CONFIG = {
  'enabled': False,
  'host': 'homeassistant.local',
  'port': 1883,
  'username': '',
  'password': '',
  'discovery_prefix': 'homeassistant',
  'node_id': 'ledfanatic',
  'name': 'Overhead Light',
}

Topics = namedtuple('Topics', 'config command state availability ha_status')


def resolve_config(cfg: dict | None) -> dict:
  merged = dict(DEFAULT_CONFIG)
  merged.update({k: v for k, v in (cfg or {}).items() if v is not None})
  return merged


def topics(cfg: dict) -> Topics:
  """Every MQTT topic the bridge uses — defined once, derived from cfg."""
  cfg = resolve_config(cfg)
  node, prefix = cfg['node_id'], cfg['discovery_prefix']
  return Topics(
    config=f"{prefix}/light/{node}/overhead/config",
    command=f"{node}/overhead/set",
    state=f"{node}/overhead/state",
    availability=f"{node}/status",
    ha_status=f"{prefix}/status",
  )


def _sw_version() -> str:
  try:
    return metadata.version('ledfanatic')
  except metadata.PackageNotFoundError:
    return 'unknown'


def discovery_payload(cfg: dict, effects: list) -> dict:
  cfg = resolve_config(cfg)
  topic = topics(cfg)
  node = cfg['node_id']
  return {
    'schema': 'json',
    'name': cfg['name'],
    'unique_id': f"{node}_overhead",
    'command_topic': topic.command,
    'state_topic': topic.state,
    'availability_topic': topic.availability,
    'payload_available': 'online',
    'payload_not_available': 'offline',
    'brightness': True,
    'brightness_scale': BRIGHTNESS_SCALE,
    'supported_color_modes': ['color_temp', 'rgb'],
    'color_temp_kelvin': True,
    'min_kelvin': MIN_KELVIN,
    'max_kelvin': MAX_KELVIN,
    'effect': True,
    'effect_list': [label for _, label in effects],
    # HA would otherwise advertise features the renderer can't honour
    'transition': False,
    'flash': False,
    'device': {
      'identifiers': [f"ledfanatic_{node}"],
      'name': 'LED Fanatic',
      'manufacturer': 'LED Fanatic',
      'model': 'Pi + Teensy 4.1 LED controller',
      'sw_version': _sw_version(),
    },
  }


def state_payload(state, effects: list) -> dict:
  labels = dict(effects)
  red, green, blue = (int(c) for c in state.rgb)
  return {
    'state': 'ON' if state.on else 'OFF',
    'brightness': int(round(max(0.0, min(1.0, state.brightness)) * BRIGHTNESS_SCALE)),
    'color_mode': state.color_mode,
    'color_temp': int(state.kelvin),
    'color': {'r': red, 'g': green, 'b': blue},
    'effect': labels.get(state.effect, state.effect),
  }


def _effect_name(value, effects: list) -> str:
  for name, label in effects:
    if value == label:
      return name
  # Accept raw names too so curl/automation debugging doesn't need labels
  for name, _ in effects:
    if value == name:
      return name
  raise ValueError(f"Unknown effect: {value!r}")


def _int(value, field: str) -> int:
  if isinstance(value, bool) or not isinstance(value, (int, float)):
    raise ValueError(f"{field} must be a number, got {value!r}")
  return int(round(value))


def parse_command(payload, effects: list) -> OverheadCommand:
  """HA command JSON (bytes or str) -> OverheadCommand. ValueError if malformed."""
  if isinstance(payload, (bytes, bytearray)):
    try:
      payload = payload.decode('utf-8')
    except UnicodeDecodeError as exc:
      raise ValueError(f"Payload is not UTF-8: {exc}") from exc
  try:
    data = json.loads(payload)
  except (TypeError, json.JSONDecodeError) as exc:
    raise ValueError(f"Malformed JSON: {exc}") from exc
  if not isinstance(data, dict):
    raise ValueError("Command payload must be a JSON object")

  on = None
  if 'state' in data:
    state = str(data['state']).upper()
    if state not in ('ON', 'OFF'):
      raise ValueError(f"state must be ON or OFF, got {data['state']!r}")
    on = state == 'ON'

  brightness = None
  if 'brightness' in data:
    raw = _int(data['brightness'], 'brightness')
    brightness = max(0, min(BRIGHTNESS_SCALE, raw)) / BRIGHTNESS_SCALE

  kelvin = _int(data['color_temp'], 'color_temp') if 'color_temp' in data else None

  rgb = None
  if 'color' in data:
    color = data['color']
    if not isinstance(color, dict) or not all(k in color for k in 'rgb'):
      raise ValueError(f"color must be an object with r, g, b: {color!r}")
    rgb = tuple(max(0, min(255, _int(color[k], f"color.{k}"))) for k in 'rgb')

  effect = _effect_name(data['effect'], effects) if 'effect' in data else None

  return OverheadCommand(on=on, brightness=brightness, effect=effect, kelvin=kelvin, rgb=rgb)
