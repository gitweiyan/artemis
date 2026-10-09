# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""An agent turn must leave tool results adjacent to the tool calls that asked for them.

Providers whose image carrier is a ``HumanMessage`` (openai / vertexai / ...)
reject an assistant message whose ``tool_calls`` are not immediately followed by
their tool responses. The 2026-10-08 Checker final review died on exactly that
400 ("insufficient tool messages following tool_calls message"), was cut off by
the checkpoint timeout, and fail-opened into four ``inconclusive`` verdicts.

These tests drive real agent loops with the failing shape: one turn, two tool
calls, one returning a screenshot and one returning text. The same shape is
covered locally for the Outputter by
``test_outputter_puts_image_messages_after_all_tool_results``; this file is the
cross-agent contract.
"""

from unittest.mock import AsyncMock, MagicMock, Mock, patch

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import StructuredTool
import pytest

from artemis.agents.checker.checker import CheckReport, _run_check_loop
from artemis.agents.diagnoser.diagnoser import Diagnoser
from artemis.context import ArtemisContext, ExecutionSetup
from artemis.graph.state import State

SHOT_BLOCKS = [
    {"type": "text", "text": "Screenshot of step 3 (pre-action) is attached."},
    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}},
]
TEXT_RESULT = "No post-action screenshot recorded for step 2."


def assert_tool_calls_paired(messages: list[BaseMessage]) -> None:
    """Every assistant turn's tool_calls are answered by the messages right after it."""
    for index, message in enumerate(messages):
        calls = getattr(message, "tool_calls", None) or []
        if not calls:
            continue
        following = messages[index + 1 : index + 1 + len(calls)]
        assert all(isinstance(m, ToolMessage) for m in following), (
            f"tool results interleaved at index {index}: {[type(m).__name__ for m in following]}"
        )
        assert [m.tool_call_id for m in following] == [c["id"] for c in calls]


def _tool(name: str, result):
    async def _run(**kwargs):
        return result

    return StructuredTool.from_function(coroutine=_run, name=name, description=name)


def _two_call_turn() -> AIMessage:
    """One turn that asks for a screenshot and for text — the failing shape."""
    return AIMessage(
        content="",
        tool_calls=[
            {"name": "get_step_screenshot", "args": {"step_number": 3}, "id": "tc-A"},
            {"name": "replay_steps", "args": {"step_range": "2"}, "id": "tc-B"},
        ],
    )


def _openai_llm() -> MagicMock:
    """A model whose provider carries images in a HumanMessage."""
    llm = MagicMock()
    llm.bind_tools.return_value = llm
    llm.endpoint.provider = "openai"
    return llm


@pytest.mark.asyncio
async def test_checker_turn_keeps_tool_results_adjacent():
    """The Checker's audit turn is the shape that produced the DeepSeek 400."""
    ctx = MagicMock(spec=ArtemisContext)
    ctx.execution_setup = ExecutionSetup()
    ctx.data_engine = MagicMock()
    ctx.data_engine.base_dir = "unused"
    ctx.data_engine.get_agent_friendly_steps.return_value = []
    ctx.data_engine.get_step_number.return_value = 7

    llm = _openai_llm()
    llm.with_structured_output.return_value.ainvoke = AsyncMock(
        return_value=CheckReport(verdicts=[])
    )
    messages = [SystemMessage(content="s"), HumanMessage(content="h")]
    tools = [_tool("get_step_screenshot", SHOT_BLOCKS), _tool("replay_steps", TEXT_RESULT)]

    with (
        patch("artemis.agents.checker.checker.get_llm", return_value=llm),
        patch(
            "artemis.agents.checker.checker.acomplete",
            new=AsyncMock(side_effect=[_two_call_turn(), AIMessage(content="done", tool_calls=[])]),
        ),
    ):
        await _run_check_loop(ctx, messages, tools, check_items=[])

    assert_tool_calls_paired(messages)


@pytest.mark.asyncio
async def test_diagnoser_batch_orders_tool_messages_before_carriers():
    """The Diagnoser aggregates parallel tool results; carriers must come last."""
    ctx = Mock(spec=ArtemisContext)
    ctx.data_engine = Mock()
    ctx.data_engine.base_dir = "/tmp"
    diagnoser = Diagnoser(ctx)
    diagnoser._llm = _openai_llm()

    async def _fake_run_tool(self, tc, traced_tools, state):  # pylint: disable=unused-argument
        if tc["name"] == "get_step_screenshot":
            return [
                ToolMessage(tool_call_id=tc["id"], content="Screenshot of step 3 is attached."),
                HumanMessage(content=[{"type": "text", "text": "[Screenshot]"}, SHOT_BLOCKS[1]]),
            ]
        return [ToolMessage(tool_call_id=tc["id"], content=TEXT_RESULT)]

    with patch.object(Diagnoser, "_run_tool", new=_fake_run_tool):
        messages = await diagnoser._execute_tool_calls(
            _two_call_turn().tool_calls, [], Mock(spec=State)
        )

    assert [type(m).__name__ for m in messages] == [
        "ToolMessage",
        "ToolMessage",
        "HumanMessage",
    ]
    assert_tool_calls_paired([_two_call_turn(), *messages])
