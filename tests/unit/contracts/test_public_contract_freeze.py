"""R4 public-contract freeze.

These are intentionally explicit snapshots, not derived from implementation
convenience: changing a public Port/DTO field must be a reviewed contract
change, not an accidental side effect of a refactor.
"""

from __future__ import annotations

from dataclasses import fields

from artemis.control.base import ActionResult, ControlSink
from artemis.core.actions import ActionReceipt, ActionState
from artemis.core.capabilities import CapabilityId, CapabilitySet
from artemis.data_engine.ids import ActionEvidence, ArtifactRef, DataIds
from artemis.perception.base import Frame, PerceptionSource, Target, TextBox, UIElement
from artemis.perception.observation import ObservationEvidence, ScreenData


def _names(cls) -> list[str]:
    return [field.name for field in fields(cls)]


def test_action_result_and_receipt_fields_are_frozen():
    assert _names(ActionResult) == [
        "ok",
        "action",
        "incident",
        "detail",
        "metrics",
        "action_id",
        "attempt",
        "state",
    ]
    assert _names(ActionReceipt) == [
        "action_id",
        "intent_id",
        "command_id",
        "attempt",
        "adapter_id",
        "state",
        "dispatched_at",
        "acknowledged_at",
        "incident",
        "metrics",
        "retry_of",
        "evidence_refs",
    ]
    assert {state.value for state in ActionState} == {
        "proposed",
        "validated",
        "dispatched",
        "adapter_acknowledged",
        "observed_effect",
        "verified_success",
        "verified_failure",
        "unknown",
        "rejected",
        "cancelled",
        "deadline_exceeded",
    }


def test_action_evidence_fields_are_frozen():
    assert _names(ActionEvidence) == [
        "ids",
        "profile_id",
        "adapter_id",
        "source",
        "target",
        "coordinate_space",
        "capability_decision",
        "dispatch_result",
        "device_ack",
        "action_state",
        "action_receipt",
        "goal_verified",
        "pre_verification",
        "post_verification",
        "latency_ms",
        "incident",
        "artifacts",
    ]
    assert _names(DataIds) == [
        "session_id",
        "device_id",
        "task_id",
        "step_id",
        "action_id",
        "frame_id",
        "trace_id",
    ]
    assert _names(ArtifactRef) == [
        "artifact_id",
        "kind",
        "content_type",
        "size_bytes",
        "uri",
    ]


def test_perception_dtos_and_observation_evidence_are_frozen():
    assert _names(Frame) == [
        "image",
        "captured_at",
        "width",
        "height",
        "source",
        "health",
        "frame_id",
        "geometry_revision",
    ]
    assert _names(Target) == [
        "label",
        "box",
        "confidence",
        "source",
        "frame_id",
        "transform_id",
    ]
    assert _names(TextBox) == ["text", "box", "confidence"]
    assert _names(UIElement) == ["text", "bounds", "resource_id"]
    assert _names(ScreenData) == [
        "base64",
        "elements",
        "width",
        "height",
        "platform",
        "evidence",
    ]
    assert _names(ObservationEvidence) == [
        "frame_id",
        "captured_at",
        "source_id",
        "geometry_revision",
        "health",
        "age_s",
    ]


def test_capability_contract_is_a_closed_set():
    assert _names(CapabilitySet) == [
        "supports_ui_tree",
        "supports_absolute_tap",
        "supports_relative_pointer",
        "requires_pointer_precheck",
        "supports_ocr",
        "supports_text_input",
        "supports_shell",
        "supports_app_launch",
    ]
    assert {capability.value for capability in CapabilityId} == {
        "frame.capture",
        "ui_tree.read",
        "ocr.read",
        "grounding.locate",
        "window.inspect",
        "log.read",
        "touch.absolute",
        "pointer.relative",
        "pointer.absolute",
        "keyboard.key",
        "text.input",
        "remote.dpad",
        "app.launch",
        "window.focus",
        "usb.hid",
        "serial.command",
        "adb",
        "ble",
        "network.rpc",
        "hdmi.cec",
        "device.health",
        "input.status",
        "capture.health",
        "firmware.info",
        "shell.exec",
    }


def test_perception_and_control_protocol_methods_are_frozen():
    for method in ("snapshot", "ui_tree", "ocr", "locate"):
        assert callable(getattr(PerceptionSource, method))
    for method in ("tap_abs", "tap_rel", "swipe", "input_text", "press_key"):
        assert callable(getattr(ControlSink, method))
