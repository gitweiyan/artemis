# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Where the phone's picture sits inside a capture card's letterboxed frame.

A UVC card hands back its own raster (1920x1080 here) with the phone occupying a
sub-rectangle and black bars around it. The rig used to carry that rectangle as a
hand-measured constant (``ARTEMIS_UVC_CROP``); re-seating the phone or swapping the
card silently invalidated it, and a crop that is 2% off moves every tap by 2% of the
screen. So the rectangle is measured from a frame instead: the bars are the only
all-black rows/columns, and trimming stops at the first lit row/column from each edge,
so a dark UI (or a black band inside the picture) is never eaten.
"""

from __future__ import annotations

from typing import Iterable

#: A pixel at or below this on every channel is a bar pixel on this card. A dark UI
#: background sits just above it (measured 12–14 on the rig), which is what keeps a
#: dark screen from being mistaken for a bar.
PIXEL_THRESHOLD = 8
#: A bar may carry a stray specular pixel (0.05% of a 1920-wide row is still a bar).
QUIET_FRACTION = 0.995
#: Anything smaller than this is a mis-detection, not a phone.
MIN_SIDE_PX = 100
#: A phone picture is portrait: height / width. Landscape or square means the detector
#: locked onto the wrong rectangle.
ASPECT_RANGE = (1.2, 3.0)


def _decode(png: bytes):
    from io import BytesIO

    import numpy as np
    from PIL import Image

    with Image.open(BytesIO(png)) as image:
        return np.asarray(image.convert("RGB"))


def _trim(quiet: Iterable[bool]) -> tuple[int, int]:
    """``(start, stop)`` of the run that is *not* quiet, exclusive at both ends."""
    flags = list(quiet)
    start = 0
    while start < len(flags) and flags[start]:
        start += 1
    stop = len(flags)
    while stop > start and flags[stop - 1]:
        stop -= 1
    return start, stop


def detect_content_box(
    png: bytes,
    *,
    threshold: int = PIXEL_THRESHOLD,
    quiet_fraction: float = QUIET_FRACTION,
    min_side_px: int = MIN_SIDE_PX,
    aspect_range: tuple[float, float] = ASPECT_RANGE,
) -> tuple[int, int, int, int] | None:
    """The phone's rectangle inside ``png`` as ``(left, top, right, bottom)``, or None.

    ``right``/``bottom`` are exclusive, matching PIL's crop box, so the result can be
    handed straight to ``UvcCapture(crop=...)``. None means "no picture here" (an
    all-black frame) or "the rectangle does not look like a phone" (a mis-detection).
    """
    try:
        pixels = _decode(png)
    except (OSError, ValueError, TypeError):
        # A truncated or non-PNG grab is "no measurement", not a crash: the caller falls
        # back to its configured crop and says why.
        return None
    height, width = pixels.shape[:2]
    if height < 2 or width < 2:
        return None
    lit = pixels.max(axis=2) > threshold
    bar_rows = lit.mean(axis=1) <= 1.0 - quiet_fraction
    bar_columns = lit.mean(axis=0) <= 1.0 - quiet_fraction
    top, bottom = _trim(bar_rows)
    left, right = _trim(bar_columns)
    if bottom - top < min_side_px or right - left < min_side_px:
        return None
    aspect = (bottom - top) / (right - left)
    if not (aspect_range[0] <= aspect <= aspect_range[1]):
        return None
    return left, top, right, bottom


def frame_aspect_mismatch(frame_size: tuple[int, int], screen_size: tuple[int, int]) -> float:
    """How far the captured frame's aspect is from the screen's, as a fraction.

    A crop that frames the phone exactly reproduces the screen's aspect; a value above
    a few percent means the crop is wrong (cut short, or a wrongly located rectangle),
    and every tap is scaled by that error. The live rig reads 0.024 — the card's raster
    is not perfectly proportional — so the tolerance is a band, not zero.
    """
    frame_width, frame_height = max(1, int(frame_size[0])), max(1, int(frame_size[1]))
    screen_width, screen_height = max(1, int(screen_size[0])), max(1, int(screen_size[1]))
    return abs(((frame_height / frame_width) / (screen_height / screen_width)) - 1.0)
