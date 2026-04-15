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

  def _build_catalog(self):
    palette_names = tuple(PALETTE_NAMES)
    for name, cls in EFFECTS.items():
      has_palette = name in self._PALETTE_EFFECTS
      self._catalog[name] = EffectMeta(
        name=name,
        label=_name_to_label(name),
        group='generative',
        description=_get_description(name, cls),
        palettes=palette_names if has_palette else (),
        palette_support=has_palette,
      )
    for name, cls in AUDIO_EFFECTS.items():
      self._catalog[name] = EffectMeta(
        name=name,
        label=_name_to_label(name),
        group='audio',
        description=_get_description(name, cls),
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
