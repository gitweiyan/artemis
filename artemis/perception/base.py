"""Perception contract shared by app automation, UVC, and camera sources."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from artemis.devices.capabilities import CapabilitySet


@dataclass(frozen=True)
class Frame:
    image: bytes
    captured_at: float
    width: int
    height: int
    source: str
    health: str = "ok"
    frame_id: str | None = None
    geometry_revision: str | None = None


@dataclass(frozen=True)
class Target:
    label: str
    box: tuple[float, float, float, float]
    confidence: float
    source: str
    frame_id: str | None = None
    transform_id: str | None = None


@dataclass(frozen=True)
class TextBox:
    text: str
    box: tuple[float, float, float, float]
    confidence: float = 1.0


@dataclass(frozen=True)
class UIElement:
    text: str
    bounds: tuple[int, int, int, int] | None = None
    resource_id: str = ""


@runtime_checkable
class PerceptionSource(Protocol):
    capabilities: CapabilitySet

    async def snapshot(self) -> Frame: ...

    async def ui_tree(self) -> list[UIElement] | None: ...

    async def ocr(self, frame: Frame) -> list[TextBox] | None: ...

    async def locate(self, frame: Frame, target: Target) -> Target | None: ...
