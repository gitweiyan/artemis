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

"""An iOS run must be expressible end to end: platform, descriptor, and target.

Nothing here touches hardware. These tests pin the descriptor that the driver factory
consumes, and the two places that used to overwrite an explicit platform with ANDROID.
"""

import pytest

from artemis.drivers.ios.config import IosConfigError, ios_device_context


def test_ios_is_a_first_class_device_platform():
    from third_party.mobile_use.context import DeviceContext, DevicePlatform

    assert DevicePlatform.IOS.value == "ios"
    context = DeviceContext(mobile_platform="ios", device_id="ios-uvc")
    assert context.mobile_platform is DevicePlatform.IOS
    assert "ios" in context.to_str()


def test_the_ios_descriptor_carries_the_configured_screen_size():
    context = ios_device_context(
        "ios-uvc", env={"ARTEMIS_ESP32_PORT": "/dev/fake", "ARTEMIS_IOS_SCREEN_SIZE": "1170x2532"}
    )

    from third_party.mobile_use.context import DevicePlatform

    assert context.mobile_platform is DevicePlatform.IOS
    assert context.device_id == "ios-uvc"
    assert (context.device_width, context.device_height) == (1170, 2532)


def test_an_ios_descriptor_still_requires_the_port():
    with pytest.raises(IosConfigError):
        ios_device_context("ios-uvc", env={})


def test_the_agent_keeps_an_explicit_ios_platform():
    from artemis.sdk.agent import Agent
    from artemis.sdk.builders.agent_config_builder import AgentConfigBuilder
    from artemis.context import DevicePlatform

    config = AgentConfigBuilder().for_device(DevicePlatform.IOS, "ios-uvc").build()
    agent = Agent(config=config, device_serial="ios-uvc")

    assert agent._config.device_platform is DevicePlatform.IOS
    assert agent._config.device_id == "ios-uvc"


def test_android_device_serial_still_forces_android():
    from artemis.sdk.agent import Agent
    from artemis.sdk.builders.agent_config_builder import AgentConfigBuilder
    from artemis.context import DevicePlatform

    config = AgentConfigBuilder().build()
    agent = Agent(config=config, device_serial="emulator-5554")

    assert agent._config.device_platform is DevicePlatform.ANDROID


def test_the_device_context_for_ios_skips_the_adb_dimension_probe(monkeypatch):
    import asyncio

    from artemis.sdk.agent import Agent
    from artemis.sdk.builders.agent_config_builder import AgentConfigBuilder
    from artemis.context import DevicePlatform

    monkeypatch.setenv("ARTEMIS_ESP32_PORT", "/dev/fake")
    agent = Agent(config=AgentConfigBuilder().for_device(DevicePlatform.IOS, "ios-uvc").build())

    context = asyncio.run(agent._get_device_context("ios-uvc", DevicePlatform.IOS))

    assert context.mobile_platform is DevicePlatform.IOS
    assert (context.device_width, context.device_height) == (1080, 2400)


def test_no_adb_client_means_no_keyguard_probe():
    import asyncio

    from artemis.sdk.agent import Agent
    from artemis.sdk.builders.agent_config_builder import AgentConfigBuilder
    from artemis.context import DevicePlatform
    from third_party.mobile_use.context import DeviceContext

    agent = Agent(
        config=AgentConfigBuilder().for_device(DevicePlatform.IOS, "ios-uvc").build()
    )
    agent._adb_client = None
    agent._device_context = DeviceContext(mobile_platform="ios", device_id="ios-uvc")

    # A non-ADB device has no keyguard to inspect; this must not raise.
    asyncio.run(agent._ensure_device_unlocked())
