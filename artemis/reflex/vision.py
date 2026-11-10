"""Compatibility shim: pointer detection moved to Perception (Phase 4.3).

Import :mod:`artemis.perception.pointer_detection` for new code. This module stays
until Phase 9-A so existing Reflex skills and tests keep their import path.
"""

from __future__ import annotations

from artemis.perception.pointer_detection import (  # noqa: F401
    POINTER_RADIUS_PX,
    BluePointerDetector,
    CompositePointerDetector,
    Pointer,
    PointerTracker,
    RedPointerDetector,
    RingShapeDetector,
    changed,
    corridor_roi,
    detect_in_roi,
    frame_scale,
    pointer,
    pointer_radius_px,
)

__all__ = [
    "POINTER_RADIUS_PX",
    "BluePointerDetector",
    "CompositePointerDetector",
    "Pointer",
    "PointerTracker",
    "RedPointerDetector",
    "RingShapeDetector",
    "changed",
    "corridor_roi",
    "detect_in_roi",
    "frame_scale",
    "pointer",
    "pointer_radius_px",
]
