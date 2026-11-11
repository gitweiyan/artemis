"""Compatibility shim: device control moved to adapters (Phase 9).

Reflex skills keep importing ``artemis.reflex.control``; the implementation now
lives in ``artemis.adapters.legacy_control`` so L4 does not depend on Reflex.
"""

from __future__ import annotations

from artemis.adapters.legacy_control import (  # noqa: F401
    ENV_ESP32_PORT,
    ESP32_HOME_SPAN,
    ESP32_MAX_MOVE,
    AdbInputControl,
    Esp32HidControl,
    HelperControl,
    esp32_port,
    parse_usb_state,
    write_line,
)

__all__ = [
    "ENV_ESP32_PORT",
    "AdbInputControl",
    "Esp32HidControl",
    "HelperControl",
    "ESP32_HOME_SPAN",
    "ESP32_MAX_MOVE",
    "esp32_port",
    "parse_usb_state",
    "write_line",
]
