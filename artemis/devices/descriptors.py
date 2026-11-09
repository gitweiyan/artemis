"""Build read-only ``DeviceDescriptor`` objects at the L4 composition root.

PR-1 scope: describe Android / iOS / Mock targets. The descriptor never opens a
device and never participates in driver or composition selection; it only records
what the runtime already knows so locks, evidence and traces can share IDs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping

from artemis.core.identity import (
    DeviceDescriptor,
    DisplayInfo,
    derive_hardware_key,
    IdentitySource,
    PlatformFamily,
    make_adb_endpoint,
    make_mock_endpoint,
    make_serial_endpoint,
    make_uvc_endpoint,
    resolve_device_id,
)

if TYPE_CHECKING:  # pragma: no cover - typing only, keeps devices -> drivers out
    from artemis.drivers.ios.config import IosRuntimeConfig

__all__ = [
    "build_android_descriptor",
    "build_ios_descriptor",
    "build_mock_descriptor",
    "descriptor_from_device_context",
]

_DEFAULT_SCREEN = (1080, 2400)


def _display(size: tuple[int, int] | None) -> DisplayInfo | None:
    if not size:
        return None
    width, height = int(size[0]), int(size[1])
    if width <= 0 or height <= 0:
        return None
    return DisplayInfo(width_px=width, height_px=height)


def _screen(value: Any) -> tuple[int, int] | None:
    if not value or len(value) != 2:
        return None
    try:
        width, height = int(value[0]), int(value[1])
    except (TypeError, ValueError):
        return None
    return (width, height) if width > 0 and height > 0 else None


def build_android_descriptor(
    *,
    device_id: str,
    serial: str | None = None,
    screen_size: tuple[int, int] | None = None,
    adb_host: str = "127.0.0.1",
    adb_port: int = 5037,
    alias: str | None = None,
    metadata: Mapping[str, str] | None = None,
) -> DeviceDescriptor:
    """One logical Android target over one ADB endpoint.

    ADB multiplexes commands, so it owns no exclusive host resource; the device
    lease (``DeviceExecutionLock``) is the only mutual exclusion on this path.
    """

    physical_serial = serial or device_id
    target_id = resolve_device_id(device_id, alias=alias)
    endpoint = make_adb_endpoint(host=adb_host, port=adb_port, serial=physical_serial)
    return DeviceDescriptor(
        device_id=target_id,
        platform_family=PlatformFamily.ANDROID,
        profile_id="android.app",
        endpoints=(endpoint,),
        display=_display(screen_size),
        metadata=metadata or {},
    )


def build_ios_descriptor(
    *,
    device_id: str,
    host_id: str,
    screen_size: tuple[int, int] | None,
    uvc_device: str,
    esp32_port: str,
    alias: str | None = None,
    uvc_hardware_key: str | None = None,
    uvc_identity_source: IdentitySource | None = None,
    metadata: Mapping[str, str] | None = None,
) -> DeviceDescriptor:
    """One iPhone = one target with a UVC observation endpoint and a serial endpoint.

    ``uvc_device`` is only a configuration hint (e.g. the AVFoundation name). A
    caller that knows a real USB serial/port path should pass ``uvc_hardware_key``;
    PR-1 does not probe USB topology.
    """

    target_id = resolve_device_id(device_id, alias=alias)
    uvc_key = derive_hardware_key(uvc_hardware_key or uvc_device)
    if not uvc_key:
        raise ValueError("uvc_device/uvc_hardware_key must not be empty")
    serial_key = derive_hardware_key(esp32_port)
    if not serial_key:
        raise ValueError("esp32_port must not be empty")
    source = uvc_identity_source or (
        IdentitySource.HARDWARE_SERIAL if uvc_hardware_key else IdentitySource.CONFIG_HINT
    )
    endpoints = (
        make_uvc_endpoint(
            host_id=host_id,
            hardware_key=uvc_key,
            identity_source=source,
            metadata={"device": uvc_device},
        ),
        make_serial_endpoint(
            host_id=host_id,
            hardware_key=serial_key,
            metadata={"path": esp32_port},
        ),
    )
    return DeviceDescriptor(
        device_id=target_id,
        platform_family=PlatformFamily.IOS,
        profile_id="ios.uvc_hid",
        endpoints=endpoints,
        display=_display(screen_size),
        metadata=metadata or {},
    )


def build_mock_descriptor(
    *,
    device_id: str = "mock-device",
    screen_size: tuple[int, int] = _DEFAULT_SCREEN,
    alias: str | None = None,
    metadata: Mapping[str, str] | None = None,
) -> DeviceDescriptor:
    target_id = resolve_device_id(device_id, alias=alias)
    return DeviceDescriptor(
        device_id=target_id,
        platform_family=PlatformFamily.MOCK,
        profile_id="mock.screen",
        endpoints=(make_mock_endpoint(target_id),),
        display=_display(screen_size),
        metadata=metadata or {},
    )


def _platform_value(device: Any) -> str:
    raw = getattr(device, "mobile_platform", None)
    value = getattr(raw, "value", raw)
    return str(value or "").strip().lower()


def descriptor_from_device_context(
    ctx: Any,
    *,
    host_id: str,
    alias: str | None = None,
    ios_config: IosRuntimeConfig | None = None,
    adb_host: str = "127.0.0.1",
    adb_port: int = 5037,
    uvc_hardware_key: str | None = None,
) -> DeviceDescriptor:
    """Describe the target already present in ``ctx`` without touching hardware."""

    device = getattr(ctx, "device", None)
    platform = _platform_value(device)
    device_id = str(getattr(device, "device_id", "") or "").strip()
    screen = _screen(
        (
            getattr(device, "device_width", 0) or 0,
            getattr(device, "device_height", 0) or 0,
        )
    )

    if platform == PlatformFamily.ANDROID.value:
        serial = device_id or "default"
        return build_android_descriptor(
            device_id=serial,
            serial=serial,
            screen_size=screen,
            adb_host=adb_host,
            adb_port=adb_port,
            alias=alias,
        )
    if platform == PlatformFamily.IOS.value:
        if ios_config is None:
            raise ValueError("ios_config is required to describe an iOS target")
        return build_ios_descriptor(
            device_id=device_id or "ios-uvc",
            host_id=host_id,
            screen_size=screen or ios_config.screen_size,
            uvc_device=ios_config.uvc_device,
            esp32_port=ios_config.esp32_port,
            alias=alias,
            uvc_hardware_key=uvc_hardware_key,
        )
    if platform == PlatformFamily.MOCK.value:
        return build_mock_descriptor(
            device_id=device_id or "mock-device",
            screen_size=screen or _DEFAULT_SCREEN,
            alias=alias,
        )
    raise ValueError(f"unsupported platform {platform!r}; expected android, ios or mock")
