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

"""Where the phone's picture sits inside the capture card's letterboxed frame.

The rig carried that rectangle as a hand-measured constant (``ARTEMIS_UVC_CROP``).
Re-seat the phone or swap the card and the constant is silently wrong, which moves
every tap by the error. These tests pin the measurement instead.
"""

from __future__ import annotations

import random
from io import BytesIO

import pytest
from PIL import Image

from artemis.perception.frame_bounds import detect_content_box, frame_aspect_mismatch

#: The live rig's measured rectangle inside a 1920x1080 card frame.
RIG_BOX = (736, 53, 1185, 1027)


def _black(width: int, height: int) -> Image.Image:
    return Image.new("RGB", (width, height), (0, 0, 0))


def _flat(width: int, height: int) -> Image.Image:
    return Image.new("RGB", (width, height), (40, 40, 44))


def _dark_content(width: int, height: int) -> Image.Image:
    """A dark settings-like screen: near-black background with bright text lines."""
    image = Image.new("RGB", (width, height), (12, 12, 14))
    for y in range(8, height - 8, 22):
        image.paste((200, 200, 205), (6, y, max(7, width - 6), y + 2))
    return image


def _png(size, box, *, background=(0, 0, 0), content=_dark_content) -> bytes:
    image = Image.new("RGB", size, background)
    left, top, right, bottom = box
    image.paste(content(right - left, bottom - top), (left, top))
    out = BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def test_the_phone_rectangle_is_found_in_a_letterboxed_frame():
    frame = _png((1920, 1080), RIG_BOX)

    assert detect_content_box(frame) == RIG_BOX


def test_black_bands_inside_the_phone_picture_are_not_trimmed():
    """A dark UI has black rows of its own; trimming may only eat the outer bars."""
    width, height = RIG_BOX[2] - RIG_BOX[0], RIG_BOX[3] - RIG_BOX[1]
    image = Image.new("RGB", (1920, 1080), (0, 0, 0))
    phone = _dark_content(width, height)
    phone.paste((0, 0, 0), (0, height // 2, width, height // 2 + 120))  # an all-black band
    image.paste(phone, (RIG_BOX[0], RIG_BOX[1]))
    out = BytesIO()
    image.save(out, "PNG")

    assert detect_content_box(out.getvalue()) == RIG_BOX


def test_a_frame_without_bars_is_the_whole_frame():
    """A portrait source with no letterbox needs no crop: the box is the frame."""
    frame = _png((1080, 2400), (0, 0, 1080, 2400), content=_flat)

    assert detect_content_box(frame) == (0, 0, 1080, 2400)


def test_a_stray_specular_pixel_in_a_bar_does_not_grow_the_box():
    image = Image.new("RGB", (1920, 1080), (0, 0, 0))
    image.paste(_dark_content(RIG_BOX[2] - RIG_BOX[0], RIG_BOX[3] - RIG_BOX[1]), RIG_BOX[:2])
    image.putpixel((10, 500), (255, 255, 255))  # one lit pixel in the left bar
    out = BytesIO()
    image.save(out, "PNG")

    assert detect_content_box(out.getvalue()) == RIG_BOX


def test_an_all_black_frame_reports_nothing():
    assert detect_content_box(_png((1920, 1080), (0, 0, 1920, 1080), content=_black)) is None


def test_a_landscape_box_is_rejected_as_a_mis_detection():
    """A phone picture is portrait; a landscape blob means the detector found the wrong thing."""
    frame = _png((1920, 1080), (100, 300, 1500, 800), content=_flat)

    assert detect_content_box(frame) is None


def test_a_box_that_is_too_small_is_rejected():
    frame = _png((1920, 1080), (900, 300, 980, 480), content=_flat)

    assert detect_content_box(frame) is None


def test_sparse_card_noise_in_the_bars_does_not_turn_the_box_into_the_frame():
    image = Image.new("RGB", (1920, 1080), (0, 0, 0))
    image.paste(_dark_content(RIG_BOX[2] - RIG_BOX[0], RIG_BOX[3] - RIG_BOX[1]), RIG_BOX[:2])
    rng = random.Random(7)
    for _ in range(400):  # ~0.02% of the bar area, dimmer than a lit UI pixel at (12,12,14)
        image.putpixel((rng.randrange(0, 700), rng.randrange(0, 1080)), (9, 9, 9))
    out = BytesIO()
    image.save(out, "PNG")

    assert detect_content_box(out.getvalue()) == RIG_BOX


@pytest.mark.parametrize(
    "frame_size,screen_size,expected",
    [
        ((449, 974), (1080, 2400), 0.024),  # the live rig: 2.4% off, tolerated
        ((449, 600), (1080, 2400), 0.399),  # a crop that cuts the phone short
        ((1080, 2400), (1080, 2400), 0.0),
    ],
)
def test_frame_aspect_mismatch_is_reported_as_a_ratio(frame_size, screen_size, expected):
    assert frame_aspect_mismatch(frame_size, screen_size) == pytest.approx(expected, abs=0.002)
