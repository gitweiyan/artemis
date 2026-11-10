"""Absolute control through the app surface (ADB / helper / a driver).

The platform-neutral counterpart of :class:`~artemis.control.hid.HidControlSink`: a
device that exposes absolute touch and a UI tree is driven by coordinates, not by a
relative pointer. Wrapping the driver behind the same ``ControlSink`` protocol is what
lets a session choose "app control first, HID as the fallback" by capability instead of
by platform name.
"""

from __future__ import annotations

import asyncio

from artemis.control.base import ActionResult
from artemis.devices.capabilities import CapabilitySet

__all__ = ["AppControlSink"]


class AppControlSink:
    """Absolute taps/swipes/text through a driver's own methods.

    ``driver`` is anything with the ``BaseDeviceDriver`` action surface (``tap``,
    ``swipe``, ``input_text``, ``press_key``); the sink adds nothing but the async
    contract and structured results.
    """

    capabilities = CapabilitySet(
        supports_absolute_tap=True,
        supports_text_input=True,
    )

    def __init__(self, driver):
        self.driver = driver

    async def _call_driver(self, method, *args, **kwargs):
        """Call a driver action inside the session-dispatch scope (ADR-006 3b).

        Android's driver delegates its public actions to the session sink; the sink
        must call the legacy implementation instead of re-entering the session.
        """

        depth = getattr(self.driver, "_session_dispatch_depth", None)
        if isinstance(depth, int):
            setattr(self.driver, "_session_dispatch_depth", depth + 1)
        try:
            return await method(*args, **kwargs)
        finally:
            if isinstance(depth, int):
                setattr(self.driver, "_session_dispatch_depth", depth)

    async def tap_abs(self, x: int, y: int) -> ActionResult:
        try:
            ok = await self._call_driver(self.driver.tap, int(x), int(y))
        except Exception as exc:  # noqa: BLE001 - a device failure is an incident
            return ActionResult(False, "tap_abs", "tap_failed", str(exc))
        if ok:
            return ActionResult(True, "tap_abs")
        return ActionResult(False, "tap_abs", "tap_failed", f"tap refused at ({x}, {y})")

    async def tap_rel(self, dx: int, dy: int) -> ActionResult:
        # An absolute surface has no relative pointer; saying so is better than
        # pretending by translating into one tap at an unknown place.
        return ActionResult(
            False,
            "tap_rel",
            "unsupported",
            "this control surface only supports absolute taps",
        )

    async def swipe(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        duration_ms: int = 800,
    ) -> ActionResult:
        try:
            ok = await self._call_driver(
                self.driver.swipe,
                int(start_x),
                int(start_y),
                int(end_x),
                int(end_y),
                duration_ms=duration_ms,
            )
        except Exception as exc:  # noqa: BLE001
            return ActionResult(False, "swipe", "swipe_failed", str(exc))
        if ok:
            return ActionResult(True, "swipe")
        return ActionResult(False, "swipe", "swipe_failed", "the device refused the swipe")

    async def input_text(self, text: str) -> ActionResult:
        try:
            ok = await self._call_driver(self.driver.input_text, text)
        except Exception as exc:  # noqa: BLE001
            return ActionResult(False, "input_text", "text_input_failed", str(exc))
        if ok:
            return ActionResult(True, "input_text")
        return ActionResult(False, "input_text", "text_input_failed", "the device refused text")

    async def press_key(self, key) -> ActionResult:
        try:
            ok = await self._call_driver(self.driver.press_key, key)
        except Exception as exc:  # noqa: BLE001
            return ActionResult(False, "press_key", "key_failed", str(exc))
        if ok:
            return ActionResult(True, "press_key")
        return ActionResult(False, "press_key", "key_failed", f"the device refused key {key!r}")

    async def preflight(self) -> None:
        """Give the driver a chance to prove it is reachable before a session starts."""
        connect = getattr(self.driver, "connect", None)
        if callable(connect):
            await asyncio.to_thread(lambda: None)  # keep the coroutine suspension explicit
            await connect()
