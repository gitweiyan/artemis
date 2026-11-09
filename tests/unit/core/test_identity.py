"""Device identity DTOs and pure ID derivation (ADR-003, PR-1)."""

from __future__ import annotations

import pytest

from artemis.core.identity import (
    DeviceDescriptor,
    DisplayInfo,
    EndpointDirection,
    EndpointKind,
    EndpointRef,
    IdentitySource,
    PlatformFamily,
    derive_hardware_key,
    make_adb_endpoint,
    make_mock_endpoint,
    make_serial_endpoint,
    make_uvc_endpoint,
    resolve_device_id,
)


def test_display_defaults_to_the_primary_surface():
    display = DisplayInfo(width_px=1080, height_px=2400)

    assert display.surface_id == "display:primary"
    assert display.rotation_degrees == 0


@pytest.mark.parametrize("rotation", [45, -90, 360])
def test_display_rejects_unsupported_rotation(rotation):
    with pytest.raises(ValueError):
        DisplayInfo(width_px=1080, height_px=2400, rotation_degrees=rotation)


def test_display_rejects_non_positive_size():
    with pytest.raises(ValueError):
        DisplayInfo(width_px=0, height_px=2400)


def test_alias_wins_over_the_platform_default():
    assert resolve_device_id("emulator-5554", alias="lab-phone-1") == "lab-phone-1"
    assert resolve_device_id("emulator-5554") == "emulator-5554"


@pytest.mark.parametrize("alias", ["", "   ", "bad\nname"])
def test_invalid_alias_is_rejected(alias):
    with pytest.raises(ValueError):
        resolve_device_id("emulator-5554", alias=alias)


def test_hardware_key_uses_the_device_basename_and_stays_readable():
    assert derive_hardware_key("/dev/cu.usbmodem21101") == "cu.usbmodem21101"
    assert derive_hardware_key("USB Video") == "USB-Video"
    assert derive_hardware_key("  ") == ""


def test_adb_endpoint_carries_the_endpoint_and_serial_but_no_exclusive_resource():
    endpoint = make_adb_endpoint(host="127.0.0.1", port=5037, serial="emulator-5554")

    assert endpoint.kind is EndpointKind.ADB
    assert endpoint.endpoint_id == "adb:tcp:127.0.0.1:5037/emulator-5554"
    assert endpoint.resource_ids == ()
    assert endpoint.direction is EndpointDirection.TRANSPORT
    assert endpoint.identity_source is IdentitySource.ADB_SERIAL


def test_uvc_endpoint_owns_a_capture_resource():
    endpoint = make_uvc_endpoint(
        host_id="host-abc",
        hardware_key="usb-1-2.3",
        identity_source=IdentitySource.USB_PORT_PATH,
    )

    assert endpoint.kind is EndpointKind.UVC
    assert endpoint.endpoint_id == "uvc:host-abc:usb-1-2.3"
    assert endpoint.resource_ids == ("uvc:host-abc:usb-1-2.3/capture",)
    assert endpoint.direction is EndpointDirection.OBSERVE


def test_serial_endpoint_owns_a_command_resource():
    endpoint = make_serial_endpoint(
        host_id="host-abc",
        hardware_key="cu.usbmodem21101",
        identity_source=IdentitySource.DEVICE_PATH,
    )

    assert endpoint.kind is EndpointKind.SERIAL
    assert endpoint.resource_ids == ("serial:host-abc:cu.usbmodem21101/command",)
    assert endpoint.direction is EndpointDirection.TRANSPORT


def test_mock_endpoint_has_no_host_resource():
    endpoint = make_mock_endpoint("mock-device")

    assert endpoint.endpoint_id == "mock:mock-device"
    assert endpoint.resource_ids == ()
    assert endpoint.identity_source is IdentitySource.MOCK


def test_endpoint_metadata_is_read_only():
    endpoint = make_uvc_endpoint(
        host_id="host",
        hardware_key="key",
        metadata={"device": "USB Video"},
    )

    with pytest.raises(TypeError):
        endpoint.metadata["device"] = "other"  # type: ignore[index]


def test_descriptor_rejects_duplicate_endpoint_ids():
    endpoint = make_uvc_endpoint(host_id="host", hardware_key="same")

    with pytest.raises(ValueError, match="duplicate endpoint_id"):
        DeviceDescriptor(
            device_id="ios-uvc",
            platform_family=PlatformFamily.IOS,
            profile_id="ios.uvc_hid",
            endpoints=(endpoint, endpoint),
        )


def test_descriptor_rejects_duplicate_resource_ids():
    first = make_uvc_endpoint(host_id="host", hardware_key="key")
    second = make_uvc_endpoint(host_id="host", hardware_key="key")
    # Make endpoint ids differ while the resource id stays the same.
    second = EndpointRef(
        kind=EndpointKind.UVC,
        endpoint_id="uvc:host:key-alias",
        resource_ids=first.resource_ids,
        direction=EndpointDirection.OBSERVE,
        identity_source=IdentitySource.CONFIG_HINT,
    )

    with pytest.raises(ValueError, match="duplicate resource_id"):
        DeviceDescriptor(
            device_id="ios-uvc",
            platform_family=PlatformFamily.IOS,
            profile_id="ios.uvc_hid",
            endpoints=(first, second),
        )


def test_descriptor_rejects_empty_identity_fields():
    with pytest.raises(ValueError):
        DeviceDescriptor(
            device_id=" ",
            platform_family=PlatformFamily.ANDROID,
            profile_id="android.app",
            endpoints=(),
        )


def test_descriptor_requires_a_resource_id_to_match_the_endpoint_kind():
    with pytest.raises(ValueError, match="resource_id"):
        EndpointRef(
            kind=EndpointKind.SERIAL,
            endpoint_id="serial:host:key",
            resource_ids=("uvc:host:key/capture",),
            direction=EndpointDirection.TRANSPORT,
            identity_source=IdentitySource.DEVICE_PATH,
        )
