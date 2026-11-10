"""Relative-pointer adapter state (ADR-010 Phase 4.3).

The pointer belief (position/gain), its move planner and the detector/tracker that
correct it belong to one adapter, not to the generic DeviceSession. The adapter is
kept in ``DeviceSession.adapter_state`` and the legacy ``session.pointer`` property
reads it for compatibility.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["RelativePointerAdapter"]


@dataclass(frozen=True)
class RelativePointerAdapter:
    session: Any
    controller: Any
    capture: Any
    geometry: Any
    detector: Any = None
    tracker: Any = None

    @property
    def position(self) -> tuple[float, float]:
        return self.session.position

    @property
    def gain(self) -> tuple[float, float]:
        return self.session.gain_x, self.session.gain_y

    @property
    def frame_size(self) -> tuple[int, int]:
        return self.session.frame_size
