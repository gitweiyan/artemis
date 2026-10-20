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

"""The arithmetic behind the XML safety net, tested without a validator or an LLM."""

from __future__ import annotations

import pytest

from artemis.safety.xml import (
    area,
    center,
    contains_point,
    find_element_at,
    iou,
    parse_bounds,
    size_mismatch,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("[0,0][10,20]", (0, 0, 10, 20)),
        ("[-5,-6][7,8]", (-5, -6, 7, 8)),
        ([0, 0, 10, 20], (0, 0, 10, 20)),
        (None, None),
        ("", None),
        ("nonsense", None),
        ([1, 2, 3], None),
    ],
)
def test_bounds_are_parsed_from_every_shape_android_uses(raw, expected):
    assert parse_bounds(raw) == expected


def test_area_and_center_are_the_obvious_values():
    assert area((10, 20, 30, 60)) == 20 * 40
    assert center((10, 20, 30, 61)) == (20, 40)
    assert area((30, 20, 10, 60)) == 0, "an inverted box has no area"


def test_containment_includes_the_edges():
    assert contains_point((0, 0, 10, 10), 10, 10) is True
    assert contains_point((0, 0, 10, 10), 11, 5) is False


def test_iou_is_one_for_identical_boxes_and_zero_without_overlap():
    assert iou((0, 0, 10, 10), (0, 0, 10, 10)) == pytest.approx(1.0)
    assert iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0
    assert iou((0, 0, 10, 10), (5, 0, 15, 10)) == pytest.approx(50 / 150)


def test_a_widget_does_not_match_the_container_behind_it():
    widget = (100, 100, 140, 140)
    container = (0, 0, 1080, 2400)

    assert size_mismatch(widget, container) is True
    assert size_mismatch(widget, (100, 100, 141, 141)) is False


def test_the_smallest_element_under_a_point_wins():
    elements = [
        {"text": "root", "bounds": "[0,0][1080,2400]"},
        {"text": "row", "bounds": "[0,500][1080,600]"},
        {"text": "button", "bounds": "[900,520][1000,580]"},
        {"text": "elsewhere", "bounds": "[0,0][10,10]"},
        "not a dict",
    ]

    found = find_element_at(elements, 950, 550)

    assert found is not None
    element, bounds = found
    assert element["text"] == "button"
    assert bounds == (900, 520, 1000, 580)


def test_no_element_at_the_point():
    assert find_element_at([{"bounds": "[0,0][10,10]"}], 500, 500) is None
