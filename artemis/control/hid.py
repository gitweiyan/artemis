"""ESP32 HID control as a standard async control sink."""

from __future__ import annotations

import asyncio

from artemis.control.base import ActionResult
from artemis.devices.capabilities import CapabilitySet

#: Where a drag starts, as a fraction of the frame height. iOS reads a downward drag
#: from the very top as Control Center and an upward drag from the bottom as Home (it
#: leaves the app), and a pointer parked on an edge has no room to drag at all. An
#: upward drag therefore starts low in the frame, a downward one high, and both stay
#: clear of the edge they move away from.
DRAG_START_RATIO = 0.65


class HidControlSink:
    """Relative mouse/keyboard sink. `tap_abs` is a target API, not absolute HID.

    ``tap_abs`` receives a **device-pixel** target (the action layer converts the
    agent's 0–1000 coordinates against the screen size). The relative-pointer
    controller, however, compares its target against the pointer it detects in the
    **captured frame**, which is the cropped phone picture — a different pixel grid.
    Supplying ``geometry`` makes the sink translate between the two; without it the
    target is passed through unchanged (a sink whose caller already speaks frame
    pixels, and every unit test that drives the controller directly).
    """

    capabilities = CapabilitySet(
        supports_relative_pointer=True,
        requires_pointer_precheck=True,
        supports_text_input=True,
    )

    def __init__(self, session, controller, *, geometry=None):
        self.session = session
        self.controller = controller
        self.geometry = geometry
        self._control = session.control

    def _frame_point(self, x: int, y: int) -> tuple[int, int]:
        """Device pixels -> capture-frame pixels (identity when no geometry is known)."""
        if self.geometry is None:
            return int(x), int(y)
        fx, fy = self.geometry.screen_to_frame(int(x), int(y))
        return int(fx), int(fy)

    @staticmethod
    def _metrics(result, *, target: tuple[int, int], frame_target: tuple[int, int]) -> dict:
        """The evidence a graph can read: where the pointer was, how far off, how old."""
        pointer = getattr(result, "pointer", None)
        metrics: dict[str, float | str] = {
            "target_x": float(target[0]),
            "target_y": float(target[1]),
            "frame_x": float(frame_target[0]),
            "frame_y": float(frame_target[1]),
            "attempts": float(getattr(result, "attempts", 0) or 0),
        }
        if pointer is not None:
            metrics["pointer_x"] = float(getattr(pointer, "x", 0.0)) * 1000.0
            metrics["pointer_y"] = float(getattr(pointer, "y", 0.0)) * 1000.0
            metrics["pointer_confidence"] = float(getattr(pointer, "confidence", 0.0))
        error = getattr(result, "error_px", None)
        if isinstance(error, (int, float)):
            metrics["error_px"] = float(error)
        age = getattr(result, "frame_age_s", None)
        if isinstance(age, (int, float)):
            metrics["frame_age_s"] = float(age)
        crop_change = getattr(result, "crop_change", None)
        if isinstance(crop_change, (int, float)):
            metrics["crop_change"] = float(crop_change)
        return metrics

    async def tap_abs(self, x: int, y: int) -> ActionResult:
        frame_x, frame_y = self._frame_point(x, y)
        result = await asyncio.to_thread(self.controller.tap, frame_x, frame_y)
        metrics = self._metrics(result, target=(x, y), frame_target=(frame_x, frame_y))
        if result.ok:
            return ActionResult(True, "tap_abs", detail=result.message, metrics=metrics)
        return ActionResult(
            False,
            "tap_abs",
            result.incident or "no_effect",
            result.message,
            metrics,
        )

    async def tap_rel(self, dx: int, dy: int) -> ActionResult:
        await asyncio.to_thread(self.session.move, dx, dy)
        return ActionResult(True, "tap_rel")

    @staticmethod
    def _frame_size(session) -> tuple[int, int] | None:
        size = getattr(session, "frame_size", None)
        if isinstance(size, (tuple, list)) and len(size) == 2:
            width, height = int(size[0]), int(size[1])
            if width > 0 and height > 0:
                return width, height
        return None

    def _place_for_drag(self, start: tuple[int, int], dy: int) -> None:
        """Re-anchor the pointer and move it to a safe drag start (blocking I/O).

        The relative pointer is a blind cursor: pressing where it happens to be
        starts the gesture wherever the last action left it — an upward drag from
        the top edge does nothing, and an edge start is read by iOS as a system
        gesture. Software home puts the pointer at a true origin; one relative move
        from there lands it at a point that clears both edges and leaves room for
        the gesture. The requested start only supplies x: the configured gain is a
        coarse estimate, so a point far from the edges is safer than the exact one.
        """
        size = self._frame_size(self.session)
        if size is None:
            return
        if not getattr(self.session, "connected", True):
            connect = getattr(self.session, "connect", None)
            if connect is None:
                return
            connect()
        home = getattr(self.session, "home", None)
        move = getattr(self.session, "move", None)
        if home is None or move is None:
            return
        width, height = size
        x = min(max(int(start[0]), 0), width - 1)
        if dy < 0:
            y = round(height * DRAG_START_RATIO)  # upward: room above, clear of Home
        elif dy > 0:
            y = round(height * (1.0 - DRAG_START_RATIO))  # downward: clear of Control Center
        else:
            y = round(height / 2)  # horizontal drag: the middle
        home()
        gain_x = float(getattr(self.session, "gain_x", 1.0)) or 1.0
        gain_y = float(getattr(self.session, "gain_y", 1.0)) or 1.0
        move(round(x / gain_x), round(y / gain_y))

    async def swipe(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        duration_ms: int = 800,
    ) -> ActionResult:
        """An iOS swipe is a drag: place the pointer, press, walk it, release.

        The pointer must be *at* the drag start before the button goes down. A
        pointer parked on an edge has no room to drag, and a drag that starts on
        the top/bottom edge is read by iOS as a system gesture (Control Center /
        Home) instead of an in-app scroll — so the pointer is re-anchored and put
        at a safe start for the direction (see :meth:`_place_for_drag`).

        ``swipe_mouse`` prefers the firmware's paced ``mswipe`` and falls back to a
        host-driven press/move/release, always releasing the button.
        """
        frame_start = self._frame_point(start_x, start_y)
        frame_end = self._frame_point(end_x, end_y)
        dx = frame_end[0] - frame_start[0]
        dy = frame_end[1] - frame_start[1]
        try:
            await asyncio.to_thread(self._place_for_drag, frame_start, dy)
        except Exception as exc:  # noqa: BLE001 - a device failure is an incident
            return ActionResult(False, "swipe", "swipe_failed", str(exc))
        swipe = getattr(self._control, "swipe_mouse", None)
        try:
            if swipe is not None:
                await asyncio.to_thread(swipe, dx, dy, duration_ms=duration_ms)
            else:

                def run():
                    self._control.mouse_down()
                    self._control.mouse_move(dx, dy)
                    self._control.mouse_up()

                await asyncio.to_thread(run)
        except Exception as exc:  # noqa: BLE001 - a device failure is an incident
            return ActionResult(False, "swipe", "swipe_failed", str(exc))
        return ActionResult(True, "swipe")

    async def input_text(self, text: str) -> ActionResult:
        await asyncio.to_thread(self._control.type_text, text)
        return ActionResult(True, "input_text")

    async def press_key(self, key) -> ActionResult:
        press = getattr(self._control, "press_named_key", None)
        if press is None:
            await asyncio.to_thread(self._control._send_with_response, f"key {key}")
            return ActionResult(True, "press_key")
        try:
            await asyncio.to_thread(press, key)
        except ValueError as exc:
            return ActionResult(False, "press_key", "unsupported_key", str(exc))
        return ActionResult(True, "press_key")
