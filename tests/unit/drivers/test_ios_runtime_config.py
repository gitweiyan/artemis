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
    assert config.pointer_gain == (1.8, 1.8)
    assert config.enable_ocr is True


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
    assert "1.8" in text
