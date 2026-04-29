"""
Effect catalog service — metadata-backed effect listing.

Provides rich metadata for all registered effects. Service logic only;
route wiring lives in api/routes/effects.py.
"""

import logging
from dataclasses import dataclass
from typing import Optional

from .generative import EFFECTS
from .audio_reactive import AUDIO_EFFECTS
from ..diagnostics.patterns import DIAGNOSTIC_EFFECTS
from .engine.palettes import PALETTE_NAMES

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EffectMeta:
  name: str
  label: str
  group: str
  description: str
  preview_supported: bool = True
  imported: bool = False
  geometry_aware: bool = False
  audio_requires: tuple = ()
  default_params: dict = None
  params: tuple = ()       # param metadata for UI controls
  palettes: tuple = ()     # available palette names
  palette_support: bool = False

  def to_dict(self) -> dict:
    result = {
      'name': self.name,
      'label': self.label,
      'group': self.group,
      'description': self.description,
      'preview_supported': self.preview_supported,
      'imported': self.imported,
      'geometry_aware': self.geometry_aware,
      'audio_requires': list(self.audio_requires),
    }
    if self.params:
      result['params'] = list(self.params)
    if self.palettes:
      result['palettes'] = list(self.palettes)
      result['palette_support'] = True
    return result


def _name_to_label(name: str) -> str:
  """Convert snake_case effect name to a readable label."""
  return name.replace('_', ' ').title()


def _get_description(name: str, effect_cls) -> str:
  """Get effect description from class docstring or generate one."""
  if effect_cls.__doc__:
    first_line = effect_cls.__doc__.strip().split('\n')[0].strip()
    if first_line:
      return first_line
  return f"{_name_to_label(name)} effect"


class EffectCatalogService:
  """Metadata-backed effect catalog."""

  def __init__(self):
    self._catalog: dict[str, EffectMeta] = {}
    self._build_catalog()

  # Generative effects that support palette selection
  _PALETTE_EFFECTS = {
    'vertical_gradient', 'rainbow_rotate', 'plasma', 'twinkle', 'spark',
    'noise_wash', 'color_wipe', 'scanline', 'fire', 'sine_bands',
    'cylinder_rotate', 'solid_color',
  }

  # Explicit display labels for audio_reactive effects — all SR-prefixed,
  # with proper acronym casing (VU not Vu).
  _AUDIO_LABELS = {
    'vu_pulse': 'SR VU Pulse',
    'band_colors': 'SR Band Colors',
    'beat_flash': 'SR Beat Flash',
    'energy_ring': 'SR Energy Ring',
    'spectral_glow': 'SR Spectral Glow',
  }

  # UI slider params for generative effects (same format as imported PARAMS)
  _EFFECT_PARAMS = {
    'solid_color': (
      {'name': 'hue', 'label': 'Hue', 'min': 0, 'max': 1, 'step': 0.01, 'default': 0, 'type': 'slider'},
      {'name': 'speed', 'label': 'Fade Speed', 'min': 0, 'max': 1, 'step': 0.05, 'default': 0, 'type': 'slider'},
    ),
    'vertical_gradient': (
      {'name': 'speed', 'label': 'Speed', 'min': 0, 'max': 0.5, 'step': 0.01, 'default': 0.05, 'type': 'slider'},
    ),
    'rainbow_rotate': (
      {'name': 'speed', 'label': 'Speed', 'min': 0, 'max': 1, 'step': 0.05, 'default': 0.1, 'type': 'slider'},
      {'name': 'scale', 'label': 'Scale', 'min': 0.1, 'max': 5, 'step': 0.1, 'default': 1.0, 'type': 'slider'},
    ),
    'plasma': (
      {'name': 'speed', 'label': 'Speed', 'min': 0, 'max': 2, 'step': 0.1, 'default': 1.0, 'type': 'slider'},
      {'name': 'scale', 'label': 'Scale', 'min': 0.5, 'max': 5, 'step': 0.1, 'default': 2.0, 'type': 'slider'},
    ),
    'twinkle': (
      {'name': 'speed', 'label': 'Speed', 'min': 0, 'max': 2, 'step': 0.1, 'default': 1.0, 'type': 'slider'},
      {'name': 'density', 'label': 'Density', 'min': 0.0001, 'max': 0.05, 'step': 0.0001, 'default': 0.005, 'type': 'slider'},
      {'name': 'darkness', 'label': 'Darkness', 'min': 0, 'max': 1, 'step': 0.05, 'default': 0, 'type': 'slider'},
    ),
    'spark': (
      {'name': 'rate', 'label': 'Rate', 'min': 1, 'max': 30, 'step': 1, 'default': 10, 'type': 'slider'},
      {'name': 'speed', 'label': 'Speed', 'min': 0.5, 'max': 5, 'step': 0.1, 'default': 2.0, 'type': 'slider'},
      {'name': 'brightness', 'label': 'Brightness', 'min': 0.1, 'max': 3, 'step': 0.1, 'default': 1.0, 'type': 'slider'},
    ),
    'noise_wash': (
      {'name': 'speed', 'label': 'Speed', 'min': 0, 'max': 1, 'step': 0.05, 'default': 0.5, 'type': 'slider'},
      {'name': 'scale', 'label': 'Scale', 'min': 1, 'max': 5, 'step': 0.1, 'default': 3.0, 'type': 'slider'},
    ),
    'color_wipe': (
      {'name': 'speed', 'label': 'Speed', 'min': 0.1, 'max': 2, 'step': 0.1, 'default': 0.5, 'type': 'slider'},
    ),
    'scanline': (
      {'name': 'speed', 'label': 'Speed', 'min': 0.1, 'max': 2, 'step': 0.1, 'default': 0.5, 'type': 'slider'},
      {'name': 'width', 'label': 'Width', 'min': 2, 'max': 20, 'step': 1, 'default': 8, 'type': 'slider'},
    ),
    'fire': (
      {'name': 'cooling', 'label': 'Cooling', 'min': 20, 'max': 100, 'step': 5, 'default': 55, 'type': 'slider'},
      {'name': 'sparking', 'label': 'Sparking', 'min': 50, 'max': 200, 'step': 5, 'default': 120, 'type': 'slider'},
    ),
    'framed_fire': (
      {'name': 'cooling', 'label': 'Cooling', 'min': 20, 'max': 80, 'step': 5, 'default': 45, 'type': 'slider'},
      {'name': 'sparking', 'label': 'Sparking', 'min': 50, 'max': 200, 'step': 5, 'default': 140, 'type': 'slider'},
    ),
    'twin_torches': (
      {'name': 'sparking', 'label': 'Sparks', 'min': 30, 'max': 200, 'step': 5, 'default': 140, 'type': 'slider'},
      {'name': 'drip_rate', 'label': 'Fire Drips', 'min': 0.0, 'max': 2.0, 'step': 0.1, 'default': 0.5, 'type': 'slider'},
    ),
    'sine_bands': (
      {'name': 'frequency', 'label': 'Frequency', 'min': 1, 'max': 10, 'step': 0.5, 'default': 3.0, 'type': 'slider'},
      {'name': 'speed', 'label': 'Speed', 'min': 0, 'max': 2, 'step': 0.1, 'default': 1.0, 'type': 'slider'},
    ),
    'cylinder_rotate': (
      {'name': 'speed', 'label': 'Speed', 'min': 0, 'max': 0.5, 'step': 0.01, 'default': 0.1, 'type': 'slider'},
    ),
    'spectral_glow': (
      {'name': 'gain', 'label': 'Gain', 'min': 0.2, 'max': 5.0, 'step': 0.1, 'default': 1.0, 'type': 'slider'},
    ),
    'energy_ring': (
      {'name': 'gain', 'label': 'Gain', 'min': 0.2, 'max': 5.0, 'step': 0.1, 'default': 1.0, 'type': 'slider'},
      {'name': 'speed', 'label': 'Speed', 'min': 0.1, 'max': 5.0, 'step': 0.1, 'default': 2.0, 'type': 'slider'},
    ),
  }

  def _build_catalog(self):
    palette_names = tuple(PALETTE_NAMES)
    for name, cls in EFFECTS.items():
      has_palette = name in self._PALETTE_EFFECTS
      params = self._EFFECT_PARAMS.get(name, ())
      self._catalog[name] = EffectMeta(
        name=name,
        label=_name_to_label(name),
        group='generative',
        description=_get_description(name, cls),
        params=params,
        palettes=palette_names if has_palette else (),
        palette_support=has_palette,
      )
    for name, cls in AUDIO_EFFECTS.items():
      params = self._EFFECT_PARAMS.get(name, ())
      self._catalog[name] = EffectMeta(
        name=name,
        label=self._AUDIO_LABELS.get(name, f"SR {_name_to_label(name)}"),
        group='audio',
        description=_get_description(name, cls),
        params=params,
        audio_requires=('level', 'bass', 'mid', 'high', 'beat'),
      )
    for name, cls in DIAGNOSTIC_EFFECTS.items():
      self._catalog[name] = EffectMeta(
        name=name,
        label=_name_to_label(name),
        group='diagnostic',
        description=_get_description(name, cls),
        preview_supported=False,
      )

  def register_imported(self, name: str, meta: EffectMeta):
    """Register an imported effect with explicit metadata."""
    self._catalog[name] = meta

  def get_catalog(self) -> dict[str, EffectMeta]:
    return dict(self._catalog)

  def get_meta(self, name: str) -> Optional[EffectMeta]:
    return self._catalog.get(name)
