"""Platform-neutral action execution port (PR-R3.6).

The service layer owns action semantics; the runtime layer owns the concrete
transport. The port deliberately uses the existing tool-result envelope so the
compatibility MCP/Controller adapter can be injected without importing it here.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

__all__ = ["ActionExecutionPort"]


@runtime_checkable
class ActionExecutionPort(Protocol):
    """One injectable device-action executor implementation."""

    @property
    def action_tool_names(self) -> frozenset[str]: ...

    async def execute(
        self,
        name: str,
        args: dict[str, Any],
        tool_call_id: str,
        state: Any,
        *,
        index_elements: list[dict[str, Any]] | None = None,
    ) -> Any: ...

    async def close(self) -> None: ...
