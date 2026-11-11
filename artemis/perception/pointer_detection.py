"""Small image checks on screen frames: where is the AssistiveTouch pointer?

Both the UVC capture card and the AirPlay receiver produce PNG/JPEG bytes, so this
helper is transport-agnostic. It is the ``vision.pointer`` half of I2: capture
chooses the eyes, this finds the pointer, and the skill's aim loop decides the move.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageChops

__all__ = [
    "POINTER_RADIUS_PX",
    "BluePointerDetector",
    "CompositePointerDetector",
    "Pointer",
    "PointerTracker",
    "RedPointerDetector",
    "RingShapeDetector",
    "changed",
    "corridor_roi",
    "detect_in_roi",
    "frame_scale",
    "pointer",
    "pointer_radius_px",
]


def _cv2():
    import cv2

    return cv2


def _numpy():
    import numpy as np

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

    @property
    def has_template(self) -> bool:
        return self._template is not None

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


class RedPointerDetector:
    """Find the saturated red AssistiveTouch ring in one on-demand UVC frame.

    The ring is much larger than red notification badges, so size/area filters do
    most of the work. ``near`` (normalized) breaks ties between multiple red blobs
    and keeps a servo loop locked to the last known pointer.
    """

    def __init__(
        self,
        *,
        size_range: tuple[int, int] = (28, 50),
        area_range: tuple[int, int] = (500, 1600),
        hue_ranges: tuple[tuple[int, int, int, int], ...] = (
            (0, 120, 80, 12),
            (168, 120, 80, 180),
        ),
        min_confidence: float = 0.5,
        border_area_min: int = 200,
        border_min_side: int = 10,
        max_fill_ratio: float = 0.7,
    ):
        self.size_range = size_range
        self.area_range = area_range
        #: A pointer at the frame edge is cut in half by the crop, so the full-ring
        #: size/area/roundness filters cannot apply. A clipped ring is still a thin rim:
        #: a low fill ratio separates it from a solid red banner or badge at the edge.
        self.border_area_min = int(border_area_min)
        self.border_min_side = int(border_min_side)
        self.max_fill_ratio = float(max_fill_ratio)
        self.hue_ranges = hue_ranges
        self.min_confidence = min_confidence

    def detect(self, image: bytes, near: tuple[float, float] | None = None) -> Pointer | None:
        cv2 = _cv2()
        np = _numpy()
        decoded = cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_COLOR)
        if decoded is None:
            return None
        hsv = cv2.cvtColor(decoded, cv2.COLOR_BGR2HSV)
        mask = None
        for low_h, low_s, low_v, high_h in self.hue_ranges:
            part = cv2.inRange(hsv, (low_h, low_s, low_v), (high_h, 255, 255))
            mask = part if mask is None else cv2.bitwise_or(mask, part)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        count, _labels, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
        height, width = mask.shape[:2]
        candidates = []
        for index in range(1, count):
            x, y, w, h, area = (int(v) for v in stats[index])
            touches_border = x <= 1 or y <= 1 or x + w >= width - 1 or y + h >= height - 1
            if touches_border:
                if area < self.border_area_min:
                    continue
                if min(w, h) < self.border_min_side:
                    continue
                if area / float(max(1, w * h)) > self.max_fill_ratio:
                    continue
            else:
                if not (self.area_range[0] <= area <= self.area_range[1]):
                    continue
                if not (self.size_range[0] <= w <= self.size_range[1]):
                    continue
                if not (self.size_range[0] <= h <= self.size_range[1]):
                    continue
                if not 0.7 <= w / max(1, h) <= 1.4:
                    continue
                # The pointer is a ring: its center shows the background, not red.
                # Solid red ad blocks/badges have a saturated center and are rejected.
                center = mask[y + h // 2 - 3 : y + h // 2 + 4, x + w // 2 - 3 : x + w // 2 + 4]
                if center.size and float(center.mean()) > 0.35 * 255.0:
                    continue
            cx, cy = centroids[index]
            candidates.append((float(cx) / width, float(cy) / height, area))
        if not candidates:
            return None
        if near is not None:
            nx, ny = near
            candidates.sort(key=lambda item: (item[0] - nx) ** 2 + (item[1] - ny) ** 2)
        else:
            candidates.sort(key=lambda item: item[2], reverse=True)
        x, y, area = candidates[0]
        confidence = min(1.0, 0.5 + area / 2000.0)
        if confidence < self.min_confidence:
            return None
        return Pointer(x, y, confidence, "red-ring")


#: The ring's size/area windows as measured on a 449-wide frame (see ``scale`` below).
DEFAULT_SIZE_RANGE = (12, 100)
DEFAULT_AREA_RANGE = (150, 4000)


class BluePointerDetector:
    """Find a hollow blue AssistiveTouch ring; reject solid blue logos/icons."""

    def __init__(
        self,
        *,
        hue_range: tuple[int, int] = (90, 135),
        min_saturation: int = 50,
        min_value: int = 60,
        area_range: tuple[int, int] = DEFAULT_AREA_RANGE,
        size_range: tuple[int, int] = DEFAULT_SIZE_RANGE,
        min_circularity: float = 0.45,
        min_hole_ratio: float = 0.18,
        min_confidence: float = 0.5,
    ):
        self.hue_range = hue_range
        self.min_saturation = int(min_saturation)
        self.min_value = int(min_value)
        self.area_range = area_range
        self.size_range = size_range
        self.min_circularity = float(min_circularity)
        self.min_hole_ratio = float(min_hole_ratio)
        self.min_confidence = float(min_confidence)

    @classmethod
    def scaled_for(cls, frame_size: tuple[int, int] | None, **overrides) -> BluePointerDetector:
        """A detector whose size/area windows follow ``frame_size``.

        The defaults were measured on a 449-wide frame. On a 2x capture the same ring is
        twice as wide and four times the area: keeping the absolute windows would reject
        it as "too big" (or, on a smaller capture, accept UI icons as pointers). Shape
        priors (circularity, hole ratio) are scale-free and stay as they are.
        """
        scale = frame_scale(frame_size)
        options: dict = {
            "area_range": (
                round(DEFAULT_AREA_RANGE[0] * scale * scale),
                round(DEFAULT_AREA_RANGE[1] * scale * scale),
            ),
            "size_range": (
                round(DEFAULT_SIZE_RANGE[0] * scale),
                round(DEFAULT_SIZE_RANGE[1] * scale),
            ),
        }
        options.update(overrides)
        return cls(**options)

    def detect(
        self, image: bytes, near: tuple[float, float] | None = None, *, roi_px: float | None = None
    ) -> Pointer | None:
        cv2 = _cv2()
        np = _numpy()
        decoded = cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_COLOR)
        if decoded is None:
            return None
        hsv = cv2.cvtColor(decoded, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(
            hsv,
            (self.hue_range[0], self.min_saturation, self.min_value),
            (self.hue_range[1], 255, 255),
        )
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        contours, hierarchy = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
        if hierarchy is None:
            return None
        hierarchy = hierarchy[0]
        height, width = mask.shape[:2]
        candidates = []
        for index, contour in enumerate(contours):
            area = cv2.contourArea(contour)
            x, y, w, h = cv2.boundingRect(contour)
            if not (self.area_range[0] <= area <= self.area_range[1]):
                continue
            if not (self.size_range[0] <= w <= self.size_range[1]):
                continue
            if not (self.size_range[0] <= h <= self.size_range[1]):
                continue
            if not 0.65 <= w / max(1, h) <= 1.55:
                continue
            perimeter = cv2.arcLength(contour, True)
            circularity = 4 * np.pi * area / (perimeter * perimeter + 1e-6)
            if circularity < self.min_circularity:
                continue
            child = hierarchy[index][2]
            if child == -1:
                continue  # solid logo/icon: no hollow center
            hole_ratio = cv2.contourArea(contours[child]) / max(area, 1.0)
            if hole_ratio < self.min_hole_ratio:
                continue
            cx, cy = (x + w / 2) / width, (y + h / 2) / height
            if near is not None and roi_px is not None:
                dx = (cx - near[0]) * width
                dy = (cy - near[1]) * height
                if (dx * dx + dy * dy) ** 0.5 > roi_px:
                    continue
            score = circularity + 0.5 * min(1.0, hole_ratio)
            candidates.append((score, cx, cy, circularity, hole_ratio))
        if not candidates:
            return None
        if near is not None:
            candidates.sort(key=lambda item: (item[1] - near[0]) ** 2 + (item[2] - near[1]) ** 2)
        else:
            candidates.sort(reverse=True)
        _score, cx, cy, circularity, hole_ratio = candidates[0]
        confidence = min(1.0, 0.5 + 0.25 * circularity + 0.5 * hole_ratio)
        if confidence < self.min_confidence:
            return None
        return Pointer(cx, cy, confidence, "blue-ring")


class CompositePointerDetector:
    """Try detectors in order; blue first, then legacy red, then shape callers."""

    def __init__(self, detectors=None):
        self.detectors = list(detectors or [BluePointerDetector(), RedPointerDetector()])

    def detect(self, image: bytes, near: tuple[float, float] | None = None) -> Pointer | None:
        for detector in self.detectors:
            found = detector.detect(image, near=near)
            if found is not None:
                return found
        return None


class RingShapeDetector:
    """Shape-only fallback for a small pointer ring over a red/colored target."""

    def __init__(self, *, min_radius: int = 6, max_radius: int = 16, param2: int = 12):
        self.min_radius = min_radius
        self.max_radius = max_radius
        self.param2 = param2

    def detect(self, image: bytes, near: tuple[float, float] | None = None) -> Pointer | None:
        cv2 = _cv2()
        np = _numpy()
        decoded = cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_COLOR)
        if decoded is None:
            return None
        gray = cv2.cvtColor(decoded, cv2.COLOR_BGR2GRAY)
        gray = cv2.medianBlur(gray, 5)
        circles = cv2.HoughCircles(
            gray,
            cv2.HOUGH_GRADIENT,
            dp=1.2,
            minDist=20,
            param1=80,
            param2=self.param2,
            minRadius=self.min_radius,
            maxRadius=self.max_radius,
        )
        if circles is None:
            return None
        height, width = gray.shape[:2]
        candidates = [
            (float(x) / width, float(y) / height, float(radius))
            for x, y, radius in np.round(circles[0]).astype(int)
        ]
        if near is not None:
            nx, ny = near
            candidates.sort(key=lambda item: (item[0] - nx) ** 2 + (item[1] - ny) ** 2)
        else:
            candidates.sort(key=lambda item: item[2], reverse=True)
        x, y, _radius = candidates[0]
        return Pointer(x, y, 0.65, "ring-shape")


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
    # The two pointer positions are `shift` apart; their bbox is wider than one
    # pointer. Small nudges keep the old 15% bound; large nudges get a bound that
    # scales with the distance the pointer actually travelled.
    max_x_spread = max(0.15, 2 * abs(shift) + 0.05)
    if max(xs) - min(xs) > max_x_spread * width or max(ys) - min(ys) > 0.07 * height:
        return None

    center_x = sum(xs) / len(xs) + left
    center_y = sum(ys) / len(ys) + top
    return center_x / width - shift / 2, center_y / height


def changed(
    before: bytes,
    after: bytes,
    *,
    ignore_top: float = 0.06,
    threshold: int = 15,
    min_pixels: int = 30,
) -> bool:
    """Whether the content changed, ignoring the status-bar clock area."""
    a, b = _gray(before), _gray(after)
    if a.size != b.size:
        return True
    width, height = a.size
    start_row = int(ignore_top * height)
    diff = ImageChops.difference(a, b).tobytes()
    count = sum(
        1 for index, value in enumerate(diff) if value > threshold and index // width >= start_row
    )
    return count >= min_pixels


#: The AssistiveTouch pointer's radius in capture-frame pixels (the blue ring is ~40 px
#: across on this rig). Used to size a motion corridor when no measurement exists yet.
POINTER_RADIUS_PX = 20.0
#: The frame width the pixel thresholds above were measured on. Every other frame size
#: scales by ``frame_width / REFERENCE_FRAME_WIDTH``; a 2x capture halves the meaning of
#: "8 px" and doubles the ring, so the thresholds are ratios, not constants.
REFERENCE_FRAME_WIDTH = 449.0


def frame_scale(frame_size: tuple[int, int] | None) -> float:
    """How much bigger/smaller a capture is than the frame the thresholds were measured on."""
    width = float(frame_size[0]) if frame_size else REFERENCE_FRAME_WIDTH
    return max(0.05, width / REFERENCE_FRAME_WIDTH)


def pointer_radius_px(frame_size: tuple[int, int] | None) -> float:
    """The ring's radius on ``frame_size``, scaled from the reference measurement."""
    return POINTER_RADIUS_PX * frame_scale(frame_size)


def corridor_roi(
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    radius: float,
    frame_size: tuple[int, int],
) -> tuple[int, int, int, int]:
    """The pixel box around a pointer's travelled segment, padded and clipped.

    ``start``/``end`` are frame pixels (the last observed position and the prediction);
    the search only has to cover the segment plus the prediction's uncertainty.
    """
    width, height = int(frame_size[0]), int(frame_size[1])
    pad = max(0.0, float(radius))
    left = int(min(start[0], end[0]) - pad)
    top = int(min(start[1], end[1]) - pad)
    right = int(max(start[0], end[0]) + pad)
    bottom = int(max(start[1], end[1]) + pad)
    left, top = max(0, min(width - 1, left)), max(0, min(height - 1, top))
    right, bottom = max(left + 1, min(width, right)), max(top + 1, min(height, bottom))
    return left, top, right, bottom


def detect_in_roi(
    detector,
    image: bytes,
    roi: tuple[int, int, int, int],
    *,
    near: tuple[float, float] | None = None,
) -> Pointer | None:
    """Detect the pointer inside ``roi``; the result is in full-frame coordinates.

    ``near`` is full-frame normalized (the detector compares it against its own
    candidates), so it is re-expressed in the crop before the call.
    """
    x1, y1, x2, y2 = (int(value) for value in roi)
    with Image.open(io.BytesIO(image)) as original:
        full = original.convert("RGB") if original.mode != "RGB" else original
        width, height = full.size
        x1, y1 = max(0, min(width - 1, x1)), max(0, min(height - 1, y1))
        x2, y2 = max(x1 + 1, min(width, x2)), max(y1 + 1, min(height, y2))
        crop = full.crop((x1, y1, x2, y2))
        crop_width, crop_height = crop.size
        buffer = io.BytesIO()
        crop.save(buffer, "PNG")
    crop_near = None
    if near is not None:
        crop_near = (
            (float(near[0]) * width - x1) / crop_width,
            (float(near[1]) * height - y1) / crop_height,
        )
    found = detector.detect(buffer.getvalue(), near=crop_near)
    if found is None:
        return None
    return Pointer(
        (x1 + found.x * crop_width) / width,
        (y1 + found.y * crop_height) / height,
        found.confidence,
        found.method,
    )


__all__ = [
    "POINTER_RADIUS_PX",
    "REFERENCE_FRAME_WIDTH",
    "Pointer",
    "PointerTracker",
    "RedPointerDetector",
    "RingShapeDetector",
    "changed",
    "corridor_roi",
    "detect_in_roi",
    "frame_scale",
    "pointer_radius_px",
    "pointer",
]
