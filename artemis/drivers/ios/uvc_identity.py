"""Resolve a stable UVC endpoint key (ADR-003/Stage 3).

The AVFoundation name ("USB Video") is a configuration hint, never a stable
identity. Prefer the USB hardware serial; otherwise use the USB topology port
path supplied by the platform probe. A caller can pin an explicit key for a
known rig, but that is recorded as a config hint unless a serial is known.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
import subprocess
from typing import Callable, Mapping

from artemis.core.identity import IdentitySource, derive_hardware_key

__all__ = [
    "UsbEntry",
    "UsbInventoryResult",
    "UsbInventoryStatus",
    "UVCDeviceNotFoundError",
    "UVCIdentityError",
    "UVCIdentityUnavailableError",
    "UVCInventoryUnavailableError",
    "UVCPortHintMismatchError",
    "AmbiguousDeviceIdentityError",
    "UVCIdentity",
    "probe_usb_identity",
    "probe_usb_inventory",
    "resolve_uvc_identity",
]


@dataclass(frozen=True)
class UsbEntry:
    """One USB product row from the host inventory."""

    product: str
    serial: str | None = None
    location_id: str | None = None


class UsbInventoryStatus(StrEnum):
    UNAVAILABLE = "unavailable"
    EMPTY = "empty"
    OK = "ok"


@dataclass(frozen=True)
class UsbInventoryResult:
    status: UsbInventoryStatus
    entries: tuple[UsbEntry, ...] = ()
    diagnostic: str | None = None


class UVCIdentityError(ValueError):
    """Base class for stable UVC identity failures."""


class UVCInventoryUnavailableError(UVCIdentityError):
    pass


class UVCDeviceNotFoundError(UVCIdentityError):
    pass


class UVCPortHintMismatchError(UVCDeviceNotFoundError):
    pass


class AmbiguousDeviceIdentityError(UVCIdentityError):
    pass


class UVCIdentityUnavailableError(UVCIdentityError):
    pass


@dataclass(frozen=True)
class UVCIdentity:
    key: str
    source: IdentitySource
    metadata: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        text = derive_hardware_key(self.key)
        if not text:
            raise ValueError("UVC identity key must not be empty")
        object.__setattr__(self, "key", text)
        object.__setattr__(self, "source", IdentitySource(self.source))
        object.__setattr__(self, "metadata", dict(self.metadata or {}))


def _usb_rows(payload: object):
    """Yield USB device dictionaries from system_profiler's nested JSON."""

    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in {"_name", "name", "product_name", "serial_num", "location_id"}:
                yield payload
                return
            if isinstance(value, (dict, list)):
                yield from _usb_rows(value)
    elif isinstance(payload, list):
        for item in payload:
            yield from _usb_rows(item)


def probe_usb_inventory(
    device_name: str,
    *,
    runner: Callable[..., object] | None = None,
) -> tuple[UsbEntry, ...]:
    """Return every matching USB row; ambiguity is preserved for the resolver."""

    name = str(device_name or "").strip().casefold()
    if not name:
        return ()
    run = runner or subprocess.run
    try:
        result = run(
            ["system_profiler", "SPUSBDataType", "-json"],
            capture_output=True,
            timeout=3.0,
        )
        payload = json.loads(getattr(result, "stdout", b"") or b"{}")
    except (OSError, subprocess.SubprocessError, TypeError, ValueError):
        return ()
    entries: list[UsbEntry] = []
    for row in _usb_rows(payload):
        product = str(row.get("_name") or row.get("product_name") or "")
        if name in product.casefold() or product.casefold() in name:
            entries.append(
                UsbEntry(
                    product=product,
                    serial=str(row.get("serial_num") or "").strip() or None,
                    location_id=str(row.get("location_id") or "").strip() or None,
                )
            )
    return tuple(entries)


def _usb_payload_rows(payload: object) -> list[dict[str, object]] | None:
    """Validate the system_profiler USB JSON envelope.

    A valid envelope must be a dict carrying a list under ``SPUSBDataType``.
    An empty list is valid EMPTY; anything else is UNAVAILABLE, never a guess.
    """

    if not isinstance(payload, dict):
        return None
    data = payload.get("SPUSBDataType")
    if not isinstance(data, list):
        return None
    rows: list[dict[str, object]] = []
    for item in data:
        if not isinstance(item, dict):
            return None
        rows.append(item)
    return rows


def probe_usb_inventory_result(
    *,
    runner: Callable[..., object] | None = None,
) -> UsbInventoryResult:
    """Read the complete host USB inventory; preserve unavailable vs empty."""

    run = runner or subprocess.run
    try:
        result = run(
            ["system_profiler", "SPUSBDataType", "-json"],
            capture_output=True,
            timeout=3.0,
        )
        payload = json.loads(getattr(result, "stdout", b"") or b"{}")
    except (OSError, subprocess.SubprocessError, TypeError, ValueError) as exc:
        return UsbInventoryResult(
            UsbInventoryStatus.UNAVAILABLE, diagnostic=f"USB inventory probe failed: {exc}"
        )
    rows = _usb_payload_rows(payload)
    if rows is None:
        return UsbInventoryResult(
            UsbInventoryStatus.UNAVAILABLE,
            diagnostic="USB inventory JSON does not match the SPUSBDataType schema",
        )
    if not rows:
        return UsbInventoryResult(UsbInventoryStatus.EMPTY)
    entries: list[UsbEntry] = []
    for row in _usb_rows(rows):
        product = str(row.get("_name") or row.get("product_name") or "")
        if not product:
            continue
        entries.append(
            UsbEntry(
                product=product,
                serial=str(row.get("serial_num") or "").strip() or None,
                location_id=str(row.get("location_id") or "").strip() or None,
            )
        )
    if not entries:
        return UsbInventoryResult(UsbInventoryStatus.EMPTY)
    return UsbInventoryResult(UsbInventoryStatus.OK, tuple(entries))


def _matches_product(device_name: str, entry: UsbEntry) -> bool:
    name = str(device_name or "").strip().casefold()
    product = entry.product.casefold()
    return bool(name and (name in product or product in name))


def probe_usb_identity(
    device_name: str,
    *,
    runner: Callable[..., object] | None = None,
) -> UVCIdentity | None:
    """Probe a single unambiguous USB device; never guess on ambiguity."""

    entries = probe_usb_inventory(device_name, runner=runner)
    if len(entries) != 1:
        return None
    row = entries[0]
    if row.serial:
        return UVCIdentity(row.serial, IdentitySource.HARDWARE_SERIAL)
    if row.location_id:
        return UVCIdentity(
            f"usb-{derive_hardware_key(row.location_id)}",
            IdentitySource.USB_PORT_PATH,
            {"location_id": row.location_id},
        )
    return None


def _config_hint_identity(
    device_name: str,
    explicit_key: str | None,
    metadata: Mapping[str, str],
) -> UVCIdentity | None:
    explicit = derive_hardware_key(explicit_key) if explicit_key else ""
    if explicit:
        return UVCIdentity(explicit, IdentitySource.CONFIG_HINT, metadata)
    hint = derive_hardware_key(device_name)
    if hint:
        return UVCIdentity(hint, IdentitySource.CONFIG_HINT, {**metadata, "device": device_name})
    return None


def resolve_uvc_identity(
    device_name: str,
    *,
    explicit_key: str | None = None,
    strict: bool = False,
    host_id: str | None = None,
    port_hint: str | None = None,
    probe: Callable[[str], UVCIdentity | None] = probe_usb_identity,
    inventory_probe: Callable[[str], tuple[UsbEntry, ...]] | None = None,
    inventory_result_probe: Callable[[], UsbInventoryResult] | None = None,
) -> UVCIdentity:
    """Resolve a host-unique hardware identity, or fail closed."""

    metadata = {"host_id": host_id} if host_id else {}
    entries: tuple[UsbEntry, ...] | None = None

    if inventory_result_probe is not None:
        result = inventory_result_probe()
        if result.status is UsbInventoryStatus.UNAVAILABLE:
            if strict or port_hint:
                raise UVCInventoryUnavailableError(result.diagnostic or "USB inventory unavailable")
            fallback = _config_hint_identity(device_name, explicit_key, metadata)
            if fallback is not None:
                return fallback
            raise UVCInventoryUnavailableError(result.diagnostic or "USB inventory unavailable")
        if result.status is UsbInventoryStatus.EMPTY:
            if strict or port_hint:
                raise UVCDeviceNotFoundError("USB inventory is empty")
            fallback = _config_hint_identity(device_name, explicit_key, metadata)
            if fallback is not None:
                return fallback
            raise UVCDeviceNotFoundError("USB inventory is empty")
        entries = result.entries
    elif inventory_probe is not None:
        entries = tuple(inventory_probe(device_name))
    elif probe is probe_usb_identity:
        result = probe_usb_inventory_result()
        if result.status is UsbInventoryStatus.UNAVAILABLE:
            if strict or port_hint:
                raise UVCInventoryUnavailableError(result.diagnostic or "USB inventory unavailable")
            fallback = _config_hint_identity(device_name, explicit_key, metadata)
            if fallback is not None:
                return fallback
            raise UVCInventoryUnavailableError(result.diagnostic or "USB inventory unavailable")
        if result.status is UsbInventoryStatus.EMPTY:
            if strict or port_hint:
                raise UVCDeviceNotFoundError("USB inventory is empty")
            fallback = _config_hint_identity(device_name, explicit_key, metadata)
            if fallback is not None:
                return fallback
            raise UVCDeviceNotFoundError("USB inventory is empty")
        entries = result.entries

    if entries is not None:
        candidates = tuple(entry for entry in entries if _matches_product(device_name, entry))
        if port_hint:
            wanted = derive_hardware_key(port_hint)
            candidates = tuple(
                entry
                for entry in candidates
                if entry.location_id and derive_hardware_key(entry.location_id) == wanted
            )
            if not candidates:
                raise UVCPortHintMismatchError(
                    f"USB port hint {port_hint!r} did not match a candidate"
                )
        if not candidates:
            raise UVCDeviceNotFoundError(f"no USB candidate matches {device_name!r}")
        if len(candidates) > 1:
            raise AmbiguousDeviceIdentityError(
                f"multiple USB candidates match {device_name!r}; provide port_hint"
            )
        selected = candidates[0]
        serials = [entry.serial for entry in entries if entry.serial]
        serial_ok = selected.serial is not None and serials.count(selected.serial) == 1
        if serial_ok:
            return UVCIdentity(selected.serial, IdentitySource.HARDWARE_SERIAL, metadata)
        locations = [entry.location_id for entry in entries if entry.location_id]
        if selected.location_id and locations.count(selected.location_id) == 1:
            return UVCIdentity(
                f"usb-{derive_hardware_key(selected.location_id)}",
                IdentitySource.USB_PORT_PATH,
                {**metadata, "location_id": selected.location_id},
            )
        explicit = derive_hardware_key(explicit_key) if explicit_key else ""
        if strict:
            raise UVCIdentityUnavailableError(
                f"no host-unique hardware identity for UVC {device_name!r}"
            )
        if explicit:
            return UVCIdentity(explicit, IdentitySource.CONFIG_HINT, metadata)
        raise UVCIdentityUnavailableError(
            f"no host-unique hardware identity for UVC {device_name!r}"
        )

    found = probe(device_name)
    if found is not None:
        return UVCIdentity(found.key, found.source, {**dict(found.metadata or {}), **metadata})

    explicit = derive_hardware_key(explicit_key) if explicit_key else ""
    if strict:
        raise UVCIdentityUnavailableError(
            f"no stable USB identity for UVC {device_name!r}; connect a device with a"
            " USB serial/topology path (explicit config hints are not hardware identity)"
        )
    if explicit:
        return UVCIdentity(explicit, IdentitySource.CONFIG_HINT, metadata)

    hint = derive_hardware_key(device_name)
    if not hint:
        raise UVCIdentityUnavailableError(
            f"no stable USB identity for UVC {device_name!r}; set an explicit identity"
            " key or connect a device with a USB serial/topology path"
        )
    return UVCIdentity(hint, IdentitySource.CONFIG_HINT, {**metadata, "device": str(device_name)})
