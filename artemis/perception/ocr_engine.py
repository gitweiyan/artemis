"""Pick the OCR engine this machine can actually run.

The iOS rig has no UI tree, so OCR is the only text locator — and a missing engine must
be a visible capability gap, not an exception deep inside a task. PaddleOCR is preferred
where it is installed (the Android-side choice); on the Mac that owns the rig, macOS
Vision is the local fallback that needs no model download or API key.
"""

from __future__ import annotations

from artemis.perception.paddle_ocr import PaddleOcrEngine
from artemis.perception.vision_ocr import VisionOcrEngine, vision_available
from third_party.mobile_use.utils.logger import get_logger

logger = get_logger(__name__)

__all__ = ["default_ocr_engine"]


def _paddle_available() -> bool:
    try:
        import paddleocr  # noqa: F401  # lazy: optional everywhere
    except Exception:  # noqa: BLE001 - absence is a capability fact
        return False
    return True


def default_ocr_engine():
    """The best available local OCR engine, or ``None`` (never a crash)."""
    if _paddle_available():
        return PaddleOcrEngine()
    if vision_available():
        logger.info("OCR engine: macOS Vision (PaddleOCR is not installed)")
        return VisionOcrEngine()
    logger.warning(
        "No local OCR engine is available (no paddleocr, no macOS Vision); text targets"
        " cannot be located on this machine."
    )
    return None
