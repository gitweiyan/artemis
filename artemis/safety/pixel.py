"""Did the click do anything? Compare pixels, not prose.

A click is verified by the *screen*, and the most informative place to look is the target
itself: a press highlight, a dialog opening over it, or nothing at all. Whole-frame diffing
is still needed (a sheet can appear elsewhere), so verification is "the target crop changed
*or* the frame changed" — permissive about what proves the click landed, strict about
reporting `no_effect` when nothing did.

Everything here is a pure function over image bytes, so the rule can be tested without a
device and reused by the pointer controller, the smoke and replay.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageChops

__all__ = [
    "DEFAULT_CROP_SIZE_PX",
    "PixelVerdict",
    "changed_fraction",
    "crop_around",
    "verify_effect",
]

#: The window compared around a target: large enough for a dialog edge, small enough that
#: unrelated activity elsewhere on the screen does not decide the verdict.
DEFAULT_CROP_SIZE_PX = 240
#: Below this fraction of changed pixels a crop is "unchanged" (JPEG noise, caret blink).
DEFAULT_MIN_CHANGED_FRACTION = 0.01
#: Pixels this much different count as changed at all.
DIFF_THRESHOLD = 15


def _decode(image: bytes) -> Image.Image:
    return Image.open(BytesIO(image)).convert("L")


def crop_around(
    image: bytes,
    point: tuple[float, float],
    *,
    size_px: int = DEFAULT_CROP_SIZE_PX,
) -> bytes:
    """A square PNG around ``point`` (pixels), clamped to the image."""
    frame = Image.open(BytesIO(image)).convert("RGB")
    width, height = frame.size
    half = max(1, int(size_px) // 2)
    x, y = int(point[0]), int(point[1])
    left = max(0, min(width - 1, x - half))
    top = max(0, min(height - 1, y - half))
    right = min(width, max(left + 1, x + half))
    bottom = min(height, max(top + 1, y + half))
    out = BytesIO()
    frame.crop((left, top, right, bottom)).save(out, "PNG")
    return out.getvalue()


def changed_fraction(
    before: bytes,
    after: bytes,
    *,
    ignore_top: float = 0.0,
    threshold: int = DIFF_THRESHOLD,
) -> float:
    """Fraction of pixels that differ between two images (1.0 when sizes differ)."""
    a, b = _decode(before), _decode(after)
    if a.size != b.size:
        return 1.0
    width, height = a.size
    start_row = int(max(0.0, min(0.9, ignore_top)) * height)
    diff = ImageChops.difference(a, b).tobytes()
    total = max(1, (height - start_row) * width)
    changed = sum(
        1 for index, value in enumerate(diff) if value > threshold and index // width >= start_row
    )
    return changed / total


@dataclass(frozen=True)
class PixelVerdict:
    """Whether the screen reacted to an action, and how much of the target did."""

    ok: bool
    incident: str | None = None
    crop_change: float | None = None
    frame_change: float | None = None


def verify_effect(
    before: bytes,
    after: bytes,
    point: tuple[float, float],
    *,
    size_px: int = DEFAULT_CROP_SIZE_PX,
    min_crop_change: float = DEFAULT_MIN_CHANGED_FRACTION,
    ignore_top: float = 0.06,
) -> PixelVerdict:
    """Decide ``no_effect`` from the target crop *or* the frame, whichever changed.

    ``point`` is in the frame's pixels. The status-bar clock area is ignored so a minute
    rollover cannot pass for a reaction.
    """
    frame_change = changed_fraction(before, after, ignore_top=ignore_top)
    try:
        crop_before = crop_around(before, point, size_px=size_px)
        crop_after = crop_around(after, point, size_px=size_px)
        crop_change = changed_fraction(crop_before, crop_after)
    except (OSError, ValueError):
        crop_change = None
    changed = (crop_change is not None and crop_change >= min_crop_change) or (
        frame_change >= min_crop_change
    )
    if changed:
        return PixelVerdict(True, None, crop_change, frame_change)
    return PixelVerdict(False, "no_effect", crop_change, frame_change)
