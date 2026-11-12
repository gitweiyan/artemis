"""Resolve a stable UVC endpoint key (ADR-003/Stage 3).

The AVFoundation name ("USB Video") is a configuration hint, never a stable
identity. Prefer the USB hardware serial; otherwise use the USB topology port
path supplied by the platform probe. A caller can pin an explicit key for a
known rig, but that is recorded as a config hint unless a serial is known.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import subprocess
from typing import Callable, Mapping

from artemis.core.identity import IdentitySource, derive_hardware_key

__all__ = [
    "UsbEntry",
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


def resolve_uvc_identity(
    device_name: str,
    *,
    explicit_key: str | None = None,
    strict: bool = False,
    host_id: str | None = None,
    probe: Callable[[str], UVCIdentity | None] = probe_usb_identity,
    inventory_probe: Callable[[str], tuple[UsbEntry, ...]] | None = None,
) -> UVCIdentity:
    """Resolve hardware-first, with host-scoped serial uniqueness."""

    metadata = {"host_id": host_id} if host_id else {}
    if inventory_probe is not None:
        entries = tuple(inventory_probe(device_name))
    elif probe is probe_usb_identity:
        entries = probe_usb_inventory(device_name)
    else:
        entries = ()
    if entries:
        serials = [entry.serial for entry in entries if entry.serial]
        unique_serial = serials[0] if len(serials) == 1 and len(entries) == 1 else None
        if unique_serial and serials.count(unique_serial) == 1:
            return UVCIdentity(unique_serial, IdentitySource.HARDWARE_SERIAL, metadata)
        locations = sorted(entry.location_id for entry in entries if entry.location_id)
        if locations:
            location = locations[0]
            return UVCIdentity(
                f"usb-{derive_hardware_key(location)}",
                IdentitySource.USB_PORT_PATH,
                {**metadata, "location_id": location},
            )

    found = probe(device_name)
    if found is not None:
        return UVCIdentity(found.key, found.source, {**dict(found.metadata or {}), **metadata})

    explicit = derive_hardware_key(explicit_key) if explicit_key else ""
    if strict:
        raise ValueError(
            f"no stable USB identity for UVC {device_name!r}; connect a device with a"
            " USB serial/topology path (explicit config hints are not hardware identity)"
        )
    if explicit:
        return UVCIdentity(explicit, IdentitySource.CONFIG_HINT, metadata)

    hint = derive_hardware_key(device_name)
    if not hint:
        raise ValueError(
            f"no stable USB identity for UVC {device_name!r}; set an explicit identity"
            " key or connect a device with a USB serial/topology path"
        )
    return UVCIdentity(hint, IdentitySource.CONFIG_HINT, {**metadata, "device": str(device_name)})
