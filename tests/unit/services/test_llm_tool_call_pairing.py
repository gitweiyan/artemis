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

"""The LLM boundary repairs tool-call/result ordering before a request goes out.

Containment for the 2026-10-08 Checker failure: a message shape produced by a
path nobody audited reached a strict provider as an opaque 400, which the
checkpoint timeout then turned into four ``inconclusive`` verdicts. The producer
is fixed at its own call sites (see ``tests/unit/agents/test_tool_result_ordering.py``);
this guard makes the boundary safe regardless of who produced the shape.
"""

from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
import pytest

from artemis.services.llm import acomplete, repair_tool_call_pairing

IMAGE = {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}}


def _turn() -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {"id": "tc-A", "name": "get_step_screenshot", "args": {}},
            {"id": "tc-B", "name": "replay_steps", "args": {}},
        ],
    )


def _broken() -> list:
    """The exact shape that produced the DeepSeek 400 on 2026-10-08."""
    return [
        SystemMessage(content="s"),
        HumanMessage(content="h"),
        _turn(),
        ToolMessage(tool_call_id="tc-A", content="Screenshot of step 3 attached."),
        HumanMessage(content=[{"type": "text", "text": "[Screenshot]"}, IMAGE]),
        ToolMessage(tool_call_id="tc-B", content="No post-action screenshot for step 2."),
    ]


def _paired(messages: list) -> None:
    for index, message in enumerate(messages):
        calls = getattr(message, "tool_calls", None) or []
        if not calls:
            continue
        following = messages[index + 1 : index + 1 + len(calls)]
        assert all(isinstance(m, ToolMessage) for m in following), (
            f"still interleaved at {index}: {[type(m).__name__ for m in following]}"
        )
        assert [m.tool_call_id for m in following] == [c["id"] for c in calls]


def test_repair_moves_the_carrier_after_every_tool_message():
    repaired = repair_tool_call_pairing(_broken())

    _paired(repaired)
    assert [type(m).__name__ for m in repaired[3:]] == [
        "ToolMessage",
        "ToolMessage",
        "HumanMessage",
    ]
    # The image itself is untouched, and no message was dropped.
    assert repaired[-1].content[-1] == IMAGE
    assert len(repaired) == len(_broken())


def test_repair_returns_the_same_list_when_nothing_is_wrong():
    """A correct transcript must pass through untouched — identity, not equality."""
    correct = [
        SystemMessage(content="s"),
        HumanMessage(content="h"),
        _turn(),
        ToolMessage(tool_call_id="tc-A", content="shot"),
        ToolMessage(tool_call_id="tc-B", content="text"),
        HumanMessage(content=[{"type": "text", "text": "[Screenshot]"}, IMAGE]),
    ]

    assert repair_tool_call_pairing(correct) is correct


def test_repair_is_idempotent():
    once = repair_tool_call_pairing(_broken())

    assert repair_tool_call_pairing(once) is once


def test_repair_labels_a_tool_call_that_never_produced_a_result():
    """An unanswered call is the other half of the same provider error."""
    messages = [
        SystemMessage(content="s"),
        _turn(),
        ToolMessage(tool_call_id="tc-A", content="shot"),
    ]

    repaired = repair_tool_call_pairing(messages)

    _paired(repaired)
    placeholder = repaired[-1]
    assert isinstance(placeholder, ToolMessage)
    assert placeholder.tool_call_id == "tc-B"
    assert placeholder.status == "error"
    assert "no tool result" in placeholder.content


def test_repair_stops_at_the_next_assistant_turn():
    """A later turn is never swallowed into an earlier turn's tool block."""
    messages = [
        SystemMessage(content="s"),
        _turn(),
        ToolMessage(tool_call_id="tc-A", content="shot"),
        HumanMessage(content=[{"type": "text", "text": "[Screenshot]"}, IMAGE]),
        ToolMessage(tool_call_id="tc-B", content="text"),
        AIMessage(
            content="",
            tool_calls=[{"id": "tc-C", "name": "read_note", "args": {}}],
        ),
        ToolMessage(tool_call_id="tc-C", content="note"),
    ]

    repaired = repair_tool_call_pairing(messages)

    _paired(repaired)
    assert repaired[-1].tool_call_id == "tc-C"


class _RecordingModel:
    """Minimal raw model double: records the messages it was streamed."""

    def __init__(self):
        self.seen = None

    async def astream(self, messages, **kwargs):  # noqa: ARG002
        self.seen = messages
        yield AIMessage(content="ok")


@pytest.mark.asyncio
async def test_acomplete_repairs_the_request_and_the_callers_transcript(caplog):
    """Wiring: the guard runs at the gateway and converges the caller's list."""
    model = _RecordingModel()
    messages = _broken()

    with caplog.at_level("WARNING"):
        await acomplete(model, messages)

    _paired(model.seen)
    _paired(messages)  # the caller's transcript stays repaired for later turns
    assert any("Repaired interleaved tool results" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_acomplete_leaves_a_correct_transcript_alone():
    model = _RecordingModel()
    correct = [
        SystemMessage(content="s"),
        _turn(),
        ToolMessage(tool_call_id="tc-A", content="shot"),
        ToolMessage(tool_call_id="tc-B", content="text"),
    ]
    before = list(correct)

    await acomplete(model, correct)

    assert model.seen is correct
    assert correct == before


def test_repair_ignores_a_conversation_without_tool_calls():
    messages = [SystemMessage(content="s"), HumanMessage(content="h")]

    assert repair_tool_call_pairing(messages) is messages


def test_repair_accepts_a_non_list_sequence_without_mutating_it():
    """Defensive: tuple input cannot be slice-assigned, so it must be replaced."""
    messages = tuple(_broken())

    repaired = repair_tool_call_pairing(messages)

    _paired(list(repaired))


@pytest.mark.asyncio
async def test_robust_wrapper_complete_repairs_before_calling_the_provider():
    """The gateway-managed path (every agent) is guarded too, not only raw models."""
    from artemis.services.llm import RobustChatModelWrapper

    base = _RecordingModel()
    wrapper = RobustChatModelWrapper(base, ctx=None, endpoint=MagicMock())
    wrapper.endpoint.provider = "openai"
    wrapper.endpoint.model_name = "test-model"

    messages = _broken()

    await wrapper.complete(messages)

    _paired(base.seen)
    _paired(messages)
