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

"""The crop is measured, not remembered: one native grab, then the phone rectangle."""

from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace

from PIL import Image

from artemis.drivers.ios.crop import detect_uvc_crop

RIG_BOX = (736, 53, 1185, 1027)


def _frame(size=(1920, 1080), box=RIG_BOX, color=(30, 30, 34)):
    image = Image.new("RGB", size, (0, 0, 0))
    if box is not None:
        left, top, right, bottom = box
        image.paste(Image.new("RGB", (right - left, bottom - top), color), (left, top))
        for y in range(top + 8, bottom - 8, 22):
            image.paste((200, 200, 205), (left + 6, y, right - 6, y + 2))
    out = BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def test_the_phone_rectangle_is_measured_from_one_native_frame():
    grabbed = []

    def grab():
        grabbed.append(1)
        return SimpleNamespace(image=_frame(), width=1920, height=1080)

    crop, why = detect_uvc_crop(device="USB Video", size=(1920, 1080), grab=grab)

    assert crop == RIG_BOX
    assert len(grabbed) == 1, "one grab, not a loop"
    assert "449x974" in why


def test_a_black_frame_is_reported_not_guessed():
    def grab():
        return SimpleNamespace(image=_frame(box=None), width=1920, height=1080)

    crop, why = detect_uvc_crop(device="USB Video", size=(1920, 1080), grab=grab)

    assert crop is None
    assert "no phone rectangle" in why


def test_a_capture_failure_is_reported_not_raised():
    def grab():
        raise RuntimeError("uvc capture failed: device busy")

    crop, why = detect_uvc_crop(device="USB Video", size=(1920, 1080), grab=grab)

    assert crop is None
    assert "device busy" in why
