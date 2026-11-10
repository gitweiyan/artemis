"""iOS HID control policy: left-edge BACK and keyboard-first navigation.

The generic pointer/keyboard/text primitives live in HidControlSink; this subclass
owns only the policies that are specific to iOS (safe drag anchors, FKA scroll, BACK).
"""

from __future__ import annotations

import asyncio

from artemis.control.base import ActionResult
from artemis.control.hid import HidControlSink
from artemis.perception.pointer_detection import changed

__all__ = ["IOSControlSink", "BACK_SWIPE_DURATION_MS", "BACK_SWIPE_END_RATIO", "BACK_SWIPE_Y_RATIO"]

DRAG_START_RATIO = 0.65
KEY_SCROLL_STEP_PX = 200
KEY_SCROLL_MAX_PRESSES = 6
KEY_SCROLL_MIN_CHANGE_RATIO = 0.05
BACK_SWIPE_END_RATIO = 0.75
BACK_SWIPE_Y_RATIO = 0.5
BACK_SWIPE_DURATION_MS = 500


class IOSControlSink(HidControlSink):
    """HID sink for the iOS rig (safe anchors, FKA, left-edge BACK)."""

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

    def _place_for_drag(self, start: tuple[int, int], dy: int, *, y: int | None = None) -> None:
        """Re-anchor the pointer and move it to a safe drag start (blocking I/O).

        The relative pointer is a blind cursor: pressing where it happens to be
        starts the gesture wherever the last action left it — an upward drag from
        the top edge does nothing, and an edge start is read by iOS as a system
        gesture. Software home puts the pointer at a true origin; one relative move
        from there lands it at a point that clears both edges and leaves room for
        the gesture. The requested start only supplies x: the configured gain is a
        coarse estimate, so a point far from the edges is safer than the exact one.

        ``y`` overrides the direction-derived height. BACK's left-edge pop gesture
        needs an exact mid-height start even though its delta is horizontal, and
        must *not* be pulled away from the edge by the safe-anchor rule below.
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
        if y is not None:
            target_y = min(max(int(y), 0), height - 1)
        elif dy < 0:
            target_y = round(height * DRAG_START_RATIO)  # upward: room above, clear of Home
        elif dy > 0:
            target_y = round(height * (1.0 - DRAG_START_RATIO))  # downward: clear of Control Center
        else:
            target_y = round(height / 2)  # horizontal drag: the middle
        # An absolute pointer places the drag start in one command, and the firmware's
        # mswipe then presses through the absolute report (iOS ignores the relative
        # report's buttons). The relative fallback needs the home + gain dance.
        move_abs = getattr(self.session, "move_abs", None)
        control = getattr(self.session, "control", None)
        if callable(move_abs) and hasattr(control, "mouse_move_abs"):
            move_abs(x, target_y)
            return
        home()
        gain_x = float(getattr(self.session, "gain_x", 1.0)) or 1.0
        gain_y = float(getattr(self.session, "gain_y", 1.0)) or 1.0
        move(round(x / gain_x), round(target_y / gain_y))

    async def _drag(
        self,
        action: str,
        start: tuple[int, int],
        dx: int,
        dy: int,
        *,
        duration_ms: int,
        y: int | None = None,
    ) -> ActionResult:
        """Place the pointer, press, walk it, release — one paced HID drag."""
        try:
            await asyncio.to_thread(self._place_for_drag, start, dy, y=y)
        except Exception as exc:  # noqa: BLE001 - a device failure is an incident
            return ActionResult(False, action, "swipe_failed", str(exc))
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
            return ActionResult(False, action, "swipe_failed", str(exc))
        return ActionResult(True, action)

    async def go_back(self) -> ActionResult:
        """iOS BACK: the interactive pop gesture, a drag from the physical left edge.

        This is the one drag that must start *on* an edge: iOS only recognises the
        pop gesture when the press begins at the left bezel and travels past roughly
        a third of the width before release. With no known frame size we refuse
        instead of firing a blind drag — a guessed edge is how BACK used to become
        four silent no-ops in the 2026-11-05 milk-tea run.
        """
        size = self._frame_size(self.session)
        if size is None:
            return ActionResult(
                False,
                "go_back",
                "swipe_failed",
                "cannot place a left-edge BACK swipe without a known frame size",
            )
        width, height = size
        y = round(height * BACK_SWIPE_Y_RATIO)
        dx = round(width * BACK_SWIPE_END_RATIO)
        return await self._drag("go_back", (0, y), dx, 0, duration_ms=BACK_SWIPE_DURATION_MS, y=y)

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
        return await self._drag("swipe", frame_start, dx, dy, duration_ms=duration_ms)
