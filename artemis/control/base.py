"""Control contract shared by app automation and HID sinks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from artemis.devices.capabilities import CapabilitySet


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    action: str
    incident: str | None = None
    detail: str = ""
    #: Structured evidence for telemetry/replay (pointer position, error, frame age,
    #: latency). Free text in ``detail`` is for humans; this is what a graph can read.
    metrics: dict[str, float | str] | None = None
    #: ADR-004 compatibility: ``ok`` only proves the adapter accepted/dispatched.
    action_id: str | None = None
    attempt: int | None = None
    state: str | None = None


@runtime_checkable
class ControlSink(Protocol):
    capabilities: CapabilitySet

    async def tap_abs(self, x: int, y: int) -> ActionResult: ...

    async def tap_rel(self, dx: int, dy: int) -> ActionResult: ...

    async def swipe(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        duration_ms: int = 800,
    ) -> ActionResult: ...

    async def input_text(self, text: str) -> ActionResult: ...

    async def press_key(self, key: Any) -> ActionResult: ...
