"""Transitional L4 control adapter over FirmwareBridge (ADR-009 4.2d).

``Esp32HidControl`` still owns the gesture/key policy (and its 100+ tests), but its
low-level serial exchange moved behind ``FirmwareBridge``. This subclass keeps the
legacy Control Protocol intact for PointerSession/HidControlSink while adding the
v2 handshake, capability facts and serial transport.

Once Phase 4.3 moves the policy into L4/L3 ports this compatibility shell can go.
"""

from __future__ import annotations

from typing import Any

from dataclasses import replace

from artemis.adapters.firmware import FirmwareBridge, FirmwareInfo
from artemis.adapters.serial import SerialTransport
from artemis.reflex.control import Esp32HidControl

__all__ = ["Esp32BridgeControl"]


class Esp32BridgeControl(Esp32HidControl):
    """ESP32 HID control whose bytes go through the firmware bridge."""

    def __init__(
        self,
        port: str,
        *,
        ack_timeout_s: float = 3.0,
        bridge: Any | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(port, ack_timeout_s=ack_timeout_s, **kwargs)
        self.bridge = bridge or FirmwareBridge(SerialTransport(port), ack_timeout_s=ack_timeout_s)
        self.firmware_info: FirmwareInfo | None = None

    def _exchange(self, command: str, *, timeout: float | None = None) -> str:
        return self.bridge.exchange(command, timeout=timeout)

    def _resync_line(self) -> None:
        resync = getattr(self.bridge, "resync", None)
        if callable(resync):
            resync()

    def preflight(self) -> dict[str, int | str | None]:
        """Negotiate the protocol, then verify the board's live HID state."""
        info = self.bridge.handshake()
        if not info.compatible:
            raise RuntimeError(
                f"firmware protocol {info.protocol} is newer than this host supports"
                f" (1..2); refusing input"
            )
        state = self.bridge.preflight()
        self.firmware_info = replace(
            info,
            mounted=state.mounted,
            suspended=state.suspended,
            mouse=state.mouse,
            keyboard=state.keyboard,
            touch=state.touch,
            bci=state.bci,
        )
        return {
            "mounted": int(state.mounted),
            "suspended": int(state.suspended),
            "mouse": int(state.mouse),
            "keyboard": int(state.keyboard),
            "touch": int(state.touch),
            "bci": int(state.bci),
            "mode": state.mode or info.mode,
            "protocol": info.protocol,
            "firmware": info.build,
        }

    def close(self) -> None:
        self.release_all()
        self.bridge.close()
        self._fd = None
