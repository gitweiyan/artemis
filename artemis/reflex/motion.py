"""Phased pointer motion: how far to move, and how many HID units that is.

The relative pointer has no absolute position and iOS accelerates it differently at
different speeds, so a single pixel-per-unit gain cannot serve both a long travel and a
fine correction. This module owns the two pure decisions of that movement — which phase
a segment belongs to, and how to convert its frame-pixel displacement into HID units —
with one gain per phase, learned from observations. It performs no I/O and knows no
session; ``SessionTapController`` does the moving and the observing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

__all__ = ["MotionCalibration", "MotionConfig", "Phase", "choose_phase"]

#: A gain outside this band is a mis-detection or a broken transport, not the rig.
MIN_GAIN = 0.3
MAX_GAIN = 4.0


class Phase(str, Enum):
    """How far this segment should travel, relative to the target's effective width."""

    BALLISTIC = "ballistic"
    APPROACH = "approach"
    FINE = "fine"


@dataclass(frozen=True)
class MotionConfig:
    """Thresholds and per-phase caps. Distances are capture-frame pixels."""

    #: A typical tappable target's width (frame px). The phase boundaries are multiples
    #: of it, because "far" is only meaningful relative to what is being aimed at.
    target_width_px: float = 96.0
    #: A distance above 4W is ballistic; above 1.5W is approach; the rest is fine.
    ballistic_factor: float = 4.0
    approach_factor: float = 1.5
    #: Fraction of the *remaining* distance each phase tries to cover in one segment.
    #: The proposal's 75/55/30 assumes cheap video observations; here one observation is
    #: a ~0.9 s frame grab, so fine corrections must close most of the gap per step or a
    #: tap costs 7-8 frames. Every segment is still observed and re-planned.
    ratios: dict[Phase, float] = field(
        default_factory=lambda: {
            Phase.BALLISTIC: 0.80,
            Phase.APPROACH: 0.75,
            Phase.FINE: 0.70,
        }
    )
    #: Hard cap on one segment, in HID units. Esp32HidControl.mouse_move splits this into
    #: <=127-unit reports and waits for each ack, so the cap is per segment, not per report.
    max_units: dict[Phase, int] = field(
        default_factory=lambda: {
            Phase.BALLISTIC: 300,
            Phase.APPROACH: 120,
            Phase.FINE: 20,
        }
    )
    #: Segments per travel before the controller gives up (with an incident, no click).
    max_segments: int = 12
    #: A segment smaller than this teaches nothing about the gain: the pointer's own
    #: rounding dominates the observed displacement, which is how the fine phase drifted
    #: to ~0.85 px/unit on the rig (bench, 2026-11-03).
    min_units_for_calibration: int = 4
    #: Below this expected displacement a segment is not worth sending.
    min_segment_px: float = 4.0


def choose_phase(distance_px: float, config: MotionConfig) -> Phase:
    """Which phase covers a travel of ``distance_px`` to a target of this width."""
    width = max(1.0, float(config.target_width_px))
    # The boundaries are the brief's contract: 4W itself is still approach, 1.5W itself is
    # already approach (only below it is fine).
    if float(distance_px) > width * float(config.ballistic_factor):
        return Phase.BALLISTIC
    if float(distance_px) >= width * float(config.approach_factor):
        return Phase.APPROACH
    return Phase.FINE


class MotionCalibration:
    """One pixels-per-HID-unit pair per phase, learned from observed displacements.

    Every phase starts at ``base`` (the session's calibrated gain). ``update`` blends a
    measured ratio into the phase that produced the segment, so a fast ballistic move and
    a slow fine correction do not average the rig's acceleration into one number.
    """

    def __init__(self, base: tuple[float, float], *, ema: float = 0.4):
        gx, gy = float(base[0]), float(base[1])
        if gx <= 0 or gy <= 0:
            raise ValueError(f"gain must be positive, got {base!r}")
        self.ema = min(1.0, max(0.0, float(ema)))
        self._gains: dict[Phase, tuple[float, float]] = {phase: (gx, gy) for phase in Phase}

    def gain(self, phase: Phase) -> tuple[float, float]:
        return self._gains[phase]

    def units(self, phase: Phase, dx_px: float, dy_px: float) -> tuple[int, int]:
        """The HID delta that moves the pointer by ``(dx_px, dy_px)`` in this phase."""
        gx, gy = self._gains[phase]
        return round(float(dx_px) / gx), round(float(dy_px) / gy)

    def update(
        self,
        phase: Phase,
        units: tuple[int, int],
        observed_px: tuple[float, float],
    ) -> None:
        """Blend a measured ``observed_px`` displacement for ``units`` into ``phase``."""
        gx, gy = self._gains[phase]
        blended = [gx, gy]
        for index, (unit, observed) in enumerate(zip(units, observed_px)):
            if not unit:
                continue  # no ratio to learn on an axis that did not move
            ratio = float(observed) / float(unit)
            if not (MIN_GAIN <= ratio <= MAX_GAIN):
                continue  # a mis-detection, not a gain
            blended[index] = min(
                MAX_GAIN,
                max(MIN_GAIN, self.ema * ratio + (1.0 - self.ema) * blended[index]),
            )
        self._gains[phase] = (blended[0], blended[1])
