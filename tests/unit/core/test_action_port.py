"""Command-shaped ActionDispatchPort contract (R3.6 Task 2)."""

from __future__ import annotations

import asyncio

from artemis.core.action_port import ActionDispatchPort, ActionDispatchResult
from artemis.core.action_types import ActionResult
from artemis.core.actions import ActionCommand, ActionReceipt, ActionState


class FakeDispatchPort:
    action_tool_names = frozenset({"click"})

    def __init__(self, *, ok=True):
        self.commands = []
        self.ok = ok

    async def dispatch(self, command):
        self.commands.append(command)
        state = ActionState.ADAPTER_ACKNOWLEDGED if self.ok else ActionState.UNKNOWN
        return ActionDispatchResult(
            receipt=ActionReceipt(
                action_id=command.command_id,
                intent_id=command.intent_id,
                command_id=command.command_id,
                attempt=command.attempt,
                adapter_id=command.adapter_id,
                state=state,
                dispatched_at=1.0,
            ),
            result=ActionResult.success("click", "dispatched")
            if self.ok
            else ActionResult.failure("click", "failed"),
        )

    async def observe(self, settle_ms=400, include_image=False):
        return None

    async def close(self):
        return None


def _command():
    return ActionCommand(
        command_id="cmd-1",
        intent_id="intent-1",
        attempt=1,
        adapter_id="click",
        parameters={"target": [500, 500]},
    )


def test_dispatch_port_takes_an_action_command_not_a_tool_name():
    port = FakeDispatchPort()
    command = _command()

    result = asyncio.run(port.dispatch(command))

    assert isinstance(port, ActionDispatchPort)
    assert port.commands == [command]
    assert result.receipt.command_id == "cmd-1"
    assert result.result.ok is True


def test_failed_dispatch_does_not_produce_a_verified_receipt():
    result = asyncio.run(FakeDispatchPort(ok=False).dispatch(_command()))

    assert result.receipt.state is ActionState.UNKNOWN
    assert result.receipt.state is not ActionState.VERIFIED_SUCCESS
