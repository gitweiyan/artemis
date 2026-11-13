"""App automation (ADB / helper / a UI tree) as a standard perception source.

The counterpart of :class:`~artemis.perception.uvc.UvcSource`: a device behind an
automation helper can hand back a screenshot *and* a UI tree, which is the strongest
perception a mobile device offers. Wrapping it in ``PerceptionSource`` is what lets the
composition selector pick "app surface" without the core orchestrator knowing whether the
device is Android.

OCR and locating a text target are left to the caller (``None``): a tree already carries
text and bounds, so a second OCR pass would only add latency — the UVC source is the one
that needs OCR.
"""

from __future__ import annotations

import asyncio
import uuid

from artemis.core.capabilities import CapabilitySet
from artemis.core.errors import DeviceDisconnectedError
from artemis.perception.base import Frame, UIElement

__all__ = ["AppAutomationSource"]


class AppAutomationSource:
    """Frames and UI elements from a driver's ``get_screen_data``."""

    def __init__(self, driver, *, name: str = "app"):
        self.driver = driver
        self.name = name
        self.capabilities = CapabilitySet(supports_ui_tree=True)
        self._last_data = None

    async def screen_data(self):
        """Raw driver observation for the Session-backed observation port."""

        self._last_data = await self.driver.get_screen_data()
        if getattr(self.driver, "last_capture_error", False):
            raise DeviceDisconnectedError(
                f"Android screenshot capture failed for {getattr(self.driver, 'device_id', 'device')}",
                source=self.name,
            )
        return self._last_data

    async def snapshot(self) -> Frame:
        data = await self.screen_data()
        image = getattr(data, "screenshot_bytes", b"") or ""
        return Frame(
            image=image,
            captured_at=float(getattr(data, "timestamp", 0.0) or 0.0),
            width=int(getattr(data, "width", 0) or 0),
            height=int(getattr(data, "height", 0) or 0),
            source=self.name,
            health="ok" if image else "black",
            frame_id=uuid.uuid4().hex,
        )

    async def ui_tree(self) -> list[UIElement] | None:
        data = self._last_data or await self.screen_data()
        elements = getattr(data, "ui_elements", None) or []
        tree: list[UIElement] = []
        for element in elements:
            bounds = element.get("bounds")
            tree.append(
                UIElement(
                    text=str(element.get("text") or ""),
                    bounds=tuple(bounds)
                    if isinstance(bounds, (list, tuple)) and len(bounds) == 4
                    else None,
                    resource_id=str(element.get("resource_id") or element.get("resource-id") or ""),
                )
            )
        return tree

    async def ocr(self, frame: Frame):
        """A UI tree already carries text; no second OCR pass is worth its latency."""
        return None

    async def locate(self, frame: Frame, target):
        """Text targets come from the tree (``ui_tree``), not from pixel locating."""
        return None
