"""Screen-data observation boundary for L2/L3 callers.

Graph perception consumes this module instead of importing the MCP observation
implementation. A real DeviceSession is authoritative; the controller fallback
remains an explicit compatibility path for callers that have no Session yet.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any

__all__ = ["ScreenData", "get_screen_data", "session_screen_data"]


@dataclass(frozen=True)
class ScreenData:
    base64: str
    elements: list[dict[str, Any]] = field(default_factory=list)
    width: int = 1080
    height: int = 2400
    platform: str = "unknown"


def _coerce(data: Any) -> ScreenData:
    return ScreenData(
        base64=str(getattr(data, "base64", None) or getattr(data, "screenshot_base64", "") or ""),
        elements=list(getattr(data, "elements", None) or getattr(data, "ui_elements", []) or []),
        width=int(getattr(data, "width", 1080) or 1080),
        height=int(getattr(data, "height", 2400) or 2400),
        platform=str(getattr(data, "platform", "unknown") or "unknown"),
    )


async def session_screen_data(ctx: Any) -> ScreenData | None:
    """Read screen data through the Session, including its health/state guard."""

    session = getattr(ctx, "device_session", None)
    if session is None or not isinstance(getattr(session, "perception", None), (list, tuple)):
        return None
    frame = await session.snapshot()
    for source in session.perception:
        cached = getattr(source, "_last_data", None)
        if cached is not None:
            return _coerce(cached)
    source = session.perception[0] if session.perception else None
    elements: list[dict[str, Any]] = []
    if source is not None:
        tree = await source.ui_tree()
        for item in tree or []:
            bounds = getattr(item, "bounds", None)
            if bounds and len(bounds) == 4:
                left, top, right, bottom = (int(value) for value in bounds)
                elements.append(
                    {
                        "text": getattr(item, "text", ""),
                        "resource-id": getattr(item, "resource_id", ""),
                        "bounds": f"[{left},{top}][{right},{bottom}]",
                        "center": [(left + right) // 2, (top + bottom) // 2],
                    }
                )
    return ScreenData(
        base64=base64.b64encode(frame.image).decode("ascii"),
        elements=elements,
        width=int(frame.width or 1080),
        height=int(frame.height or 2400),
        platform=str(getattr(source, "name", None) or getattr(frame, "source", None) or "unknown"),
    )


async def get_screen_data(ctx: Any, controller: Any | None = None) -> ScreenData:
    """Session first; controller is only used when no usable Session exists."""

    data = await session_screen_data(ctx)
    if data is not None:
        return data
    if controller is None:
        from artemis.controllers.unified_controller import UnifiedMobileController

        controller = UnifiedMobileController(ctx)
    return _coerce(await controller.get_screen_data())
