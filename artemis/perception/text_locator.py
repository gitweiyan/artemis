"""Turn OCR text candidates into one normalized target."""

from __future__ import annotations

from artemis.perception.base import Target, TextBox


def locate_text(
    boxes: list[TextBox], query: str, *, min_confidence: float = 0.5
) -> Target | None:
    if not query:
        return None
    eligible = [box for box in boxes if box.confidence >= min_confidence]
    exact = [box for box in eligible if box.text == query]
    contains = [box for box in eligible if query in box.text]
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
