"""Absolute reflex gestures on a phone that only accepts a relative pointer.

A reflex skill (Tetris and friends) works in absolute device pixels: "tap the rotate
button at (540, 1800)". Android can do that with a digitizer; iOS cannot — the only
input is a relative mouse pointer behind AssistiveTouch. This adapter is the seam:

* **tap**  -> travel the pointer to the target on the calibrated belief, verify it in a
  live frame, click, and require the screen to change. A refusal is an error, never a
  silent success, so the runtime's failure counter sees it.
* **swipe** -> position the pointer, press, move in int8-sized segments, release.

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
        remaining = self.controller.travel(frame_x, frame_y)
        if remaining is not None and float(remaining) > _DRAG_START_TOLERANCE_PX:
            raise RuntimeError(
                f"pointer is {float(remaining):.0f}px from the drag start"
                f" ({start_x:.0f}, {start_y:.0f}); refusing to press there"
            )

        target_x, target_y = self._frame_point(end_x, end_y)
        dx = target_x - self.session.position[0]
        dy = target_y - self.session.position[1]
        control = self._hid_control()
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
