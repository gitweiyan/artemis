"""Several control surfaces, one sink: absolute first, relative as the fallback.

A device can offer more than one way in (ADB's absolute touch, an ESP32 board's relative
pointer). The choice is made once, by capability, *before* acting: retrying a half-applied
action through a second surface would act twice. So this sink selects, and then reports
exactly what the chosen surface said.
"""

from __future__ import annotations

from dataclasses import fields

from artemis.control.base import ActionResult, ControlSink
from artemis.devices.capabilities import CapabilitySet

__all__ = ["CompositeControlSink"]


class CompositeControlSink:
    """Ordered sinks; the most precise one that can express the action is used."""

    def __init__(self, sinks: list[ControlSink]):
        if not sinks:
            raise ValueError("a composite control needs at least one sink")
        self.sinks = list(sinks)

    @property
    def capabilities(self) -> CapabilitySet:
        values = {item.name: False for item in fields(CapabilitySet)}
        for sink in self.sinks:
            caps = sink.capabilities
            for name in values:
                values[name] = values[name] or bool(getattr(caps, name))
        return CapabilitySet(**values)

    def _for(self, capability: str) -> ControlSink | None:
        for sink in self.sinks:
            if getattr(sink.capabilities, capability, False):
                return sink
        return None

    def _absolute(self) -> ControlSink | None:
        return self._for("supports_absolute_tap") or self._for("supports_relative_pointer")

    def _relative(self) -> ControlSink | None:
        return self._for("supports_relative_pointer")

    @staticmethod
    def _unavailable(action: str, what: str) -> ActionResult:
        return ActionResult(False, action, "unsupported", f"no control surface supports {what}")

    def close(self) -> None:
        for sink in self.sinks:
            closer = getattr(sink, "close", None)
            if callable(closer):
                closer()

    async def tap_abs(self, x: int, y: int) -> ActionResult:
        sink = self._absolute()
        if sink is None:
            return self._unavailable("tap_abs", "a tap")
        return await sink.tap_abs(x, y)

    async def tap_rel(self, dx: int, dy: int) -> ActionResult:
        sink = self._relative()
        if sink is None:
            return self._unavailable("tap_rel", "a relative move")
        return await sink.tap_rel(dx, dy)

    async def swipe(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        duration_ms: int = 800,
    ) -> ActionResult:
        sink = self._absolute() or self._relative()
        if sink is None:
            return self._unavailable("swipe", "a swipe")
        return await sink.swipe(start_x, start_y, end_x, end_y, duration_ms)

    async def input_text(self, text: str) -> ActionResult:
        sink = self._for("supports_text_input")
        if sink is None:
            return self._unavailable("input_text", "text input")
        return await sink.input_text(text)

    async def press_key(self, key) -> ActionResult:
        sink = self._absolute() or self._relative()
        if sink is None:
            return self._unavailable("press_key", "a key press")
        return await sink.press_key(key)
