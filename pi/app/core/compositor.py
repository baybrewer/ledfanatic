"""
Compositor -- layer-based effect compositing with blend modes.

All blend modes follow the canonical opacity rule:
  result = alpha_blend(base, mode_fn(base, top), opacity)
This ensures consistent opacity behavior across all modes.
"""

import logging

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
