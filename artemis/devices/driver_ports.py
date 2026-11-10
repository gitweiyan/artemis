"""Driver → DeviceSession/Ports compatibility adapter (ADR-006 Phase 3a).

The legacy Driver surface stays intact for callers, but L3 should consume the
Session/Ports view. This adapter reuses an existing ``driver.session`` when the
driver already owns one (iOS and the lazy Android session), otherwise it wraps
the driver in the app-surface composition. It opens nothing itself.
"""

from __future__ import annotations

from typing import Any

from artemis.control.base import ActionResult, ControlSink
from artemis.devices.capabilities import CapabilitySet
from artemis.devices.session import DeviceSession
from artemis.perception.base import Frame, PerceptionSource

__all__ = ["DriverSessionAdapter"]


class DriverSessionAdapter:
    """A ports-first view over one legacy driver, without changing driver behavior."""

    def __init__(
        self,
        driver: Any,
        *,
        screen_size: tuple[int, int] | None = None,
        content_rect: tuple[int, int, int, int] | None = None,
    ) -> None:
        self.driver = driver
        existing = getattr(driver, "session", None)
        if isinstance(existing, DeviceSession):
            self.session = existing
        else:
            from artemis.devices.app_session import build_app_session

            self.session = build_app_session(
                driver,
                screen_size=screen_size or getattr(driver, "screen_size", None),
                content_rect=content_rect,
            )

    @property
    def capabilities(self) -> CapabilitySet:
        return self.session.capabilities

    def observation(self) -> PerceptionSource:
        if not self.session.perception:
            raise RuntimeError("driver session has no perception source")
        return self.session.perception[0]

    def control(self, *, absolute: bool = True) -> ControlSink:
        return self.session.control_for(absolute=absolute)

    async def snapshot(self) -> Frame:
        return await self.session.snapshot()

    async def tap_abs(self, x: int, y: int) -> ActionResult:
        return await self.control(absolute=True).tap_abs(x, y)

    async def tap_rel(self, dx: int, dy: int) -> ActionResult:
        return await self.control(absolute=False).tap_rel(dx, dy)

    async def swipe(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        duration_ms: int = 800,
    ) -> ActionResult:
        return await self.control(absolute=True).swipe(start_x, start_y, end_x, end_y, duration_ms)

    async def input_text(self, text: str) -> ActionResult:
        return await self.control(absolute=True).input_text(text)

    async def press_key(self, key: Any) -> ActionResult:
        return await self.control(absolute=True).press_key(key)
