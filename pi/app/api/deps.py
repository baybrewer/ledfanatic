"""Shared dependency container for route modules."""

from dataclasses import dataclass

from ..core.renderer import Renderer, RenderState
from ..core.state import StateManager
from ..core.brightness import BrightnessEngine
from ..transport.usb import TeensyTransport
from ..media.manager import MediaManager
from ..audio.analyzer import AudioAnalyzer


@dataclass
class AppDeps:
    transport: TeensyTransport
    renderer: Renderer
    render_state: RenderState
    state_manager: StateManager
    brightness_engine: BrightnessEngine
    media_manager: MediaManager
    audio_analyzer: AudioAnalyzer
    max_upload_bytes: int = 50 * 1024 * 1024
