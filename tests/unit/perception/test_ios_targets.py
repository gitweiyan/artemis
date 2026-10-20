# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""On the iOS springboard the icon opens the app; the label under it does nothing."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from artemis.perception.ios_targets import icon_above_label

FRAME = (449, 974)


def _png(draw) -> bytes:
    image = np.full((FRAME[1], FRAME[0], 3), 235, dtype=np.uint8)
    draw(image)
    return cv2.imencode(".png", image)[1].tobytes()


def _label(png: bytes, box_rel):
    """A target for a text line at ``box_rel`` (normalized)."""
    from artemis.perception.base import Target

    return Target("微信", box_rel, 0.9, "ocr")


def _draw_icon(image, left, top, size=48):
    """A busy icon: a plate plus inner detail (a real icon is full of edges)."""
    cv2.rectangle(image, (left, top), (left + size, top + size), (60, 160, 60), -1)
    cv2.circle(image, (left + size // 2, top + size // 2), size // 4, (240, 240, 240), 3)
    cv2.line(image, (left + 6, top + 6), (left + size - 6, top + size - 6), (20, 20, 20), 2)


def test_the_icon_above_a_label_is_returned_instead_of_the_text():
    def draw(image):
        _draw_icon(image, 104, 320)  # the icon: 48 px tall, ending 2 px above the label
        cv2.rectangle(image, (108, 370), (148, 386), (90, 90, 90), -1)  # 16 px label

    target = _label(_png(draw), (108 / 449, 370 / 974, 148 / 449, 386 / 974))

    icon = icon_above_label(_png(draw), target.box)

    assert icon is not None
    center_x = (icon.box[0] + icon.box[2]) / 2 * 449
    center_y = (icon.box[1] + icon.box[3]) / 2 * 974
    assert abs(center_x - 128) < 6, "the icon's horizontal centre"
    assert abs(center_y - 344) < 8, "the icon's vertical centre, above the label"
    assert icon.source == "icon-above-label"


def test_plain_text_with_nothing_above_it_is_left_alone():
    def draw(image):
        cv2.rectangle(image, (108, 500), (148, 512), (90, 90, 90), -1)

    target = _label(_png(draw), (108 / 449, 500 / 974, 148 / 449, 512 / 974))

    assert icon_above_label(_png(draw), target.box) is None


def test_an_unrelated_block_off_to_the_side_is_not_mistaken_for_an_icon():
    def draw(image):
        _draw_icon(image, 20, 320)  # far left
        cv2.rectangle(image, (300, 370), (340, 386), (90, 90, 90), -1)  # the label

    target = _label(_png(draw), (300 / 449, 370 / 974, 340 / 449, 386 / 974))

    assert icon_above_label(_png(draw), target.box) is None


def test_a_flat_coloured_block_is_not_an_icon():
    """Walpaper/plates are smooth; an icon is busy. Colour alone is not the test."""

    def draw(image):
        cv2.rectangle(image, (104, 320), (152, 368), (60, 160, 60), -1)
        cv2.rectangle(image, (108, 370), (148, 386), (90, 90, 90), -1)

    box = (108 / 449, 370 / 974, 148 / 449, 386 / 974)

    assert icon_above_label(_png(draw), box) is None


def test_a_label_at_the_very_top_has_no_room_for_an_icon():
    target = _label(_png(lambda image: None), (0.4, 0.01, 0.5, 0.02))

    assert icon_above_label(_png(lambda image: None), target.box) is None


def test_an_undecodable_frame_is_ignored():
    assert icon_above_label(b"not an image", (0.4, 0.4, 0.5, 0.5)) is None


def test_the_adjusted_target_is_above_the_original():
    def draw(image):
        _draw_icon(image, 104, 320)
        cv2.rectangle(image, (108, 370), (148, 386), (90, 90, 90), -1)

    box = (108 / 449, 370 / 974, 148 / 449, 386 / 974)
    icon = icon_above_label(_png(draw), box)

    assert icon is not None
    assert icon.box[3] < box[1], "the icon sits entirely above the label"
