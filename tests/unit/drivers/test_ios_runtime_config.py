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

"""The iOS runtime config is the one place env truth becomes a session.

Every value here has a measured default from the live rig, so a bare
``ARTEMIS_ESP32_PORT`` is enough to start, and a malformed value fails at
configuration time instead of deep inside a capture.
"""

import pytest

from artemis.drivers.ios.config import IosConfigError, IosRuntimeConfig


def test_defaults_reproduce_the_measured_rig():
    config = IosRuntimeConfig.from_env({"ARTEMIS_ESP32_PORT": "/dev/tty.usbmodem1101"})

    assert config.esp32_port == "/dev/tty.usbmodem1101"
    assert config.uvc_device == "USB Video"
    assert config.uvc_crop == (736, 53, 1185, 1027)
    assert config.frame_size == (449, 974), "the cropped frame is what skills calibrate against"
    assert config.screen_size == (1080, 2400)
    assert config.pointer_gain == (1.48, 1.48), "measured with artemis ios-calibrate"
    assert config.enable_ocr is True
    assert config.capture_mode == "frame", (
        "one-shot grabs stay live on this card; a persistent AVFoundation session"
        " returned a frozen frame (measured 2026-10-21)"
    )


def test_a_missing_port_names_the_setting():
    with pytest.raises(IosConfigError) as excinfo:
        IosRuntimeConfig.from_env({})

    message = str(excinfo.value)
    assert "ARTEMIS_ESP32_PORT" in message
    assert "iOS" in message


@pytest.mark.parametrize(
    "env,needle",
    [
        ({"ARTEMIS_UVC_CROP": "1,2,3"}, "ARTEMIS_UVC_CROP"),
        ({"ARTEMIS_UVC_CROP": "0,0,0,10"}, "ARTEMIS_UVC_CROP"),
        ({"ARTEMIS_IOS_SCREEN_SIZE": "1080by2400"}, "ARTEMIS_IOS_SCREEN_SIZE"),
        ({"ARTEMIS_IOS_POINTER_GAIN": "1.8"}, "ARTEMIS_IOS_POINTER_GAIN"),
        ({"ARTEMIS_IOS_POINTER_GAIN": "0,1.8"}, "ARTEMIS_IOS_POINTER_GAIN"),
    ],
)
def test_bad_values_fail_loudly_and_name_the_setting(env, needle):
    with pytest.raises(IosConfigError) as excinfo:
        IosRuntimeConfig.from_env({"ARTEMIS_ESP32_PORT": "/dev/fake", **env})

    assert needle in str(excinfo.value)


def test_overrides_are_parsed():
    config = IosRuntimeConfig.from_env(
        {
            "ARTEMIS_ESP32_PORT": "/dev/fake",
            "ARTEMIS_UVC_DEVICE": "Cam Link 4K",
            "ARTEMIS_UVC_CROP": "10,20,210,420",
            "ARTEMIS_IOS_SCREEN_SIZE": "1170x2532",
            "ARTEMIS_IOS_POINTER_GAIN": "2,1.5",
            "ARTEMIS_IOS_OCR": "0",
        }
    )

    assert config.uvc_device == "Cam Link 4K"
    assert config.frame_size == (200, 400)
    assert config.screen_size == (1170, 2532)
    assert config.pointer_gain == (2.0, 1.5)
    assert config.enable_ocr is False


def test_describe_lists_the_runtime_facts_without_secrets():
    config = IosRuntimeConfig.from_env({"ARTEMIS_ESP32_PORT": "/dev/fake"})

    text = config.describe()

    assert "/dev/fake" in text
    assert "449x974" in text
    assert "1080x2400" in text
    assert "1.48" in text


def test_the_capture_mode_can_be_pinned_to_a_persistent_stream():
    config = IosRuntimeConfig.from_env(
        {"ARTEMIS_ESP32_PORT": "/dev/fake", "ARTEMIS_IOS_CAPTURE": "stream"}
    )
    assert config.capture_mode == "stream"

    with pytest.raises(IosConfigError) as excinfo:
        IosRuntimeConfig.from_env(
            {"ARTEMIS_ESP32_PORT": "/dev/fake", "ARTEMIS_IOS_CAPTURE": "hologram"}
        )
    assert "ARTEMIS_IOS_CAPTURE" in str(excinfo.value)


def test_a_defaulted_crop_is_marked_as_not_explicit():
    """The factory may replace a default; it must never second-guess an operator."""
    from artemis.drivers.ios.config import IosRuntimeConfig

    defaulted = IosRuntimeConfig.from_env({"ARTEMIS_ESP32_PORT": "/dev/tty.usbmodem1101"})
    explicit = IosRuntimeConfig.from_env(
        {
            "ARTEMIS_ESP32_PORT": "/dev/tty.usbmodem1101",
            "ARTEMIS_UVC_CROP": "700,40,1200,1040",
        }
    )

    assert defaulted.uvc_crop_explicit is False
    assert explicit.uvc_crop_explicit is True
    assert explicit.uvc_crop == (700, 40, 1200, 1040)


def test_replacing_the_crop_recomputes_the_frame_size():
    """``frame_size`` is derived; a swapped crop must not keep the old frame."""
    from dataclasses import replace

    from artemis.drivers.ios.config import IosRuntimeConfig

    config = IosRuntimeConfig.from_env({"ARTEMIS_ESP32_PORT": "/dev/tty.usbmodem1101"})
    swapped = replace(config, uvc_crop=(700, 40, 1200, 1040))

    assert swapped.frame_size == (500, 1000)


def test_uvc_port_hint_is_optional_and_loaded_from_the_environment():
    base = IosRuntimeConfig.from_env({"ARTEMIS_ESP32_PORT": "/dev/fake"})
    hinted = IosRuntimeConfig.from_env(
        {"ARTEMIS_ESP32_PORT": "/dev/fake", "ARTEMIS_UVC_PORT_HINT": "1-2.4"}
    )

    assert base.uvc_port_hint is None
    assert hinted.uvc_port_hint == "1-2.4"


def test_profile_config_supplies_port_hint_but_env_wins(tmp_path, monkeypatch):
    config_file = tmp_path / "artemis.jsonc"
    config_file.write_text('{"ios": {"uvc_port_hint": "1-2.4"}}', encoding="utf-8")
    monkeypatch.setattr("artemis.config.paths.get_config_path", lambda _name: config_file)

    profile = IosRuntimeConfig.from_env({"ARTEMIS_ESP32_PORT": "/dev/fake"})
    env = IosRuntimeConfig.from_env(
        {"ARTEMIS_ESP32_PORT": "/dev/fake", "ARTEMIS_UVC_PORT_HINT": "1-2.5"}
    )

    assert profile.uvc_port_hint == "1-2.4"
    assert env.uvc_port_hint == "1-2.5"
