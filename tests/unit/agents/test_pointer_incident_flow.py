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

"""A relative-pointer tap failure reaches the Operator as a classified incident.

The iOS UVC + ESP32 HID path is the first driver that reports *why* a tap failed
(``pointer_missing`` and friends). These tests pin the contract end to end through the
Validator: no silent success, no retry storm of UVC grabs, and a category the Operator
prompt can act on. Android behavior is covered by the incident tests in
``test_validator.py``.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from artemis.agents.validator.categories import ValidationErrorCategory
from artemis.agents.validator.validator import ValidatorNode
from artemis.context import ArtemisContext
from artemis.mcp.action_types import ActionCode, ActionResult


class DummyState:
    def __init__(self, structured_decisions, latest_screenshot):
        self.structured_decisions = structured_decisions
        self.current_step_id = None
        self.latest_screenshot = latest_screenshot
        self.open_incident = None


class PointerIncidentSession:
    """A session whose click fails with a structured driver incident."""

    def __init__(self, incident="pointer_missing", ok=False):
        self.started = True
        self.incident = incident
        self.ok = ok
        self.calls = []
        self.screenshot = "ZHVtbXlfZGF0YQ=="
        self.hierarchy = []

    async def call(self, name, args):
        self.calls.append((name, args))
        if name == "click":
            return ActionResult(
                ok=self.ok,
                code=ActionCode.OK if self.ok else ActionCode.DEVICE_ERROR,
                action=name,
                message="Error executing click: Tap failed at (540, 1200)",
                detail="pointer not detected before click; refusing to click",
                incident=None if self.ok else self.incident,
            )
        return ActionResult(ok=True, code=ActionCode.OK, action=name, message="")

    def calls_for(self, name):
        return [args for n, args in self.calls if n == name]

    async def screenshot_b64(self, timeout=None):
        return self.screenshot

    async def ui_hierarchy(self, timeout=None):
        return self.hierarchy

    async def aclose(self):
        pass


@pytest.fixture
def temp_screenshot(tmp_path):
    path = tmp_path / "screenshot.png"
    path.write_bytes(b"dummy_data")
    return str(path)


@pytest.fixture
def mock_context(tmp_path):
    ctx = Mock(spec=ArtemisContext)
    ctx.llm_config = Mock()
    ctx.data_engine = Mock()
    ctx.data_engine.base_dir = tmp_path
    ctx.data_engine.get_relative_time.return_value = 1.0
    ctx.device = SimpleNamespace(device_width=1080, device_height=2400)
    return ctx


async def _run(node, state, session):
    async def _get_session(ctx, actuator=None):
        return session

    with (
        patch("artemis.agents.validator.validator.get_action_session", _get_session),
        patch("artemis.utils.image_diff.check_ui_change", return_value=False),
        patch.object(
            ValidatorNode,
            "_validate_action_precondition_pixel",
            new_callable=AsyncMock,
            return_value=(True, ValidationErrorCategory.NONE, ""),
        ),
    ):
        return await node(state)


@pytest.mark.asyncio
async def test_pointer_missing_opens_a_classified_incident_without_retrying(
    mock_context, temp_screenshot
):
    session = PointerIncidentSession("pointer_missing")
    decisions = json.dumps(
        [{"action": "tap", "normalized_coordinates": [500, 500], "coordinates": [540, 1200]}]
    )
    state = DummyState(decisions, temp_screenshot)

    report = (await _run(ValidatorNode(mock_context), state, session))["last_execution_result"]

    assert report["status"] == "failed"
    incident = report["incident"]
    assert incident["category"] == "pointer_missing"
    assert incident["kind"] == "exec_error"
    assert "Tap failed" in incident["reason"]
    assert incident["evidence"]["driver_incident"] == "pointer_missing"
    assert "action_result" not in incident["action"]
    assert len(session.calls_for("click")) == 1, (
        "the controller already ran its bounded correction loop; a pointer incident"
        " must not be retried into a burst of UVC grabs"
    )
    assert report["execution"][0]["incident"] == "pointer_missing"


@pytest.mark.asyncio
async def test_no_effect_incident_is_reported_as_no_effect(mock_context, temp_screenshot):
    session = PointerIncidentSession("no_effect")
    decisions = json.dumps([{"action": "tap", "normalized_coordinates": [500, 500]}])
    state = DummyState(decisions, temp_screenshot)

    report = (await _run(ValidatorNode(mock_context), state, session))["last_execution_result"]

    assert report["incident"]["category"] == "no_effect"
    assert report["incident"]["evidence"]["driver_incident"] == "no_effect"


@pytest.mark.asyncio
async def test_android_style_failure_still_opens_a_general_incident(mock_context, temp_screenshot):
    class _NoIncidentSession(PointerIncidentSession):
        async def call(self, name, args):
            self.calls.append((name, args))
            if name == "click":
                return ActionResult(
                    ok=False,
                    code=ActionCode.TARGET_NOT_FOUND,
                    action=name,
                    message="Error: Target not found",
                )
            return ActionResult(ok=True, code=ActionCode.OK, action=name, message="")

    session = _NoIncidentSession()
    decisions = json.dumps([{"action": "tap", "normalized_coordinates": [500, 500]}])
    state = DummyState(decisions, temp_screenshot)

    report = (await _run(ValidatorNode(mock_context), state, session))["last_execution_result"]

    incident = report["incident"]
    assert incident["category"] == str(ValidationErrorCategory.GENERAL)
    assert "driver_incident" not in incident["evidence"]
    assert "action_result" not in incident["action"]


@pytest.mark.asyncio
async def test_the_incident_evidence_carries_the_action_metrics(mock_context, temp_screenshot):
    class WithMetrics(PointerIncidentSession):
        async def call(self, name, args):
            result = await super().call(name, args)
            if name == "click":
                return result.model_copy(update={"metrics": {"error_px": 21.0, "frame_age_s": 0.4}})
            return result

    session = WithMetrics()
    decisions = json.dumps([{"action": "tap", "normalized_coordinates": [500, 500]}])
    state = DummyState(decisions, temp_screenshot)

    report = (await _run(ValidatorNode(mock_context), state, session))["last_execution_result"]

    assert report["incident"]["evidence"]["action_metrics"] == {
        "error_px": 21.0,
        "frame_age_s": 0.4,
    }
    assert "action_metrics" not in report["incident"]["action"]
