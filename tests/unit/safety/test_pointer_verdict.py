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

"""One definition of "the pointer is on the target", shared by every click path."""

import pytest

from artemis.safety.pointer import check_in_box, check_point


def test_a_centered_pointer_may_click():
    verdict = check_point(100, 100, 102, 103)

    assert verdict.ok is True
    assert verdict.incident is None
    assert verdict.error_px == pytest.approx(3.6, abs=0.1)


def test_a_pointer_inside_the_target_but_off_center_is_not_centered():
    verdict = check_point(100, 100, 100, 120, tolerance_px=8.0, inside=True)

    assert verdict.ok is False
    assert verdict.incident == "pointer_not_centered"
    assert verdict.error_px == pytest.approx(20.0)


def test_a_pointer_outside_the_target_is_off_target():
    verdict = check_in_box((80, 80, 120, 120), 200, 200)

    assert verdict.ok is False
    assert verdict.incident == "pointer_off_target"


def test_a_point_inside_the_box_and_within_tolerance_passes():
    verdict = check_in_box((80, 80, 120, 120), 101, 99)

    assert verdict.ok is True


def test_an_inverted_box_is_still_understood():
    """OCR can hand back (right, bottom, left, top) after a crop; the rule must not care."""
    verdict = check_in_box((120, 120, 80, 80), 101, 99)

    assert verdict.ok is True


def test_the_tolerance_is_the_only_policy_knob():
    strict = check_point(100, 100, 110, 100, tolerance_px=8.0)
    loose = check_point(100, 100, 110, 100, tolerance_px=12.0)

    assert strict.ok is False
    assert loose.ok is True
