"""R4 error responsibility, retryability, and evidence correlation."""

from __future__ import annotations

from artemis.core.actions import ActionReceipt, ActionState
from artemis.core.errors import (
    ActionReceiptContractError,
    DeviceDisconnectedError,
    DeviceFault,
    ObservationProcessingError,
    SessionClosedError,
)
from artemis.data_engine.ids import ActionEvidence, DataIds
from artemis.perception.observation import ObservationEvidence


def test_error_retryability_is_explicit():
    assert DeviceFault.retryable is True
    assert DeviceDisconnectedError.retryable is True
    assert ObservationProcessingError.retryable is True
    assert SessionClosedError.retryable is False
    assert ActionReceiptContractError.retryable is False


def test_observation_action_effect_and_goal_evidence_are_correlated():
    observation = ObservationEvidence(
        frame_id="frame-1",
        captured_at=10.0,
        source_id="uvc",
        geometry_revision="geom-1",
        health="ok",
        age_s=0.1,
    )
    receipt = ActionReceipt(
        action_id="action-1",
        intent_id="intent-1",
        command_id="command-1",
        attempt=1,
        adapter_id="click",
        state=ActionState.OBSERVED_EFFECT,
        dispatched_at=10.0,
        acknowledged_at=10.1,
        evidence_refs=("artifact-1",),
    )
    evidence = ActionEvidence(
        ids=DataIds(
            session_id="session-1",
            frame_id=observation.frame_id,
            action_id=receipt.action_id,
        ),
        action_state=receipt.state.value,
        action_receipt=receipt.as_dict(),
        post_verification={"ok": True, "frame_change": 0.8},
        goal_verified=False,
    )

    assert evidence.ids.frame_id == observation.frame_id
    assert evidence.ids.action_id == receipt.action_id
    assert evidence.action_receipt["state"] == "observed_effect"
    assert evidence.post_verification["ok"] is True
    assert evidence.goal_verified is False
