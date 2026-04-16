"""Pydantic request/response models for the API."""

from typing import Optional
from pydantic import BaseModel


class SceneRequest(BaseModel):
    effect: str
    params: Optional[dict] = None


class BrightnessConfigRequest(BaseModel):
    manual_cap: Optional[float] = None
    auto_enabled: Optional[bool] = None
    location: Optional[dict] = None
    solar: Optional[dict] = None


class BlackoutRequest(BaseModel):
    enabled: bool


class FPSRequest(BaseModel):
    value: int


class SceneSaveRequest(BaseModel):
    name: str
    effect: str
    params: dict = {}


class TestPatternRequest(BaseModel):
    pattern: str


class AudioConfigRequest(BaseModel):
    device_index: Optional[int] = None
    sensitivity: Optional[float] = None  # legacy: applies to all bands equally
    gain: Optional[float] = None
    bass_sensitivity: Optional[float] = None
    mid_sensitivity: Optional[float] = None
    treble_sensitivity: Optional[float] = None


class StripConfigRequest(BaseModel):
    channel: Optional[int] = None
    offset: Optional[int] = None
    direction: Optional[str] = None
    led_count: Optional[int] = None
    color_order: Optional[str] = None
