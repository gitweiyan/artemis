"""Canonical device identity helpers (Stage 3)."""

from __future__ import annotations

import pytest

from artemis.core.identity import (
    IdentitySource,
    PlatformFamily,
    make_uvc_endpoint,
    resolve_canonical_device_id,
)
from artemis.devices.descriptors import build_android_descriptor
from artemis.drivers.ios.uvc_identity import UVCIdentity, resolve_uvc_identity


def test_alias_is_canonical_before_a_descriptor_exists():
    assert resolve_canonical_device_id("emulator-5554", alias="lab-phone") == "lab-phone"


def test_a_built_descriptor_wins_over_the_physical_serial():
    descriptor = build_android_descriptor(
        device_id="emulator-5554",
        serial="emulator-5554",
        alias="lab-phone",
    )

    assert resolve_canonical_device_id("emulator-5554", descriptor=descriptor) == "lab-phone"


def test_uvc_identity_prefers_hardware_then_explicit_hint():
    serial = UVCIdentity("SERIAL-1", IdentitySource.HARDWARE_SERIAL)
    assert resolve_uvc_identity("USB Video", probe=lambda _: serial) == serial

    port = UVCIdentity("usb-1-2.3", IdentitySource.USB_PORT_PATH)
    assert resolve_uvc_identity("USB Video", probe=lambda _: port) == port

    # An explicit key is a fallback hint; it must not mask a usable hardware id.
    assert (
        resolve_uvc_identity("USB Video", explicit_key="rig-camera", probe=lambda _: serial)
        == serial
    )
    assert (
        resolve_uvc_identity("USB Video", explicit_key="rig-camera", probe=lambda _: port) == port
    )

    hint = resolve_uvc_identity("USB Video", explicit_key="rig-camera", probe=lambda _: None)
    assert hint.key == "rig-camera"
    assert hint.source is IdentitySource.CONFIG_HINT

    with pytest.raises(ValueError, match="stable USB identity"):
        resolve_uvc_identity("USB Video", probe=lambda _: None, strict=True)
    with pytest.raises(ValueError, match="explicit config hints are not hardware identity"):
        resolve_uvc_identity(
            "USB Video", explicit_key="rig-camera", probe=lambda _: None, strict=True
        )


def test_a_config_hint_is_visible_in_the_uvc_endpoint_source():
    endpoint = make_uvc_endpoint(
        host_id="host-a",
        hardware_key="USB-Video",
        identity_source=IdentitySource.CONFIG_HINT,
    )

    assert endpoint.identity_source is IdentitySource.CONFIG_HINT
    assert endpoint.endpoint_id == "uvc:host-a:USB-Video"
    assert endpoint.resource_ids == ("uvc:host-a:USB-Video/capture",)


def test_platform_family_is_still_a_separate_field():
    descriptor = build_android_descriptor(device_id="emulator-5554")
    assert descriptor.platform_family is PlatformFamily.ANDROID
    assert "android" not in descriptor.device_id
