"""L3 action service: the Agent-facing contract over an injected action port.

The compatibility MCP/Controller executor can be injected by composition, but
``artemis.actions`` never imports it. The service adds the production
``ActionReceipt`` envelope and leaves device translation to the port until the
remaining adapter extraction lands.
"""

from __future__ import annotations

import time
from typing import Any

from artemis.actions.results import ToolExecutionResult
from artemis.core.action_port import ActionExecutionPort
from artemis.core.actions import ActionReceipt, ActionState, state_from_result

__all__ = ["ActionService"]


class ActionService:
    """Execute agent actions through a platform-neutral port."""

    def __init__(self, port: ActionExecutionPort):
        self.port = port

    @property
    def action_tool_names(self) -> frozenset[str]:
        return self.port.action_tool_names

    def __getattr__(self, name: str) -> Any:
        """Compatibility proxy for Validator session methods while R3.6 completes."""
        return getattr(self.port, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "port" or name.startswith("__"):
            object.__setattr__(self, name, value)
        else:
            # Compatibility for old tests/callers that attach a fake session to
            # the executor; production code should use the port directly.
            object.__getattribute__(self, "port")
            setattr(self.port, name, value)

    async def execute(
        self,
        name: str,
        args: dict[str, Any],
        tool_call_id: str,
        state: Any,
        *,
        index_elements: list[dict[str, Any]] | None = None,
    ) -> ToolExecutionResult:
        result = await self.port.execute(
            name,
            args,
            tool_call_id,
            state,
            index_elements=index_elements,
        )
        if not isinstance(result, ToolExecutionResult):
            raise TypeError("action port returned a non-ToolExecutionResult")
        metadata = dict(result.metadata or {})
        if "action_receipt" not in metadata:
            action_id = str(metadata.get("action_id") or tool_call_id)
            state_value = state_from_result(
                str(result.status) == "success",
                metadata.get("incident"),
            )
            receipt = ActionReceipt(
                action_id=action_id,
                intent_id=action_id,
                command_id=action_id,
                attempt=int(metadata.get("action_attempt") or 1),
                adapter_id=str(result.tool_name),
                state=state_value,
                dispatched_at=time.time(),
                acknowledged_at=time.time() if result.status == "success" else None,
                incident=metadata.get("incident"),
            )
            metadata["action_receipt"] = receipt.as_dict()
        result.metadata = metadata
        return result

    async def close(self) -> None:
        await self.port.close()
