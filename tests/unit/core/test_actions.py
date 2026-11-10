"""Action intent/command/receipt/verification semantics (ADR-004)."""

from __future__ import annotations

import pytest

from artemis.core.actions import (
    ActionCommand,
    ActionIntent,
    ActionReceipt,
    ActionState,
    Idempotency,
    VerificationResult,
    can_retry,
    state_from_result,
    transition_allowed,
)
from artemis.core.coordinates import CoordinateSpace, PointRef


def _command(**overrides):
    values = {
        "command_id": "cmd-1",
        "intent_id": "intent-1",
        "attempt": 1,
        "adapter_id": "esp32_hid",
        "parameters": {"target": [500, 500]},
        "frame_id": "frame-1",
        "transform_id": "xform-abc",
    }
    values.update(overrides)
    return ActionCommand(**values)


def _receipt(**overrides):
    values = {
        "action_id": "action-1",
        "intent_id": "intent-1",
        "command_id": "cmd-1",
        "attempt": 1,
        "adapter_id": "esp32_hid",
        "state": ActionState.ADAPTER_ACKNOWLEDGED,
        "dispatched_at": 100.0,
    }
    values.update(overrides)
    return ActionReceipt(**values)


def test_an_adapter_ack_is_not_a_verified_success():
    assert state_from_result(True) is ActionState.ADAPTER_ACKNOWLEDGED
    assert state_from_result(False) is ActionState.UNKNOWN
    assert state_from_result(False, "capability_unavailable") is ActionState.REJECTED
    assert transition_allowed(ActionState.ADAPTER_ACKNOWLEDGED, ActionState.OBSERVED_EFFECT)
    assert not transition_allowed(ActionState.ADAPTER_ACKNOWLEDGED, ActionState.VERIFIED_SUCCESS)
    assert not transition_allowed(ActionState.VERIFIED_SUCCESS, ActionState.DISPATCHED)


def test_unsafe_is_the_default_and_only_safe_actions_retry_blindly():
    assert ActionIntent("i", "tap").idempotency is Idempotency.UNSAFE
    assert can_retry(Idempotency.UNSAFE) is False
    assert can_retry(Idempotency.CONDITIONAL) is False
    assert can_retry(Idempotency.CONDITIONAL, observed_no_effect=True) is True
    assert can_retry(Idempotency.SAFE) is True


def test_intent_and_command_keep_coordinate_refs_and_attempts():
    target = PointRef(CoordinateSpace.AGENT_POINT, 500, 500, frame_id="frame-1")
    intent = ActionIntent("intent-1", "tap", target, {"label": "设置"}, Idempotency.CONDITIONAL)
    command = _command(target_ref=target)

    assert intent.target is target
    assert command.attempt == 1
    assert command.frame_id == "frame-1"
    assert command.transform_id == "xform-abc"


def test_attempt_must_be_positive():
    with pytest.raises(ValueError, match="attempt"):
        _command(attempt=0)
    with pytest.raises(ValueError, match="attempt"):
        _receipt(attempt=0)


def test_receipt_rejects_terminal_verification_states():
    with pytest.raises(ValueError, match="receipt cannot carry"):
        _receipt(state=ActionState.VERIFIED_SUCCESS)

    retry = _receipt(attempt=2, retry_of="action-1")
    assert retry.retry_of == "action-1"


def test_verification_success_requires_evidence_and_unknown_does_not():
    with pytest.raises(ValueError, match="evidence"):
        VerificationResult("action-1", "success", 100.0)

    result = VerificationResult(
        "action-1", "success", 100.0, evidence_refs=("artifact-1",), confidence=None
    )
    assert result.confidence is None

    unknown = VerificationResult("action-1", "unknown", 100.0, reason="frame_not_fresh")
    assert unknown.status == "unknown"


def test_confidence_bounds_are_validated():
    with pytest.raises(ValueError, match="confidence"):
        VerificationResult("action-1", "failure", 1.0, confidence=1.5)
