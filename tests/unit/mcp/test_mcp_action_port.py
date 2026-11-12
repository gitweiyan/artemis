"""MCP command adapter contract (R3.6 Task 2)."""

from __future__ import annotations

import asyncio

from artemis.core.action_types import ActionCode, ActionResult, ObserveResult
from artemis.core.actions import ActionCommand, ActionState
from artemis.mcp.action_port import McpActionPort


class FakeSession:
    action_tool_names = frozenset({"click"})

    def __init__(self, ok=True):
        self.ok = ok
        self.calls = []
        self.closed = False

    async def call(self, name, args):
        self.calls.append((name, args))
        if self.ok:
            return ActionResult(ok=True, code=ActionCode.OK, action=name, message="clicked")
        return ActionResult(ok=False, code=ActionCode.DEVICE_ERROR, action=name, message="failed")

    async def observe(self, settle_ms=400, include_image=False):
        return ObserveResult(ok=True, message="observed", elements=[])

    async def aclose(self):
        self.closed = True


def _command():
    return ActionCommand(
        command_id="cmd-1",
        intent_id="intent-1",
        attempt=1,
        adapter_id="click",
        parameters={"target": [100, 200]},
    )


def test_mcp_port_dispatches_a_command_and_carries_the_receipt():
    session = FakeSession()
    result = asyncio.run(McpActionPort(session).dispatch(_command()))

    assert session.calls == [("click", {"target": [100, 200]})]
    assert result.receipt.state is ActionState.ADAPTER_ACKNOWLEDGED
    assert result.receipt.command_id == "cmd-1"
    assert result.result.ok is True


def test_mcp_port_failure_is_not_verified():
    result = asyncio.run(McpActionPort(FakeSession(ok=False)).dispatch(_command()))

    assert result.receipt.state is ActionState.UNKNOWN
    assert result.receipt.state is not ActionState.VERIFIED_SUCCESS


def test_mcp_port_forwards_observation_and_close():
    session = FakeSession()
    port = McpActionPort(session)

    assert asyncio.run(port.observe(settle_ms=1)).ok is True
    asyncio.run(port.close())
    assert session.closed is True
