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

"""How the plan validator asks for its verdict.

The verdict is a structured object, and langchain-openai's default way of asking
for one is ``response_format: {"type": "json_schema"}``. OpenAI-compatible
endpoints reject that — DeepSeek answers 400 "This response_format type is
unavailable now" — and ``run_async_planner_validation`` swallows every exception
as an approval, so that 400 silently disables plan validation for the whole run.
"""

from unittest.mock import patch

import pytest

from artemis.agents.planner import planner as planner_module
from artemis.agents.planner.planner import ValidationResult, run_async_planner_validation


class DummyDataEngine:
    def get_agent_friendly_steps(self):
        return []


class DummyCtx:
    """Minimal context: the request shape is the subject, not the prompt inputs."""

    data_engine = DummyDataEngine()
    execution_setup = None


def _model_binding_kwargs(runnable):
    """The kwargs langchain-openai attaches to the underlying model request."""
    for step in getattr(runnable, "steps", []):
        kwargs = getattr(step, "kwargs", None)
        if kwargs:
            return kwargs
    raise AssertionError(f"no model binding found in {runnable!r}")


@pytest.mark.asyncio
async def test_validation_verdict_is_requested_with_tool_calling():
    from langchain_openai import ChatOpenAI

    real_llm = ChatOpenAI(
        model="deepseek-flash", api_key="dummy", base_url="https://api.deepseek.com"
    )
    captured = {}

    class RecordingLLM:
        """Builds the real structured-output binding before the transport."""

        def with_structured_output(self, schema, **kwargs):
            runnable = real_llm.with_structured_output(schema, **kwargs)
            captured["runnable"] = runnable
            return runnable

    async def fake_invoke(coro):
        coro.close()  # never touch the network; the request shape is the subject
        return ValidationResult(is_approved=True, feedback="Valid plan modification")

    with (
        patch.object(planner_module, "get_llm", return_value=RecordingLLM()),
        patch.object(planner_module, "invoke_llm_with_timeout_message", fake_invoke),
    ):
        res = await run_async_planner_validation(
            ctx=DummyCtx(),
            initial_goal="Open Settings and check the Battery level",
            content_before="- [/] Open Settings",
            content_after="- [/] Open Settings\n- [ ] Swipe down the notification shade",
        )

    assert res.get("status") == "success"
    binding = _model_binding_kwargs(captured["runnable"])
    assert "response_format" not in binding, (
        "verdict requested via response_format, which OpenAI-compatible endpoints"
        f" reject with 400: {sorted(binding)}"
    )
    assert binding["tool_choice"] == "auto"
    assert [tool["function"]["name"] for tool in binding["tools"]] == ["ValidationResult"]
