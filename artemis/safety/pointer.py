"""Pointer-versus-target agreement: the one rule that decides whether a click may run.

Two places needed this and had drifted apart: the iOS driver's ``validate_pointer`` (used
by the rig smoke) and the relative-pointer controller's own tolerance check. Keeping the
rule in one module means "visible, inside the target, and within ``tolerance_px`` of its
center" has exactly one definition, one set of incident names, and one place to test.

Coordinates are capture-frame pixels: the pointer is detected in the frame, so every
caller converts its target into that grid first (``ScreenGeometry.screen_to_frame``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

__all__ = ["DEFAULT_TOLERANCE_PX", "PointerVerdict", "check_in_box", "check_point"]

#: How far from the target's center a pointer may sit and still click it.
DEFAULT_TOLERANCE_PX = 8.0


@dataclass(frozen=True)
class PointerVerdict:
    """Whether a click at this pointer position is allowed, and why not."""

    ok: bool
    incident: str | None = None
    error_px: float | None = None


def check_point(
    target_x: float,
    target_y: float,
    pointer_x: float,
    pointer_y: float,
    *,
    tolerance_px: float = DEFAULT_TOLERANCE_PX,
    inside: bool | None = None,
) -> PointerVerdict:
    """Decide a click from a target point and the pointer the frame shows.

    ``inside`` lets a caller that knows the target's *box* say that the pointer is not
    even within it, which is a different repair from "close but not centered".
    """
    error = math.hypot(float(pointer_x) - float(target_x), float(pointer_y) - float(target_y))
    if inside is False:
        return PointerVerdict(False, "pointer_off_target", error)
    if error > float(tolerance_px):
        return PointerVerdict(False, "pointer_not_centered", error)
    return PointerVerdict(True, None, error)


def check_in_box(
    box: tuple[float, float, float, float],
    pointer_x: float,
    pointer_y: float,
    *,
    tolerance_px: float = DEFAULT_TOLERANCE_PX,
) -> PointerVerdict:
    """Decide a click from a target box (frame pixels) and the pointer."""
    left, top, right, bottom = (float(value) for value in box)
    center_x = (left + right) / 2
    center_y = (top + bottom) / 2
    inside = min(left, right) <= float(pointer_x) <= max(left, right) and min(top, bottom) <= float(
        pointer_y
    ) <= max(top, bottom)
    return check_point(
        center_x, center_y, pointer_x, pointer_y, tolerance_px=tolerance_px, inside=inside
    )
