"""Compatibility shim: phased pointer motion moved to perception (Phase 9)."""

from __future__ import annotations

from artemis.perception.motion import (  # noqa: F401
    MAX_GAIN,
    MIN_GAIN,
    MotionCalibration,
    MotionConfig,
    Phase,
    choose_phase,
)

__all__ = ["MAX_GAIN", "MIN_GAIN", "MotionCalibration", "MotionConfig", "Phase", "choose_phase"]
