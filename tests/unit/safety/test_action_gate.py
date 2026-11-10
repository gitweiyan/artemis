"""Task-mode ActionGate port and canonical incident mapping (Phase 5)."""

from __future__ import annotations

import pytest

from artemis.agents.validator.categories import ValidationErrorCategory
from artemis.safety.gate import (
    ActionGate,
    GateDecision,
    SafetyMode,
    SafetyPolicy,
    SafetyRequest,
)
from artemis.safety.incidents import CANONICAL_INCIDENTS, canonical_incident


class FakeGate:
    def __init__(self):
        self.checked = []
        self.verified = []

    async def check(self, request):
        self.checked.append(request)
        return GateDecision(False, "target_unverified", "target moved")

    async def verify(self, request, result):
        self.verified.append((request, result))
        return GateDecision(True, None, "changed")


def test_requests_validate_mode_and_confidence():
    request = SafetyRequest("tap", confidence=None)

    assert request.mode is SafetyMode.TASK
    with pytest.raises(ValueError):
        SafetyRequest("tap", confidence=1.1)
    with pytest.raises(ValueError):
        SafetyRequest("")


def test_a_blocked_decision_must_name_a_canonical_incident():
    with pytest.raises(ValueError):
        GateDecision(False)

    decision = GateDecision(True)
    assert decision.requires_post_verification is True
    assert decision.as_dict()["allowed"] is True


def test_the_policy_is_a_structural_gate():
    gate = FakeGate()
    policy = SafetyPolicy("validator.default", SafetyMode.TASK, gate)

    assert isinstance(gate, ActionGate)
    assert policy.mode is SafetyMode.TASK
    assert policy.policy_id == "validator.default"


def test_internal_validator_categories_map_to_cross_layer_incidents():
    assert canonical_incident(ValidationErrorCategory.PIXEL_TARGET_DISAPPEARED) == (
        "target_disappeared"
    )
    assert canonical_incident(ValidationErrorCategory.PIXEL_BYPASSED) == "verification_failed"
    assert canonical_incident(ValidationErrorCategory.XML_BYPASSED) == "verification_failed"
    assert canonical_incident(ValidationErrorCategory.POINTER_MISSING) == "pointer_missing"
    assert canonical_incident(ValidationErrorCategory.NONE) is None
    assert canonical_incident("something_internal") is None
    assert "target_unverified" in CANONICAL_INCIDENTS


def test_execution_loop_delegates_to_the_node_action_gate():
    import asyncio
    from types import SimpleNamespace

    from artemis.agents.validator.execution_loop import _run_precondition_gate
    from artemis.agents.validator.categories import ValidationErrorCategory

    class Gate:
        def __init__(self):
            self.request = None

        async def check(self, request):
            self.request = request
            return GateDecision(
                False,
                "target_unverified",
                "target moved",
                {"category": ValidationErrorCategory.TARGET_DISAPPEARED.value},
            )

        async def verify(self, request, result):
            return GateDecision(True)

    gate = Gate()
    node = SimpleNamespace(safety_gate=gate)

    passed, category, reason = asyncio.run(
        _run_precondition_gate(
            node,
            object(),
            {"action": "tap", "coordinates": [10, 20]},
            None,
            "pre",
            [10, 20],
        )
    )

    assert passed is False
    assert category is ValidationErrorCategory.TARGET_DISAPPEARED
    assert reason == "target moved"
    assert gate.request.action == "tap"
