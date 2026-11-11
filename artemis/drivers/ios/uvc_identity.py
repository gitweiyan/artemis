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

__all__ = ["UVCIdentity", "probe_usb_identity", "resolve_uvc_identity"]


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


def probe_usb_identity(
    device_name: str,
    *,
    runner: Callable[..., object] | None = None,
) -> UVCIdentity | None:
    """Probe macOS USB devices for a matching product; never guess on ambiguity."""

    name = str(device_name or "").strip().casefold()
    if not name:
        return None
    run = runner or subprocess.run
    try:
        result = run(
            ["system_profiler", "SPUSBDataType", "-json"],
            capture_output=True,
            timeout=3.0,
        )
        payload = json.loads(getattr(result, "stdout", b"") or b"{}")
    except (OSError, subprocess.SubprocessError, TypeError, ValueError):
        return None
    matches = []
    for row in _usb_rows(payload):
        product = str(row.get("_name") or row.get("product_name") or "")
        if name in product.casefold() or product.casefold() in name:
            matches.append(row)
    if len(matches) != 1:
        return None
    row = matches[0]
    serial = str(row.get("serial_num") or "").strip()
    if serial:
        return UVCIdentity(derive_hardware_key(serial), IdentitySource.HARDWARE_SERIAL)
    location = str(row.get("location_id") or "").strip()
    if location:
        return UVCIdentity(
            f"usb-{derive_hardware_key(location)}",
            IdentitySource.USB_PORT_PATH,
            {"location_id": location},
        )
    return None


def resolve_uvc_identity(
    device_name: str,
    *,
    explicit_key: str | None = None,
    strict: bool = False,
    probe: Callable[[str], UVCIdentity | None] = probe_usb_identity,
) -> UVCIdentity:
    """Resolve a UVC key; config hint is explicit, and strict mode fails closed."""

    if explicit_key and derive_hardware_key(explicit_key):
        return UVCIdentity(derive_hardware_key(explicit_key), IdentitySource.CONFIG_HINT)
    found = probe(device_name)
    if found is not None:
        return found
    hint = derive_hardware_key(device_name)
    if strict or not hint:
        raise ValueError(
            f"no stable USB identity for UVC {device_name!r}; set an explicit identity"
            " key or connect a device with a USB serial/topology path"
        )
    return UVCIdentity(hint, IdentitySource.CONFIG_HINT, {"device": str(device_name)})
