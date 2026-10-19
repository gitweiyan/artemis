"""On-demand UVC capture as a standard perception source."""

from __future__ import annotations

import asyncio
from io import BytesIO

from PIL import Image

from artemis.devices.capabilities import CapabilitySet
from artemis.perception.base import Frame, Target


def _classify(image: bytes) -> str:
    try:
        with Image.open(BytesIO(image)) as decoded:
            sample = decoded.convert("RGB").resize((64, 64))
            raw = sample.tobytes()
    except Exception:  # noqa: BLE001 - a bad frame is data, not a crash
        return "decode_error"
    pixels = [tuple(raw[i : i + 3]) for i in range(0, len(raw), 3)]
    mean = sum(sum(pixel) / 3 for pixel in pixels) / len(pixels)
    nonblack = sum(1 for pixel in pixels if max(pixel) > 8) / len(pixels)
    if mean <= 2.0 or nonblack <= 0.01:
        return "black"
    return "ok"


class UvcSource:
    """One frame per snapshot; no persistent stream and no HID coupling."""

    capabilities = CapabilitySet()

    def __init__(self, capture, *, name: str = "uvc"):
        self.capture = capture
        self.name = name

    async def snapshot(self) -> Frame:
        raw = await asyncio.to_thread(self.capture.grab)
        return Frame(
            image=raw.image,
            captured_at=raw.captured_at,
            width=raw.width,
            height=raw.height,
            source=self.name,
            health=_classify(raw.image),
        )

    async def ui_tree(self):
        return None

    async def ocr(self, frame: Frame):
        return None

    async def locate(self, frame: Frame, target: Target):
        return None
