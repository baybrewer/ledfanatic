"""
Canonical effect registry — single source of truth for all effect classes.

Every effect that can be activated via renderer.register_effect() or
benchmarked via bench_effects.py is registered here.
"""

from .generative import EFFECTS as _GENERATIVE
from .audio_reactive import AUDIO_EFFECTS as _AUDIO
from .imported import IMPORTED_EFFECTS as _IMPORTED
from .simulation import SIMULATION_EFFECTS as _SIMULATION
from .fractals import FRACTAL_EFFECTS as _FRACTAL
from .fluids import FLUID_EFFECTS as _FLUIDS
from ..diagnostics.patterns import DIAGNOSTIC_EFFECTS as _DIAGNOSTIC
from .tetris import Tetris, TetrisAutoplay
from .fireworks import SRFireworks
from .scrolltext import ScrollingText
from .switcher import AnimationSwitcher

ALL_EFFECTS: dict[str, type] = {
    **_GENERATIVE,
    **_AUDIO,
    **_DIAGNOSTIC,
    **_IMPORTED,
    **_SIMULATION,
    **_FRACTAL,
    **_FLUIDS,
    'tetris': Tetris,
    'tetris_auto': TetrisAutoplay,
    'sr_fireworks': SRFireworks,
    'scrolling_text': ScrollingText,
    'animation_switcher': AnimationSwitcher,
}
