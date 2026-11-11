"""Shared low-level Reflex Capture/Control DTOs (Phase 9 boundary)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Frame:
    """One captured screen, as the skill's perception wants it."""

    image: bytes
    captured_at: float
    capture_ms: float
    width: int | None = None
    height: int | None = None


@dataclass(frozen=True)
class Gesture:
    """One device interaction a skill can ask for."""

    kind: str
    points: tuple[tuple[float, float], ...]
    duration_ms: int = 120
    description: str = ""

    @staticmethod
    def tap(x: float, y: float, *, description: str = "") -> Gesture:
        return Gesture(kind="tap", points=((float(x), float(y)),), description=description)

    @staticmethod
    def swipe(
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        *,
        duration_ms: int = 120,
        description: str = "",
    ) -> Gesture:
        return Gesture(
            kind="swipe",
            points=((float(x1), float(y1)), (float(x2), float(y2))),
            duration_ms=int(duration_ms),
            description=description,
        )


class Capture(Protocol):
    """What the runtime needs from a frame source (device or recording)."""

    def grab(self) -> Frame:
        """Return one frame; raise :class:`CaptureError` when none is usable."""

    def close(self) -> None:
        """Release whatever the capture holds."""


class Control(Protocol):
    """What the runtime needs from an actuator."""

    def dispatch(self, gesture: Gesture) -> float:
        """Perform one gesture; return its latency in milliseconds."""


class CaptureError(RuntimeError):
    """No usable frame (device gone, decode failed, session dropped)."""

    def __init__(self, message: str, *, incident: str | None = None) -> None:
        super().__init__(message)
        self.incident = incident


__all__ = ["Capture", "CaptureError", "Control", "Frame", "Gesture"]
