"""L3 ActionService contract (R3.6 Task 4)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from artemis.actions.results import ToolExecutionResult
from artemis.actions.service import ActionService
from artemis.core.action_port import ActionDispatchResult
from artemis.core.action_types import ActionCode, ActionResult, ObserveResult
from artemis.core.actions import ActionReceipt, ActionState


class HelperPort:
    action_tool_names = frozenset()

    def __init__(self):
        self.calls = []
        self.closed = False

    async def execute(self, name, args, tool_call_id, state, *, index_elements=None):
        self.calls.append((name, args))
        return ToolExecutionResult(
            tool_call_id=tool_call_id,
            tool_name=name,
            status="success",
            text_summary="helper",
            metadata={"action_id": "helper-1", "action_state": "adapter_acknowledged"},
        )

    async def close(self):
        self.closed = True


class SessionPort:
    action_tool_names = frozenset({"click"})

    def __init__(self):
        self.calls = []

    async def call(self, name, args):
        self.calls.append((name, args))
        return ActionResult(ok=True, code=ActionCode.OK, action=name, message="clicked")

    async def call_raw(self, name, args):
        raise AssertionError("not used")

    async def observe(self, settle_ms=400, include_image=False):
        return ObserveResult(ok=True, message="observed", elements=[], hierarchy_ok=True)

    async def close(self):
        return None


class DispatchPort:
    action_tool_names = frozenset({"click"})

    def __init__(self):
        self.commands = []

    async def dispatch(self, command):
        self.commands.append(command)
        return ActionDispatchResult(
            result=ActionResult(ok=True, code=ActionCode.OK, action="click", message="clicked"),
            receipt=ActionReceipt(
                action_id=command.command_id,
                intent_id=command.intent_id,
                command_id=command.command_id,
                attempt=command.attempt,
                adapter_id=command.adapter_id,
                state=ActionState.ADAPTER_ACKNOWLEDGED,
                dispatched_at=1.0,
            ),
        )

    async def observe(self, settle_ms=400, include_image=False):
        return ObserveResult(ok=True, message="observed", elements=[])

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_helper_tools_use_the_explicit_helper_port():
    helper = HelperPort()
    service = ActionService(None, DispatchPort(), helper_port=helper)

    result = await service.execute("read_note", {"key": "x"}, "tc", SimpleNamespace())

    assert helper.calls == [("read_note", {"key": "x"})]
    assert result.status == "success"


@pytest.mark.asyncio
async def test_semantic_service_dispatches_a_command_not_a_tool_call():
    port = DispatchPort()
    ctx = SimpleNamespace(indexed_elements=[], data_engine=None)
    service = ActionService(ctx, port)

    result = await service.execute(
        "click",
        {"target": [500, 500], "target_description": "button"},
        "tc-command",
        SimpleNamespace(indexed_elements=[]),
    )

    assert len(port.commands) == 1
    command = port.commands[0]
    assert command.adapter_id == "click"
    assert command.parameters["target"] == [500, 500]
    assert result.metadata["action_receipt"]["state"] == "adapter_acknowledged"


@pytest.mark.asyncio
async def test_session_port_compatibility_still_owns_target_translation():
    port = SessionPort()
    service = ActionService(SimpleNamespace(indexed_elements=[], data_engine=None), port)
    state = SimpleNamespace(indexed_elements=[])

    result = await service.execute(
        "click", {"target": [500, 500], "target_description": "button"}, "tc-semantic", state
    )

    assert port.calls == [("click", {"target": [500, 500], "times": 1, "delay_ms": 100})]
    assert result.status == "success"
    assert result.metadata["action_receipt"]["state"] == "adapter_acknowledged"


@pytest.mark.asyncio
async def test_action_service_close_closes_helper_port():
    helper = HelperPort()
    service = ActionService(None, DispatchPort(), helper_port=helper)

    await service.close()

    assert helper.closed is True
