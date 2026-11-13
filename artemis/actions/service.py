"""L3 action service: command dispatch plus explicit helper-tool ownership."""

from __future__ import annotations

from typing import Any

from artemis.actions.device_actions import DeviceActionService
from artemis.actions.results import ToolExecutionResult
from artemis.core.action_port import ActionDispatchPort
from artemis.core.errors import ActionReceiptContractError

__all__ = ["ActionService"]


class ActionService:
    """Execute device actions through a command port and helpers explicitly."""

    def __init__(
        self,
        ctx: Any,
        port: ActionDispatchPort,
        *,
        observation_port: Any | None = None,
        helper_port: Any | None = None,
    ):
        self.ctx = ctx
        self.port = port
        self.observation_port = observation_port or getattr(ctx, "observation_port", None)
        self.helper_port = helper_port
        self._closed = False
        self._device_service = DeviceActionService(
            ctx, port, observation_port=self.observation_port
        )

    @property
    def action_tool_names(self) -> frozenset[str]:
        return self.port.action_tool_names

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
            if result.metadata and "action_receipt" in result.metadata:
                raise ActionReceiptContractError(
                    f"helper tool {raw_name!r} must not provide an ActionReceipt"
                )
        if not isinstance(result, ToolExecutionResult):
            raise TypeError("action port returned a non-ToolExecutionResult")
        result.metadata = dict(result.metadata or {})
        return result

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self.port.close()
        if self.helper_port is not None:
            closer = getattr(self.helper_port, "aclose", None) or getattr(
                self.helper_port, "close", None
            )
            if callable(closer):
                result = closer()
                if hasattr(result, "__await__"):
                    await result
