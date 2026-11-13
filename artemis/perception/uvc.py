"""On-demand UVC capture as a standard perception source."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from io import BytesIO
import time
import uuid

from PIL import Image

from artemis.core.capabilities import (
    CapabilityDeclaration,
    CapabilityId,
    CapabilityManifest,
)
from artemis.core.capability_registry import ProbeResult
from artemis.core.capabilities import CapabilitySet
from artemis.core.errors import DeviceDisconnectedError
from artemis.core.reflex_contract import CaptureError
from artemis.perception.base import Frame, Target, TextBox
from third_party.mobile_use.utils.logger import get_logger

logger = get_logger(__name__)


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

    def __init__(
        self,
        capture,
        *,
        name: str = "uvc",
        ocr_engine=None,
        target_adjuster=None,
        locator=None,
        locate_url: str | None = None,
    ):
        self.capture = capture
        self.name = name
        self.ocr_engine = ocr_engine
        #: A generic grounding locator supplied by the Profile. It is tried before OCR;
        #: the legacy ``locate_url`` path creates a LocateAnything client lazily.
        self.locator = locator
        raw_url = "" if locate_url is None else str(locate_url).strip()
        self.locate_url = "" if raw_url.lower() in ("off", "none", "0") else raw_url
        self._locate_client = None
        #: Lifts a matched *label* onto the element that is actually tappable (the iOS
        #: springboard's icon above it). Without it a tap lands on the text and does
        #: nothing — measured live 2026-10-21.
        self.target_adjuster = target_adjuster
        self.capabilities = CapabilitySet(supports_ocr=ocr_engine is not None)

    async def snapshot(self) -> Frame:
        try:
            raw = await asyncio.to_thread(self.capture.grab)
        except CaptureError as exc:
            if exc.incident in {"device_disconnected", "transport_error", "uvc_no_signal"}:
                raise DeviceDisconnectedError(str(exc), source=self.name) from exc
            raise
        return Frame(
            image=raw.image,
            captured_at=raw.captured_at,
            width=raw.width,
            height=raw.height,
            source=self.name,
            health=_classify(raw.image),
            frame_id=uuid.uuid4().hex,
        )

    @property
    def capability_manifest(self) -> CapabilityManifest:
        declarations = [
            CapabilityDeclaration(CapabilityId.FRAME_CAPTURE),
            CapabilityDeclaration(CapabilityId.CAPTURE_HEALTH),
        ]
        if self.ocr_engine is not None:
            declarations.append(CapabilityDeclaration(CapabilityId.OCR_READ))
        if self.locator is not None or self.locate_url:
            declarations.append(CapabilityDeclaration(CapabilityId.GROUNDING_LOCATE))
        return CapabilityManifest("uvc", "1", tuple(declarations))

    async def probe_capabilities(self) -> dict[str, ProbeResult]:
        """One frame + OCR availability; never clicks and never raises."""

        now = time.time()
        try:
            frame = await self.snapshot()
        except (OSError, RuntimeError, ValueError, AttributeError) as exc:
            reason = f"capture_failed: {exc}"
            return {
                CapabilityId.FRAME_CAPTURE: ProbeResult(False, self.name, reason, now),
                CapabilityId.CAPTURE_HEALTH: ProbeResult(False, self.name, reason, now),
            }
        healthy = getattr(frame, "health", "ok") == "ok"
        reason = None if healthy else f"frame_health={getattr(frame, 'health', 'unknown')}"
        return {
            CapabilityId.FRAME_CAPTURE: ProbeResult(healthy, self.name, reason, now),
            CapabilityId.CAPTURE_HEALTH: ProbeResult(healthy, self.name, reason, now),
            CapabilityId.OCR_READ: (
                ProbeResult(True, f"{self.name}.ocr", None, now)
                if self.ocr_engine is not None
                else ProbeResult(False, f"{self.name}.ocr", "no_ocr_engine", now)
            ),
        }

    def close(self) -> None:
        """Close the underlying capture handle (idempotent, best effort)."""

        closer = getattr(self.capture, "close", None)
        if not callable(closer):
            return
        try:
            closer()
        except (OSError, RuntimeError, ValueError, AttributeError) as exc:
            logger.warning(f"Closing UVC capture failed: {exc}")

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
        point = None
        if self.locator is not None:
            try:
                point = await asyncio.to_thread(self.locator.locate, frame.image, target.label)
            except (OSError, RuntimeError, ValueError, AttributeError):
                point = None
        elif self.locate_url:
            if self._locate_client is None:
                from artemis.perception.locate_anything import LocateAnythingDetector

                self._locate_client = LocateAnythingDetector(self.locate_url)
            point = await asyncio.to_thread(self._locate_client.locate, frame.image, target.label)
        if point is not None:
            x, y = point
            half = 0.005
            box = (
                max(0.0, x - half),
                max(0.0, y - half),
                min(1.0, x + half),
                min(1.0, y + half),
            )
            return replace(
                Target(
                    label=target.label,
                    box=box,
                    confidence=0.9,
                    source="locate-anything",
                ),
                frame_id=frame.frame_id,
            )
        boxes = await self.ocr(frame)
        if boxes is None:
            return None
        from artemis.perception.text_locator import locate_text

        found = locate_text(boxes, target.label)
        if found is None:
            return None
        found = replace(found, frame_id=frame.frame_id)
        if self.target_adjuster is None:
            return found
        adjusted = await asyncio.to_thread(self.target_adjuster, frame.image, found.box)
        if adjusted is None:
            return found
        # Keep the matched text and its confidence: the geometry changed, the evidence
        # did not.
        return replace(
            found,
            box=adjusted.box,
            source=adjusted.source,
        )
