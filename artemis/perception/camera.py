"""A physical camera pointed at the phone, as a last-resort perception source.

When no capture card is available (or the card goes dark behind HDCP), a camera in front
of the phone still sees the screen — slowly, and with its own framing. That framing is the
catch: normalized coordinates only mean something once the camera's view of the screen is
calibrated (``content_rect`` on the geometry), so a camera composition is chosen by the
selector only when nothing better exists, and the caller owns that calibration.

The grabber is a seam: production uses macOS screen/ffmpeg capture through
``artemis.reflex.capture.UvcCapture``-style code, tests inject frames.
"""

from __future__ import annotations

import asyncio
import uuid

from artemis.devices.capabilities import CapabilitySet
from artemis.perception.base import Frame
from third_party.mobile_use.utils.logger import get_logger

logger = get_logger(__name__)

__all__ = ["CameraSource"]


class CameraSource:
    """Frames from a callable grabber (``() -> (image_bytes, width, height)``)."""

    def __init__(self, grabber=None, *, name: str = "camera", capture=None):
        if grabber is None and capture is None:
            raise ValueError("a camera source needs a grabber or a capture object")
        self._grabber = grabber
        self.capture = capture
        self.name = name
        self.capabilities = CapabilitySet()

    def _grab(self):
        if self.capture is not None:
            raw = self.capture.grab()
            return raw.image, raw.width, raw.height, getattr(raw, "captured_at", 0.0)
        image, width, height = self._grabber()
        return image, width, height, 0.0

    async def snapshot(self) -> Frame:
        try:
            image, width, height, captured_at = await asyncio.to_thread(self._grab)
        except Exception as exc:  # noqa: BLE001 - a missing camera is data, not a crash
            # Report it the same way every other source reports "no picture", so the
            # session's health gate and the incident taxonomy keep working unchanged.
            logger.warning(f"camera source failed: {exc}")
            return Frame(
                image=b"",
                captured_at=0.0,
                width=0,
                height=0,
                source=self.name,
                health="black",
                frame_id=uuid.uuid4().hex,
            )
        from artemis.perception.uvc import _classify

        return Frame(
            image=image,
            captured_at=float(captured_at or 0.0),
            width=int(width),
            height=int(height),
            source=self.name,
            health=_classify(image) if image else "black",
        )

    async def ui_tree(self):
        return None

    async def ocr(self, frame: Frame):
        return None

    async def locate(self, frame: Frame, target):
        return None
