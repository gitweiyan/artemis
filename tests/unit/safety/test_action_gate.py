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


def _png(color: tuple[int, int, int]) -> bytes:
    from io import BytesIO

    from PIL import Image

    out = BytesIO()
    Image.new("RGB", (100, 100), color).save(out, "PNG")
    return out.getvalue()


def test_post_action_verify_uses_pixel_evidence_and_refuses_unknown_evidence():
    from base64 import b64encode
    from types import SimpleNamespace

    from artemis.agents.validator.safety_gate import ValidatorActionGate

    gate = ValidatorActionGate(SimpleNamespace())
    request = SafetyRequest(
        "tap",
        metadata={
            "before_image_b64": b64encode(_png((0, 0, 0))).decode(),
            "after_image_b64": b64encode(_png((0, 0, 0))).decode(),
            "point_px": (50, 50),
        },
    )

    import asyncio

    unchanged = asyncio.run(gate.verify(request, SimpleNamespace(ok=True)))
    assert unchanged.allowed is False
    assert unchanged.incident == "no_effect"

    request = SafetyRequest("tap", metadata={})
    missing = asyncio.run(gate.verify(request, SimpleNamespace(ok=True)))
    assert missing.allowed is False
    assert missing.incident == "target_unverified"


def test_post_action_verify_passes_when_the_screen_changed():
    from base64 import b64encode
    from types import SimpleNamespace
    import asyncio

    from artemis.agents.validator.safety_gate import ValidatorActionGate

    gate = ValidatorActionGate(SimpleNamespace())
    request = SafetyRequest(
        "tap",
        metadata={
            "before_image_b64": b64encode(_png((0, 0, 0))).decode(),
            "after_image_b64": b64encode(_png((255, 255, 255))).decode(),
            "point_px": (50, 50),
        },
    )

    decision = asyncio.run(gate.verify(request, SimpleNamespace(ok=True)))

    assert decision.allowed is True
    assert decision.incident is None
    assert decision.evidence["frame_change"] > 0


def test_opt_in_post_verification_records_the_gate_decision():
    import asyncio
    from types import SimpleNamespace

    from artemis.agents.validator.execution_loop import _ActionOutcome, _verify_after

    class Gate:
        def __init__(self):
            self.request = None

        async def check(self, request):
            return GateDecision(True)

        async def verify(self, request, result):
            self.request = request
            return GateDecision(False, "no_effect", "nothing changed", {})

    class Session:
        async def screenshot_b64(self):
            from base64 import b64encode

            return b64encode(_png((0, 0, 0))).decode()

    gate = Gate()
    node = SimpleNamespace(
        ctx=SimpleNamespace(device=SimpleNamespace(device_width=1000, device_height=1000)),
        safety_gate=gate,
    )
    outcome = _ActionOutcome(action_item={"action": "tap"})
    action = {"action": "tap", "coordinates": [500, 500], "verify_after": True}

    asyncio.run(
        _verify_after(
            node,
            Session(),
            outcome,
            __import__("base64").b64encode(_png((0, 0, 0))).decode(),
            action,
        )
    )

    assert outcome.success is False
    assert outcome.error_category.value == "no_effect"
    assert action["post_verification"]["incident"] == "no_effect"
    assert gate.request.metadata["point_px"] == (500, 500)


def test_evidence_policy_reflects_pointer_and_remote_capabilities():
    from artemis.safety.evidence import EvidenceKind, evidence_policy

    absolute = evidence_policy("tap", absolute_touch=True, relative_pointer=False)
    assert absolute.pointer_precheck is False
    assert EvidenceKind.POINTER_ALIGNMENT not in absolute.evidence

    relative = evidence_policy("tap", absolute_touch=False, relative_pointer=True)
    assert relative.pointer_precheck is True
    assert EvidenceKind.POINTER_ALIGNMENT in relative.evidence

    remote = evidence_policy("press_key", remote_input=True)
    assert remote.pointer_precheck is False
    assert EvidenceKind.DISPATCH_RECEIPT in remote.evidence

    assert evidence_policy("wait_for_delay").requires_post_verification is False


def test_high_value_launch_verifies_by_default_and_blocks_no_effect():
    import asyncio
    from base64 import b64encode
    from types import SimpleNamespace

    from artemis.agents.validator.execution_loop import _ActionOutcome, _verify_after

    class Gate:
        async def check(self, request):
            return GateDecision(True)

        async def verify(self, request, result):
            return GateDecision(False, "no_effect", "launch did not change the screen")

    class Session:
        async def screenshot_b64(self):
            return b64encode(_png((0, 0, 0))).decode()

    node = SimpleNamespace(
        ctx=SimpleNamespace(device=SimpleNamespace(device_width=1000, device_height=1000)),
        safety_gate=Gate(),
    )
    outcome = _ActionOutcome(action_item={"action": "launch_app"})
    action = {"action": "launch_app", "app_name": "Settings"}
    before = b64encode(_png((0, 0, 0))).decode()

    asyncio.run(_verify_after(node, Session(), outcome, before, action))

    assert outcome.success is False
    assert action["post_verification"]["incident"] == "no_effect"
