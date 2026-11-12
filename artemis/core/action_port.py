"""Platform-neutral action command port (PR-R3.6 Task 2).

The service layer owns semantic translation; the runtime layer owns transport.
``ActionDispatchPort`` consumes a fully-formed ``ActionCommand`` and returns the
production receipt plus the adapter result. The old execute-shaped protocol is
kept only as a compatibility/test alias during migration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from artemis.core.action_types import ActionResult
from artemis.core.actions import ActionCommand, ActionReceipt

__all__ = ["ActionDispatchPort", "ActionDispatchResult", "ActionExecutionPort"]


@dataclass(frozen=True)
class ActionDispatchResult:
    """The adapter result and the receipt that proves what was dispatched."""

    receipt: ActionReceipt
    result: ActionResult


@runtime_checkable
class ActionDispatchPort(Protocol):
    """One command-level device-action transport."""

    @property
    def action_tool_names(self) -> frozenset[str]: ...

    async def dispatch(self, command: ActionCommand) -> ActionDispatchResult: ...

    async def observe(self, settle_ms: int = 400, include_image: bool = False) -> Any: ...

    async def close(self) -> None: ...


@runtime_checkable
class ActionExecutionPort(Protocol):
    """Deprecated execute-shaped seam; remove after the Task 4 Agent rewire."""

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
