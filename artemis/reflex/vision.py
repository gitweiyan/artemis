"""Small image checks on screen frames: where is the AssistiveTouch pointer?

Both the UVC capture card and the AirPlay receiver produce PNG/JPEG bytes, so this
helper is transport-agnostic. It is the ``vision.pointer`` half of I2: capture
chooses the eyes, this finds the pointer, and the skill's aim loop decides the move.
"""

from __future__ import annotations

import io

from PIL import Image, ImageChops


def _gray(image: bytes) -> Image.Image:
    return Image.open(io.BytesIO(image)).convert("L")


def pointer(
    before: bytes,
    after: bytes,
    near: tuple[float, float],
    shift: float,
    reach: tuple[float, float] = (0.12, 0.08),
) -> tuple[float, float] | None:
    """Where the pointer is in ``before``, given it moved right by ``shift`` in ``after``.

    Only the pointer should move between the two frames. The changed pixels within
    ``reach`` of ``near`` are the pointer at both spots; their centroid sits
    ``shift / 2`` to the right of the first position. Returns normalized ``(x, y)``
    with origin top-left, or ``None`` when the change is empty, too large, too
    spread out, or outside ``reach``.

    This is deliberately a *helper*, not a policy: callers pass the expected
    position and the known move (e.g. the nudge used to expose the pointer). It does
    not decide whether the pointer is visible enough to click.
    """
    a, b = _gray(before), _gray(after)
    if a.size != b.size:
        return None

    width, height = a.size
    reach_x, reach_y = reach
    left = max(0, int((near[0] - reach_x) * width))
    top = max(int(0.05 * height), int((near[1] - reach_y) * height))  # skip the status bar
    right = min(width, int((near[0] + reach_x) * width))
    bottom = min(height, int((near[1] + reach_y) * height))
    if right <= left or bottom <= top:
        return None

    diff = ImageChops.difference(
        a.crop((left, top, right, bottom)), b.crop((left, top, right, bottom))
    ).tobytes()
    box_width = right - left
    hits = [(i % box_width, i // box_width) for i, value in enumerate(diff) if value > 30]
    if not 15 <= len(hits) <= len(diff) // 4:  # nothing moved, or much more than a pointer
        return None

    xs = [x for x, _ in hits]
    ys = [y for _, y in hits]
    if (
        max(xs) - min(xs) > 0.15 * width or max(ys) - min(ys) > 0.07 * height
    ):  # not one compact spot
        return None

    center_x = sum(xs) / len(xs) + left
    center_y = sum(ys) / len(ys) + top
    return center_x / width - shift / 2, center_y / height


__all__ = ["pointer"]
