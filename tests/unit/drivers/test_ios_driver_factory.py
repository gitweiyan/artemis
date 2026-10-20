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

"""Driver selection for an iOS platform is decided by config, never by falling back."""

import logging
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from artemis.drivers.factory import create_driver
from artemis.drivers.ios.config import IosConfigError
from artemis.drivers.ios.ios_uvc_hid_driver import IOSUvcHidDriver


def _ios_ctx(width=1080, height=2400):
    return SimpleNamespace(
        device=SimpleNamespace(
            mobile_platform="ios",
            device_id="ios-uvc",
            device_width=width,
            device_height=height,
        ),
        adb_client=None,
        ui_adb_client=None,
    )


def test_ios_platform_builds_the_uvc_hid_driver(monkeypatch):
    monkeypatch.setenv("ARTEMIS_ESP32_PORT", "/dev/fake-esp32")
    monkeypatch.delenv("ARTEMIS_IOS_SCREEN_SIZE", raising=False)

    with (
        patch("artemis.reflex.capture.UvcStream") as capture,
        patch("artemis.reflex.control.Esp32HidControl") as control,
        patch("artemis.drivers.factory.default_ocr_engine") as ocr,
    ):
        driver = create_driver(_ios_ctx())

    assert isinstance(driver, IOSUvcHidDriver)
    assert driver.device_id == "ios-uvc-hid"
    assert driver.capabilities.supports_ui_tree is False
    assert driver.capabilities.supports_absolute_tap is False
    assert driver.capabilities.requires_pointer_precheck is True
    assert driver.capabilities.supports_relative_pointer is True
    assert capture.called and control.called, "the session must be built from the real parts"
    control.assert_called_once_with("/dev/fake-esp32", ack_timeout_s=3.0)
    assert ocr.called, "OCR is the only text locator on a device without a UI tree"


def test_a_missing_port_is_a_config_error_not_a_fallback(monkeypatch):
    monkeypatch.delenv("ARTEMIS_ESP32_PORT", raising=False)

    with pytest.raises(IosConfigError) as excinfo:
        create_driver(_ios_ctx())

    assert "ARTEMIS_ESP32_PORT" in str(excinfo.value)


def test_the_descriptor_screen_size_wins_over_the_config(monkeypatch):
    """The action layer converts normalized coordinates against the descriptor.

    A session geometry on a different grid would put every tap off by the ratio, so the
    factory follows the descriptor and says so in the log when they disagree.
    """
    monkeypatch.setenv("ARTEMIS_ESP32_PORT", "/dev/fake-esp32")
    monkeypatch.setenv("ARTEMIS_IOS_SCREEN_SIZE", "1170x2532")

    with (
        patch("artemis.reflex.capture.UvcCapture"),
        patch("artemis.reflex.control.Esp32HidControl"),
        patch("artemis.drivers.factory.default_ocr_engine"),
    ):
        driver = create_driver(_ios_ctx(width=1080, height=2400))

    assert driver.screen_size == (1080, 2400), "the descriptor is the action layer's frame"


def test_a_bare_descriptor_falls_back_to_the_configured_screen_size(monkeypatch):
    monkeypatch.setenv("ARTEMIS_ESP32_PORT", "/dev/fake-esp32")
    monkeypatch.setenv("ARTEMIS_IOS_SCREEN_SIZE", "1170x2532")

    with (
        patch("artemis.reflex.capture.UvcCapture"),
        patch("artemis.reflex.control.Esp32HidControl"),
        patch("artemis.drivers.factory.default_ocr_engine"),
    ):
        driver = create_driver(_ios_ctx(width=0, height=0))

    assert driver.screen_size == (1170, 2532)


def test_the_factory_logs_the_driver_and_its_capabilities(monkeypatch, caplog):
    monkeypatch.setenv("ARTEMIS_ESP32_PORT", "/dev/fake-esp32")

    with (
        patch("artemis.reflex.capture.UvcCapture"),
        patch("artemis.reflex.control.Esp32HidControl"),
        patch("artemis.drivers.factory.default_ocr_engine"),
        caplog.at_level(logging.INFO),
    ):
        create_driver(_ios_ctx())

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "ios-uvc-hid" in logged
    assert "requires_pointer_precheck=True" in logged
    assert "supports_ui_tree=False" in logged


def test_the_driver_instance_reports_the_rigs_real_capabilities(monkeypatch):
    monkeypatch.setenv("ARTEMIS_ESP32_PORT", "/dev/fake-esp32")
    monkeypatch.setenv("ARTEMIS_IOS_OCR", "0")

    with (
        patch("artemis.reflex.capture.UvcCapture"),
        patch("artemis.reflex.control.Esp32HidControl"),
        patch("artemis.drivers.factory.default_ocr_engine"),
    ):
        driver = create_driver(_ios_ctx())

    assert driver.capabilities.supports_ocr is False, "OCR is disabled for this run"
    assert driver.capabilities.supports_ui_tree is False
    assert driver.capabilities.requires_pointer_precheck is True
    # The class declaration stays the contract; instances report the session's truth.
    assert IOSUvcHidDriver.capabilities.supports_relative_pointer is True


def test_the_pointer_path_uses_a_persistent_stream_by_default(monkeypatch):
    monkeypatch.setenv("ARTEMIS_ESP32_PORT", "/dev/fake-esp32")
    monkeypatch.delenv("ARTEMIS_IOS_CAPTURE", raising=False)

    with (
        patch("artemis.reflex.capture.UvcStream") as stream,
        patch("artemis.reflex.capture.UvcCapture") as single,
        patch("artemis.reflex.control.Esp32HidControl"),
        patch("artemis.drivers.factory.default_ocr_engine"),
    ):
        create_driver(_ios_ctx())

    assert stream.called, "a live stream is what makes the pointer observable"
    assert not single.called


def test_the_capture_mode_can_be_pinned_to_single_frames(monkeypatch):
    monkeypatch.setenv("ARTEMIS_ESP32_PORT", "/dev/fake-esp32")
    monkeypatch.setenv("ARTEMIS_IOS_CAPTURE", "frame")

    with (
        patch("artemis.reflex.capture.UvcStream") as stream,
        patch("artemis.reflex.capture.UvcCapture") as single,
        patch("artemis.reflex.control.Esp32HidControl"),
        patch("artemis.drivers.factory.default_ocr_engine"),
    ):
        create_driver(_ios_ctx())

    assert single.called
    assert not stream.called
