"""Turn OCR text candidates into one normalized target."""

from __future__ import annotations

from artemis.perception.base import Target, TextBox


#: An OCR box holding exactly the requested label is strong evidence even when the
#: engine's own confidence is low: macOS Vision reports correct Chinese labels at 0.30,
#: while a *partial* match at that confidence is often a misread of something else.
DEFAULT_EXACT_MIN_CONFIDENCE = 0.25


def locate_text(
    boxes: list[TextBox],
    query: str,
    *,
    min_confidence: float = 0.5,
    exact_min_confidence: float = DEFAULT_EXACT_MIN_CONFIDENCE,
) -> Target | None:
    if not query:
        return None
    exact = [box for box in boxes if box.text == query and box.confidence >= exact_min_confidence]
    contains = [box for box in boxes if query in box.text and box.confidence >= min_confidence]
    candidates = exact or contains
    if not candidates:
        return None
    best = max(candidates, key=lambda box: box.confidence)
    return Target(
        label=best.text,
        box=best.box,
        confidence=best.confidence,
        source="ocr",
    )
