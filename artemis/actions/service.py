"""L3 action service: command dispatch plus explicit helper-tool ownership."""

from __future__ import annotations

import time
from typing import Any

from artemis.actions.device_actions import DeviceActionService
from artemis.actions.results import ToolExecutionResult
from artemis.core.action_port import ActionDispatchPort
from artemis.core.actions import ActionReceipt, ActionState, state_from_result

__all__ = ["ActionService"]


class ActionService:
    """Execute device actions through a command port and helpers explicitly."""

    def __init__(
        self,
        ctx: Any,
        port: ActionDispatchPort,
        *,
        helper_port: Any | None = None,
    ):
        object.__setattr__(self, "ctx", ctx)
        object.__setattr__(self, "port", port)
        object.__setattr__(self, "helper_port", helper_port)
        object.__setattr__(self, "_device_service", DeviceActionService(ctx, port))

    @property
    def action_tool_names(self) -> frozenset[str]:
        return self.port.action_tool_names

    def __getattr__(self, name: str) -> Any:
        if name in {"_device_service", "ctx", "port", "helper_port"}:
            raise AttributeError(name)
        if hasattr(self.port, name):
            return getattr(self.port, name)
        raise AttributeError(name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in {"ctx", "port", "helper_port", "_device_service", "execute"} or name.startswith("__"):
            object.__setattr__(self, name, value)
        elif hasattr(self.port, name):
            setattr(self.port, name, value)
        else:
            object.__setattr__(self, name, value)

    async def execute(
        self,
        name: str,
        args: dict[str, Any],
        tool_call_id: str,
        state: Any,
        *,
        index_elements: list[dict[str, Any]] | None = None,
    ) -> ToolExecutionResult:
        raw_name = name.split(":")[-1] if ":" in name else name
        if raw_name in self.action_tool_names:
            result = await self._device_service.execute(
                name,
                args,
                tool_call_id,
                state,
                index_elements=index_elements,
            )
        else:
            if self.helper_port is None:
                raise RuntimeError(
                    f"no helper action port for tool {raw_name!r}; "
                    "compose an explicit helper executor"
                )
            result = await self.helper_port.execute(
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
            recorded_state = metadata.get("action_state")
            try:
                state_value = (
                    ActionState(str(recorded_state))
                    if recorded_state
                    else state_from_result(
                        str(result.status) == "success",
                        metadata.get("incident"),
                    )
                )
            except ValueError:
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
        if self.helper_port is not None:
            closer = getattr(self.helper_port, "aclose", None) or getattr(
                self.helper_port, "close", None
            )
            if callable(closer):
                result = closer()
                if hasattr(result, "__await__"):
                    await result
