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

"""Single-action execution and turn-input helpers for the Validator.

Extracted from ``validator.py``; the public entry points remain the
``ValidatorNode`` methods, which delegate here.
"""

import asyncio
import base64
import json
from pathlib import Path

from artemis.agents.validator.categories import ValidationErrorCategory
from artemis.context import ArtemisContext
from artemis.graph.state import State
from artemis.mcp.action_names import to_canonical_call
from artemis.mcp.action_session import ActionSession
from third_party.mobile_use.utils.logger import get_logger

logger = get_logger(__name__)

#: Driver incident codes (relative-pointer platforms) and the Validator category each one
#: maps to. A driver reports *why* a tap failed; the safety net reports the same facts as
#: categories so the Operator gets one taxonomy regardless of how the failure was found.
_INCIDENT_CATEGORIES: dict[str, ValidationErrorCategory] = {
    "pointer_missing": ValidationErrorCategory.POINTER_MISSING,
    "pointer_off_target": ValidationErrorCategory.POINTER_OFF_TARGET,
    "pointer_not_centered": ValidationErrorCategory.POINTER_NOT_CENTERED,
    "no_effect": ValidationErrorCategory.NO_EFFECT,
    "uvc_no_signal": ValidationErrorCategory.UVC_NO_SIGNAL,
    "uvc_decode_error": ValidationErrorCategory.UVC_NO_SIGNAL,
    "capture_timeout": ValidationErrorCategory.CAPTURE_TIMEOUT,
    "stale_frame": ValidationErrorCategory.STALE_FRAME,
}


def incident_category(incident: str | None) -> ValidationErrorCategory:
    """The Validator category for a driver incident code (``GENERAL`` if unknown)."""
    return _INCIDENT_CATEGORIES.get(str(incident or ""), ValidationErrorCategory.GENERAL)


def parse_decisions(structured_decisions: str) -> tuple[list[dict] | None, str | None]:
    """Parses the Operator's structured decisions JSON into a list of actions."""
    if not structured_decisions:
        return None, "No structured decisions found, nothing to execute."
    try:
        return json.loads(structured_decisions), None
    except json.JSONDecodeError as e:
        return None, f"Failed to parse structured decisions: {e}"


async def read_initial_screenshot(state: State) -> tuple[str, str | None]:
    """Loads the turn-initial screenshot (what the Operator saw) from state."""
    screenshot_path = getattr(state, "latest_screenshot", None)
    if not screenshot_path:
        logger.error("No screenshot path found in state.latest_screenshot")
        raise ValueError("No screenshot path found in state.latest_screenshot")

    if not Path(screenshot_path).exists():
        logger.error(f"Screenshot file does not exist: {screenshot_path}")
        raise FileNotFoundError(f"Screenshot file does not exist: {screenshot_path}")

    try:
        with open(screenshot_path, "rb") as f:
            screenshot_b64 = base64.b64encode(f.read()).decode("utf-8")
    except Exception as e:
        logger.error(f"Failed to read screenshot from {screenshot_path}: {e}")
        raise e

    screenshot_name = Path(screenshot_path).stem
    return screenshot_b64, screenshot_name


async def exec_action(
    ctx: ArtemisContext, session: ActionSession, action_item: dict
) -> tuple[bool, str]:
    """Executes one Operator action item through the unified action session.

    The Operator's internal verbs (tap, long_press_on, focus_and_input_text, ...)
    are translated to canonical tool calls in ``to_canonical_call`` using the
    ``normalized_coordinates`` the Operator already computed. Success comes from
    the structured ``ActionResult.ok`` -- no string parsing.
    """
    action_name = action_item.get("action")

    if action_name == "wait_for_delay":
        time_in_ms = action_item.get("time_in_ms", 0)
        logger.info(f"Validator performing wait_for_delay for {time_in_ms}ms...")
        await asyncio.sleep(time_in_ms / 1000.0)
        return True, ""

    width = getattr(ctx.device, "device_width", 1080) if ctx.device else 1080
    height = getattr(ctx.device, "device_height", 2400) if ctx.device else 2400

    try:
        tool_name, wire_args = to_canonical_call(action_item, dims=(width, height))
    except ValueError as e:
        return False, str(e)

    try:
        res = await session.call(tool_name, wire_args)
    except Exception as e:
        return False, f"MCP call failed: {e}"

    # A driver that knows why a tap failed (relative pointer platforms) returns a
    # structured incident. Record it on the action item so the execution loop can open
    # the matching incident and the trace keeps the evidence. Platforms that report
    # nothing leave the action item byte-identical to before.
    if getattr(res, "incident", None):
        action_item["incident"] = res.incident
        action_item["action_result"] = {
            "ok": bool(res.ok),
            "action": tool_name,
            "incident": res.incident,
            "detail": getattr(res, "detail", None),
        }

    return (True, "") if res.ok else (False, res.message)
