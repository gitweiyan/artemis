"""macOS Vision OCR adapter: text, confidence, normalized boxes.

Vision is the local OCR on the Mac that owns the iOS rig. It ships with the OS (no model
download, no API key, no 403), reads Simplified Chinese well, and is fast enough for the
single frame an iOS task takes. It plugs into the same seam as ``PaddleOcrEngine``:
``recognize(image_bytes, width=..., height=...) -> list[TextBox]`` with boxes normalized
to 0–1, so ``locate_text`` and the rest of the perception layer do not care which engine
produced them.

The framework is imported lazily: it is only needed on the machine that runs an iOS task,
and every test of this module injects its own observer.
"""

from __future__ import annotations

import math
from typing import Callable

from artemis.perception.base import TextBox

__all__ = ["VisionOcrEngine", "vision_available"]

#: ``(text, confidence, (x0, y0, x1, y1))`` with the box in Vision coordinates
#: (origin bottom-left, 0..1).
Observation = tuple[str, float, tuple[float, float, float, float]]


def vision_available() -> bool:
    """Whether the macOS Vision text API can be imported in this interpreter."""
    try:
        import Vision  # noqa: F401  # lazy: non-macOS installs have no Vision
    except Exception:  # noqa: BLE001 - absence is a capability fact, not an error
        return False
    return True


class VisionOcrEngine:
    """Lazily own a Vision text-recognition request (macOS only).

    ``observer`` is the test/backend seam: ``observer(image_bytes) -> list[Observation]``.
    Production leaves it ``None`` and the native path runs.
    """

    def __init__(
        self,
        observer: Callable[[bytes], list[Observation]] | None = None,
        *,
        languages: tuple[str, ...] = ("zh-Hans", "en-US"),
        min_confidence: float = 0.0,
    ):
        self.observer = observer
        self.languages = tuple(languages)
        self.min_confidence = float(min_confidence)

    def recognize(self, image: bytes, *, width: int, height: int) -> list[TextBox]:
        observations = (self.observer or self._observe)(image)
        return self.to_text_boxes(observations, width, height)

    def to_text_boxes(
        self, observations: list[Observation] | None, width: int, height: int
    ) -> list[TextBox]:
        """Vision observations -> ``TextBox`` values in the top-left 0–1 space."""
        boxes: list[TextBox] = []
        for text, confidence, (x0, y0, x1, y1) in observations or []:
            if not text:
                continue
            confidence = float(confidence)
            if math.isnan(confidence) or confidence < self.min_confidence:
                continue
            # Vision's origin is bottom-left; the perception layer's is top-left.
            top = 1.0 - max(y0, y1)
            bottom = 1.0 - min(y0, y1)
            boxes.append(
                TextBox(
                    text=str(text),
                    box=(
                        max(0.0, min(1.0, min(x0, x1))),
                        max(0.0, min(1.0, top)),
                        max(0.0, min(1.0, max(x0, x1))),
                        max(0.0, min(1.0, bottom)),
                    ),
                    confidence=confidence,
                )
            )
        return boxes

    # --- native path -------------------------------------------------------------

    def _observe(self, image: bytes) -> list[Observation]:
        import Quartz
        import Vision
        from Foundation import NSData

        data = NSData.dataWithBytes_length_(image, len(image))
        source = Quartz.CGImageSourceCreateWithData(data, None)
        if source is None:
            raise ValueError("frame is not a decodable image")
        cg_image = Quartz.CGImageSourceCreateImageAtIndex(source, 0, None)
        if cg_image is None:
            raise ValueError("frame could not be decoded into a CGImage")

        results: list[Observation] = []

        def handler(request, _error):
            for observation in request.results() or []:
                candidates = observation.topCandidates_(1)
                if not candidates:
                    continue
                best = candidates[0]
                box = observation.boundingBox()
                x0, y0 = box.origin.x, box.origin.y
                results.append(
                    (
                        str(best.string()),
                        float(best.confidence()),
                        (x0, y0, x0 + box.size.width, y0 + box.size.height),
                    )
                )

        request = Vision.VNRecognizeTextRequest.alloc().initWithCompletionHandler_(handler)
        request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        request.setUsesLanguageCorrection_(True)
        if self.languages:
            request.setRecognitionLanguages_(list(self.languages))

        handler_obj = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg_image, None)
        ok, error = handler_obj.performRequests_error_([request], None)
        if not ok:
            raise RuntimeError(f"Vision text recognition failed: {error}")
        return results
