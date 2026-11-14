"""Runtime identity proof: all Agent paths consume the same ObservationPort."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from artemis.actions.service import ActionService
from artemis.agents.checker.checker import _capture_final_screen
from artemis.agents.planner.planner import PlannerNode
from artemis.context import DevicePlatform
from artemis.core.action_types import ActionResult, ActionCode, ObserveResult
from artemis.core.action_port import ActionDispatchResult
from artemis.core.actions import ActionCommand, ActionReceipt, ActionState
from artemis.graph.perception import perception_node
from artemis.graph.state import State


class RecordingPort:
    def __init__(self):
        self.ids: list[int] = []

    async def observe(self, settle_ms=400, ocr_results=None):
        self.ids.append(id(self))
        return (
            ObserveResult(
                ok=True,
                message="observed",
                elements=[],
                width=1080,
                height=2400,
            ),
            b"image",
        )

    async def screen_data(self):
        self.ids.append(id(self))
        from artemis.perception.observation import ScreenData

        return ScreenData(base64="", elements=[])


class DispatchPort:
    action_tool_names = frozenset({"click"})

    async def dispatch(self, command: ActionCommand):
        return ActionDispatchResult(
            result=ActionResult(ok=True, code=ActionCode.OK, action="click", message="clicked"),
            receipt=ActionReceipt(
                action_id=command.command_id,
                intent_id=command.intent_id,
                command_id=command.command_id,
                attempt=1,
                adapter_id="click",
                state=ActionState.ADAPTER_ACKNOWLEDGED,
                dispatched_at=1.0,
            ),
        )

    async def close(self):
        return None


async def _exercise_planner(ctx):
    state = State(initial_goal="identity check")
    state.latest_screenshot = None
    with patch(
        "artemis.agents.planner.planner.get_llm",
        side_effect=RuntimeError("stop after observation"),
    ):
        with pytest.raises(RuntimeError, match="stop after observation"):
            await PlannerNode(ctx)(state)


def test_planner_checker_graph_and_action_share_one_port_instance():
    port = RecordingPort()
    engine = MagicMock()
    engine.current_step_id = "step-identity"
    engine.base_dir = ""
    ctx = SimpleNamespace(
        observation_port=port,
        device=SimpleNamespace(
            mobile_platform=DevicePlatform.ANDROID,
            device_width=1080,
            device_height=2400,
        ),
        execution_setup=None,
        data_engine=engine,
        indexed_elements=[],
        device_session=None,
    )

    asyncio.run(_exercise_planner(ctx))
    asyncio.run(_capture_final_screen(ctx))

    graph_state = State(initial_goal="identity check")
    with patch("artemis.graph.perception.is_ocr_configured", return_value=False):
        asyncio.run(perception_node(graph_state, ctx))

    service = ActionService(ctx, DispatchPort())
    asyncio.run(
        service.execute(
            "click",
            {"target": [500, 500], "target_description": "button"},
            "tc-identity",
            SimpleNamespace(indexed_elements=[]),
        )
    )

    assert port.ids == [id(port)] * 4
    assert service.observation_port is port
