"""Stable device identity for the L4 Device Runtime (ADR-003, PR-1).

This module is deliberately platform-neutral and handle-free:

- ``DeviceDescriptor`` describes *what* a target is and which endpoints/resources
  belong to it; it never owns an ADB/USB/Serial handle.
- Endpoint/resource IDs are derived here (one place) so locks, evidence and traces
  agree on the same strings.
- ``core`` must stay importable with the standard library only.

Identity layers (ADR-003):
  host_id -> device_id (logical target) -> endpoint_id -> resource_id,
  plus surface_id for displays/windows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping

__all__ = [
    "DeviceDescriptor",
    "DisplayInfo",
    "EndpointDirection",
    "EndpointKind",
    "EndpointRef",
    "IdentitySource",
    "PlatformFamily",
    "derive_hardware_key",
    "helper_resource_id",
    "make_adb_endpoint",
    "make_mock_endpoint",
    "make_serial_endpoint",
    "make_uvc_endpoint",
    "resolve_canonical_device_id",
    "resolve_device_id",
]

_CONTROL_CHARS = frozenset(chr(code) for code in range(0x20)) | {"\x7f"}


class PlatformFamily(StrEnum):
    """The target platform family, never the host OS."""

    ANDROID = "android"
    IOS = "ios"
    MACOS = "macos"
    WINDOWS = "windows"
    LINUX = "linux"
    TV = "tv"
    MOCK = "mock"


class EndpointKind(StrEnum):
    ADB = "adb"
    UVC = "uvc"
    SERIAL = "serial"
    CAMERA = "camera"
    BLE = "ble"
    NETWORK = "network"
    CEC = "cec"
    IR = "ir"
    MOCK = "mock"


class EndpointDirection(StrEnum):
    OBSERVE = "observe"
    ACTUATE = "actuate"
    TRANSPORT = "transport"


class IdentitySource(StrEnum):
    """How stable the endpoint key is (ADR-003 D1)."""

    ADB_SERIAL = "adb_serial"
    HARDWARE_SERIAL = "hardware_serial"
    USB_PORT_PATH = "usb_port_path"
    DEVICE_PATH = "device_path"
    CONFIG_HINT = "config_hint"
    MOCK = "mock"


def _freeze(values: Mapping[str, str] | None) -> Mapping[str, str]:
    return MappingProxyType(dict(values or {}))


def _text(value: str, *, field_name: str, max_length: int = 128) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string, got {type(value).__name__}")
    text = value.strip()
    if not text:
        raise ValueError(f"{field_name} must not be empty")
    if len(text) > max_length:
        raise ValueError(f"{field_name} must be at most {max_length} characters")
    if any(char in _CONTROL_CHARS for char in text):
        raise ValueError(f"{field_name} must not contain control characters")
    return text


def _key(value: str, *, field_name: str) -> str:
    """A filesystem/log friendly identifier component (no spaces, no slashes)."""

    text = _text(value, field_name=field_name, max_length=96)
    if " " in text or "/" in text or "\\" in text:
        raise ValueError(f"{field_name} must not contain spaces or path separators")
    return text


def _resource_id(kind: EndpointKind, endpoint_id: str, access: str) -> str:
    return f"{kind.value}:{endpoint_id.split(':', 1)[1]}/{access}"


def derive_hardware_key(raw: str) -> str:
    """A stable-ish key from a serial/port/name (empty input -> empty string).

    Device paths keep only their basename; the full path belongs in metadata.
    The result is restricted to ``[A-Za-z0-9._-]`` so it can be used in IDs and
    lease filenames without escaping.
    """

    text = str(raw or "").strip()
    if not text:
        return ""
    basename = text.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = "".join(char if (char.isalnum() or char in "._-") else "-" for char in basename)
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-._")[:64]


@dataclass(frozen=True)
class DisplayInfo:
    """The logical surface a target presents (ADR-003 surface_id)."""

    width_px: int
    height_px: int
    surface_id: str = "display:primary"
    rotation_degrees: int = 0

    def __post_init__(self) -> None:
        if int(self.width_px) <= 0 or int(self.height_px) <= 0:
            raise ValueError("display size must be positive")
        if int(self.rotation_degrees) not in (0, 90, 180, 270):
            raise ValueError("rotation_degrees must be one of 0, 90, 180, 270")
        object.__setattr__(
            self,
            "surface_id",
            _text(self.surface_id, field_name="surface_id", max_length=96),
        )


@dataclass(frozen=True)
class EndpointRef:
    """One physical/logical connection owned by a target."""

    kind: EndpointKind
    endpoint_id: str
    resource_ids: tuple[str, ...] = ()
    direction: EndpointDirection = EndpointDirection.OBSERVE
    identity_source: IdentitySource = IdentitySource.CONFIG_HINT
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.kind, EndpointKind):
            raise ValueError(f"kind must be an EndpointKind, got {self.kind!r}")
        endpoint_id = _text(self.endpoint_id, field_name="endpoint_id", max_length=192)
        if not endpoint_id.startswith(f"{self.kind.value}:"):
            raise ValueError(f"endpoint_id must start with {self.kind.value!r}: {endpoint_id!r}")
        seen: set[str] = set()
        resources: list[str] = []
        for resource_id in self.resource_ids:
            resource = _text(resource_id, field_name="resource_id", max_length=224)
            if not resource.startswith(f"{self.kind.value}:"):
                raise ValueError(
                    f"resource_id {resource!r} must match endpoint kind {self.kind.value!r}"
                )
            if resource in seen:
                raise ValueError(f"duplicate resource_id {resource!r}")
            seen.add(resource)
            resources.append(resource)
        object.__setattr__(self, "endpoint_id", endpoint_id)
        object.__setattr__(self, "resource_ids", tuple(resources))
        object.__setattr__(self, "metadata", _freeze(self.metadata))


@dataclass(frozen=True)
class DeviceDescriptor:
    """What one logical target is; pure data, never a device handle."""

    device_id: str
    platform_family: PlatformFamily
    profile_id: str
    endpoints: tuple[EndpointRef, ...] = ()
    display: DisplayInfo | None = None
    fingerprint: Mapping[str, str] = field(default_factory=dict)
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.platform_family, PlatformFamily):
            raise ValueError(f"platform_family must be a PlatformFamily: {self.platform_family!r}")
        if self.display is not None and not isinstance(self.display, DisplayInfo):
            raise ValueError("display must be a DisplayInfo or None")
        object.__setattr__(
            self, "device_id", _text(self.device_id, field_name="device_id", max_length=96)
        )
        object.__setattr__(
            self, "profile_id", _text(self.profile_id, field_name="profile_id", max_length=96)
        )
        endpoints = tuple(self.endpoints)
        endpoint_ids: set[str] = set()
        resource_ids: set[str] = set()
        for endpoint in endpoints:
            if not isinstance(endpoint, EndpointRef):
                raise ValueError("endpoints must be EndpointRef instances")
            if endpoint.endpoint_id in endpoint_ids:
                raise ValueError(f"duplicate endpoint_id {endpoint.endpoint_id!r}")
            endpoint_ids.add(endpoint.endpoint_id)
            for resource_id in endpoint.resource_ids:
                if resource_id in resource_ids:
                    raise ValueError(f"duplicate resource_id {resource_id!r}")
                resource_ids.add(resource_id)
        object.__setattr__(self, "endpoints", endpoints)
        object.__setattr__(self, "fingerprint", _freeze(self.fingerprint))
        object.__setattr__(self, "metadata", _freeze(self.metadata))

    @property
    def resource_ids(self) -> tuple[str, ...]:
        return tuple(
            resource_id for endpoint in self.endpoints for resource_id in endpoint.resource_ids
        )

    @property
    def endpoint_ids(self) -> tuple[str, ...]:
        return tuple(endpoint.endpoint_id for endpoint in self.endpoints)

    def endpoint(self, kind: EndpointKind) -> EndpointRef | None:
        return next((endpoint for endpoint in self.endpoints if endpoint.kind is kind), None)


def resolve_device_id(default_id: str, *, alias: str | None = None) -> str:
    """ADR-003 D1: ``ARTEMIS_DEVICE_ALIAS`` wins over the platform default."""

    if alias is not None:
        return _text(alias, field_name="ARTEMIS_DEVICE_ALIAS", max_length=96)
    return _text(default_id, field_name="device_id", max_length=96)


def resolve_canonical_device_id(
    default_id: str,
    *,
    descriptor: DeviceDescriptor | None = None,
    alias: str | None = None,
) -> str:
    """The one target ID used by locks, evidence and traces.

    A built descriptor is authoritative. Before it exists (early lock
    acquisition), use the explicit alias or the physical default without
    mutating the transport serial.
    """

    if descriptor is not None:
        return _text(descriptor.device_id, field_name="canonical_device_id", max_length=96)
    if alias is None:
        import os

        alias = os.environ.get("ARTEMIS_DEVICE_ALIAS")
    return resolve_device_id(default_id, alias=alias)


def make_adb_endpoint(
    *,
    host: str,
    port: int,
    serial: str,
    metadata: Mapping[str, str] | None = None,
) -> EndpointRef:
    clean_host = _key(host, field_name="adb_host")
    clean_serial = _text(serial, field_name="adb_serial", max_length=96)
    if not 1 <= int(port) <= 65535:
        raise ValueError(f"adb_port must be between 1 and 65535, got {port!r}")
    socket = f"tcp:{clean_host}:{int(port)}"
    endpoint_id = f"adb:{socket}/{clean_serial}"
    values = {"socket": socket, **(metadata or {})}
    return EndpointRef(
        kind=EndpointKind.ADB,
        endpoint_id=endpoint_id,
        resource_ids=(),
        direction=EndpointDirection.TRANSPORT,
        identity_source=IdentitySource.ADB_SERIAL,
        metadata=values,
    )


def make_uvc_endpoint(
    *,
    host_id: str,
    hardware_key: str,
    identity_source: IdentitySource = IdentitySource.CONFIG_HINT,
    metadata: Mapping[str, str] | None = None,
) -> EndpointRef:
    host = _key(host_id, field_name="host_id")
    key = _key(hardware_key, field_name="uvc_hardware_key")
    endpoint_id = f"uvc:{host}:{key}"
    return EndpointRef(
        kind=EndpointKind.UVC,
        endpoint_id=endpoint_id,
        resource_ids=(_resource_id(EndpointKind.UVC, endpoint_id, "capture"),),
        direction=EndpointDirection.OBSERVE,
        identity_source=identity_source,
        metadata=metadata,
    )


def make_serial_endpoint(
    *,
    host_id: str,
    hardware_key: str,
    identity_source: IdentitySource = IdentitySource.DEVICE_PATH,
    metadata: Mapping[str, str] | None = None,
) -> EndpointRef:
    host = _key(host_id, field_name="host_id")
    key = _key(hardware_key, field_name="serial_hardware_key")
    endpoint_id = f"serial:{host}:{key}"
    return EndpointRef(
        kind=EndpointKind.SERIAL,
        endpoint_id=endpoint_id,
        resource_ids=(_resource_id(EndpointKind.SERIAL, endpoint_id, "command"),),
        direction=EndpointDirection.TRANSPORT,
        identity_source=identity_source,
        metadata=metadata,
    )


def make_mock_endpoint(device_id: str) -> EndpointRef:
    clean = _text(device_id, field_name="device_id", max_length=96)
    return EndpointRef(
        kind=EndpointKind.MOCK,
        endpoint_id=f"mock:{clean}",
        resource_ids=(),
        direction=EndpointDirection.OBSERVE,
        identity_source=IdentitySource.MOCK,
        metadata={},
    )


def helper_resource_id(*, host_id: str, adb_socket: str, serial: str) -> str:
    """Lease key for one accessibility-helper tunnel (ADR-011 D6).

    The local port is allocated by ``adb forward tcp:0`` after the tunnel is
    requested, so it cannot be part of the key; the serial + ADB endpoint scope
    the resource instead.
    """

    host = _key(host_id, field_name="host_id")
    socket = _text(adb_socket, field_name="adb_socket", max_length=128)
    target = _text(serial, field_name="serial", max_length=96)
    return f"helper:{host}:{socket}/{target}/forward"
