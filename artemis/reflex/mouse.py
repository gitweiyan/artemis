"""Mouse Controller v2: a target in, relative HID moves + click + verification out.

The controller is deliberately camera-grounded: it never trusts its own dx/dy
integration. Each attempt re-observes the pointer, rejects low-confidence
readings, moves in a coarse step, and clicks only when the click visibly changed
the screen.

Coordinates are normalized to the captured frame (0..1, origin top-left), so a
crop/resolution change does not change a caller's target.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from artemis.reflex.vision import Pointer, changed


@dataclass(frozen=True)
class TapResult:
    ok: bool
    attempts: int
    pointer: Pointer | None
    error_px: float | None
    message: str


class MouseController:
    """Turn ``tap(x, y)`` into the smallest reliable sequence of relative moves."""

    def __init__(
        self,
        control,
        capture,
        tracker,
        *,
        frame_size: tuple[int, int] = (449, 974),
        gain: float = 1.3,
        tolerance_px: float = 8.0,
        max_attempts: int = 3,
        nudge: int = 60,
        max_step: int = 300,
        min_confidence: float = 0.5,
        settle_s: float = 0.04,
    ):
        if gain <= 0:
            raise ValueError("gain must be positive")
        self.control = control
        self.capture = capture
        self.tracker = tracker
        self.frame_size = (int(frame_size[0]), int(frame_size[1]))
        self.gain = float(gain)
        self.tolerance_px = float(tolerance_px)
        self.max_attempts = int(max_attempts)
        self.nudge = int(nudge)
        self.max_step = int(max_step)
        self.min_confidence = float(min_confidence)
        self.settle_s = float(settle_s)

    def tap(self, x: float, y: float) -> TapResult:
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            raise ValueError(f"target must be normalized 0..1, got ({x}, {y})")

        self.control.preflight()
        target = (float(x), float(y))
        expected = (0.5, 0.5)
        last_pointer: Pointer | None = None
        last_error: float | None = None

        for attempt in range(1, self.max_attempts + 1):
            pointer = self._locate(expected)
            last_pointer = pointer
            if pointer is None or pointer.confidence < self.min_confidence:
                self.control.mouse_home()
                expected = (0.5, 0.5)
                continue

            error_x = (target[0] - pointer.x) * self.frame_size[0]
            error_y = (target[1] - pointer.y) * self.frame_size[1]
            last_error = math.hypot(error_x, error_y)
            if max(abs(error_x), abs(error_y)) <= self.tolerance_px:
                before = self.capture.grab_latest()
                self.control.mouse_click()
                after = self.capture.grab_latest()
                if changed(before.image, after.image):
                    return TapResult(
                        True,
                        attempt,
                        pointer,
                        last_error,
                        "clicked; screen changed",
                    )
                continue

            dx = max(-self.max_step, min(self.max_step, round(error_x / self.gain)))
            dy = max(-self.max_step, min(self.max_step, round(error_y / self.gain)))
            if dx == 0 and dy == 0:
                continue
            self.control.mouse_move(dx, dy)
            expected = (
                pointer.x + dx * self.gain / self.frame_size[0],
                pointer.y + dy * self.gain / self.frame_size[1],
            )

        return TapResult(
            False,
            self.max_attempts,
            last_pointer,
            last_error,
            "not converged or click did not change the screen",
        )

    def _locate(self, expected: tuple[float, float]) -> Pointer | None:
        before = self.capture.grab_latest()
        if getattr(self.tracker, "has_template", False):
            detected = self.tracker.detect(before.image)
            if detected is not None:
                return detected

        self.control.mouse_move(self.nudge, 0)
        try:
            if self.settle_s > 0:
                time.sleep(self.settle_s)
            after = self.capture.grab_latest()
            return self.tracker.locate(
                before.image,
                after.image,
                near=expected,
                shift=(self.nudge * self.gain) / self.frame_size[0],
                reach=(0.5, 0.5),
            )
        finally:
            self.control.mouse_move(-self.nudge, 0)


__all__ = ["MouseController", "TapResult"]
