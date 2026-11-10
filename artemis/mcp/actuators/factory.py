"""L4-ish actuator selection for the generic action executor (ADR-006 3d).

The executor no longer imports/constructs the concrete ADB actuator; callers with
an injected ``ctx.actuator`` keep it, and the default is built here. The ADB
implementation still lives in ``artemis.mcp.actuators.adb`` until Phase 4/9 moves
the file; this module is the only selection point.
"""

from __future__ import annotations

from typing import Any

__all__ = ["build_actuator"]


def build_actuator(ctx: Any, controller: Any = None) -> Any:
    existing = getattr(ctx, "actuator", None)
    if existing is not None:
        return existing
    from artemis.mcp.actuators.adb import AdbActuator

    return AdbActuator(ctx, controller)
