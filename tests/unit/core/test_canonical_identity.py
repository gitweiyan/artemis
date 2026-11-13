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


def test_uvc_serial_is_stable_across_usb_port_changes():
    from artemis.drivers.ios.uvc_identity import UsbEntry

    entry = (UsbEntry("USB Video", "SERIAL-1", "1-2.3"),)
    identity = resolve_uvc_identity("USB Video", host_id="host-a", inventory_probe=lambda _: entry)

    assert identity.key == "SERIAL-1"
    assert identity.source is IdentitySource.HARDWARE_SERIAL
    assert identity.metadata["host_id"] == "host-a"


def test_duplicate_uvc_serial_falls_back_to_port_path():
    from artemis.drivers.ios.uvc_identity import UsbEntry

    entries = (
        UsbEntry("USB Video", "DUP-1", "1-2.3"),
        UsbEntry("Other USB", "DUP-1", "1-2.4"),
    )
    identity = resolve_uvc_identity(
        "USB Video", host_id="host-a", inventory_probe=lambda _: entries
    )

    assert identity.source is IdentitySource.USB_PORT_PATH
    assert identity.key == "usb-1-2.3"


def test_multiple_matching_candidates_require_a_port_hint():
    from artemis.drivers.ios.uvc_identity import (
        AmbiguousDeviceIdentityError,
        UsbEntry,
    )

    entries = (
        UsbEntry("USB Video", "DUP-1", "1-2.3"),
        UsbEntry("USB Video", "DUP-1", "1-2.4"),
    )
    with pytest.raises(AmbiguousDeviceIdentityError):
        resolve_uvc_identity("USB Video", host_id="host-a", inventory_probe=lambda _: entries)

    selected = resolve_uvc_identity(
        "USB Video",
        host_id="host-a",
        inventory_probe=lambda _: entries,
        port_hint="1-2.4",
    )
    assert selected.key == "usb-1-2.4"


def test_same_serial_on_different_hosts_is_not_a_collision():
    from artemis.drivers.ios.uvc_identity import UsbEntry

    entry = (UsbEntry("USB Video", "SERIAL-1", "1-2.3"),)
    a = resolve_uvc_identity("USB Video", host_id="host-a", inventory_probe=lambda _: entry)
    b = resolve_uvc_identity("USB Video", host_id="host-b", inventory_probe=lambda _: entry)

    assert a.key == b.key == "SERIAL-1"
    assert a.metadata["host_id"] == "host-a"
    assert b.metadata["host_id"] == "host-b"


def test_same_device_keeps_serial_identity_after_moving_usb_port():
    from artemis.drivers.ios.uvc_identity import UsbEntry

    probes = iter(
        (
            (UsbEntry("USB Video", "SERIAL-1", "1-2.3"),),
            (UsbEntry("USB Video", "SERIAL-1", "1-2.4"),),
        )
    )
    first = resolve_uvc_identity("USB Video", inventory_probe=lambda _: next(probes))
    second = resolve_uvc_identity("USB Video", inventory_probe=lambda _: next(probes))

    assert first.key == second.key == "SERIAL-1"
    assert first.source is IdentitySource.HARDWARE_SERIAL
    assert second.source is IdentitySource.HARDWARE_SERIAL


def test_duplicate_serial_entries_are_visible_in_host_inventory():
    from artemis.drivers.ios.uvc_identity import probe_usb_inventory

    def runner(*args, **kwargs):
        class Result:
            stdout = (
                b'{"SPUSBDataType": [{"_name": "USB Video", "serial_num": "DUP-1",'
                b' "location_id": "1-2.3"}, {"_name": "USB Video", "serial_num":'
                b' "DUP-1", "location_id": "1-2.4"}]}'
            )

        return Result()

    entries = probe_usb_inventory("USB Video", runner=runner)

    assert [entry.serial for entry in entries] == ["DUP-1", "DUP-1"]
    assert [entry.location_id for entry in entries] == ["1-2.3", "1-2.4"]


def test_conflicting_serial_devices_are_distinguished_by_port_hint():
    from artemis.drivers.ios.uvc_identity import UsbEntry

    entries = (
        UsbEntry("USB Video", "DUP-1", "1-2.3"),
        UsbEntry("USB Video", "DUP-1", "1-2.4"),
    )
    first = resolve_uvc_identity("USB Video", inventory_probe=lambda _: entries, port_hint="1-2.3")
    second = resolve_uvc_identity("USB Video", inventory_probe=lambda _: entries, port_hint="1-2.4")

    assert first.key == "usb-1-2.3"
    assert second.key == "usb-1-2.4"
    assert first.key != second.key


def test_usb_inventory_result_distinguishes_unavailable_empty_and_ok():
    from artemis.drivers.ios.uvc_identity import (
        UsbInventoryStatus,
        probe_usb_inventory_result,
    )

    class Result:
        def __init__(self, stdout):
            self.stdout = stdout

    def unavailable(*args, **kwargs):
        raise OSError("system_profiler missing")

    def empty(*args, **kwargs):
        return Result(b"")

    def ok(*args, **kwargs):
        return Result(
            b'{"SPUSBDataType": [{"_name": "USB Video", "serial_num": "S",'
            b' "location_id": "1-2.3"}]}'
        )

    assert probe_usb_inventory_result(runner=unavailable).status is UsbInventoryStatus.UNAVAILABLE
    assert probe_usb_inventory_result(runner=empty).status is UsbInventoryStatus.EMPTY
    result = probe_usb_inventory_result(runner=ok)
    assert result.status is UsbInventoryStatus.OK
    assert result.entries[0].serial == "S"
