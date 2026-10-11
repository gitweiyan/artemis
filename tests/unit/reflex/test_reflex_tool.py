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

"""``play_reflex`` as the agent sees it: one tool, one step, one structured report.

The tool is the framework's entire side of the reflex deal — it must stay generic
(no game knowledge), must be declared to both profiles, and must answer with
numbers the agent can act on even when the skill or the device misbehaves.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from artemis.mcp.action_executor import AGENT_TOOL_NAMES, McpActionExecutor
from artemis.reflex.contract import Frame
from artemis.tools import reflex_tool as reflex_tool_module
from artemis.tools.reflex_tool import (
    LIST_SKILLS_TOOL_NAME,
    PLAY_REFLEX_TOOL_NAME,
    list_skills,
    play_reflex,
    reflex_tool_declarations,
)


class FakeCapture:
    def __init__(self, *args, **kwargs):
        self.grabbed = 0

    def grab(self) -> Frame:
        self.grabbed += 1
        return Frame(image=b"\xff\xd8\xff" + b"x" * 8, captured_at=0.0, capture_ms=9.0)

    def close(self) -> None:
        return None


class FakeControl:
    def __init__(self, *args, **kwargs):
        self.calls: list = []

    def dispatch(self, gesture) -> float:
        self.calls.append(gesture)
        return 4.0


@pytest.fixture
def wired(monkeypatch):
    monkeypatch.setattr(reflex_tool_module, "default_capture", FakeCapture)
    monkeypatch.setattr(reflex_tool_module, "HelperControl", FakeControl)
    monkeypatch.setattr(reflex_tool_module, "AdbInputControl", FakeControl)


def _ctx():
    return SimpleNamespace(device=SimpleNamespace(device_id="SERIAL"), data_engine=None)


def test_the_tool_is_available_when_a_skill_loads():
    assert play_reflex.is_available() is True
    assert play_reflex.name == PLAY_REFLEX_TOOL_NAME
    assert list_skills.is_available() is True


def test_both_profiles_are_offered_the_reflex_tools():
    names = [declaration.name for declaration in reflex_tool_declarations()]

    assert names == [PLAY_REFLEX_TOOL_NAME, LIST_SKILLS_TOOL_NAME]
    # The Pro operator binds the wrappers; the Flash runner dispatches by name.
    assert LIST_SKILLS_TOOL_NAME in AGENT_TOOL_NAMES


def test_a_session_reports_frames_latency_and_the_stop_reason(wired):
    result = asyncio.run(play_reflex.execute(driver=None, ctx=_ctx(), skill="noop", seconds=0.3))

    assert "reflex[noop]" in result
    assert "time_budget" in result
    assert "fps" in result
    assert "capture p50" in result


def test_an_unknown_skill_is_an_answer_with_the_available_list(wired):
    result = asyncio.run(play_reflex.execute(driver=None, ctx=_ctx(), skill="nope", seconds=1))

    assert result.startswith("Error:")
    assert "nope" in result
    assert "noop" in result, "the error must say what exists"


def test_a_missing_skill_name_lists_what_can_be_run(wired):
    result = asyncio.run(play_reflex.execute(driver=None, ctx=_ctx(), skill="", seconds=1))

    assert "Available:" in result and "noop" in result


def test_without_a_device_the_tool_explains_itself(wired):
    result = asyncio.run(
        play_reflex.execute(driver=None, ctx=SimpleNamespace(device=None), skill="noop", seconds=1)
    )

    assert result.startswith("Error:")
    assert "device serial" in result


def test_a_reflex_skill_that_cannot_be_reached_is_reported_not_raised(monkeypatch, wired):
    def explode(*args, **kwargs):
        raise OSError("capture path is gone")

    monkeypatch.setattr(reflex_tool_module, "default_capture", explode)

    result = asyncio.run(play_reflex.execute(driver=None, ctx=_ctx(), skill="noop", seconds=1))

    assert isinstance(result, str), "a broken capture path must not raise into the agent"


def test_list_skills_answers_with_what_is_installed():
    result = asyncio.run(list_skills.execute(driver=None, ctx=None, verbose=True))

    assert "noop" in result
    assert "Reflex skills in" in result


def test_the_executor_dispatches_the_reflex_tools_agent_side():
    """``play_reflex`` must not be proxied through the per-action validator."""

    async def run():
        executor = object.__new__(McpActionExecutor)
        executor.ctx = None
        executor.agent_name = "operator"
        return await executor._reflex_tool("list_skills", {})

    text = asyncio.run(run())

    assert "noop" in text
