"""
Compiled runtime output plan.

Compiles installation config + controller profile into a frozen plan
that the runtime mapper uses for frame packing. Handles per-strip
color order, direction, LED count, and channel slot placement.
"""

from dataclasses import dataclass
from itertools import permutations

from ..hardware_constants import (
  CHANNELS, LEDS_PER_CHANNEL, LEDS_PER_STRIP, STRIPS,
  CONTROLLER_WIRE_ORDER, ACTIVE_OUTPUTS, TOTAL_OUTPUTS,
  ELECTRICAL_LEDS_PER_OUTPUT, PHYSICAL_LEDS_PER_STRIP,
)


@dataclass(frozen=True)
class ControllerProfile:
  output_backend: str = "octows2811"
  signal_family: str = "ws281x_800khz"
  controller_wire_order: str = "BGR"
  active_outputs: int = 5
  total_outputs: int = 8
  electrical_leds_per_output: int = 344
  physical_leds_per_strip: int = 172


@dataclass(frozen=True)
class CompiledStripPlan:
  strip_id: int
  enabled: bool
  logical_order: int
  output_channel: int
  output_slot: int
  output_offset: int
  direction: str
  installed_led_count: int
  color_order: str
  precontroller_swizzle: tuple[int, int, int]


@dataclass(frozen=True)
class CompiledOutputPlan:
  controller: ControllerProfile
  strips: tuple[CompiledStripPlan, ...]
  logical_width: int
  logical_height: int
  channels: int
  leds_per_channel: int


# --- Color order permutation logic ---
#
# Model: Effects produce logical RGB (R at index 0, G at 1, B at 2).
# OctoWS2811 is configured with a color format constant (e.g., WS2811_BGR)
# that tells it what the strip expects. It assumes the Pi sends RGB input
# and reorders bytes to match the strip's native wire protocol.
#
# controller_wire_order = the strip type OctoWS2811 is globally configured for.
# strip_native_order = the actual wire protocol of a specific strip.
#
# When all strips match the controller config, no Pi-side swizzle is needed.
# When a strip differs, the Pi must pre-compensate so that OctoWS2811's
# reorder for the wrong strip type still produces correct output.

_RGB_INDEX = {'R': 0, 'G': 1, 'B': 2}


def _order_to_component_map(order: str) -> tuple[int, int, int]:
  """Map byte positions to RGB component indices for a given order string.

  For 'BGR': position 0 is B(=2), position 1 is G(=1), position 2 is R(=0)
  Returns: (2, 1, 0)
  """
  return tuple(_RGB_INDEX[c] for c in order)


def _invert_perm(perm: tuple[int, int, int]) -> tuple[int, int, int]:
  """Compute the inverse of a permutation."""
  inv = [0, 0, 0]
  for i, v in enumerate(perm):
    inv[v] = i
  return tuple(inv)


def derive_precontroller_swizzle(
  controller_wire_order: str,
  strip_native_order: str,
) -> tuple[int, int, int]:
  """Derive the permutation to apply to logical RGB pixels before sending
  to the controller, so that the strip displays the intended color.

  The OctoWS2811 controller assumes RGB input and reorders to match its
  configured strip type (controller_wire_order). If a strip's actual
  native order differs, we pre-compensate.

  Formula: swizzle[j] = strip_component[ctrl_inverse[j]]
  where ctrl_component maps byte positions to RGB indices for the controller order,
  and strip_component does the same for the strip order.
  """
  ctrl_component = _order_to_component_map(controller_wire_order)
  ctrl_inverse = _invert_perm(ctrl_component)
  strip_component = _order_to_component_map(strip_native_order)
  return tuple(strip_component[ctrl_inverse[j]] for j in range(3))


def simulate_display(
  intended: tuple[int, int, int],
  swizzle: tuple[int, int, int],
  controller_wire_order: str,
  strip_native_order: str,
) -> tuple[int, int, int]:
  """Simulate what color a strip actually displays.

  Pipeline:
  1. Apply precontroller swizzle to intended RGB
  2. OctoWS2811 reorders assuming RGB input → controller_wire_order output
  3. Strip interprets bytes according to its native order
  """
  # Step 1: swizzle
  after_swizzle = tuple(intended[swizzle[i]] for i in range(3))

  # Step 2: OctoWS2811 reorders from RGB input to controller_wire_order output
  ctrl_component = _order_to_component_map(controller_wire_order)
  wire = [0, 0, 0]
  for i in range(3):
    # Output position i gets the ctrl_component[i]-th component of input
    wire[i] = after_swizzle[ctrl_component[i]]

  # Step 3: strip interprets wire bytes according to its native order
  strip_component = _order_to_component_map(strip_native_order)
  displayed = [0, 0, 0]
  for i in range(3):
    displayed[strip_component[i]] = wire[i]

  return tuple(displayed)


# --- Plan compilation ---

def load_controller_profile(hw_config: dict | None = None) -> ControllerProfile:
  """Load controller profile from hardware config or use defaults."""
  if hw_config and 'controller' in hw_config:
    ctrl = hw_config['controller']
    return ControllerProfile(
      output_backend=ctrl.get('output_backend', 'octows2811'),
      signal_family=ctrl.get('signal_family', 'ws281x_800khz'),
      controller_wire_order=ctrl.get('controller_wire_order', CONTROLLER_WIRE_ORDER),
      active_outputs=ctrl.get('active_outputs', ACTIVE_OUTPUTS),
      total_outputs=ctrl.get('total_outputs', TOTAL_OUTPUTS),
      electrical_leds_per_output=ctrl.get('electrical_leds_per_output', ELECTRICAL_LEDS_PER_OUTPUT),
      physical_leds_per_strip=ctrl.get('physical_leds_per_strip', PHYSICAL_LEDS_PER_STRIP),
    )
  return ControllerProfile(
    controller_wire_order=CONTROLLER_WIRE_ORDER,
    active_outputs=ACTIVE_OUTPUTS,
    total_outputs=TOTAL_OUTPUTS,
    electrical_leds_per_output=ELECTRICAL_LEDS_PER_OUTPUT,
    physical_leds_per_strip=PHYSICAL_LEDS_PER_STRIP,
  )


def compile_output_plan(installation, controller: ControllerProfile) -> CompiledOutputPlan:
  """Compile an installation config + controller profile into a frozen output plan."""
  strips = []
  for strip_cfg in installation.strips:
    output_offset = strip_cfg.output_slot * controller.physical_leds_per_strip
    swizzle = derive_precontroller_swizzle(
      controller.controller_wire_order,
      strip_cfg.color_order,
    )
    strips.append(CompiledStripPlan(
      strip_id=strip_cfg.id,
      enabled=strip_cfg.enabled,
      logical_order=strip_cfg.logical_order,
      output_channel=strip_cfg.output_channel,
      output_slot=strip_cfg.output_slot,
      output_offset=output_offset,
      direction=strip_cfg.direction,
      installed_led_count=strip_cfg.installed_led_count,
      color_order=strip_cfg.color_order,
      precontroller_swizzle=swizzle,
    ))

  # Logical dimensions: use max physical strip as height
  logical_height = controller.physical_leds_per_strip
  logical_width = max((s.logical_order for s in strips if s.enabled), default=0) + 1

  return CompiledOutputPlan(
    controller=controller,
    strips=tuple(strips),
    logical_width=logical_width,
    logical_height=logical_height,
    channels=controller.active_outputs,
    leds_per_channel=controller.electrical_leds_per_output,
  )


def compile_channel_plan(installation, controller: ControllerProfile) -> CompiledOutputPlan:
  """Compile a channel-oriented installation into an output plan.

  Each channel becomes one CompiledStripPlan entry. The channel's LED count
  is used directly (no strip pairing).
  """
  strips = []
  for ch in installation.channels:
    if ch.led_count == 0:
      continue
    swizzle = derive_precontroller_swizzle(
      controller.controller_wire_order,
      ch.color_order,
    )
    strips.append(CompiledStripPlan(
      strip_id=ch.channel,
      enabled=True,
      logical_order=ch.channel,
      output_channel=ch.channel,
      output_slot=0,
      output_offset=0,
      direction='bottom_to_top',
      installed_led_count=ch.led_count,
      color_order=ch.color_order,
      precontroller_swizzle=swizzle,
    ))

  # Use hardware dimensions — Teensy always expects fixed channel count and size
  logical_width = max((s.logical_order for s in strips), default=0) + 1 if strips else 0

  return CompiledOutputPlan(
    controller=controller,
    strips=tuple(strips),
    logical_width=logical_width,
    logical_height=controller.electrical_leds_per_output,
    channels=controller.total_outputs,
    leds_per_channel=controller.electrical_leds_per_output,
  )
