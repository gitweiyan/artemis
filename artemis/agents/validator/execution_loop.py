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

"""Per-turn action execution loop for the Validator.

Two execution tiers, selected by how many turn-ending actions the Operator
emitted in the turn:

* **Vetted single action** (one action): the pre-execution Safety Net runs
  first (XML-first with pixel fallback), then the action executes locally with
  a short retry loop.
* **Fast-action burst** (two or more actions): the actions fire back to back
  with no safety net, no retries and no screenshots in between. The burst is
  the Operator's tool against turn latency (transient menus, toasts, control
  bars); the Operator owns the risk. The first failure aborts the rest.

Either way, a failure does not spawn a repair agent. The loop opens an
*execution incident* (:mod:`artemis.agents.validator.incidents`) that rides in
graph state and in the step's execution report, and the Operator resolves it
on its next turn with the live screen in front of it.

Extracted from ``validator.py``; the public entry point remains
``ValidatorNode.__call__``, which delegates here. All patchable seams
(_exec_action, _validate_action_precondition*, _get_initial_screenshot, ...)
are dispatched through the node so existing test patches keep working.
"""

import asyncio
import base64
from dataclasses import dataclass
import time
import uuid
from uuid import UUID

from artemis.agents.validator.action_execution import incident_category
from artemis.runtime import observation_clock
from artemis.agents.validator.categories import ValidationErrorCategory
from artemis.agents.validator.incidents import (
    KIND_EXEC_ERROR,
    KIND_SAFETY_NET,
    open_incident,
)
from artemis.data_engine.trace import CURRENT_TRACE_ID
from artemis.graph.state import State
from artemis.utils.coordinates import normalize_action_dict
from artemis.utils.task_tree import format_action_clean
from third_party.mobile_use.utils.logger import get_logger

logger = get_logger(__name__)

#: ``attempts`` marker for actions a burst never reached.
BURST_SKIPPED_MARKER = "Skipped (burst aborted)"

#: High-value actions get post-action verification by default (ADR-007/Phase 5).
#: Pointer taps still rely on their verified pointer loop; launch/link actions are
#: one-shot and otherwise only have an adapter ACK.
DEFAULT_POST_VERIFY_ACTIONS = frozenset({"manage_app", "launch_app", "open_link"})


@dataclass
class _ActionOutcome:
    """Result of processing a single action item (pre-check + execution)."""

    action_item: dict
    success: bool = False
    error_msg: str = ""
    error_category: ValidationErrorCategory = ValidationErrorCategory.GENERAL
    intercepted: bool = False
    post_screenshot_b64: str | None = None


async def _run_precondition_gate(
    node, session, action_item: dict, state: State, pre_screenshot_b64: str, original_coords: list
) -> tuple[bool, ValidationErrorCategory, str]:
    """Run the task-mode ActionGate (XML-first with pixel fallback)."""

    from artemis.safety.gate import SafetyRequest

    gate = getattr(node, "safety_gate", None)
    if gate is None:
        from artemis.agents.validator.safety_gate import ValidatorActionGate

        gate = ValidatorActionGate(node)
    request = SafetyRequest(
        action=str(action_item.get("action") or "action"),
        target=action_item.get("coordinates"),
        metadata={
            "session": session,
            "action_item": action_item,
            "state": state,
            "pre_screenshot_b64": pre_screenshot_b64,
            "original_coords": original_coords,
        },
    )
    decision = await gate.check(request)
    raw_category = decision.evidence.get("category", "general")
    try:
        category = ValidationErrorCategory(str(raw_category))
    except ValueError:
        category = ValidationErrorCategory.GENERAL
    return bool(decision.allowed), category, decision.reason


async def _attempt_local_execution(
    node, session, action_item: dict, action_name, attempts_log: list, *, max_local_retries: int
) -> tuple[bool, str]:
    """Executes the action locally with a short retry loop."""
    success = False
    error_msg = ""
    for attempt in range(max_local_retries):
        action_item["action_attempt"] = attempt + 1
        try:
            exec_success, exec_error = await node._exec_action(session, action_item)
        except Exception as e:
            exec_success = False
            exec_error = str(e)

        # No effect polling: "dispatched" only says the device accepted the
        # command; whether it had the intended effect is the Operator's call.
        if exec_success:
            success = True
            error_msg = ""
            attempts_log.append("Dispatched")
            break
        else:
            success = False
            error_msg = exec_error
            attempts_log.append(error_msg)

        # A structured driver incident (pointer_missing/no_effect) means the controller
        # already exhausted its own bounded correction loop; retrying here would only
        # multiply single-frame UVC grabs. Report it once.
        if action_item.get("incident"):
            break

        if attempt < max_local_retries - 1:
            await asyncio.sleep(0.5)

    return success, error_msg


async def _capture_live_screenshot(session, context_desc: str) -> str | None:
    try:
        return await session.screenshot_b64()
    except Exception as e:
        logger.error(f"Failed to capture {context_desc}: {e!r}")
        return None


async def _verify_after(
    node,
    session,
    outcome: _ActionOutcome,
    pre_screenshot_b64: str,
    action_item: dict,
) -> None:
    """Optional post-action probe for explicitly high-value actions.

    This is opt-in via ``verify_after`` so existing turns keep their latency. The
    gate receives the turn-initial and post-action screenshots; a missing/empty
    probe is an incident, not a silent dispatch-only success.
    """

    explicit = action_item.get("verify_after")
    should_verify = (
        bool(explicit)
        if explicit is not None
        else str(action_item.get("action") or "") in DEFAULT_POST_VERIFY_ACTIONS
    )
    if not should_verify:
        return
    try:
        after_b64 = await session.screenshot_b64()
    except (OSError, RuntimeError, ValueError, AttributeError) as exc:
        outcome.success = False
        outcome.error_msg = f"Post-action verification could not capture the screen: {exc}"
        outcome.error_category = ValidationErrorCategory.CAPTURE_TIMEOUT
        return
    outcome.post_screenshot_b64 = after_b64
    ctx = getattr(node, "ctx", None)
    width = getattr(getattr(ctx, "device", None), "device_width", 1080) or 1080
    height = getattr(getattr(ctx, "device", None), "device_height", 2400) or 2400
    coords = action_item.get("coordinates") or []
    if len(coords) == 2:
        try:
            nx, ny = int(coords[0]), int(coords[1])
            point = (
                max(0, min(width - 1, round(nx * width / 1000))),
                max(0, min(height - 1, round(ny * height / 1000))),
            )
        except (TypeError, ValueError):
            point = (width // 2, height // 2)
    else:
        point = (width // 2, height // 2)
    from artemis.safety.gate import SafetyRequest

    gate = getattr(node, "safety_gate", None)
    if gate is None:
        return
    decision = await gate.verify(
        SafetyRequest(
            action=str(action_item.get("action") or "action"),
            metadata={
                "before_image_b64": pre_screenshot_b64,
                "after_image_b64": after_b64,
                "point_px": point,
            },
        ),
        None,
    )
    action_item["post_verification"] = decision.as_dict()
    if not decision.allowed:
        # A legacy/test capture can be non-image bytes. For the automatic
        # high-value path, record that the probe was unavailable instead of
        # turning a successfully dispatched launch into a false tap failure.
        if decision.incident == "decode_error" and explicit is None:
            action_item["post_verification"]["skipped"] = "evidence_unavailable"
            return
        outcome.success = False
        outcome.error_msg = f"Post-action verification failed: {decision.reason}"
        outcome.error_category = incident_category(decision.incident)


async def _process_action(
    node,
    state: State,
    session,
    action_item: dict,
    action_name,
    pre_screenshot_b64: str,
    *,
    burst: bool,
) -> _ActionOutcome:
    """Runs precondition checks (vetted tier only) and execution for one action item."""
    action_item = dict(action_item)  # Make a copy to avoid mutating original state actions
    # How stale is the screen this action was planned from? Recorded before the
    # safety net so a target that vanished during the window still leaves data (I5).
    observation_clock.note_dispatch_skew(getattr(node, "ctx", None))
    outcome = _ActionOutcome(action_item=action_item)
    attempts_log = []

    validation_passed = True
    if not burst:
        # 1. Pre-execution validation (Safety Net)
        original_coords = list(action_item.get("coordinates") or [])
        (
            validation_passed,
            validation_category,
            validation_error,
        ) = await _run_precondition_gate(
            node, session, action_item, state, pre_screenshot_b64, original_coords
        )
        if not validation_passed:
            outcome.success = False
            outcome.intercepted = True
            outcome.error_msg = f"Pre-execution validation failed: {validation_error}"
            outcome.error_category = validation_category
            attempts_log.append(outcome.error_msg)

    if validation_passed:
        # 2. Local execution: a vetted action gets a short retry loop, a burst
        # member fires exactly once (retries would break the burst's timing).
        if burst:
            max_local_retries = 1
        else:
            max_local_retries = 1 if action_name == "launch_app" else 2
        outcome.success, outcome.error_msg = await _attempt_local_execution(
            node,
            session,
            action_item,
            action_name,
            attempts_log,
            max_local_retries=max_local_retries,
        )
        if not outcome.success and action_item.get("incident"):
            outcome.error_category = incident_category(action_item["incident"])
        if outcome.success:
            await _verify_after(node, session, outcome, pre_screenshot_b64, action_item)

    if not outcome.success:
        logger.info("Action failed, capturing failure screenshot...")
        outcome.post_screenshot_b64 = await _capture_live_screenshot(session, "failure screenshot")

    # Enrich the executed action record
    if len(attempts_log) > 1 or not outcome.success:
        action_item["attempts"] = list(attempts_log)

    return outcome


def _absorb_post_screenshot(ctx, state: State, post_screenshot_b64: str) -> str | None:
    """Persists the post-action screenshot and points state at it.

    Returns the stored image name (None when no data engine is attached).
    """
    decoded_bytes = base64.b64decode(post_screenshot_b64)
    logger.info(f"Successfully decoded post_screenshot_b64 ({len(decoded_bytes)} bytes)")
    if not ctx.data_engine:
        return None

    post_image_name = ctx.data_engine.get_or_create_image(decoded_bytes)
    screenshot_path = str(ctx.data_engine.get_image_path(post_image_name))
    state.latest_screenshot = screenshot_path
    logger.info(f"Validator updated state.latest_screenshot to: {screenshot_path}")
    return post_image_name


def _record_action_start(ctx, step_id, action_name, action_item, parent_id, action_trace_id):
    if ctx.data_engine and step_id:
        ctx.data_engine.record_trace(
            type="action",
            name=action_name,
            payload={"action": action_item, "status": "running"},
            status="running",
            parent_trace_id=parent_id,
            step_id=step_id,
            trace_id=action_trace_id,
        )


def _record_action_end(
    ctx,
    step_id,
    action_name,
    outcome: _ActionOutcome,
    post_image_name,
    parent_id,
    action_trace_id,
    start_time: float,
    *,
    burst: bool,
):
    duration = time.time() - start_time
    if ctx.data_engine and step_id:
        relative_time = ctx.data_engine.get_relative_time(time.time())
        ctx.data_engine.record_trace(
            type="action",
            name=action_name,
            payload={
                "action": outcome.action_item,
                "success": outcome.success,
                "error_msg": outcome.error_msg,
                "post_screenshot": post_image_name,
                "timestamp": time.time(),
                "relative_time": relative_time,
                "burst": burst,
            },
            status="success" if outcome.success else "failed",
            duration=duration,
            parent_trace_id=parent_id,
            step_id=step_id,
            trace_id=action_trace_id,
        )


def _normalize_point(ctx, point) -> list[int] | None:
    """Pixel [x, y] -> normalized 0-1000 [x, y] using the device dims."""
    try:
        x, y = int(point[0]), int(point[1])
    except (TypeError, ValueError, IndexError, KeyError):
        return None
    width = getattr(getattr(ctx, "device", None), "device_width", None) or 1080
    height = getattr(getattr(ctx, "device", None), "device_height", None) or 2400
    return [
        max(0, min(1000, round(x * 1000 / width))),
        max(0, min(1000, round(y * 1000 / height))),
    ]


def _incident_evidence(ctx, outcome: _ActionOutcome) -> dict:
    """Structured facts the safety net attached to the action item, normalized."""
    raw = outcome.action_item.pop("safety_net_evidence", None) or {}
    evidence: dict = {}
    if raw.get("new_center"):
        normalized = _normalize_point(ctx, raw["new_center"])
        if normalized:
            evidence["new_location"] = normalized
        if raw.get("new_bounds") is not None:
            evidence["new_bounds"] = raw["new_bounds"]
    if raw.get("occupant"):
        evidence["occupant"] = raw["occupant"]
        if raw.get("occupant_bounds") is not None:
            evidence["occupant_bounds"] = raw["occupant_bounds"]
    # A driver that reports its own failure reason (relative pointer platforms) keeps
    # that code as evidence; the category above already mirrors it.
    stem_incident = outcome.action_item.get("incident")
    if stem_incident:
        evidence["driver_incident"] = stem_incident
    metrics = outcome.action_item.get("action_metrics")
    if metrics:
        # Pointer position, error, frame age: what telemetry and a bug report need.
        evidence["action_metrics"] = dict(metrics)
    return evidence


def _current_step_number(ctx, state: State) -> int | None:
    step_id = getattr(state, "current_step_id", None)
    if ctx.data_engine and step_id:
        try:
            return ctx.data_engine.get_step_number(UUID(step_id))
        except Exception:
            return None
    return None


def _model_facing_action(ctx, action_item: dict) -> dict:
    """The action item in the Operator's own 0–1000 coordinate space.

    The live item carries the controller's pixels; every phrase rendered for
    a model is normalized first, and the space marker makes that idempotent.
    """
    width = getattr(getattr(ctx, "device", None), "device_width", None) or 1080
    height = getattr(getattr(ctx, "device", None), "device_height", None) or 2400
    return normalize_action_dict(action_item, width, height)


def _build_incident(ctx, state: State, outcome: _ActionOutcome, *, index: int, total: int) -> dict:
    step_number = _current_step_number(ctx, state)
    return open_incident(
        previous=getattr(state, "open_incident", None),
        kind=KIND_SAFETY_NET if outcome.intercepted else KIND_EXEC_ERROR,
        category=outcome.error_category,
        reason=outcome.error_msg,
        action_item=outcome.action_item,
        action_description=format_action_clean(_model_facing_action(ctx, outcome.action_item)),
        action_index=index,
        burst_size=total,
        step_number=step_number,
        evidence=_incident_evidence(ctx, outcome),
    )


async def run_validation_loop(node, state: State) -> dict:
    """Executes all pending Operator decisions and returns the execution report.

    ``node`` is the ValidatorNode facade; every overridable seam is invoked
    through it.
    """
    ctx = node.ctx

    actions, error_msg = node._parse_decisions(state.structured_decisions)
    if error_msg:
        return {}

    execution: list[dict] = []
    actions_to_execute = list(actions)
    total_actions = len(actions_to_execute)
    burst = total_actions > 1
    if burst:
        logger.info(
            f"Fast-action burst: {total_actions} actions will fire back to back"
            " without the safety net."
        )

    session = await node._get_mcp_session()

    try:
        last_screenshot_b64, last_screenshot_name = await node._get_initial_screenshot(
            session, state
        )
        # Preserve the turn-initial screenshot (what the operator saw when making decisions)
        decision_screenshot_name = last_screenshot_name
    except Exception:
        return {}

    step_id = UUID(state.current_step_id) if state.current_step_id else None

    incident: dict | None = None
    success = False
    index = 0

    while actions_to_execute:
        action_item = actions_to_execute.pop(0)
        action_name = action_item.get("action")

        logger.info(f"Executing action: {action_name}")

        action_trace_id = uuid.uuid4()
        parent_id = CURRENT_TRACE_ID.get()
        token = CURRENT_TRACE_ID.set(action_trace_id)
        start_time = time.time()

        _record_action_start(ctx, step_id, action_name, action_item, parent_id, action_trace_id)

        try:
            outcome = await _process_action(
                node, state, session, action_item, action_name, last_screenshot_b64, burst=burst
            )
            success = outcome.success
            execution.append(outcome.action_item)

            post_image_name = None
            if outcome.post_screenshot_b64:
                post_image_name = _absorb_post_screenshot(ctx, state, outcome.post_screenshot_b64)
                last_screenshot_b64 = outcome.post_screenshot_b64
                if post_image_name:
                    last_screenshot_name = post_image_name

            _record_action_end(
                ctx,
                step_id,
                action_name,
                outcome,
                post_image_name,
                parent_id,
                action_trace_id,
                start_time,
                burst=burst,
            )
        finally:
            CURRENT_TRACE_ID.reset(token)

        if not success:
            incident = _build_incident(ctx, state, outcome, index=index, total=total_actions)
            logger.warning(
                f"Action failed: {outcome.action_item}. Opened execution incident"
                f" ({incident['kind']}/{incident['category']},"
                f" consecutive={incident['consecutive_failures']}) for the Operator."
            )
            break
        index += 1

    # Append any skipped actions to the execution log
    for skipped in actions_to_execute:
        skipped_copy = dict(skipped)
        skipped_copy["attempts"] = [BURST_SKIPPED_MARKER if burst else "Skipped"]
        execution.append(skipped_copy)

    report = {
        "execution": execution,
        "status": "dispatched" if success else "failed",
        "burst": burst,
        "incident": incident,
    }

    # Determine if a distinct post-action screenshot was captured
    distinct_post_image_name = (
        last_screenshot_name
        if (last_screenshot_name and last_screenshot_name != decision_screenshot_name)
        else None
    )

    if ctx.data_engine and step_id:
        ctx.data_engine.update_step_execution_result(
            step_id, report, post_image_name=distinct_post_image_name
        )

    # A successful turn closes whatever incident was open (handing the Operator
    # a one-turn CLOSED notice); a failed one keeps and escalates it.
    closed = None
    previous = getattr(state, "open_incident", None)
    if success and isinstance(previous, dict) and previous.get("kind"):
        closed = dict(previous)
        closed["closed_at_step"] = _current_step_number(ctx, state)
    return {
        "last_execution_result": report,
        "open_incident": incident,
        "last_closed_incident": closed,
    }
