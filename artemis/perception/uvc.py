"""On-demand UVC capture as a standard perception source."""

from __future__ import annotations

import asyncio
from io import BytesIO

from PIL import Image

from artemis.devices.capabilities import CapabilitySet
from artemis.perception.base import Frame, Target, TextBox


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

    def __init__(self, capture, *, name: str = "uvc", ocr_engine=None, target_adjuster=None):
        self.capture = capture
        self.name = name
        self.ocr_engine = ocr_engine
        #: Lifts a matched *label* onto the element that is actually tappable (the iOS
        #: springboard's icon above it). Without it a tap lands on the text and does
        #: nothing — measured live 2026-10-21.
        self.target_adjuster = target_adjuster
        self.capabilities = CapabilitySet(supports_ocr=ocr_engine is not None)

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

    async def ocr(self, frame: Frame) -> list[TextBox] | None:
        if self.ocr_engine is None:
            return None
        return await asyncio.to_thread(
            self.ocr_engine.recognize,
            frame.image,
            width=frame.width,
            height=frame.height,
        )

    async def locate(self, frame: Frame, target: Target) -> Target | None:
        boxes = await self.ocr(frame)
        if boxes is None:
            return None
        from artemis.perception.text_locator import locate_text

        found = locate_text(boxes, target.label)
        if found is None or self.target_adjuster is None:
            return found
        adjusted = await asyncio.to_thread(self.target_adjuster, frame.image, found.box)
        if adjusted is None:
            return found
        # Keep the matched text and its confidence: the geometry changed, the evidence
        # did not.
        return Target(
            label=found.label,
            box=adjusted.box,
            confidence=found.confidence,
            source=adjusted.source,
        )
