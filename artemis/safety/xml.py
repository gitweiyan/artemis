"""Geometry facts about an element tree: what covers a point, how far a target moved.

The XML safety net's decisions were expressed as arithmetic on ``[left,top][right,bottom]``
strings inline in the validator. Those facts are platform-neutral and worth testing on
their own: *does this box contain the point*, *how much do two boxes overlap*, *is the
element under the coordinates a different (interactive) thing than the one we recorded*.

Nothing here decides policy. It reports numbers and identifiers, so the validator keeps
owning the thresholds while the arithmetic lives in one tested place.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

__all__ = [
    "Bounds",
    "area",
    "center",
    "contains_point",
    "find_element_at",
    "iou",
    "parse_bounds",
    "size_mismatch",
]

Bounds = tuple[int, int, int, int]

_BOUNDS_RE = re.compile(r"\[(\-?\d+),(\-?\d+)\]\[(\-?\d+),(\-?\d+)\]")


def parse_bounds(bounds: Any) -> Bounds | None:
    """``"[l,t][r,b]"`` or a 4-sequence → ``(l, t, r, b)``; anything else → ``None``."""
    if bounds is None:
        return None
    if isinstance(bounds, str):
        match = _BOUNDS_RE.match(bounds)
        return tuple(int(value) for value in match.groups()) if match else None
    if isinstance(bounds, (list, tuple)) and len(bounds) == 4:
        try:
            return tuple(int(value) for value in bounds)  # type: ignore[return-value]
        except (TypeError, ValueError):
            return None
    return None


def area(bounds: Bounds) -> int:
    left, top, right, bottom = bounds
    return max(0, right - left) * max(0, bottom - top)


def center(bounds: Bounds) -> tuple[int, int]:
    left, top, right, bottom = bounds
    return (left + right) // 2, (top + bottom) // 2


def contains_point(bounds: Bounds, x: float, y: float) -> bool:
    left, top, right, bottom = bounds
    return left <= x <= right and top <= y <= bottom


def iou(a: Bounds, b: Bounds) -> float:
    """Intersection over union of two boxes (0.0 when they do not overlap)."""
    left = max(a[0], b[0])
    top = max(a[1], b[1])
    right = min(a[2], b[2])
    bottom = min(a[3], b[3])
    if right <= left or bottom <= top:
        return 0.0
    intersection = (right - left) * (bottom - top)
    union = area(a) + area(b) - intersection
    return intersection / union if union > 0 else 0.0


def size_mismatch(a: Bounds, b: Bounds, *, max_ratio: float = 2.5) -> bool:
    """Whether one box is so much bigger than the other that they cannot be the same thing.

    This is what stops a small widget from matching the giant container behind it.
    """
    for first, second in (((a[2] - a[0]), (b[2] - b[0])), ((a[3] - a[1]), (b[3] - b[1]))):
        if first <= 0 or second <= 0:
            return False
        if max(first, second) / min(first, second) > max_ratio:
            return True
    return False


def find_element_at(
    elements: Iterable[dict[str, Any]], x: float, y: float
) -> tuple[dict[str, Any], Bounds] | None:
    """The *smallest* element containing the point — the most specific thing there.

    Returned with its bounds, because the caller usually needs to compare them with what
    it recorded for the target.
    """
    best: tuple[dict[str, Any], Bounds] | None = None
    best_area: int | None = None
    for element in elements:
        if not isinstance(element, dict):
            continue
        bounds = parse_bounds(element.get("bounds"))
        if bounds is None or not contains_point(bounds, x, y):
            continue
        element_area = area(bounds)
        if best_area is None or element_area < best_area:
            best, best_area = (element, bounds), element_area
    return best
