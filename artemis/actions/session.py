"""Compatibility typing shim; the MCP transport implementation lives in L1.

New action code uses ``artemis.actions.service.ActionService`` with an injected
port. This module intentionally contains no MCP import.
"""

from __future__ import annotations

from typing import Any, Protocol


class ActionSession(Protocol):
    started: bool

    async def call(self, name: str, args: dict) -> object: ...

    async def observe(self, settle_ms: int = 400, include_image: bool = False) -> object: ...

    async def aclose(self) -> None: ...


async def get_action_service(ctx: Any, actuator: Any = None) -> Any:
    """Return the composed L3 action service for an Agent run."""

    service = getattr(ctx, "action_service", None)
    if service is None:
        raise RuntimeError(
            "no action service is composed on this context; build a device profile first"
        )
    return service


#: Compatibility alias for callers/tests written before the Service naming.
get_action_session = get_action_service
