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

"""A provider 400 must print the request's message shape.

"An assistant message with 'tool_calls' must be followed by tool messages
responding to each 'tool_call_id' (insufficient tool messages following
tool_calls message)" names no message and no id, so the rejected request had to
be reconstructed from the DataEngine payload by hand. Logging the role/tool
shape at the moment of rejection makes the next occurrence readable straight
from ``stdout.log`` — the same gap upstream issue #156 reported on Vertex,
where the reporter could not confirm the request shape at all.
"""

from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
import pytest

from artemis.services.llm import RobustChatModelWrapper, summarize_message_shape

IMAGE = {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}}


def _two_call_turn() -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {"id": "tc-A", "name": "get_step_screenshot", "args": {}},
            {"id": "tc-B", "name": "replay_steps", "args": {}},
        ],
    )


def test_shape_summary_names_the_roles_and_the_tool_ids():
    messages = [
        SystemMessage(content="s"),
        HumanMessage(content="h"),
        _two_call_turn(),
        ToolMessage(tool_call_id="tc-A", content="Screenshot of step 3 attached."),
        HumanMessage(content=[{"type": "text", "text": "[Screenshot]"}, IMAGE]),
        ToolMessage(tool_call_id="tc-B", content="No post-action screenshot for step 2."),
    ]

    shape = summarize_message_shape(messages)

    # Exactly the request as it was sent, left to right: the carrier sits between
    # the two tool results, which is the whole point of printing this.
    assert shape.split(" -> ") == [
        "system",
        "human",
        "assistant[calls=tc-A,tc-B]",
        "tool[tc-A]",
        "human",
        "tool[tc-B]",
    ]


def test_shape_summary_handles_an_empty_conversation():
    assert summarize_message_shape([]) == ""


class _BadRequestModel:
    """Raises a 400 the way a provider SDK does: ``.code`` carries the status."""

    def __init__(self):
        self.seen = None

    async def astream(self, messages, **kwargs):  # noqa: ARG002
        self.seen = messages
        error = RuntimeError(
            "Error code: 400 - insufficient tool messages following tool_calls message"
        )
        error.code = 400
        raise error
        yield  # pragma: no cover


@pytest.mark.asyncio
async def test_a_rejected_request_logs_its_shape(caplog):
    base = _BadRequestModel()
    wrapper = RobustChatModelWrapper(base, ctx=None, endpoint=MagicMock())
    wrapper.endpoint.provider = "openai"
    wrapper.endpoint.model_name = "test-model"
    messages = [
        SystemMessage(content="s"),
        _two_call_turn(),
        ToolMessage(tool_call_id="tc-A", content="shot"),
        HumanMessage(content=[{"type": "text", "text": "[Screenshot]"}, IMAGE]),
        ToolMessage(tool_call_id="tc-B", content="text"),
    ]

    # Driven at the attempt layer: the recovery policy around it retries and
    # ultimately pauses, which is not what this test is about.
    with caplog.at_level("WARNING"):
        with pytest.raises(RuntimeError):
            await wrapper._complete_attempt(False, messages)

    shapes = [r.message for r in caplog.records if "assistant[calls=" in r.message]
    assert shapes, f"no request shape logged; records={[r.message for r in caplog.records]}"
    assert "tool[tc-A]" in shapes[0] and "tool[tc-B]" in shapes[0]


@pytest.mark.asyncio
async def test_a_transient_failure_does_not_log_a_shape(caplog):
    """Shape logging is for rejected requests, not for every hiccup."""

    class _UnavailableModel:
        async def astream(self, messages, **kwargs):  # noqa: ARG002
            error = RuntimeError("503 service unavailable")
            error.code = 503
            raise error
            yield  # pragma: no cover

    wrapper = RobustChatModelWrapper(_UnavailableModel(), ctx=None, endpoint=MagicMock())
    wrapper.endpoint.provider = "openai"
    wrapper.endpoint.model_name = "test-model"

    with caplog.at_level("WARNING"):
        with pytest.raises(RuntimeError):
            await wrapper._complete_attempt(False, [SystemMessage(content="s"), _two_call_turn()])

    assert not [r for r in caplog.records if "assistant[calls=" in r.message]
