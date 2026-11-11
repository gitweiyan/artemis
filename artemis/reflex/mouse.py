"""Compatibility shim: pointer planning moved to adapters (Phase 9).

New code should import :mod:`artemis.adapters.relative_pointer`; this path is
kept for Reflex skills/tests until the Phase 9 cleanup window.
"""

from __future__ import annotations

from artemis.adapters.relative_pointer import (  # noqa: F401
    DEFAULT_TAP_TOLERANCE_PX,
    MotionReport,
    MouseController,
    PointerSession,
    SessionTapController,
    TapResult,
)

__all__ = [
    "DEFAULT_TAP_TOLERANCE_PX",
    "MotionReport",
    "MouseController",
    "PointerSession",
    "SessionTapController",
    "TapResult",
]
