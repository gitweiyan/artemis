"""Global capability evidence store and artifacts (ADR-008)."""

from __future__ import annotations

import hashlib
import json

import pytest

from artemis.core.capabilities import (
    CapabilityEvidence,
    CapabilityId,
    capability_fingerprint,
    fingerprint_fields,
)
from artemis.data_engine.capability_evidence import (
    MAX_ARTIFACT_BYTES,
    CapabilityArtifactStore,
    SqliteCapabilityEvidenceStore,
)

FIELDS = fingerprint_fields(platform_family="ios", adapter_id="esp32_hid", adapter_version="1")
FINGERPRINT = capability_fingerprint(FIELDS)


def _store(tmp_path):
    artifacts = CapabilityArtifactStore(tmp_path / "artifacts")
    return artifacts, SqliteCapabilityEvidenceStore(tmp_path / "evidence.db", artifacts=artifacts)


def _evidence(artifacts, *, evidence_id="ev-1", conclusion="verified", executed=100.0, **overrides):
    ref = artifacts.put(b"test report", kind="test_report")
    values = {
        "evidence_id": evidence_id,
        "capability_id": CapabilityId.POINTER_RELATIVE,
        "conclusion": conclusion,
        "device_id": "ios-uvc",
        "endpoint_ids": ("serial:host:key", "uvc:host:key"),
        "fingerprint": FINGERPRINT,
        "fingerprint_fields": FIELDS,
        "test_id": "ios-smoke.pointer",
        "test_version": "1",
        "evidence_kind": "smoke",
        "executed_at": executed,
        "artifact_refs": (ref,),
    }
    values.update(overrides)
    return CapabilityEvidence(**values)


def test_record_then_lookup_roundtrip(tmp_path):
    artifacts, store = _store(tmp_path)
    evidence = _evidence(artifacts)
    store.record(evidence)

    found = store.lookup(
        capability_id=CapabilityId.POINTER_RELATIVE,
        device_id="ios-uvc",
        endpoint_ids=("uvc:host:key", "serial:host:key"),
        fingerprint=FINGERPRINT,
    )

    assert found is not None
    assert found.evidence_id == "ev-1"
    assert found.conclusion == "verified"
    assert found.fingerprint_fields["adapter_id"] == "esp32_hid"


def test_record_is_idempotent_by_evidence_id(tmp_path):
    artifacts, store = _store(tmp_path)
    evidence = _evidence(artifacts)
    store.record(evidence)
    store.record(evidence)

    assert len(store.list()) == 1


def test_a_new_conclusion_supersedes_the_old_one(tmp_path):
    artifacts, store = _store(tmp_path)
    store.record(_evidence(artifacts, evidence_id="ev-ok", conclusion="verified", executed=100))
    store.record(_evidence(artifacts, evidence_id="ev-bad", conclusion="failed", executed=200))

    found = store.lookup(
        capability_id=CapabilityId.POINTER_RELATIVE,
        device_id="ios-uvc",
        endpoint_ids=("uvc:host:key", "serial:host:key"),
        fingerprint=FINGERPRINT,
    )
    assert found is not None and found.evidence_id == "ev-bad"

    all_rows = store.list(include_superseded=True)
    assert [row.evidence_id for row in all_rows] == ["ev-ok", "ev-bad"]
    assert all_rows[0].superseded_by == "ev-bad"


def test_fingerprint_mismatch_does_not_return_evidence(tmp_path):
    artifacts, store = _store(tmp_path)
    store.record(_evidence(artifacts))

    assert (
        store.lookup(
            capability_id=CapabilityId.POINTER_RELATIVE,
            device_id="ios-uvc",
            endpoint_ids=("uvc:host:key", "serial:host:key"),
            fingerprint="b" * 64,
        )
        is None
    )


def test_device_and_capability_scope_are_enforced(tmp_path):
    artifacts, store = _store(tmp_path)
    store.record(_evidence(artifacts))

    assert (
        store.lookup(
            capability_id=CapabilityId.KEYBOARD_KEY,
            device_id="ios-uvc",
            endpoint_ids=("uvc:host:key", "serial:host:key"),
            fingerprint=FINGERPRINT,
        )
        is None
    )
    assert (
        store.lookup(
            capability_id=CapabilityId.POINTER_RELATIVE,
            device_id="other-device",
            endpoint_ids=("uvc:host:key", "serial:host:key"),
            fingerprint=FINGERPRINT,
        )
        is None
    )


def test_evidence_persists_across_store_instances(tmp_path):
    artifacts, store = _store(tmp_path)
    store.record(_evidence(artifacts))

    reopened = SqliteCapabilityEvidenceStore(tmp_path / "evidence.db", artifacts=artifacts)
    found = reopened.lookup(
        capability_id=CapabilityId.POINTER_RELATIVE,
        device_id="ios-uvc",
        endpoint_ids=("serial:host:key", "uvc:host:key"),
        fingerprint=FINGERPRINT,
    )
    assert found is not None


def test_record_requires_the_artifact_to_exist(tmp_path):
    artifacts, store = _store(tmp_path)
    evidence = _evidence(artifacts).__class__(
        **{
            **_evidence(artifacts).__dict__,
            "artifact_refs": ("c" * 64,),
        }
    )

    with pytest.raises(ValueError, match="artifact"):
        store.record(evidence)


def test_artifacts_are_content_addressed_and_deduplicated(tmp_path):
    artifacts = CapabilityArtifactStore(tmp_path / "artifacts")
    first = artifacts.put(b"same", kind="log")
    second = artifacts.put(b"same", kind="log")

    assert first == second == hashlib.sha256(b"same").hexdigest()
    assert artifacts.metadata(first)["size"] == 4
    assert artifacts.path(first).read_bytes() == b"same"


def test_artifact_size_cap_and_ref_validation(tmp_path):
    artifacts = CapabilityArtifactStore(tmp_path / "artifacts")

    with pytest.raises(ValueError, match="cap"):
        artifacts.put(b"x" * (MAX_ARTIFACT_BYTES + 1), kind="log")
    with pytest.raises(ValueError, match="artifact ref"):
        artifacts.path("../../etc/passwd")


def test_evidence_dir_is_independent_of_the_run_traces_path(tmp_path, monkeypatch):
    db = tmp_path / "global" / "evidence.db"
    artifacts_dir = tmp_path / "global" / "artifacts"
    monkeypatch.setenv("ARTEMIS_CAPABILITY_DB", str(db))
    monkeypatch.setenv("ARTEMIS_CAPABILITY_ARTIFACTS", str(artifacts_dir))
    monkeypatch.setenv("ARTEMIS_TRACES_DIR", str(tmp_path / "run-a"))

    store = SqliteCapabilityEvidenceStore()
    assert store.db_path == db

    evidence = _evidence(store.artifacts)
    store.record(evidence)
    assert db.exists()

    monkeypatch.setenv("ARTEMIS_TRACES_DIR", str(tmp_path / "run-b"))
    reopened = SqliteCapabilityEvidenceStore()
    assert (
        reopened.lookup(
            capability_id=CapabilityId.POINTER_RELATIVE,
            device_id="ios-uvc",
            endpoint_ids=("uvc:host:key", "serial:host:key"),
            fingerprint=FINGERPRINT,
        )
        is not None
    )


def test_lookup_prefers_the_latest_conclusion(tmp_path):
    artifacts, store = _store(tmp_path)
    store.record(_evidence(artifacts, evidence_id="ev-1", conclusion="verified", executed=100))
    store.record(_evidence(artifacts, evidence_id="ev-2", conclusion="failed", executed=300))
    store.record(_evidence(artifacts, evidence_id="ev-3", conclusion="verified", executed=200))

    found = store.lookup(
        capability_id=CapabilityId.POINTER_RELATIVE,
        device_id="ios-uvc",
        endpoint_ids=("uvc:host:key", "serial:host:key"),
        fingerprint=FINGERPRINT,
    )
    assert found is not None and found.evidence_id == "ev-2"


def test_superseded_rows_keep_their_payload(tmp_path):
    artifacts, store = _store(tmp_path)
    store.record(_evidence(artifacts, evidence_id="ev-ok", executed=100))
    store.record(_evidence(artifacts, evidence_id="ev-bad", conclusion="failed", executed=200))

    rows = {row.evidence_id: row for row in store.list(include_superseded=True)}
    assert rows["ev-ok"].conclusion == "verified"
    assert rows["ev-ok"].superseded_by == "ev-bad"
    assert json.loads(json.dumps(dict(rows["ev-ok"].environment))) == {}
