"""Turn a matched *label* into the tappable *icon* above it (iOS springboard).

On the iOS Home screen an app's text sits **below** its icon, and only the icon opens the
app — a click on the label does nothing at all. That is not a click failure to retry; it is
the wrong point. Every OCR target on the springboard therefore has to be lifted onto the
icon before anyone taps it.

The lift is geometric and appearance-agnostic: an app icon sits directly above its label,
centred on it, about three label-heights tall. What distinguishes an icon from wallpaper or
an empty area is *busyness* — a real icon is full of edges (the 设置 gear included; it is
grey, so colour cannot be the test). An in-app label (a tab, a button caption, a list row)
has no busy block above it, so those targets are left exactly where the text is, which is
where their tap belongs.
"""

from __future__ import annotations

from artemis.perception.base import Target

__all__ = ["icon_above_label"]

#: An app icon is about this many label-heights tall (measured on the rig: 50 px icon,
#: 16 px label box).
_ICON_LABEL_RATIO = 3.0
#: The icon's bottom edge sits this many label-heights above the label's top.
_GAP_LABEL_HEIGHTS = 0.15
#: Edge density an icon must show absolutely, and relative to the calmer of its two
#: neighbours at the same height (grid neighbours alternate with wallpaper gaps, so the
#: calmer side is the background — a global frame ratio fails on a textured wallpaper).
_MIN_EDGE_DENSITY = 0.06
_NEIGHBOUR_DENSITY_FACTOR = 1.8
#: The interior must be busy too: a flat coloured plate has edges only on its border.
_MIN_INNER_DENSITY = 0.03
_INNER_FRACTION = 0.6


def _decode(image: bytes):
    import cv2
    import numpy as np

    return cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_COLOR)


def _edge_density(edges, box: tuple[int, int, int, int]) -> float:
    left, top, right, bottom = box
    region = edges[top:bottom, left:right]
    if region.size == 0:
        return 0.0
    return float((region > 0).mean())


def icon_above_label(image: bytes, box: tuple[float, float, float, float]) -> Target | None:
    """The icon box above a label box (both normalized 0–1), or ``None``.

    ``box`` is the matched text. The returned ``Target`` carries the *icon's* geometry so a
    caller taps the icon without needing to know why.
    """
    cv2 = __import__("cv2")
    decoded = _decode(image)
    if decoded is None:
        return None
    height, width = decoded.shape[:2]
    left = int(round(box[0] * width))
    right = int(round(box[2] * width))
    top = int(round(box[1] * height))
    bottom = int(round(box[3] * height))
    label_height = max(1, bottom - top)
    if left >= right or label_height <= 0:
        return None

    size = max(8, int(round(_ICON_LABEL_RATIO * label_height)))
    gap = int(round(_GAP_LABEL_HEIGHTS * label_height))
    icon_bottom = top - gap
    icon_top = icon_bottom - size
    center_x = (left + right) // 2
    icon_left = center_x - size // 2
    icon_right = icon_left + size
    if icon_top < 0 or icon_left < 0 or icon_right > width:
        return None

    gray = cv2.cvtColor(decoded, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 60, 160)
    candidate = (icon_left, icon_top, icon_right, icon_bottom)
    density = _edge_density(edges, candidate)
    if density < _MIN_EDGE_DENSITY:
        return None

    inset_x = int((icon_right - icon_left) * (1 - _INNER_FRACTION) / 2)
    inset_y = int((icon_bottom - icon_top) * (1 - _INNER_FRACTION) / 2)
    inner = (
        icon_left + inset_x,
        icon_top + inset_y,
        icon_right - inset_x,
        icon_bottom - inset_y,
    )
    if _edge_density(edges, inner) < _MIN_INNER_DENSITY:
        return None  # a flat plate, not an icon with details

    # Compare with the calmer neighbour on the same row: one side is normally wallpaper.
    side_width = int(size * 0.6)
    gap = max(2, int(size * 0.15))
    neighbours = [
        _edge_density(
            edges, (icon_left - gap - side_width, icon_top, icon_left - gap, icon_bottom)
        ),
        _edge_density(
            edges, (icon_right + gap, icon_top, icon_right + gap + side_width, icon_bottom)
        ),
    ]
    background = min(neighbours)
    if density < _NEIGHBOUR_DENSITY_FACTOR * background:
        return None

    return Target(
        label="",
        box=(
            icon_left / width,
            icon_top / height,
            icon_right / width,
            icon_bottom / height,
        ),
        confidence=1.0,
        source="icon-above-label",
    )
