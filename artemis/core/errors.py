"""Stable error types shared by L3/L4 device contracts."""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "ActionReceiptContractError",
    "DeviceDisconnectedError",
    "DeviceFault",
    "DeviceFaultKind",
    "DeviceSessionError",
    "ObservationProcessingError",
    "SessionClosedError",
]


class DeviceSessionError(RuntimeError):
    """A session cannot satisfy a requested perception or control capability."""

    retryable = False


class SessionClosedError(DeviceSessionError):
    """The session has been closed (or is closing) and cannot serve requests."""

    retryable = False


class ActionReceiptContractError(ValueError):
    """A component returned a receipt that did not come from a real dispatch."""

    retryable = False


class DeviceFaultKind(StrEnum):
    DISCONNECTED = "device_disconnected"
    TRANSPORT_FAILED = "transport_error"
    CAPTURE_UNHEALTHY = "capture_unhealthy"
    SESSION_FAILED = "session_failed"


class DeviceFault(DeviceSessionError):
    """A classified hardware fault at a device I/O boundary."""

    retryable = True

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


class ObservationProcessingError(DeviceSessionError):
    """A frame could not be processed; this is not evidence the device is gone."""

    kind = "observation_processing_error"
    retryable = True

    def __init__(self, message: str, *, source: str = "perception") -> None:
        super().__init__(message)
        self.source = source
