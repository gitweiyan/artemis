"""Measure the pointer's real gain on the rig, instead of guessing it.

iOS accelerates the pointer: measured 1.2 device px per HID unit for small moves and up to
~2.1 for large ones (2026-10-21). ``ARTEMIS_IOS_POINTER_GAIN`` is only a starting value —
the tap loop estimates the ratio online — but a *measured* starting value saves correction
steps, and the spread between a small-move gain and a large-move gain is the number that
tells an operator whether this rig accelerates hard.

The procedure needs nothing but the pieces the tap loop already uses: software home, a
known relative move, a frame, a detection. It reports per-axis gain for each probe size,
plus one suggested value for the config.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from statistics import median

__all__ = [
    "CalibrationResult",
    "GainSample",
    "calibrate_pointer",
    "fit_gain",
]


@dataclass(frozen=True)
class GainSample:
    """One probe: the moves issued, and the displacement they produced (frame px)."""

    dx: int
    dy: int
    observed_x: float
    observed_y: float

    @property
    def magnitude(self) -> int:
        return max(abs(self.dx), abs(self.dy))

    def gain(self, axis: str) -> float | None:
        issued = self.dx if axis == "x" else self.dy
        observed = self.observed_x if axis == "x" else self.observed_y
        if abs(issued) < 5:
            return None
        ratio = observed / issued
        return ratio if 0.2 <= ratio <= 5.0 else None


@dataclass(frozen=True)
class CalibrationResult:
    """What the probes measured, ready to print and to put into the environment."""

    samples: tuple[GainSample, ...] = ()
    small_gain: float | None = None
    large_gain: float | None = None
    suggested: tuple[float, float] | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def acceleration_ratio(self) -> float | None:
        """How much harder a large move pulls than a small one (>1 means acceleration)."""
        if not self.small_gain or not self.large_gain:
            return None
        return self.large_gain / self.small_gain

    def env_line(self) -> str:
        if self.suggested is None:
            return "ARTEMIS_IOS_POINTER_GAIN=<unmeasured>"
        gain_x, gain_y = self.suggested
        return f"ARTEMIS_IOS_POINTER_GAIN={gain_x:.2f},{gain_y:.2f}"

    def describe(self) -> str:
        if self.suggested is None:
            return "calibration failed: " + ("; ".join(self.notes) or "no usable pointer")
        lines = [
            f"gain: suggested={self.suggested[0]:.2f},{self.suggested[1]:.2f}"
            f" (small {self.small_gain or float('nan'):.2f},"
            f" large {self.large_gain or float('nan'):.2f},"
            f" acceleration×{self.acceleration_ratio or float('nan'):.2f})",
            f"env: {self.env_line()}",
        ]
        lines.extend(f"note: {note}" for note in self.notes)
        return "\n".join(lines)


def fit_gain(samples: list[GainSample], axis: str) -> float | None:
    """The median per-axis ratio (robust to one bad detection)."""
    ratios = [gain for sample in samples if (gain := sample.gain(axis)) is not None]
    return median(ratios) if ratios else None


def _band(samples: list[GainSample], low: int, high: int) -> list[GainSample]:
    return [sample for sample in samples if low <= sample.magnitude <= high]


async def calibrate_pointer(
    controller, *, probe_moves: tuple[int, ...] = (25, 75), repeats: int = 2
) -> CalibrationResult:
    """Probe the pointer with known moves and report the measured gain.

    ``controller`` is a ``SessionTapController``: it owns the pointer session (moves and
    the belief), the capture and the detector, which is everything the measurement needs.
    """
    session = controller.session
    capture = controller.capture
    detector = controller.detector
    width, height = session.frame_size
    notes: list[str] = []
    samples: list[GainSample] = []

    if capture is None or detector is None:
        return CalibrationResult(
            notes=("this controller has no capture/detector to calibrate with",)
        )

    if not session.connected:
        session.connect()

    def locate():
        frame = capture.grab_latest()
        pointer = detector.detect(
            frame.image,
            near=(session.position[0] / width, session.position[1] / height),
        )
        return pointer

    for probe in probe_moves:
        for _repeat in range(max(1, repeats)):
            session.home()
            await asyncio.to_thread(lambda: None)  # yield between serial round-trips
            start = locate()
            if start is None:
                notes.append(f"pointer not visible after home (probe {probe})")
                continue
            session.observe(start.x * width, start.y * height)
            # Diagonal probes exercise both axes in one round-trip.
            session.move(probe, probe)
            end = locate()
            if end is None:
                notes.append(f"pointer not visible after a {probe}-unit move")
                continue
            samples.append(
                GainSample(
                    dx=probe,
                    dy=probe,
                    observed_x=end.x * width - start.x * width,
                    observed_y=end.y * height - start.y * height,
                )
            )

    if not samples:
        return CalibrationResult(notes=tuple(notes) or ("no pointer was ever seen",))

    small_band = [sample for sample in samples if sample.magnitude <= 40]
    large_band = [sample for sample in samples if sample.magnitude > 40]
    small_x, small_y = fit_gain(small_band, "x"), fit_gain(small_band, "y")
    large_x, large_y = fit_gain(large_band, "x"), fit_gain(large_band, "y")

    # The tap loop starts from one number and learns the rest, so prefer the band that a
    # typical correction move falls into (the small one); fall back to whatever measured.
    chosen_x = small_x or large_x or fit_gain(samples, "x")
    chosen_y = small_y or large_y or fit_gain(samples, "y")
    small = None
    large = None
    if small_x is not None and small_y is not None:
        small = (small_x + small_y) / 2
    if large_x is not None and large_y is not None:
        large = (large_x + large_y) / 2
    if small and large and large / small > 1.35:
        notes.append(
            f"this rig accelerates: a {max(probe_moves)}-unit move pulls"
            f" {large / small:.2f}x harder than a 25-unit one, so the loop must stay closed"
        )
    if notes:
        notes.append("re-run after changing phone, capture card or pointer speed")

    return CalibrationResult(
        samples=tuple(samples),
        small_gain=small,
        large_gain=large,
        suggested=(chosen_x, chosen_y) if chosen_x and chosen_y else None,
        notes=tuple(notes),
    )
