"""ESP32 HID control as a standard async control sink."""

from __future__ import annotations

import asyncio

from artemis.control.base import ActionResult
from artemis.devices.capabilities import CapabilitySet
from artemis.reflex.vision import changed

#: Where a drag starts, as a fraction of the frame height. iOS reads a downward drag
#: from the very top as Control Center and an upward drag from the bottom as Home (it
#: leaves the app), and a pointer parked on an edge has no room to drag at all. An
#: upward drag therefore starts low in the frame, a downward one high, and both stay
#: clear of the edge they move away from.
DRAG_START_RATIO = 0.65

#: One arrow press scrolls roughly this far in a scroll view (measured ~170 px on the
#: Taobao shop page). It only sizes the press burst; the frame check decides.
KEY_SCROLL_STEP_PX = 200
#: A long burst would walk the focus ring out of the scroll container.
KEY_SCROLL_MAX_PRESSES = 6
#: A scroll moves at least this much of the frame. The first arrow press can merely
#: put the focus ring around a container — a change, but not a scroll.
KEY_SCROLL_MIN_CHANGE_RATIO = 0.05


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
        motion = getattr(result, "motion", None)
        if motion is not None:
            metrics["motion_segments"] = float(getattr(motion, "segments", 0) or 0)
            metrics["motion_observations"] = float(getattr(motion, "observations", 0) or 0)
            phases = getattr(motion, "phases", None) or []
            metrics["motion_phases"] = ",".join(str(phase) for phase in phases)
            prediction = getattr(motion, "prediction_error_px", None)
            if isinstance(prediction, (int, float)):
                metrics["motion_prediction_error_px"] = float(prediction)
            metrics["motion_global_recoveries"] = float(
                getattr(motion, "global_recoveries", 0) or 0
            )
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

    def _scroll_changed(self, before, after) -> bool:
        """Whether two frames differ by enough to be a scroll (not just a focus ring)."""
        size = self._frame_size(self.session)
        area = size[0] * size[1] if size else 0
        min_pixels = max(30, round(area * KEY_SCROLL_MIN_CHANGE_RATIO)) if area else 30
        return changed(before.image, after.image, min_pixels=min_pixels)

    async def _scroll_with_key(self, key: str, distance: float) -> bool:
        """Scroll with an arrow key and keep it only if the screen really moved.

        iOS scroll views scroll on an external keyboard's arrows — no pointer, no
        gain calibration, no edge gesture. The press count only sizes the burst
        from the requested distance; the frame comparison decides, and a screen
        that did not move falls back to the pointer drag.
        """
        press = getattr(self._control, "press_named_key", None)
        capture = getattr(self.controller, "capture", None)
        grab = getattr(capture, "grab_latest", None) or getattr(capture, "grab", None)
        if not callable(press) or not callable(grab):
            return False
        presses = max(1, min(KEY_SCROLL_MAX_PRESSES, round(distance / KEY_SCROLL_STEP_PX)))
        try:
            before = await asyncio.to_thread(grab)
            for _ in range(presses):
                await asyncio.to_thread(press, key)
            after = await asyncio.to_thread(grab)
            if self._scroll_changed(before, after):
                return True
            # A streamed capture can hand back a frame taken before the presses.
            after = await asyncio.to_thread(grab)
        except Exception:  # noqa: BLE001 - the pointer drag is the fallback for any of this
            return False
        return self._scroll_changed(before, after)

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
        # An absolute pointer places the drag start in one command, and the firmware's
        # mswipe then presses through the absolute report (iOS ignores the relative
        # report's buttons). The relative fallback needs the home + gain dance.
        move_abs = getattr(self.session, "move_abs", None)
        control = getattr(self.session, "control", None)
        if callable(move_abs) and hasattr(control, "mouse_move_abs"):
            move_abs(x, y)
            return
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
        if dy != 0 and abs(dy) >= abs(dx):
            # Keyboard first: an arrow scroll avoids the pointer, its gain and its
            # edge gestures entirely. A vertical swipe up means "scroll down".
            key = "down" if dy < 0 else "up"
            if await self._scroll_with_key(key, abs(dy)):
                return ActionResult(True, "swipe", detail=f"scrolled with the {key} key")
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
