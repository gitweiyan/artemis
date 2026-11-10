"""Capability catalog, view, compatibility and evidence DTOs (ADR-001)."""

from __future__ import annotations

import pytest

from artemis.core.capabilities import (
    CATALOG,
    CapabilityDeclaration,
    CapabilityEvidence,
    CapabilityId,
    CapabilityManifest,
    CapabilityRecord,
    CapabilityState,
    CapabilityView,
    capability_fingerprint,
    fingerprint_fields,
)

FINGERPRINT = "a" * 64


def _evidence(**overrides):
    values = {
        "evidence_id": "ev-1",
        "capability_id": CapabilityId.POINTER_RELATIVE,
        "conclusion": "verified",
        "device_id": "ios-uvc",
        "endpoint_ids": ("uvc:host:key",),
        "fingerprint": FINGERPRINT,
        "fingerprint_fields": {"adapter_id": "esp32"},
        "test_id": "ios-smoke.pointer",
        "test_version": "1",
        "evidence_kind": "smoke",
        "executed_at": 100.0,
        "artifact_refs": (FINGERPRINT,),
    }
    values.update(overrides)
    return CapabilityEvidence(**values)


def test_catalog_is_closed_and_contains_the_contract_ids():
    assert len(CATALOG) == 25
    assert CapabilityId.FRAME_CAPTURE in CATALOG
    assert CapabilityId.SHELL_EXEC in CATALOG
    assert CapabilityId.GROUNDING_LOCATE in CATALOG
    assert "requires_pointer_precheck" not in CATALOG


def test_unknown_capability_id_is_rejected():
    with pytest.raises(ValueError, match="unknown capability"):
        CapabilityDeclaration("touch.magic")


def test_unknown_and_unavailable_records_need_a_reason():
    with pytest.raises(ValueError, match="reason"):
        CapabilityRecord(CapabilityId.OCR_READ, CapabilityState.UNKNOWN, "manifest:x")
    with pytest.raises(ValueError, match="reason"):
        CapabilityRecord(CapabilityId.OCR_READ, CapabilityState.UNAVAILABLE, "manifest:x")

    record = CapabilityRecord(
        CapabilityId.OCR_READ, CapabilityState.UNKNOWN, "manifest:x", reason="not_probed"
    )
    assert record.state == "unknown"


def test_view_keeps_planning_and_execution_semantics_apart():
    view = CapabilityView(
        [
            CapabilityRecord(CapabilityId.UI_TREE_READ, CapabilityState.DECLARED, "manifest:a"),
            CapabilityRecord(CapabilityId.POINTER_RELATIVE, CapabilityState.AVAILABLE, "probe:p"),
            CapabilityRecord(
                CapabilityId.TOUCH_ABSOLUTE,
                CapabilityState.UNAVAILABLE,
                "probe:p",
                reason="no_helper",
            ),
        ]
    )

    assert view.supports(CapabilityId.UI_TREE_READ) is True
    assert view.supports(CapabilityId.UI_TREE_READ, for_execution=True) is False
    assert view.supports(CapabilityId.POINTER_RELATIVE, for_execution=True) is True
    assert view.supports(CapabilityId.TOUCH_ABSOLUTE) is False
    assert view.supports(CapabilityId.OCR_READ) is False
    assert len(view.by_domain("observation")) == 1


def test_manifest_rejects_duplicate_capabilities():
    with pytest.raises(ValueError, match="duplicate capability"):
        CapabilityManifest(
            "adapter",
            "1",
            (
                CapabilityDeclaration(CapabilityId.OCR_READ),
                CapabilityDeclaration(CapabilityId.OCR_READ),
            ),
        )


def test_manual_evidence_cannot_be_verified():
    with pytest.raises(ValueError, match="manual evidence"):
        _evidence(evidence_kind="manual", conclusion="verified")


def test_evidence_requires_an_artifact():
    with pytest.raises(ValueError, match="artifact"):
        _evidence(artifact_refs=())


def test_fingerprint_is_order_independent_and_normalizes_unknown():
    left = fingerprint_fields(
        platform_family="ios",
        adapter_id="esp32",
        adapter_version="1",
        permissions={"Input": "granted"},
    )
    right = fingerprint_fields(
        adapter_version="1",
        adapter_id="esp32",
        platform_family="ios",
        permissions={"input": "granted"},
    )
    assert capability_fingerprint(left) == capability_fingerprint(right)
    assert left["device_model"] == "unknown"
    assert left["permission.input"] == "granted"
