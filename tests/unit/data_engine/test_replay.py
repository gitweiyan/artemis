"""Action evidence query (Phase 6)."""

from __future__ import annotations

from types import SimpleNamespace

from artemis.data_engine.replay import query_action_evidence


def _trace(action_id, *, device="ios-uvc", artifact="a1"):
    return SimpleNamespace(
        payload={
            "action_evidence": {
                "ids": {"action_id": action_id, "session_id": "s1", "device_id": device},
                "artifacts": [{"artifact_id": artifact}],
            }
        }
    )


def test_query_by_action_device_and_artifact():
    traces = [_trace("a1"), _trace("a2", device="android", artifact="a2")]

    assert [item["ids"]["action_id"] for item in query_action_evidence(traces, action_id="a2")] == [
        "a2"
    ]
    assert query_action_evidence(traces, device_id="ios-uvc")[0]["ids"]["action_id"] == "a1"
    assert query_action_evidence(traces, artifact_id="a2")[0]["ids"]["action_id"] == "a2"
    assert query_action_evidence(traces, session_id="s1", limit=1) == [
        traces[0].payload["action_evidence"]
    ]


def test_traces_without_action_evidence_are_ignored():
    traces = [SimpleNamespace(payload={}), SimpleNamespace(payload=None), _trace("a1")]

    assert len(query_action_evidence(traces)) == 1
