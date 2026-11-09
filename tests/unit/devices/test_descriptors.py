"""Device descriptors built from the runtime context (PR-1)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from artemis.core.identity import EndpointKind, PlatformFamily
from artemis.devices.descriptors import descriptor_from_device_context
from artemis.drivers.ios.config import IosRuntimeConfig


def _ctx(platform: str, device_id: str, width: int = 1080, height: int = 2400):
    return SimpleNamespace(
        device=SimpleNamespace(
            mobile_platform=platform,
            device_id=device_id,
            device_width=width,
            device_height=height,
        )
    )


def _ios_config(**overrides) -> IosRuntimeConfig:
    values = {
        "esp32_port": "/dev/cu.usbmodem21101",
        "uvc_device": "USB Video",
        "screen_size": (1080, 2400),
    }
    values.update(overrides)
    return IosRuntimeConfig(**values)


def test_android_descriptor_has_one_adb_endpoint_and_no_exclusive_resource():
    descriptor = descriptor_from_device_context(
        _ctx("android", "emulator-5554"),
        host_id="host-a",
        adb_host="127.0.0.1",
        adb_port=5037,
    )

    assert descriptor.device_id == "emulator-5554"
    assert descriptor.platform_family is PlatformFamily.ANDROID
    assert descriptor.profile_id == "android.app"
    assert len(descriptor.endpoints) == 1
    endpoint = descriptor.endpoints[0]
    assert endpoint.kind is EndpointKind.ADB
    assert endpoint.endpoint_id == "adb:tcp:127.0.0.1:5037/emulator-5554"
    assert endpoint.resource_ids == ()
    assert descriptor.display is not None
    assert descriptor.display.surface_id == "display:primary"
    assert descriptor.display.width_px == 1080


def test_ios_descriptor_is_one_target_with_a_uvc_and_a_serial_endpoint():
    descriptor = descriptor_from_device_context(
        _ctx("ios", "ios-uvc"),
        host_id="host-a",
        ios_config=_ios_config(),
    )

    assert descriptor.device_id == "ios-uvc"
    assert descriptor.platform_family is PlatformFamily.IOS
    assert descriptor.profile_id == "ios.uvc_hid"
    assert [endpoint.kind for endpoint in descriptor.endpoints] == [
        EndpointKind.UVC,
        EndpointKind.SERIAL,
    ]
    assert len({endpoint.endpoint_id for endpoint in descriptor.endpoints}) == 2
    assert {
        resource for endpoint in descriptor.endpoints for resource in endpoint.resource_ids
    } == {
        "uvc:host-a:USB-Video/capture",
        "serial:host-a:cu.usbmodem21101/command",
    }
    assert descriptor.display is not None
    assert descriptor.display.surface_id == "display:primary"


def test_the_device_alias_changes_the_target_not_the_endpoint_serial():
    descriptor = descriptor_from_device_context(
        _ctx("android", "emulator-5554"),
        host_id="host-a",
        alias="lab-phone-1",
        adb_host="127.0.0.1",
        adb_port=5037,
    )

    assert descriptor.device_id == "lab-phone-1"
    assert descriptor.endpoints[0].endpoint_id.endswith("/emulator-5554")


def test_the_same_target_keeps_its_id_when_the_uvc_hint_changes():
    first = descriptor_from_device_context(
        _ctx("ios", "ios-uvc"),
        host_id="host-a",
        ios_config=_ios_config(uvc_device="USB Video"),
    )
    second = descriptor_from_device_context(
        _ctx("ios", "ios-uvc"),
        host_id="host-a",
        ios_config=_ios_config(uvc_device="USB Video 2"),
    )

    assert first.device_id == second.device_id == "ios-uvc"
    assert first.endpoints[0].endpoint_id != second.endpoints[0].endpoint_id


def test_an_explicit_uvc_hardware_key_replaces_the_config_hint():
    descriptor = descriptor_from_device_context(
        _ctx("ios", "ios-uvc"),
        host_id="host-a",
        ios_config=_ios_config(),
        uvc_hardware_key="usb-1-2.3",
    )

    assert descriptor.endpoints[0].endpoint_id == "uvc:host-a:usb-1-2.3"


def test_ios_screen_size_falls_back_to_the_config_when_the_context_has_none():
    descriptor = descriptor_from_device_context(
        _ctx("ios", "ios-uvc", width=0, height=0),
        host_id="host-a",
        ios_config=_ios_config(screen_size=(1170, 2532)),
    )

    assert descriptor.display is not None
    assert (descriptor.display.width_px, descriptor.display.height_px) == (1170, 2532)


def test_mock_descriptor_has_no_host_resource():
    descriptor = descriptor_from_device_context(
        _ctx("mock", "mock-device"),
        host_id="host-a",
    )

    assert descriptor.platform_family is PlatformFamily.MOCK
    assert descriptor.profile_id == "mock.screen"
    assert descriptor.endpoints[0].endpoint_id == "mock:mock-device"
    assert descriptor.endpoints[0].resource_ids == ()


def test_ios_descriptor_requires_the_rig_config():
    with pytest.raises(ValueError, match="ios_config"):
        descriptor_from_device_context(_ctx("ios", "ios-uvc"), host_id="host-a")


def test_an_unknown_platform_is_rejected_instead_of_guessed():
    with pytest.raises(ValueError, match="unsupported platform"):
        descriptor_from_device_context(_ctx("windows", "desktop"), host_id="host-a")


def test_descriptor_does_not_expose_device_handles():
    descriptor = descriptor_from_device_context(
        _ctx("ios", "ios-uvc"),
        host_id="host-a",
        ios_config=_ios_config(),
    )

    for forbidden in ("handle", "driver", "session", "adb_client", "capture", "serial_fd"):
        assert not hasattr(descriptor, forbidden)
