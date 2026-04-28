"""
Compositor -- layer-based effect compositing with blend modes.

All blend modes follow the canonical opacity rule:
  result = alpha_blend(base, mode_fn(base, top), opacity)
This ensures consistent opacity behavior across all modes.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


def _alpha_blend(base: np.ndarray, result: np.ndarray, opacity: float) -> np.ndarray:
  """Apply opacity: mix base and result by opacity factor."""
  if opacity >= 1.0:
    return result
  if opacity <= 0.0:
    return base.copy()
  return (base.astype(np.float32) * (1 - opacity) + result.astype(np.float32) * opacity).astype(np.uint8)


def _mode_normal(base: np.ndarray, top: np.ndarray) -> np.ndarray:
  return top


def _mode_add(base: np.ndarray, top: np.ndarray) -> np.ndarray:
  return np.clip(base.astype(np.uint16) + top.astype(np.uint16), 0, 255).astype(np.uint8)


def _mode_screen(base: np.ndarray, top: np.ndarray) -> np.ndarray:
  a = base.astype(np.float32) / 255.0
  b = top.astype(np.float32) / 255.0
  return ((1.0 - (1.0 - a) * (1.0 - b)) * 255).astype(np.uint8)


def _mode_multiply(base: np.ndarray, top: np.ndarray) -> np.ndarray:
  return (base.astype(np.float32) * top.astype(np.float32) / 255.0).astype(np.uint8)


def _mode_max(base: np.ndarray, top: np.ndarray) -> np.ndarray:
  return np.maximum(base, top)


BLEND_MODES = {
  'normal': _mode_normal,
  'add': _mode_add,
  'screen': _mode_screen,
  'multiply': _mode_multiply,
  'max': _mode_max,
}


def blend(base: np.ndarray, top: np.ndarray, opacity: float, mode: str = 'normal') -> np.ndarray:
  """Apply blend mode then opacity. Canonical rule for all modes."""
  mode_fn = BLEND_MODES.get(mode, _mode_normal)
  blended = mode_fn(base, top)
  return _alpha_blend(base, blended, opacity)


@dataclass
class Layer:
  """One layer in the compositor stack."""
  effect_name: str
  params: dict = field(default_factory=dict)
  opacity: float = 1.0
  blend_mode: str = 'normal'
  enabled: bool = True

  def to_dict(self) -> dict:
    return {
      'effect_name': self.effect_name,
      'params': dict(self.params),
      'opacity': self.opacity,
      'blend_mode': self.blend_mode,
      'enabled': self.enabled,
    }


class Compositor:
  """Renders a stack of layers with blend modes into a single frame."""

  def __init__(self, width: int, height: int, effect_registry: dict,
               effects_config: Optional[dict] = None):
    self.width = width
    self.height = height
    self._effect_registry = effect_registry
    self._effects_config = effects_config or {}
    self.layers: list[Layer] = []
    self._effect_instances: list[Optional[object]] = []
    self.compositor_ms: float = 0.0

  def add_layer(self, layer: Layer, index: Optional[int] = None) -> int:
    if index is None:
      self.layers.append(layer)
      idx = len(self.layers) - 1
    else:
      self.layers.insert(index, layer)
      idx = index
    self._rebuild_instances()
    return idx

  def remove_layer(self, index: int):
    if 0 <= index < len(self.layers):
      self.layers.pop(index)
      self._rebuild_instances()

  def move_layer(self, from_idx: int, to_idx: int):
    if 0 <= from_idx < len(self.layers):
      layer = self.layers.pop(from_idx)
      to_idx = min(to_idx, len(self.layers))
      self.layers.insert(to_idx, layer)
      self._rebuild_instances()

  def update_layer(self, index: int, **kwargs):
    if 0 <= index < len(self.layers):
      layer = self.layers[index]
      for key, value in kwargs.items():
        if key == 'params' and index < len(self._effect_instances):
          instance = self._effect_instances[index]
          if instance:
            instance.update_params(value)
          layer.params.update(value)
        elif hasattr(layer, key):
          setattr(layer, key, value)

  def apply_layout(self, width: int, height: int):
    """Rebuild all effect instances at new dimensions (layout hot-swap)."""
    self.width = width
    self.height = height
    self._rebuild_instances()

  def _create_effect(self, effect_name: str, params: dict) -> Optional[object]:
    """Create an effect instance honoring RENDER_SCALE, YAML param merge,
    and animation_switcher's _effect_registry injection -- same contract as
    renderer._set_scene() so layered mode preserves all existing behavior."""
    cls = self._effect_registry.get(effect_name)
    if cls is None:
      logger.warning(f"Unknown effect: {effect_name}")
      return None
    try:
      # Merge: YAML config defaults < caller params (mirrors renderer._set_scene)
      merged = dict(params)
      if self._effects_config:
        for section in ('effects', 'audio_effects'):
          section_data = self._effects_config.get(section, {})
          if effect_name in section_data:
            yaml_params = section_data[effect_name].get('params', {})
            merged = {**yaml_params, **params}
            break
      # AnimationSwitcher needs effect_registry
      if effect_name == 'animation_switcher':
        merged['_effect_registry'] = self._effect_registry
      # Honor RENDER_SCALE
      width = self.width
      height = self.height
      render_scale = getattr(cls, 'RENDER_SCALE', 1)
      if render_scale > 1:
        width *= render_scale
        height *= render_scale
      instance = cls(width=width, height=height, params=merged)
      instance._compositor_render_scale = render_scale
      return instance
    except Exception as e:
      logger.error(f"Failed to create effect '{effect_name}': {e}")
      return None

  def _rebuild_instances(self):
    self._effect_instances = [
      self._create_effect(layer.effect_name, layer.params)
      for layer in self.layers
    ]

  def render(self, t: float, state) -> np.ndarray:
    start = time.perf_counter()
    result = np.zeros((self.width, self.height, 3), dtype=np.uint8)

    for i, layer in enumerate(self.layers):
      if not layer.enabled or i >= len(self._effect_instances):
        continue
      instance = self._effect_instances[i]
      if instance is None:
        continue
      try:
        frame = instance.render(t, state)
        # Downsample if effect uses RENDER_SCALE > 1
        scale = getattr(instance, '_compositor_render_scale', 1)
        if scale > 1:
          from PIL import Image
          img = Image.fromarray(frame.transpose(1, 0, 2))
          img = img.resize((self.width, self.height), Image.LANCZOS)
          frame = np.array(img).transpose(1, 0, 2)
        # R10-M3: blend inside try so bad shape/dtype doesn't crash compositor
        result = blend(result, frame, layer.opacity, layer.blend_mode)
      except Exception as e:
        logger.error(f"Layer {i} '{layer.effect_name}' crashed: {e}", exc_info=True)
        continue

    self.compositor_ms = (time.perf_counter() - start) * 1000
    return result

  def to_dict(self) -> dict:
    return {'layers': [l.to_dict() for l in self.layers]}

  @staticmethod
  def from_dict(data: dict, width: int, height: int, effect_registry: dict,
                effects_config: Optional[dict] = None) -> 'Compositor':
    comp = Compositor(width, height, effect_registry, effects_config=effects_config)
    for ld in data.get('layers', []):
      comp.add_layer(Layer(
        effect_name=ld['effect_name'],
        params=ld.get('params', {}),
        opacity=ld.get('opacity', 1.0),
        blend_mode=ld.get('blend_mode', 'normal'),
        enabled=ld.get('enabled', True),
      ))
    return comp
