"""Correlation IDs, artifacts and action evidence (Phase 6)."""

from __future__ import annotations

import pytest

from artemis.data_engine.ids import ActionEvidence, ArtifactRef, DataIds


def test_data_ids_require_a_session_and_normalize_optional_links():
    ids = DataIds(
        "session-1",
        device_id="ios-uvc",
        step_id=None,
        action_id="action-1",
        frame_id="frame-1",
        trace_id="trace-1",
    )

    assert ids.as_dict()["device_id"] == "ios-uvc"
    assert ids.as_dict()["frame_id"] == "frame-1"
    with pytest.raises(ValueError):
        DataIds("")


def test_artifacts_are_references_not_inline_images():
    ref = ArtifactRef("a" * 64, "screenshot", "image/png", size_bytes=123)

    assert ref.as_dict()["artifact_id"] == "a" * 64
    assert ref.as_dict()["size_bytes"] == 123
    with pytest.raises(ValueError):
        ArtifactRef("", "screenshot")
    with pytest.raises(ValueError):
        ArtifactRef("hash", "image", size_bytes=-1)


def test_action_evidence_serializes_replay_facts_without_image_bytes():
    evidence = ActionEvidence(
        ids=DataIds("session-1", device_id="ios-uvc", action_id="a1"),
        profile_id="ios.uvc_hid",
        adapter_id="click",
        target={"coordinates": [100, 200]},
        coordinate_space="agent_point",
        dispatch_result={"ok": True},
        device_ack="adapter_acknowledged",
        post_verification={"allowed": True},
        latency_ms=12.5,
        artifacts=(ArtifactRef("b" * 64, "screenshot", "image/png"),),
    )

    payload = evidence.as_dict()

    assert payload["ids"]["action_id"] == "a1"
    assert payload["latency_ms"] == 12.5
    assert payload["artifacts"][0]["artifact_id"] == "b" * 64
    assert "image" not in payload
