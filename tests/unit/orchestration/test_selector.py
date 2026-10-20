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

"""Choosing a device's perception/control composition is a table, not a platform if.

Target doc §5: app tree + absolute tap is fastest and most stable; without a tree, UVC
plus a relative pointer is the next best thing; a camera is the last resort. The decision
is a pure function of what the device reports, so it can be tested without a device — and
adding a composition later is a table entry plus a builder, not another branch in the
driver factory.
"""

from __future__ import annotations

import pytest

from artemis.devices.capabilities import CapabilitySet
from artemis.orchestration.selector import (
    CompositionError,
    DeviceProfile,
    select_composition,
)

#: What the iOS rig reports: relative pointer, mandatory precheck, no tree, no absolute tap.
IOS_RIG = CapabilitySet(
    supports_relative_pointer=True,
    requires_pointer_precheck=True,
    supports_text_input=True,
    supports_app_launch=False,
)
#: What an Android device behind ADB reports.
ANDROID = CapabilitySet(
    supports_ui_tree=True,
    supports_absolute_tap=True,
    supports_text_input=True,
    supports_shell=True,
)


def _ios(**overrides):
    defaults = dict(
        platform="ios",
        device_id="ios-uvc",
        screen_size=(1080, 2400),
        capabilities=IOS_RIG,
        uvc_available=True,
        camera_available=False,
        capture_mode="frame",
    )
    defaults.update(overrides)
    return DeviceProfile(**defaults)


def test_a_ui_tree_with_absolute_touch_is_the_first_choice():
    composition = select_composition(
        DeviceProfile(platform="android", device_id="emulator-5554", capabilities=ANDROID)
    )

    assert composition.kind == "app"
    assert composition.perception == ("app",)
    assert composition.control == ("app",)
    assert composition.requires_pointer_precheck is False
    assert "tree" in composition.reason


def test_no_tree_but_a_uvc_rig_selects_uvc_plus_hid():
    composition = select_composition(_ios())

    assert composition.kind == "uvc-hid"
    assert composition.perception == ("uvc",)
    assert composition.control == ("hid",)
    assert composition.requires_pointer_precheck is True, (
        "a relative pointer is only ever safe behind a precheck"
    )
    assert composition.capture_mode == "frame"
    assert "relative" in composition.reason


def test_the_configured_capture_mode_is_carried_through():
    assert select_composition(_ios(capture_mode="stream")).capture_mode == "stream"
    assert select_composition(_ios(capture_mode="frame")).capture_mode == "frame"


def test_a_camera_is_the_last_resort_and_says_so():
    composition = select_composition(
        _ios(uvc_available=False, camera_available=True, capture_mode="frame")
    )

    assert composition.kind == "camera-hid"
    assert composition.perception == ("camera",)
    assert composition.requires_pointer_precheck is True
    assert "camera" in composition.reason.lower()


def test_a_device_with_no_perception_path_fails_with_the_missing_pieces():
    with pytest.raises(CompositionError) as excinfo:
        select_composition(_ios(uvc_available=False, camera_available=False))

    message = str(excinfo.value)
    assert "no usable perception" in message.lower()
    assert "uvc" in message.lower() and "camera" in message.lower()


def test_a_device_without_any_control_path_fails():
    blind = CapabilitySet(supports_ui_tree=False, supports_ocr=True)

    with pytest.raises(CompositionError) as excinfo:
        select_composition(
            DeviceProfile(platform="ios", device_id="x", capabilities=blind, uvc_available=True)
        )

    assert "control" in str(excinfo.value).lower()


def test_the_profile_is_immutable_and_reports_what_it_knows():
    profile = _ios()

    assert profile.screen_size == (1080, 2400)
    with pytest.raises(Exception):
        profile.platform = "android"  # frozen: a decision must not depend on a mutable input


def test_an_unknown_capture_mode_is_rejected_by_the_selector():
    with pytest.raises(CompositionError):
        select_composition(_ios(capture_mode="hologram"))


def test_the_composition_names_its_own_reason_for_the_log():
    for profile in (DeviceProfile(platform="android", device_id="d", capabilities=ANDROID), _ios()):
        composition = select_composition(profile)
        assert composition.reason and len(composition.reason) > 10
