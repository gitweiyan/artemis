"""Small image checks on screen frames: where is the AssistiveTouch pointer?

Both the UVC capture card and the AirPlay receiver produce PNG/JPEG bytes, so this
helper is transport-agnostic. It is the ``vision.pointer`` half of I2: capture
chooses the eyes, this finds the pointer, and the skill's aim loop decides the move.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageChops


def _cv2():
    import cv2  # type: ignore

    return cv2


def _numpy():
    import numpy as np  # type: ignore

    return np


@dataclass(frozen=True)
class Pointer:
    """A pointer reading in normalized screen coordinates (origin top-left)."""

    x: float
    y: float
    confidence: float
    method: str


class PointerTracker:
    """Find the AssistiveTouch pointer without confusing it with its menu button.

    The menu button and the pointer are both gray circles; a raw template match
    can confidently return the menu button. ``menu_box`` (pixel coordinates in the
    captured frame) masks the actual button before matching; it is configurable
    because AssistiveTouch lets the user drag the button. The empty default means
    "no mask", so a caller that knows the button position should pass it. Single-
    frame detection needs a pointer template; ``locate()`` is the two-frame
    nudge/diff path and needs no template. Low confidence is a valid "do not click".
    """

    def __init__(
        self,
        template: bytes | None = None,
        *,
        menu_box: tuple[int, int, int, int] = (0, 0, 0, 0),
        threshold: float = 0.7,
    ):
        self.menu_box = tuple(int(value) for value in menu_box)
        self.threshold = float(threshold)
        self._template = None
        if template is not None:
            cv2 = _cv2()
            np = _numpy()
            decoded = cv2.imdecode(np.frombuffer(template, np.uint8), cv2.IMREAD_COLOR)
            if decoded is None:
                raise ValueError("pointer template is not a decodable image")
            self._template = decoded

    def _mask_menu(self, image):
        height, width = image.shape[:2]
        left, top, right, bottom = self.menu_box
        left = max(0, min(width, left))
        right = max(0, min(width, right))
        top = max(0, min(height, top))
        bottom = max(0, min(height, bottom))
        if right > left and bottom > top:
            image[top:bottom, left:right] = 0
        return image

    def _in_menu(self, x: float, y: float, width: int, height: int) -> bool:
        left, top, right, bottom = self.menu_box
        px, py = x * width, y * height
        return left <= px <= right and top <= py <= bottom

    def detect(self, frame: bytes) -> Pointer | None:
        """Find the pointer in one frame using the configured template."""
        if self._template is None:
            raise ValueError(
                "detect() needs a pointer template; use locate() for a two-frame nudge"
            )
        cv2 = _cv2()
        np = _numpy()
        image = cv2.imdecode(np.frombuffer(frame, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError("frame is not a decodable image")
        height, width = image.shape[:2]
        template_height, template_width = self._template.shape[:2]
        if template_height > height or template_width > width:
            raise ValueError("pointer template is larger than the frame")

        masked = self._mask_menu(image.copy())
        result = cv2.matchTemplate(masked, self._template, cv2.TM_CCOEFF_NORMED)
        _min, score, _min_loc, max_loc = cv2.minMaxLoc(result)
        if score < self.threshold:
            return None
        x = (max_loc[0] + template_width / 2) / width
        y = (max_loc[1] + template_height / 2) / height
        return Pointer(round(x, 4), round(y, 4), round(float(score), 4), "template")

    def _diff_confidence(self, before: bytes, after: bytes, x: float, y: float) -> float:
        a, b = _gray(before), _gray(after)
        if a.size != b.size:
            return 0.0
        width, height = a.size
        window_x, window_y = 0.10 * width, 0.10 * height
        left = max(0, int(x * width - window_x))
        right = min(width, int(x * width + window_x))
        top = max(int(0.05 * height), int(y * height - window_y))
        bottom = min(height, int(y * height + window_y))
        if right <= left or bottom <= top:
            return 0.0
        diff = ImageChops.difference(
            a.crop((left, top, right, bottom)), b.crop((left, top, right, bottom))
        ).tobytes()
        changed = sum(1 for value in diff if value > 30)
        return min(0.99, 0.5 + changed / 2000.0)

    def locate(
        self,
        before: bytes,
        after: bytes,
        near: tuple[float, float],
        shift: float,
        reach: tuple[float, float] = (0.12, 0.08),
    ) -> Pointer | None:
        """Two-frame nudge/diff location; no template required."""
        found = pointer(before, after, near, shift, reach)
        if found is None:
            return None
        a = _gray(before)
        width, height = a.size
        if self._in_menu(found[0], found[1], width, height):
            return None
        confidence = self._diff_confidence(before, after, found[0], found[1])
        return Pointer(round(found[0], 4), round(found[1], 4), round(confidence, 4), "diff")


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


__all__ = ["Pointer", "PointerTracker", "pointer"]
