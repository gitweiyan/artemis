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

"""Unit tests for Universal FlashRunner."""

import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
import pytest
from langchain_core.messages import HumanMessage, ToolMessage

from artemis.agents.explorer.constants import ASK_EXPLORER_DESCRIPTION
from artemis.agents.flash.runner import FlashRunner
from artemis.agents.validator.tool_declarations import (
    ASK_EXPLORER_TOOL,
    ToolExecutionResult,
)
from artemis.context import ArtemisContext
from artemis.actions.results import ToolExecutionResult as ActionToolResult
from artemis.actions.service import ActionService
from artemis.perception.observation import ControllerObservationPort


class _ActionPort:
    action_tool_names = frozenset(
        {
            "click",
            "click_sequence",
            "long_press",
            "input_text",
            "focus_and_input_text",
            "swipe",
            "press_key",
            "manage_app",
            "open_link",
            "wait_for_delay",
            "wait_for_text",
            "erase_one_char",
            "clear_text",
            "focus_and_clear_text",
        }
    )

    async def execute(self, name, args, tool_call_id, state, *, index_elements=None):
        return ActionToolResult(
            tool_call_id=tool_call_id,
            tool_name=name,
            status="success",
            text_summary=f"{name} executed",
        )

    async def close(self):
        return None


@pytest.fixture
def mock_context():
    ctx = Mock(spec=ArtemisContext)
    ctx.llm_config = Mock()
    mock_llm_cfg = Mock()
    mock_llm_cfg.model = "gemini-2.5-flash"
    mock_llm_cfg.temperature = 0.1
    ctx.llm_config.get_agent.return_value = mock_llm_cfg

    ctx.device = Mock()
    ctx.device.device_width = 1080
    ctx.device.device_height = 2400
    ctx.data_engine = None
    ctx.adb_client = None
    ctx.driver = Mock()
    ctx.action_service = ActionService(None, _ActionPort(), helper_port=_ActionPort())
    ctx.observation_port = ControllerObservationPort(
        ctx, SimpleNamespace(get_screen_data=AsyncMock(return_value=_screen([])))
    )
    return ctx


def test_flash_runner_tools_initialization(mock_context):
    """Verify FlashRunner gathers correct active tools."""
    with patch("artemis.controllers.unified_controller.get_driver"):
        runner = FlashRunner(mock_context, goal="Test Open Settings")
        tools = runner._get_tools()

        tool_names = [t.name for t in tools]
        assert "click" in tool_names
        assert "click_sequence" in tool_names
        assert "swipe" in tool_names
        assert "input_text" in tool_names
        assert "ask_explorer" in tool_names
        assert "report_task_status" in tool_names
        # Validator's report_failure_analysis should NOT be in FlashRunner
        assert "report_failure_analysis" not in tool_names
        # Flash has no note-writing tool and its prompt never teaches notes:
        # the Validator set's note readers are not bound.
        assert "read_note" not in tool_names
        assert "list_notes" not in tool_names
        assert tool_names[:2] == ["click", "click_sequence"]


# --- Post-action screenshot fallback (failed action / unknown tool) ----------------


def _screen(elements):
    return SimpleNamespace(
        base64=base64.b64encode(b"POST-IMG").decode("ascii"),
        elements=elements,
        width=1080,
        height=2400,
    )


def _failed_click(tc_id="tc1"):
    return ToolExecutionResult(
        tool_call_id=tc_id,
        tool_name="click",
        status="error",
        text_summary="Error during click: Invalid target format: 3",
    )


@pytest.mark.asyncio
async def test_fallback_observation_renders_the_indexed_ui_list(mock_context, tmp_path):
    """After an action that produced no observation, the fallback capture must
    hand the next tail the same indexed minimal list the success path builds
    (never the raw element dict repr) and refresh the state like it."""
    with patch("artemis.controllers.unified_controller.get_driver"):
        runner = FlashRunner(mock_context, goal="g")
    controller = SimpleNamespace(
        get_screen_data=AsyncMock(
            return_value=_screen(
                [
                    {
                        "text": "Settings",
                        "content-desc": "",
                        "bounds": "[40,120][1040,270]",
                        "class": "android.widget.TextView",
                        "resource-id": "android:id/title",
                    }
                ]
            )
        )
    )
    runner.observation_port = ControllerObservationPort(runner.ctx, controller)
    state = Mock()
    state.latest_screenshot = "old.jpg"
    exec_result = _failed_click()

    with patch("artemis.perception.observation.get_temp_dir", return_value=tmp_path):
        post = await runner._capture_post_screenshot(
            exec_result, "click", frozenset({"click"}), state
        )

    assert post == b"POST-IMG"
    assert isinstance(exec_result.ui_elements_text, str)
    assert exec_result.ui_elements_text == "[1] Text: 'Settings' | Bounds: [37,50][962,112]"
    assert "{'text'" not in exec_result.ui_elements_text
    # State refreshed exactly as the executor's success path does.
    assert state.indexed_elements[0]["index"] == 1
    assert state.indexed_elements[0]["text"] == "Settings"
    assert state.indexed_points == [state.indexed_elements[0]["center"]]
    assert state.latest_screenshot != "old.jpg"
    assert state.latest_screenshot.startswith(str(tmp_path))
    assert exec_result.screenshot_path == state.latest_screenshot


@pytest.mark.asyncio
async def test_fallback_observation_is_skipped_for_non_action_tools(mock_context):
    with patch("artemis.controllers.unified_controller.get_driver"):
        runner = FlashRunner(mock_context, goal="g")
    controller = SimpleNamespace(get_screen_data=AsyncMock(return_value=_screen([])))
    runner.observation_port = ControllerObservationPort(runner.ctx, controller)
    state = Mock()
    exec_result = ToolExecutionResult(
        tool_call_id="tc", tool_name="ask_explorer", status="success", text_summary="found"
    )

    post = await runner._capture_post_screenshot(
        exec_result, "ask_explorer", frozenset({"click"}), state
    )

    assert post is None
    assert exec_result.ui_elements_text is None
    controller.get_screen_data.assert_not_awaited()


@pytest.mark.asyncio
async def test_fallback_observation_keeps_the_executors_ui_list(mock_context, tmp_path):
    """An action that reported a UI list but no screenshot keeps its own list."""
    with patch("artemis.controllers.unified_controller.get_driver"):
        runner = FlashRunner(mock_context, goal="g")
    controller = SimpleNamespace(
        get_screen_data=AsyncMock(
            return_value=_screen([{"text": "Other", "bounds": "[0,0][100,100]"}])
        )
    )
    runner.observation_port = ControllerObservationPort(runner.ctx, controller)
    exec_result = ToolExecutionResult(
        tool_call_id="tc",
        tool_name="click",
        status="success",
        text_summary="click executed",
        ui_elements_text="[1] Text: 'Mine' | Bounds: [0,0][10,10]",
    )

    with patch("artemis.perception.observation.get_temp_dir", return_value=tmp_path):
        post = await runner._capture_post_screenshot(
            exec_result, "click", frozenset({"click"}), None
        )

    assert post == b"POST-IMG"
    assert exec_result.ui_elements_text == "[1] Text: 'Mine' | Bounds: [0,0][10,10]"


@pytest.mark.asyncio
async def test_fallback_observation_failure_is_contained(mock_context):
    with patch("artemis.controllers.unified_controller.get_driver"):
        runner = FlashRunner(mock_context, goal="g")
    controller = SimpleNamespace(get_screen_data=AsyncMock(side_effect=RuntimeError("adb gone")))
    runner.observation_port = ControllerObservationPort(runner.ctx, controller)
    state = Mock()
    state.latest_screenshot = "old.jpg"
    exec_result = _failed_click()

    post = await runner._capture_post_screenshot(exec_result, "click", frozenset({"click"}), state)

    assert post is None
    assert exec_result.ui_elements_text is None
    assert state.latest_screenshot == "old.jpg"


def test_flash_runner_uses_the_injected_action_service(mock_context):
    """The agent receives the L3 service; it never constructs an MCP executor."""
    with patch("artemis.controllers.unified_controller.get_driver"):
        runner = FlashRunner(mock_context, goal="Test Open Settings")

    assert isinstance(runner.executor, ActionService)


def test_ask_explorer_declaration_is_tier_agnostic(mock_context):
    """Flash binds the shared ask_explorer contract: query + context_feedback, no tier."""
    with patch("artemis.controllers.unified_controller.get_driver"):
        runner = FlashRunner(mock_context, goal="Test Open Settings")
        declaration = next(t for t in runner._get_tools() if t.name == "ask_explorer")

    assert declaration is ASK_EXPLORER_TOOL
    assert declaration.description == ASK_EXPLORER_DESCRIPTION
    assert set(declaration.parameters["properties"]) == {"query", "context_feedback"}
    assert declaration.parameters["required"] == ["query"]


def test_flash_runner_screenshot_pruning(mock_context):
    """Verify that earlier screenshot blocks are pruned, keeping only the latest."""
    with patch("artemis.controllers.unified_controller.get_driver"):
        runner = FlashRunner(mock_context, goal="Test Pruning")

        messages = [
            HumanMessage(
                content=[
                    {"type": "text", "text": "Turn 1 goal"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/jpeg;base64,SCREENSHOT_1"},
                    },
                ]
            ),
            ToolMessage(
                tool_call_id="tc1",
                name="click",
                content=[
                    {"type": "text", "text": "Clicked button"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/jpeg;base64,SCREENSHOT_2"},
                    },
                ],
            ),
            ToolMessage(
                tool_call_id="tc2",
                name="swipe",
                content=[
                    {"type": "text", "text": "Swiped up"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/jpeg;base64,SCREENSHOT_3"},
                    },
                ],
            ),
        ]

        runner._prune_intermediate_screenshots(messages)

        # Check Turn 1: image should be removed
        assert len(messages[0].content) == 1
        assert messages[0].content[0]["type"] == "text"

        # Check Turn 2 (Tool 1): image should be removed
        assert len(messages[1].content) == 1
        assert messages[1].content[0]["type"] == "text"

        # Check Turn 3 (Tool 2 - Latest): image MUST be preserved
        assert len(messages[2].content) == 2
        assert messages[2].content[0]["type"] == "text"
        assert messages[2].content[1]["type"] == "image_url"
        assert messages[2].content[1]["image_url"]["url"] == "data:image/jpeg;base64,SCREENSHOT_3"


# --- Action receipt: ACK is the ceiling without observed effect -----------------


def test_flash_action_evidence_never_promotes_an_ack_to_verified():
    from types import SimpleNamespace

    from artemis.agents.flash.runner import FlashRunner

    success = SimpleNamespace(status="success")
    assert FlashRunner._evidence_action_state(success, {}) == "unknown"
    assert (
        FlashRunner._evidence_action_state(success, {"post_verification": {"ok": True}})
        == "observed_effect"
    )
    assert (
        FlashRunner._evidence_action_state(success, {"effect_observed": True}) == "observed_effect"
    )
    assert (
        FlashRunner._evidence_action_state(
            success,
            {
                "post_verification": {"ok": False, "incident": "no_effect"},
                "action_state": "adapter_acknowledged",
                "action_receipt": {"state": "adapter_acknowledged"},
            },
        )
        == "adapter_acknowledged"
    )
    assert FlashRunner._evidence_action_state(SimpleNamespace(status="error"), {}) == "unknown"
    assert (
        FlashRunner._evidence_action_state(success, {"safety_precondition": {"allowed": False}})
        == "rejected"
    )


def test_pointer_precheck_receipt_requires_evidence_not_just_a_sink():
    from types import SimpleNamespace

    from artemis.agents.flash.runner import FlashRunner

    precondition = SimpleNamespace(pointer_precheck="delegated")
    success = SimpleNamespace(status="success")

    verified = FlashRunner._pointer_precheck_receipt(
        precondition, success, {"action_metrics": {"error_px": 0.0}}
    )
    assert verified["state"] == "verified"
    assert verified["error_px"] == 0.0

    failed = FlashRunner._pointer_precheck_receipt(
        precondition, SimpleNamespace(status="error"), {"action_metrics": {}}
    )
    assert failed["state"] == "failed"

    unknown = FlashRunner._pointer_precheck_receipt(precondition, success, {})
    assert unknown["state"] == "delegated"  # ACK without alignment metrics is not verified
    assert (
        FlashRunner._pointer_precheck_receipt(
            SimpleNamespace(pointer_precheck="not_required"), success, {}
        )
        is None
    )


# --- Flash burst budget is a dispatch gate, not a metadata annotation ------------


@pytest.mark.asyncio
async def test_a_non_allowlisted_device_action_is_denied_before_dispatch():
    from types import SimpleNamespace

    runner = object.__new__(FlashRunner)
    runner.executor = SimpleNamespace(action_tool_names=frozenset({"future_tap"}))
    runner.executor.execute = AsyncMock()

    state = SimpleNamespace(indexed_elements=[])
    turn = SimpleNamespace(step_keys=[], actions=[])
    messages: list = []

    await runner._process_tool_calls(
        [{"name": "future_tap", "args": {"target": [500, 500]}, "id": "tc-denied"}],
        state,
        messages,
        "thought",
        {},
        b"pre",
        ["xml"],
        0,
        turn,
    )

    runner.executor.execute.assert_not_awaited()
    assert turn.actions == [
        (
            "future_tap",
            "error",
            "Flash burst policy rejected action 'future_tap' (not allowlisted); action was not dispatched.",
        )
    ]
    assert messages[0].status == "error"
    assert "not dispatched" in messages[0].content


@pytest.mark.asyncio
async def test_the_ninth_device_action_is_denied_even_when_it_is_allowlisted():
    from types import SimpleNamespace

    runner = object.__new__(FlashRunner)
    runner.executor = SimpleNamespace(action_tool_names=frozenset({"manage_app"}))
    runner._execute_and_record_action = AsyncMock(return_value=(b"pre", ["xml"], 0))

    state = SimpleNamespace(indexed_elements=[])
    turn = SimpleNamespace(step_keys=[], actions=[])
    messages: list = []
    calls = [
        {"name": "manage_app", "args": {"action": "launch", "app_name": "a"}, "id": f"tc-{i}"}
        for i in range(9)
    ]

    await runner._process_tool_calls(
        calls,
        state,
        messages,
        "thought",
        {},
        b"pre",
        ["xml"],
        0,
        turn,
    )

    assert runner._execute_and_record_action.await_count == 8
    assert turn.actions[-1][0] == "manage_app"
    assert turn.actions[-1][1] == "error"
    assert "budget exceeded" in turn.actions[-1][2]
    assert messages[-1].status == "error"


# --- Batch item 5: the no-op guard is wired into the action loop -------------------


@pytest.mark.asyncio
async def test_repeated_no_ops_stamp_the_step_and_queue_strategy_guidance(mock_context):
    """Four silent BACKs: the guard must see them even with success status."""
    from types import SimpleNamespace

    with patch("artemis.controllers.unified_controller.get_driver"):
        runner = FlashRunner(mock_context, goal="g")
    runner.summarizer = None
    runner._llm = Mock()
    runner.ctx.data_engine = Mock()
    runner.ctx.data_engine.current_step_id = None
    runner.ctx.data_engine.record_step.return_value = "step-1"
    runner.ctx.transcript_ledger = None

    exec_result = SimpleNamespace(
        status="success",
        text_summary="Pressed key 'BACK'.",
        metadata={"incident": "no_effect", "action_metrics": {"crop_change": 0.0}},
        ui_elements_text=None,
        raw_result=None,
    )
    runner.executor = Mock()
    runner.executor.action_tool_names = frozenset({"press_key"})
    runner.executor.execute = AsyncMock(return_value=exec_result)
    runner._capture_post_screenshot = AsyncMock(return_value=b"post")

    async def press_back():
        await runner._execute_and_record_action(
            "press_key",
            {"key": "BACK"},
            "tc-1",
            Mock(),
            messages,
            "thought",
            {},
            b"pre",
            ["xml"],
            0,
            turn,
        )

    messages: list = []
    turn = SimpleNamespace(step_keys=[], actions=[])
    with patch("artemis.agents.flash.runner.tool_result_messages", return_value=[]):
        await press_back()
        assert runner.no_ops.consecutive == 1
        assert not runner._no_op_notice, "one stall is not yet worth a nudge"

        await press_back()
        assert runner._no_op_notice and "switch" in runner._no_op_notice.lower()
        assert not runner._no_op_abort

        await press_back()

    assert runner.no_ops.consecutive == 3
    # The abort must not clobber the strategy guidance already queued for the tail.
    assert runner._no_op_notice and "switch" in runner._no_op_notice.lower()
    assert runner._no_op_abort and "Stopping" in runner._no_op_abort
    recorded = runner.ctx.data_engine.record_step.call_args.kwargs["action_taken"]
    assert recorded["no_op_consecutive"] == 3, "the counter must ship as step evidence"


# --- Recorded action shape (coordinate space + ledger rendering) ---------------------


def _record(runner, name, args, metadata=None):
    from types import SimpleNamespace

    runner.ctx.data_engine = Mock()
    runner.ctx.data_engine.current_step_id = None
    runner.ctx.data_engine.record_step.return_value = "step-1"
    exec_result = SimpleNamespace(
        status="success",
        text_summary=f"{name} executed",
        metadata=metadata or {},
        ui_elements_text="ui",
    )
    step_id = runner._record_action_step(
        name, args, exec_result, "thought", {}, b"pre", ["xml"], b"post"
    )
    assert step_id == "step-1"
    return runner.ctx.data_engine.record_step.call_args.kwargs["action_taken"]


def test_record_action_step_stamps_the_normalized_coordinate_space(mock_context):
    """Flash stores the model's own 0–1000 target verbatim, marked as
    normalized, so the agent-friendly view never re-normalizes it."""
    from artemis.utils.coordinates import (
        COORDINATE_SPACE_KEY,
        COORDINATE_SPACE_NORMALIZED,
        normalize_any_structure,
    )

    with patch("artemis.controllers.unified_controller.get_driver"):
        runner = FlashRunner(mock_context, goal="g")
    recorded = _record(runner, "click", {"target": [320, 399]})
    assert recorded["coordinates"] == [320, 399]
    assert recorded["normalized_coordinates"] == [320, 399]
    assert recorded[COORDINATE_SPACE_KEY] == COORDINATE_SPACE_NORMALIZED
    assert normalize_any_structure(recorded, 1080, 2400)["coordinates"] == [320, 399]


def test_flash_records_render_in_the_ledger_with_their_arguments(mock_context):
    """The band-③ phrase reads the tool arguments Flash keeps under ``args``:
    app launches name the package, key presses the key, inputs the text,
    direction swipes the direction — never ``'None'``."""
    from artemis.utils.task_tree import format_actions_clean

    with patch("artemis.controllers.unified_controller.get_driver"):
        runner = FlashRunner(mock_context, goal="g")

    launch = _record(runner, "manage_app", {"action": "launch", "app_name": "com.android.settings"})
    assert format_actions_clean(launch) == "Launched app 'com.android.settings'"
    stop = _record(runner, "manage_app", {"action": "stop", "app_name": "com.android.settings"})
    assert format_actions_clean(stop) == "Stopped app 'com.android.settings'"
    direct = _record(runner, "launch_app", {"app_name": "com.android.settings"})
    assert format_actions_clean(direct) == "Launched app 'com.android.settings'"

    key = _record(runner, "press_key", {"key": "BACK"})
    assert format_actions_clean(key) == "Pressed key 'BACK'"

    typed = _record(runner, "input_text", {"target": [500, 600], "text": "hello"})
    assert format_actions_clean(typed) == "Inputted 'hello' into field at [500, 600]"

    # A directional swipe names the direction first; the recorded canonical
    # path is the parenthesised detail.
    swipe_dir = _record(runner, "swipe", {"direction": "up"})
    assert format_actions_clean(swipe_dir) == "Swiped up (from [600, 700] to [600, 300])"
    swipe_fa = _record(runner, "swipe", {"action": "down"})
    assert format_actions_clean(swipe_fa) == "Swiped down (from [600, 300] to [600, 700])"
    swipe_coords = _record(runner, "swipe", {"coordinates": [556, 289, 556, 124]})
    assert format_actions_clean(swipe_coords) == "Swiped from [556, 289] to [556, 124]"


def test_flash_records_the_executors_target_semantics(mock_context):
    """Flash targets are coordinates, so the model's own description (validated
    and shaped by the executor into ``metadata.target_semantics``) is the only
    target semantics on record: top level, the shape Pro records, rendered as the
    action's label. Nothing is inferred when the executor hands back nothing."""
    from artemis.utils.task_tree import format_actions_clean

    with patch("artemis.controllers.unified_controller.get_driver"):
        runner = FlashRunner(mock_context, goal="g")

    click_args = {"target": [320, 399], "target_description": "play button"}
    click = _record(
        runner,
        "click",
        click_args,
        metadata={"target_semantics": {"target_description": "play button"}},
    )
    assert click["target_description"] == "play button"
    assert "target_text" not in click
    assert format_actions_clean(click) == "Tapped 'play button' (self-described) at [320, 399]"

    burst = _record(
        runner,
        "click_sequence",
        {"sequence": [[500, 280], [885, 362]], "target_descriptions": ["video body", "skip"]},
        metadata={"target_semantics": {"target_descriptions": ["video body", "skip"]}},
    )
    assert burst["target_descriptions"] == ["video body", "skip"]
    assert format_actions_clean(burst) == (
        "Tapped sequence of targets: 'video body' (self-described) at [500, 280],"
        " 'skip' (self-described) at [885, 362]"
    )

    bare = _record(runner, "click", {"target": [320, 399]}, metadata={"target_semantics": {}})
    assert "target_description" not in bare
    assert "target_text" not in bare


def test_flash_records_an_index_target_as_the_resolved_point_with_observed_semantics(
    mock_context,
):
    """An element index is not a coordinate: the record carries the normalized
    point the executor resolved it to, plus the element's observed fields in
    the shape Pro records (never a self-described label)."""
    from artemis.utils.task_tree import format_actions_clean

    with patch("artemis.controllers.unified_controller.get_driver"):
        runner = FlashRunner(mock_context, goal="g")

    click = _record(
        runner,
        "click",
        {"target": 3},
        metadata={
            "target_coordinates": [500, 520],
            "target_semantics": {
                "target_text": "Wi-Fi",
                "target_bounds": [37, 480, 962, 560],
                "target_resource_id": "android:id/title",
                "target_class": "android.widget.TextView",
            },
        },
    )
    assert click["coordinates"] == [500, 520]
    assert click["normalized_coordinates"] == [500, 520]
    assert click["target_text"] == "Wi-Fi"
    assert click["target_bounds"] == [37, 480, 962, 560]
    assert "target_description" not in click
    assert click["args"] == {"target": 3}  # the tool call itself, verbatim
    assert format_actions_clean(click) == "Tapped 'Wi-Fi' at [500, 520]"

    typed = _record(
        runner,
        "input_text",
        {"target": 2, "text": "hello"},
        metadata={"target_coordinates": [500, 300], "target_semantics": {"target_text": "Search"}},
    )
    assert typed["coordinates"] == [500, 300]
    assert format_actions_clean(typed) == "Inputted 'hello' into 'Search' at [500, 300]"


@pytest.mark.asyncio
async def test_visual_lens_receives_the_recorded_action_shape(mock_context):
    """The lens gets the recorded action minus its verb (what the Pro
    summarizer hands it), so ``manage_app``'s own ``action`` argument can no
    longer overwrite the action name in the rendered phrase."""
    from artemis.agents.flash.runner import _TurnRecord
    from artemis.agents.flash.summarizer import VisualStepSummarizer

    with patch("artemis.controllers.unified_controller.get_driver"):
        runner = FlashRunner(mock_context, goal="g")
    runner.summarizer = Mock()
    runner.executor.execute = AsyncMock(
        return_value=ToolExecutionResult(
            tool_call_id="tc",
            tool_name="manage_app",
            status="success",
            text_summary="Launched app 'Settings' (com.android.settings); foreground confirmed.",
            screenshot_bytes=b"post",
            metadata={"target_semantics": {}, "target_coordinates": None},
        )
    )
    args = {"action": "launch", "app_name": "Settings"}

    await runner._execute_and_record_action(
        "manage_app", args, "tc", Mock(), [], "thought", {}, b"pre", "xml", 0, _TurnRecord()
    )

    runner.summarizer.dispatch.assert_called_once()
    kwargs = runner.summarizer.dispatch.call_args.kwargs
    assert kwargs["action_name"] == "manage_app"
    assert "action" not in kwargs["action_args"]
    assert kwargs["action_args"]["args"] == args
    assert VisualStepSummarizer._action_phrase("manage_app", kwargs["action_args"]) == (
        "Launched app 'Settings'"
    )


# --- Native thinking (thought summaries) reach the step record ----------------------


def _thinking_response(content):
    from types import SimpleNamespace

    return SimpleNamespace(content=content, tool_calls=[])


def test_extract_response_thinking_reads_only_thinking_blocks(mock_context):
    """``include_thoughts`` replies carry ``thinking`` blocks next to ``text``
    blocks; only the former are native thinking, and plain-string replies
    carry none."""
    with patch("artemis.controllers.unified_controller.get_driver"):
        runner = FlashRunner(mock_context, goal="g")

    blocks = [
        {"type": "thinking", "thinking": "**Verifying**\n\nMaps is open."},
        {"type": "text", "text": "Tapping the search bar."},
        {"type": "thinking", "thinking": "  "},
    ]
    assert runner._extract_response_thinking(_thinking_response(blocks)) == (
        "**Verifying**\n\nMaps is open."
    )
    assert runner._extract_response_text(_thinking_response(blocks)) == "Tapping the search bar."
    assert runner._extract_response_thinking(_thinking_response("plain reply")) == ""
    assert (
        runner._extract_response_thinking(_thinking_response([{"type": "text", "text": "t"}])) == ""
    )


def test_record_action_step_persists_native_thinking(mock_context):
    from types import SimpleNamespace

    with patch("artemis.controllers.unified_controller.get_driver"):
        runner = FlashRunner(mock_context, goal="g")
    runner.ctx.data_engine = Mock()
    runner.ctx.data_engine.current_step_id = None
    runner.ctx.data_engine.record_step.return_value = "step-1"
    exec_result = SimpleNamespace(
        status="success", text_summary="click executed", metadata={}, ui_elements_text="ui"
    )

    runner._record_action_step(
        "click",
        {"target": [1, 2]},
        exec_result,
        "thought",
        {},
        b"pre",
        ["xml"],
        b"post",
        native_text="native summary",
    )
    kwargs = runner.ctx.data_engine.record_step.call_args.kwargs
    assert kwargs["operator_raw_thinking"] == "thought"
    assert kwargs["operator_native_thinking"] == "native summary"

    runner._record_action_step(
        "click", {"target": [1, 2]}, exec_result, "thought", {}, b"pre", ["xml"], b"post"
    )
    assert runner.ctx.data_engine.record_step.call_args.kwargs["operator_native_thinking"] is None


@pytest.mark.asyncio
async def test_final_report_persists_native_thinking(mock_context):
    with patch("artemis.controllers.unified_controller.get_driver"):
        runner = FlashRunner(mock_context, goal="g")
    runner.ctx.data_engine = Mock()
    runner.ctx.data_engine.current_step_id = None
    runner.summarizer = None
    messages = []

    report = await runner._finalize_task_report(
        "report_task_status",
        {"status": "completed"},
        "tc",
        "final text",
        {},
        b"pre",
        ["xml"],
        messages,
        native_text="native summary",
    )
    assert report == {"status": "completed"}
    kwargs = runner.ctx.data_engine.record_step.call_args.kwargs
    assert kwargs["operator_raw_thinking"] == "final text"
    assert kwargs["operator_native_thinking"] == "native summary"
    assert isinstance(messages[-1], ToolMessage)


@pytest.mark.asyncio
async def test_a_ten_click_sequence_is_rejected_as_one_tool_call():
    from types import SimpleNamespace

    runner = object.__new__(FlashRunner)
    runner.executor = SimpleNamespace(action_tool_names=frozenset({"click_sequence"}))
    runner.executor.execute = AsyncMock()

    await runner._process_tool_calls(
        [
            {
                "name": "click_sequence",
                "args": {
                    "sequence": [[i, i] for i in range(10)],
                    "target_descriptions": [f"t{i}" for i in range(10)],
                },
                "id": "tc-seq",
            }
        ],
        SimpleNamespace(indexed_elements=[]),
        [],
        "thought",
        {},
        b"pre",
        ["xml"],
        0,
        SimpleNamespace(step_keys=[], actions=[]),
    )

    runner.executor.execute.assert_not_awaited()


def test_high_value_action_without_effect_evidence_is_not_success():
    from types import SimpleNamespace

    result = SimpleNamespace(status="success", text_summary="launched")
    metadata = {"post_verification": {}}

    FlashRunner._require_effect_evidence("manage_app", result, metadata)

    assert result.status == "error"
    assert metadata["incident"] == "target_unverified"


def test_flash_runner_never_accesses_a_bomb_controller(mock_context):
    class Bomb:
        def __getattr__(self, name):
            raise AssertionError(f"controller touched: {name}")

    class FakePort:
        async def observe(self, settle_ms=400, ocr_results=None):
            from artemis.core.action_types import ObserveResult

            return ObserveResult(ok=True, message="ok", elements=[]), b"img"

        async def screen_data(self):
            from artemis.perception.observation import ScreenData

            return ScreenData(base64="", elements=[])

    mock_context.controller = Bomb()
    mock_context.observation_port = FakePort()
    runner = FlashRunner(mock_context, goal="g")

    assert not hasattr(runner, "controller")
    assert runner.observation_port is mock_context.observation_port


@pytest.mark.asyncio
async def test_fallback_observation_uses_the_observation_port(mock_context):
    from artemis.core.action_types import ObserveResult

    seen = []

    class FakePort:
        async def observe(self, settle_ms=400, ocr_results=None):
            seen.append(settle_ms)
            return ObserveResult(
                ok=True,
                message="ok",
                elements_text="[1] Text: 'Port'",
                elements=[{"index": 1, "text": "Port", "center": [2, 3]}],
                screenshot_path="/tmp/port.jpg",
            ), b"PORT"

        async def screen_data(self):
            from artemis.perception.observation import ScreenData
            import base64

            return ScreenData(
                base64=base64.b64encode(b"PORT").decode("ascii"),
                elements=[
                    {
                        "text": "Port",
                        "bounds": "[1,2][3,4]",
                        "center": [2, 3],
                    }
                ],
                width=1080,
                height=2400,
            )

    mock_context.observation_port = FakePort()
    runner = FlashRunner(mock_context, goal="g")
    state = Mock()
    result = _failed_click()

    post = await runner._capture_post_screenshot(result, "click", frozenset({"click"}), state)

    assert post == b"PORT"
    assert seen == [0]
    assert result.ui_elements_text == "[1] Text: 'Port'"
    assert state.latest_screenshot == "/tmp/port.jpg"


@pytest.mark.asyncio
async def test_bomb_controller_is_untouched_by_fallback_observation(mock_context):
    from artemis.core.action_types import ObserveResult

    class Bomb:
        def __getattr__(self, name):
            raise AssertionError(f"controller touched: {name}")

    class FakePort:
        async def observe(self, settle_ms=400, ocr_results=None):
            return ObserveResult(ok=True, message="ok"), b"img"

        async def screen_data(self):
            raise AssertionError("not used")

    mock_context.controller = Bomb()
    mock_context.observation_port = FakePort()
    runner = FlashRunner(mock_context, goal="g")
    state = Mock()
    result = _failed_click()

    await runner._capture_post_screenshot(result, "click", frozenset({"click"}), state)


def test_flash_stops_when_the_device_session_is_failed(mock_context):
    from artemis.devices.session import SessionState

    mock_context.device_session = SimpleNamespace(
        state=SessionState.FAILED, fault_reason="uvc unplugged"
    )
    runner = FlashRunner(mock_context, goal="g")

    assert "Device session failed" in runner._device_failure()
