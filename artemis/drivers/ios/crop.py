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

"""Measure the phone's rectangle in the capture card's frame (one native grab).

The rig's crop was a hand-measured constant for one phone in one position on one card.
Re-seat the phone, and every tap is scaled by the error with nothing in the log to say
so. This module answers the question once per run, from the frame itself; the operator
can still pin it with ``ARTEMIS_UVC_CROP`` (an explicit value is never overridden).
"""

from __future__ import annotations

from artemis.perception.frame_bounds import detect_content_box

__all__ = ["detect_uvc_crop"]


def detect_uvc_crop(
    *,
    device: str,
    size: tuple[int, int],
    framerate: int = 30,
    timeout_s: float = 12.0,
    grab=None,
) -> tuple[tuple[int, int, int, int] | None, str]:
    """``(crop, why)`` for the card's current picture; ``crop`` is None when unsure.

    ``grab`` is a test seam: it must return an object with ``.image`` (PNG bytes) at the
    card's **native** size (no crop applied). Failing to measure is not an error — the
    caller falls back to its configured crop and logs the reason.
    """
    if grab is None:
        from artemis.reflex.capture import UvcCapture

        capture = UvcCapture(
            device,
            size=size,
            framerate=framerate,
            crop=None,
            timeout_s=timeout_s,
        )

        def grab():
            return capture.grab()

    # ``CaptureError`` is a RuntimeError; ffmpeg/OS failures arrive as OSError or
    # SubprocessError. Anything else is a bug and should surface.
    try:
        frame = grab()
    except (RuntimeError, OSError, ValueError) as exc:
        return None, f"capture failed: {exc}"

    image = getattr(frame, "image", None)
    if not isinstance(image, (bytes, bytearray)):
        return None, "the grab returned no image bytes"
    box = detect_content_box(bytes(image))
    if box is None:
        return None, (
            "no phone rectangle found in the frame (all black, or the picture has no"
            " letterbox edge to measure against)"
        )
    width, height = box[2] - box[0], box[3] - box[1]
    return box, f"{width}x{height} inside {size[0]}x{size[1]}"
