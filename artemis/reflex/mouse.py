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
    """Outcome of one tap attempt.

    ``incident`` is the machine-readable reason a failed tap failed:
    ``pointer_missing`` (no verified pointer, so no click), ``pointer_not_centered``
    (the pointer never converged inside the tolerance) or ``no_effect`` (the click was
    dispatched but the screen did not change). Callers must not parse ``message``.
    """

    ok: bool
    attempts: int
    pointer: Pointer | None
    error_px: float | None
    message: str
    incident: str | None = None


class PointerSession:
    """Dead-reckoned pointer state for one iOS mouse connection.

    iOS starts a newly connected pointer at the top-left and keeps clicking at the
    last position even after the cursor hides. ``home`` is verified to re-anchor
    repeatedly, so state is ``origin + calibrated HID deltas``, clamped to the screen.
    UVC is only needed for calibration/correction, not for every tap.
    """

    def __init__(
        self,
        control,
        *,
        frame_size: tuple[int, int] = (449, 974),
        gain: tuple[float, float] = (1.4, 1.4),
        home_settle_s: float = 0.8,
    ):
        if gain[0] <= 0 or gain[1] <= 0:
            raise ValueError("gain must be positive")
        self.control = control
        self.frame_size = (int(frame_size[0]), int(frame_size[1]))
        self.gain_x = float(gain[0])
        self.gain_y = float(gain[1])
        self._x = 0.0
        self._y = 0.0
        self.connected = False
        self.home_settle_s = max(0.0, float(home_settle_s))

    @property
    def position(self) -> tuple[float, float]:
        return self._x, self._y

    def ensure_home_screen(self) -> None:
        """Return to the iOS Home screen without touching the pointer state."""
        if hasattr(self.control, "ios_home"):
            self.control.ios_home()
            if self.home_settle_s > 0:
                time.sleep(self.home_settle_s)

    def connect(self) -> None:
        """Preflight, return to iOS Home, then re-anchor the pointer to the top-left."""
        self.control.preflight()
        self.ensure_home_screen()
        self.control.mouse_home()
        self._x = 0.0
        self._y = 0.0
        self.connected = True

    def home(self) -> None:
        """Re-anchor to the top-left (home is verified to zero repeatedly)."""
        self.control.mouse_home()
        self._x = 0.0
        self._y = 0.0

    def move(self, dx: int, dy: int) -> tuple[float, float]:
        if not self.connected:
            raise RuntimeError("pointer session is not connected; call connect() first")
        self.control.mouse_move(int(dx), int(dy))
        self._x = max(0.0, min(float(self.frame_size[0]), self._x + int(dx) * self.gain_x))
        self._y = max(0.0, min(float(self.frame_size[1]), self._y + int(dy) * self.gain_y))
        return self.position

    def _target(self, x: float, y: float) -> tuple[int, int]:
        width, height = self.frame_size
        if not (0.0 <= x <= width and 0.0 <= y <= height):
            raise ValueError(f"target outside frame {self.frame_size}: ({x}, {y})")
        return round((x - self._x) / self.gain_x), round((y - self._y) / self.gain_y)

    def click(self) -> None:
        """Left-click at the current state position (no move)."""
        if not self.connected:
            raise RuntimeError("pointer session is not connected; call connect() first")
        self.control.mouse_click()

    def tap(self, x: float, y: float) -> tuple[int, int]:
        """Move by the calibrated delta, then click at the current position."""
        if not self.connected:
            raise RuntimeError("pointer session is not connected; call connect() first")
        dx, dy = self._target(x, y)
        if dx or dy:
            self.move(dx, dy)
        self.click()
        return dx, dy

    def observe(self, x: float, y: float) -> None:
        """Correct dead reckoning from a trusted UVC/template reading."""
        self._x = max(0.0, min(float(self.frame_size[0]), float(x)))
        self._y = max(0.0, min(float(self.frame_size[1]), float(y)))

    def calibrate(self, samples: list[tuple[int, int, float, float]]) -> None:
        """Fit gain from ``(dx, dy, observed_x, observed_y)`` samples taken from home."""
        x_pairs = [
            (int(dx), float(observed_x)) for dx, _dy, observed_x, _observed_y in samples if dx
        ]
        y_pairs = [
            (int(dy), float(observed_y)) for _dx, dy, _observed_x, observed_y in samples if dy
        ]
        if not x_pairs or not y_pairs:
            raise ValueError("calibration needs at least one nonzero x and y sample")
        gain_x = sum(dx * observed for dx, observed in x_pairs) / sum(dx * dx for dx, _ in x_pairs)
        gain_y = sum(dy * observed for dy, observed in y_pairs) / sum(dy * dy for dy, _ in y_pairs)
        if gain_x <= 0 or gain_y <= 0:
            raise ValueError("calibration produced a non-positive gain")
        self.gain_x = gain_x
        self.gain_y = gain_y
        self._x = 0.0
        self._y = 0.0


class SessionTapController:
    """Tap with a mandatory pre-click pointer check when a detector is supplied.

    The verified path grabs a frame, detects the pointer, corrects with bounded
    moves, and clicks only when the detected pointer is within ``tolerance_px`` of
    the target. A missing pointer means no click. The legacy dead-reckoning path is
    kept for callers without a capture/detector, but it cannot pre-verify.
    """

    def __init__(
        self,
        session: PointerSession,
        *,
        capture=None,
        detector=None,
        max_attempts: int = 3,
        tolerance_px: float = 8.0,
        max_steps: int = 6,
        max_step: int = 100,
        observe_attempts: int = 3,
        wake_step: int = 60,
        settle_timeout_s: float = 0.6,
        max_frame_age_s: float = 3.0,
    ):
        if detector is not None and capture is None:
            raise ValueError("pre-click pointer verification needs a capture")
        self.session = session
        self.capture = capture
        self.detector = detector
        self.max_attempts = max(1, int(max_attempts))
        self.tolerance_px = float(tolerance_px)
        self.max_steps = max(1, int(max_steps))
        self.max_step = max(1, int(max_step))
        #: One empty frame is not proof the pointer is gone: the AssistiveTouch ring
        #: fades in and out and a frame can predate the move that woke it. Re-observe a
        #: bounded number of times before refusing to click.
        self.observe_attempts = max(1, int(observe_attempts))
        #: iOS hides the AssistiveTouch pointer after a moment of inactivity; a move of
        #: at least this many HID units reveals it again (measured 2026-10-21: 15 units
        #: is too small, 60 is reliable). The correction loop compensates the offset.
        self.wake_step = max(1, int(wake_step))
        #: How long to wait for a frame that reflects the last move. A camera (and the
        #: UVC pipeline) lags the pointer by a frame or two; re-detecting on the pre-move
        #: frame makes the correction loop chase its own tail.
        self.settle_timeout_s = max(0.0, float(settle_timeout_s))
        #: How old a verification frame may be before the tap is refused. A capture path
        #: that froze (a stray process holding the card) otherwise looks like a pointer
        #: that will not move; refusing keeps "no verified pointer, no click" true.
        self.max_frame_age_s = max(0.0, float(max_frame_age_s))

    def _frame_age_s(self, frame) -> float | None:
        """Seconds between capture and now, when the frame can be dated."""
        captured_at = getattr(frame, "captured_at", None)
        if not isinstance(captured_at, (int, float)):
            return None
        return max(0.0, time.monotonic() - float(captured_at))

    def _stale_reason(self, frame) -> str | None:
        age = self._frame_age_s(frame)
        if age is None or self.max_frame_age_s <= 0:
            return None
        if age <= self.max_frame_age_s:
            return None
        return f"verification frame is {age:.1f}s old (budget {self.max_frame_age_s:.1f}s)"

    def _frame_after(self, previous: bytes):
        """A frame that is not the one captured before the last move (bounded wait)."""
        deadline = time.monotonic() + self.settle_timeout_s
        frame = self.capture.grab_latest()
        while not changed(previous, frame.image) and time.monotonic() < deadline:
            frame = self.capture.grab_latest()
        return frame

    def _observe(self, after: bytes | None = None):
        """Grab frames until a pointer is detected, or ``observe_attempts`` run out.

        ``after`` is the image from before the last move: the first frame is then taken
        only once the picture reflects that move.
        """
        width, height = self.session.frame_size
        near = (self.session.position[0] / width, self.session.position[1] / height)
        if after is not None:
            frame = self._frame_after(after)
        else:
            # Advance past whatever was buffered before the move we just made: a queued
            # frame describes the previous pointer position, not this one.
            self.capture.grab_latest()
            frame = self.capture.grab_latest()
        for index in range(self.observe_attempts):
            if index > 0:
                frame = self.capture.grab_latest()
            pointer = self.detector.detect(frame.image, near=near)
            if pointer is not None:
                return frame, pointer
        return frame, None

    def _travel_on_belief(self, x: float, y: float) -> None:
        """Drive the pointer to the target using only the calibrated belief.

        The AssistiveTouch pointer fades within a second, and a UVC grab is slow enough
        that a camera-in-the-loop correction often reads frames that no longer show it
        (measured live 2026-10-21: the loop then chases its own tail and never clicks).
        Relative moves are exactly predictable, so travel is dead reckoning; the frame is
        used to *verify* the arrival, which is the part that must not be blind.
        """
        for _step in range(self.max_steps):
            error_x = x - self.session.position[0]
            error_y = y - self.session.position[1]
            if math.hypot(error_x, error_y) <= self.tolerance_px:
                return
            dx = max(-self.max_step, min(self.max_step, round(error_x / self.session.gain_x)))
            dy = max(-self.max_step, min(self.max_step, round(error_y / self.session.gain_y)))
            if dx == 0 and dy == 0:
                return
            self.session.move(dx, dy)

    def _verified_tap(self, x: float, y: float) -> TapResult:
        width, height = self.session.frame_size
        last_pointer: Pointer | None = None
        last_error: float | None = None

        # A reference frame from before the travel: the verification frame must differ
        # from it, which means it was captured after the moves (the pointer is drawn for
        # only a moment after each move, so a queued frame is useless for this).
        before = self.capture.grab_latest()
        self._travel_on_belief(x, y)

        for attempt in range(1, self.max_attempts + 1):
            frame, pointer = self._observe(after=before.image)
            if pointer is None:
                # Wake the faded pointer with a deliberate move, then look for a frame
                # that reflects it.
                self.session.move(self.wake_step, self.wake_step)
                frame, pointer = self._observe(after=frame.image)
            if pointer is None:
                return TapResult(
                    False,
                    attempt,
                    None,
                    None,
                    "pointer not detected before click; refusing to click",
                    "pointer_missing",
                )
            stale = self._stale_reason(frame)
            if stale is not None:
                return TapResult(
                    False,
                    attempt,
                    pointer,
                    None,
                    f"{stale}; refusing to click on a frame we cannot trust",
                    "stale_frame",
                )
            last_pointer = pointer
            self.session.observe(pointer.x * width, pointer.y * height)
            error = math.hypot(x - self.session.position[0], y - self.session.position[1])
            last_error = error
            if error <= self.tolerance_px:
                before = frame.image
                self.session.click()
                after = self.capture.grab_latest()
                if changed(before, after.image):
                    return TapResult(
                        True,
                        attempt,
                        pointer,
                        error,
                        "pointer verified at target; click changed the screen",
                    )
                return TapResult(
                    False,
                    attempt,
                    pointer,
                    error,
                    "pointer verified at target, but click did not change the screen",
                    "no_effect",
                )
            # The detection disagreed with the belief (gain drift): correct and retry.
            self._travel_on_belief(x, y)

        return TapResult(
            False,
            self.max_attempts,
            last_pointer,
            last_error,
            "pointer did not converge to the target; refusing to click",
            "pointer_not_centered",
        )

    def tap(self, x: float, y: float) -> TapResult:
        if not self.session.connected:
            self.session.connect()
        if self.capture is not None and self.detector is not None:
            return self._verified_tap(x, y)
        # Legacy dead-reckoning path; no pre-click verification is possible here.
        for attempt in range(1, self.max_attempts + 1):
            before = self.capture.grab_latest() if self.capture is not None else None
            deltas = self.session.tap(x, y)
            if self.capture is None:
                return TapResult(
                    True,
                    attempt,
                    None,
                    0.0,
                    f"clicked by pointer state (delta={deltas}); no screen verification",
                )
            after = self.capture.grab_latest()
            if changed(before.image, after.image):
                return TapResult(
                    True,
                    attempt,
                    None,
                    0.0,
                    f"clicked by pointer state (delta={deltas}); screen changed",
                )
            self.session.ensure_home_screen()
            self.session.home()
        return TapResult(
            False,
            self.max_attempts,
            None,
            None,
            "click did not change the screen; pointer was re-anchored",
            "no_effect",
        )


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
            "no_effect",
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


__all__ = ["MouseController", "PointerSession", "SessionTapController", "TapResult"]
