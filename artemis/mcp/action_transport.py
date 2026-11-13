"""L1 transport adapter for the command-shaped MCP action path.

This class owns only session acquisition and raw MCP calls. Semantic translation
and receipt construction live in L3; the legacy McpActionExecutor remains for
helper-tool compatibility and old callers.
"""

from __future__ import annotations

from typing import Any

from artemis.actions.manifest import OPTIONAL_ACTIONS, REQUIRED_ACTIONS
from artemis.mcp.action_session_impl import get_action_session
from artemis.mcp.actuators.factory import build_actuator

__all__ = ["McpActionTransport"]


class McpActionTransport:
    def __init__(self, ctx: Any, actuator: Any | None = None):
        self.ctx = ctx
        self._actuator = actuator or build_actuator(ctx)
        self._session = None

    @property
    def action_tool_names(self) -> frozenset[str]:
        extensions = getattr(self._actuator, "extensions", lambda: ())()
        return (REQUIRED_ACTIONS | OPTIONAL_ACTIONS) | {item.name for item in extensions}

    async def _session_or_start(self):
        if self._session is None or not self._session.started:
            self._session = await get_action_session(
                self.ctx,
                actuator=self._actuator,
                observation_port=getattr(self.ctx, "observation_port", None),
            )
        return self._session

    async def call(self, name: str, args: dict[str, Any]):
        result = await (await self._session_or_start()).call(name, args)
        self._report_fault(name, result)
        return result

    def _report_fault(self, name: str, result: Any) -> None:
        incident = getattr(result, "incident", None)
        if incident not in {"device_disconnected", "transport_error"}:
            return
        session = getattr(self.ctx, "device_session", None)
        recorder = getattr(session, "record_fault", None)
        if not callable(recorder):
            return
        from artemis.core.errors import DeviceFault, DeviceFaultKind

        kind = (
            DeviceFaultKind.DISCONNECTED
            if incident == "device_disconnected"
            else DeviceFaultKind.TRANSPORT_FAILED
        )
        recorder(DeviceFault(f"{name}: {incident}", kind=kind, source=f"action:{name}"))

    async def call_raw(self, name: str, args: dict[str, Any]):
        return await (await self._session_or_start()).call_raw(name, args)

    async def observe(self, settle_ms: int = 400, include_image: bool = False):
        return await (await self._session_or_start()).observe(
            settle_ms=settle_ms, include_image=include_image
        )

    async def screenshot_b64(self, timeout: float | None = None):
        return await (await self._session_or_start()).screenshot_b64(timeout)

    async def ui_hierarchy(self, timeout: float | None = None):
        return await (await self._session_or_start()).ui_hierarchy(timeout)

    async def close(self) -> None:
        if self._session is not None:
            await self._session.aclose()
            self._session = None
