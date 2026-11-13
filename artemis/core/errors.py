"""Stable error types shared by L3/L4 device contracts."""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "ActionReceiptContractError",
    "DeviceDisconnectedError",
    "DeviceFault",
    "DeviceFaultKind",
    "DeviceSessionError",
    "SessionClosedError",
]


class DeviceSessionError(RuntimeError):
    """A session cannot satisfy a requested perception or control capability."""


class SessionClosedError(DeviceSessionError):
    """The session has been closed (or is closing) and cannot serve requests."""


class ActionReceiptContractError(ValueError):
    """A component returned a receipt that did not come from a real dispatch."""


class DeviceFaultKind(StrEnum):
    DISCONNECTED = "device_disconnected"
    TRANSPORT_FAILED = "transport_error"
    SESSION_FAILED = "session_failed"


class DeviceFault(DeviceSessionError):
    """A classified hardware fault at a device I/O boundary."""

    def __init__(
        self,
        message: str,
        *,
        kind: DeviceFaultKind = DeviceFaultKind.SESSION_FAILED,
        source: str = "device",
        fatal: bool = True,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.source = source
        self.fatal = fatal


class DeviceDisconnectedError(DeviceFault):
    def __init__(self, message: str, *, source: str = "device") -> None:
        super().__init__(
            message,
            kind=DeviceFaultKind.DISCONNECTED,
            source=source,
        )
