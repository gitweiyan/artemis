"""Absolute reflex gestures on a phone whose only input is an overlay pointer.

A reflex skill (Tetris and friends) works in absolute device pixels: "tap the rotate
button at (540, 1800)". On iOS the input is a mouse pointer behind AssistiveTouch, and
the pointer has an **absolute** report (`amove`) as well as a relative one. This adapter
is the seam:

* **tap**  -> the controller's absolute path: one `amove`, one `aclick`, verified by the
  target region's change. No pointer detection is involved.
* **swipe** -> one `amove` to the drag's start, then the firmware's `mswipe` (which
  presses through the absolute report). No dead reckoning, no gain.

Both gestures therefore share one coordinate story: absolute placement, then act. The
relative-belief implementation survives only for sinks without an absolute report
(bench, AirPlay, an old board) — mixing the two is what iOS punishes by dropping the
next absolute report (measured 5/5 on 2026-11-04).

It implements the runtime's one-method ``Control`` protocol, so a skill never learns
about dead reckoning, prechecks or frames.
"""

from __future__ import annotations

import time

from artemis.devices.geometry import ScreenGeometry
from artemis.reflex.contract import Gesture
from third_party.mobile_use.utils.logger import get_logger

logger = get_logger(__name__)

__all__ = ["PointerControl"]

#: The firmware casts each ``move`` axis through ``int8_t``; larger values wrap.
_MAX_MOVE = 127
#: How far the pointer may be from the drag's start point before the drag is refused.
_DRAG_START_TOLERANCE_PX = 24.0


class PointerControl:
    """Drive a relative-pointer phone from reflex gestures (device pixels in)."""

    def __init__(self, controller, *, session=None, geometry: ScreenGeometry | None = None):
        self.controller = controller
        self.session = session or getattr(controller, "session", None)
        self.geometry = geometry

    # --- geometry ---------------------------------------------------------------

    def _frame_point(self, x: float, y: float) -> tuple[float, float]:
        """Device pixels (skill coordinates) -> capture-frame pixels (pointer space)."""
        if self.geometry is None:
            return float(x), float(y)
        return tuple(float(value) for value in self.geometry.screen_to_frame(int(x), int(y)))

    def _hid_control(self):
        control = getattr(self.session, "control", None)
        if control is None:
            raise RuntimeError("no relative pointer control behind this session")
        return control

    # --- Control protocol -------------------------------------------------------

    def preflight(self):
        control = self._hid_control()
        probe = getattr(control, "preflight", None)
        return probe() if callable(probe) else {}

    def dispatch(self, gesture: Gesture) -> float:
        """Perform one skill gesture; return its latency in milliseconds."""
        started = time.monotonic()
        if gesture.kind == "tap":
            self._tap(gesture)
        elif gesture.kind == "swipe":
            self._swipe(gesture)
        else:
            raise ValueError(f"pointer control cannot express gesture {gesture.kind!r}")
        return (time.monotonic() - started) * 1000.0

    # --- gestures ---------------------------------------------------------------

    def _tap(self, gesture: Gesture) -> None:
        ((x, y),) = gesture.points
        frame_x, frame_y = self._frame_point(x, y)
        result = self.controller.tap(frame_x, frame_y)
        if not getattr(result, "ok", False):
            incident = getattr(result, "incident", None) or "tap_failed"
            message = getattr(result, "message", "") or ""
            raise RuntimeError(f"tap at ({x:.0f}, {y:.0f}) refused ({incident}): {message}")

    def _swipe(self, gesture: Gesture) -> None:
        (start_x, start_y), (end_x, end_y) = gesture.points
        frame_x, frame_y = self._frame_point(start_x, start_y)
        target_x, target_y = self._frame_point(end_x, end_y)
        control = self._hid_control()
        if callable(getattr(control, "mouse_move_abs", None)):
            self._swipe_absolutely(
                control, frame_x, frame_y, target_x, target_y, gesture.duration_ms
            )
            return
        self._swipe_on_belief(control, frame_x, frame_y, target_x, target_y)

    def _swipe_absolutely(self, control, start_x, start_y, end_x, end_y, duration_ms) -> None:
        """Place the press with one absolute report, then let the board drag.

        `amove` puts the press exactly on the start point (no gain, no dead reckoning),
        and `mswipe` walks the pointer from there. This is the same unification as the
        agent's tap: a relative move right before an absolute report is dropped by iOS,
        so a skill gesture must not depend on the relative belief either.
        """
        self.session.move_abs(round(start_x), round(start_y))
        dx, dy = round(end_x - start_x), round(end_y - start_y)
        swipe = getattr(control, "swipe_mouse", None)
        if callable(swipe):
            swipe(dx, dy, duration_ms=max(1, int(duration_ms)))
        else:  # an absolute report but no mswipe: press, walk, release from here
            control.mouse_down()
            try:
                control.mouse_move(dx, dy)  # segmented by the control, int8-safe
            finally:
                control.mouse_up()
        self.session.observe(end_x, end_y)

    def _swipe_on_belief(self, control, frame_x, frame_y, target_x, target_y) -> None:
        """Compatibility drag for a sink without an absolute report."""
        remaining = self.controller.travel(frame_x, frame_y)
        if remaining is not None and float(remaining) > _DRAG_START_TOLERANCE_PX:
            raise RuntimeError(
                f"pointer is {float(remaining):.0f}px from the drag start"
                f" ({frame_x:.0f}, {frame_y:.0f} in frame pixels); refusing to press there"
            )

        dx = target_x - self.session.position[0]
        dy = target_y - self.session.position[1]
        control.mouse_down()
        try:
            while abs(dx) > 0.5 or abs(dy) > 0.5:
                step_x = max(-_MAX_MOVE, min(_MAX_MOVE, round(dx)))
                step_y = max(-_MAX_MOVE, min(_MAX_MOVE, round(dy)))
                self.session.move(step_x, step_y)
                dx -= step_x
                dy -= step_y
        finally:
            # A failed move must not leave the phone with the button held down.
            control.mouse_up()
