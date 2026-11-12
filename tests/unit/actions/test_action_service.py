"""L3 ActionService contract (PR-R3.6)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from artemis.actions.results import ToolExecutionResult
from artemis.actions.service import ActionService
from artemis.core.action_port import ActionExecutionPort


class FakePort:
    action_tool_names = frozenset({"click"})

    def __init__(self):
        self.calls = []
        self.closed = False

    async def execute(self, name, args, tool_call_id, state, *, index_elements=None):
        self.calls.append((name, args, tool_call_id, index_elements))
        return ToolExecutionResult(
            tool_call_id=tool_call_id,
            tool_name=name,
            status="success",
            text_summary="dispatched",
            metadata={
                "action_id": "a-1",
                "action_attempt": 1,
                "action_state": "adapter_acknowledged",
            },
        )

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_action_service_uses_the_injected_port_and_adds_a_receipt():
    port = FakePort()
    service = ActionService(port)
    state = SimpleNamespace(indexed_elements=[])

    result = await service.execute("click", {"target": 1}, "tc-1", state)

    assert isinstance(service.port, ActionExecutionPort)
    assert port.calls == [("click", {"target": 1}, "tc-1", None)]
    assert result.metadata["action_receipt"]["state"] == "adapter_acknowledged"
    assert result.metadata["action_receipt"]["action_id"] == "a-1"


@pytest.mark.asyncio
async def test_action_service_close_delegates_to_the_port():
    port = FakePort()
    await ActionService(port).close()
    assert port.closed is True


class SessionPort:
    action_tool_names = frozenset({"click"})

    def __init__(self):
        self.calls = []

    async def call(self, name, args):
        from artemis.core.action_types import ActionCode, ActionResult

        self.calls.append((name, args))
        return ActionResult(ok=True, code=ActionCode.OK, action=name, message="clicked")

    async def call_raw(self, name, args):
        raise AssertionError("not used")

    async def observe(self, settle_ms=400, include_image=False):
        from artemis.core.action_types import ObserveResult

        return ObserveResult(ok=True, message="observed", elements=[], hierarchy_ok=True)

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_semantic_service_owns_target_translation_and_receipt():
    port = SessionPort()
    service = ActionService(port, SimpleNamespace(indexed_elements=[], data_engine=None))
    state = SimpleNamespace(indexed_elements=[])

    result = await service.execute("click", {"target": [500, 500], "target_description": "button"}, "tc-semantic", state)

    assert port.calls == [("click", {"target": [500, 500], "times": 1, "delay_ms": 100})]
    assert result.status == "success"
    assert result.metadata["action_receipt"]["state"] == "adapter_acknowledged"
