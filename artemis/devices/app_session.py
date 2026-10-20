"""Compose the app surface (UI tree + absolute control) into a device session.

The composition the selector calls ``app``: a driver behind an automation helper offers a
UI tree, absolute taps and text. Building it as a ``DeviceSession`` means the orchestrator
sees the same shape it sees for the iOS rig — sources, sinks, geometry — and the choice of
composition stays a decision, not a driver special case.
"""

from __future__ import annotations

from artemis.control.app_control import AppControlSink
from artemis.devices.geometry import ScreenGeometry
from artemis.devices.session import DeviceSession
from artemis.perception.app_automation import AppAutomationSource

__all__ = ["build_app_session"]


def build_app_session(
    driver,
    *,
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
) -> DeviceSession:
    """A session over ``driver`` (anything with the ``BaseDeviceDriver`` action surface)."""
    width, height = screen_size or (getattr(driver, "screen_size", None) or (1080, 2400))
    return DeviceSession(
        perception=[AppAutomationSource(driver)],
        control=[AppControlSink(driver)],
        geometry=ScreenGeometry((width, height), (width, height), content_rect),
    )
