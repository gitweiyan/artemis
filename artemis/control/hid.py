"""ESP32 HID control as a standard async control sink."""

from __future__ import annotations

import asyncio

from artemis.control.base import ActionResult
from artemis.devices.capabilities import CapabilitySet


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

    async def tap_abs(self, x: int, y: int) -> ActionResult:
        frame_x, frame_y = self._frame_point(x, y)
        result = await asyncio.to_thread(self.controller.tap, frame_x, frame_y)
        if result.ok:
            return ActionResult(True, "tap_abs", detail=result.message)
        return ActionResult(
            False,
            "tap_abs",
            result.incident or "no_effect",
            result.message,
        )

    async def tap_rel(self, dx: int, dy: int) -> ActionResult:
        await asyncio.to_thread(self.session.move, dx, dy)
        return ActionResult(True, "tap_rel")

    async def swipe(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        duration_ms: int = 800,
    ) -> ActionResult:
        def run():
            self._control.mouse_down()
            self._control.mouse_move(end_x - start_x, end_y - start_y)
            self._control.mouse_up()

        await asyncio.to_thread(run)
        return ActionResult(True, "swipe")

    async def input_text(self, text: str) -> ActionResult:
        await asyncio.to_thread(self._control.type_text, text)
        return ActionResult(True, "input_text")

    async def press_key(self, key) -> ActionResult:
        await asyncio.to_thread(self._control._send_with_response, f"key {key}")
        return ActionResult(True, "press_key")
