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

"""The structured verdict request must survive every provider's constraints.

Two independent provider rules bite here:

- The request must never end on a model turn (Gemini rejects that with 400
  "Requests ending with a model turn are not supported"), which is the natural
  state after the loop's tool-free reply.
- The structured-output *mode* must be one the endpoint implements. langchain-openai
  defaults to ``method="json_schema"``, which DeepSeek rejects with 400
  "This response_format type is unavailable now" (upstream issue #45 / PR #148).
  The 2026-10-08 run had already reasoned to a ``passed`` verdict with evidence
  and lost it to that 400, so the ledger recorded ``inconclusive``: the verdict
  existed, only its delivery failed.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from artemis.agents.checker.checker import CheckReport, CheckVerdict, _structured_report


def _llm(report: CheckReport):
    structured = MagicMock()
    structured.ainvoke = AsyncMock(return_value=report)
    llm = MagicMock()
    llm.with_structured_output.return_value = structured
    return llm, structured


@pytest.mark.asyncio
async def test_structured_report_appends_user_turn_after_model_reply():
    llm, structured = _llm(CheckReport(verdicts=[]))
    messages = [SystemMessage(content="s"), HumanMessage(content="h"), AIMessage(content="done")]

    await _structured_report(llm, messages)

    sent = structured.ainvoke.call_args.args[0]
    assert isinstance(sent[-1], HumanMessage)
    assert sent[-2] is messages[-1]
    assert len(messages) == 3  # caller's list untouched


@pytest.mark.asyncio
async def test_structured_report_keeps_user_ending_conversation_as_is():
    llm, structured = _llm(CheckReport(verdicts=[]))
    messages = [SystemMessage(content="s"), HumanMessage(content="final iteration")]

    await _structured_report(llm, messages)

    assert structured.ainvoke.call_args.args[0] is messages


@pytest.mark.asyncio
async def test_structured_report_requests_function_calling_with_auto_tool_choice():
    """The mode every OpenAI-compatible endpoint accepts; json_schema is not."""
    report = CheckReport(
        verdicts=[
            CheckVerdict(
                item_text="The Battery screen shows a numeric percentage",
                kind="verify",
                status="passed",
                evidence="Step 4 screenshot shows 93% and 充电中.",
            )
        ]
    )
    llm, _ = _llm(report)

    result = await _structured_report(llm, [SystemMessage(content="s")])

    assert result is report
    _, kwargs = llm.with_structured_output.call_args
    assert kwargs["method"] == "function_calling"
    assert kwargs["tool_choice"] == "auto"
    assert llm.with_structured_output.call_args.args[0] is CheckReport


@pytest.mark.asyncio
async def test_structured_report_names_the_tool_in_the_closing_nudge():
    """``tool_choice="auto"`` still needs the model told WHICH tool to call."""
    llm, structured = _llm(CheckReport(verdicts=[]))
    messages = [SystemMessage(content="s"), AIMessage(content="done")]

    await _structured_report(llm, messages)

    sent = structured.ainvoke.call_args.args[0]
    assert isinstance(sent[-1], HumanMessage)
    assert "CheckReport" in sent[-1].content


@pytest.mark.asyncio
async def test_structured_report_still_accepts_a_plain_dict_answer():
    """A provider returning the schema as a dict keeps working."""
    llm, _ = _llm({"verdicts": []})

    result = await _structured_report(llm, [SystemMessage(content="s")])

    assert isinstance(result, CheckReport)
