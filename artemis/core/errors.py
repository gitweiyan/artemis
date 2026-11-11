"""Stable error types shared by L3/L4 device contracts."""

from __future__ import annotations

__all__ = ["DeviceSessionError", "SessionClosedError"]


class DeviceSessionError(RuntimeError):
    """A session cannot satisfy a requested perception or control capability."""


class SessionClosedError(DeviceSessionError):
    """The session has been closed (or is closing) and cannot serve requests."""
