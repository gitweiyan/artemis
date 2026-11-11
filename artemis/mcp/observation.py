"""Compatibility shim: observation now lives in artemis.perception (Phase 9).

MCP servers/callers may keep importing this path; new L2/L3 code should import
``artemis.perception.observation`` directly.
"""

from __future__ import annotations

from artemis.perception.observation import (  # noqa: F401
    ControllerObservationPort,
    ObservationEvidence,
    ObservationPort,
    ScreenData,
    SessionObservationPort,
    get_screen_data,
    observe,
    session_screen_data,
)

__all__ = [
    "ControllerObservationPort",
    "ObservationEvidence",
    "ObservationPort",
    "ScreenData",
    "SessionObservationPort",
    "get_screen_data",
    "observe",
    "session_screen_data",
]
