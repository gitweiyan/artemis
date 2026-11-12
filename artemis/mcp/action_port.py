"""MCP command-dispatch adapter (L1) for the L3 ActionService."""

from __future__ import annotations

import time
from typing import Any

from artemis.core.action_port import ActionDispatchResult
from artemis.core.action_types import ActionResult
from artemis.core.actions import ActionCommand, ActionReceipt, state_from_result

__all__ = ["McpActionPort"]


class McpActionPort:
    """Translate an ActionCommand into one MCP tool call and back."""

    def __init__(self, session: Any):
        self.session = session

    @property
    def action_tool_names(self) -> frozenset[str]:
        return frozenset(getattr(self.session, "action_tool_names", ()))

    def __getattr__(self, name: str) -> Any:
        # Flash still reads the compatibility controller for observation; keep
        # that legacy attribute behind the adapter rather than in L3.
        return getattr(self.session, name)

    async def dispatch(self, command: ActionCommand) -> ActionDispatchResult:
        result = await self.session.call(command.adapter_id, dict(command.parameters))
        if not isinstance(result, ActionResult):
            raise TypeError("MCP action session returned a non-ActionResult")
        state = state_from_result(result.ok, result.incident)
        receipt = ActionReceipt(
            action_id=command.command_id,
            intent_id=command.intent_id,
            command_id=command.command_id,
            attempt=command.attempt,
            adapter_id=command.adapter_id,
            state=state,
            dispatched_at=time.time(),
            acknowledged_at=time.time() if result.ok else None,
            incident=result.incident,
        )
        return ActionDispatchResult(receipt=receipt, result=result)

    async def observe(self, settle_ms: int = 400, include_image: bool = False):
        return await self.session.observe(settle_ms, include_image)

    async def call(self, name: str, args: dict[str, Any]):
        return await self.session.call(name, args)

    async def screenshot_b64(self, timeout: float | None = None):
        return await self.session.screenshot_b64(timeout)

    async def ui_hierarchy(self, timeout: float | None = None):
        return await self.session.ui_hierarchy(timeout)

    async def execute(
        self, name: str, args: dict[str, Any], tool_call_id: str, state: Any, **kwargs
    ):
        """Compatibility path for helper tools during the Task 4 rewire."""
        return await self.session.execute(name, args, tool_call_id, state, **kwargs)

    async def close(self) -> None:
        closer = getattr(self.session, "aclose", None) or getattr(self.session, "close", None)
        if callable(closer):
            await closer()
