"""ios-smoke report → capability evidence (ADR-001/008)."""

from __future__ import annotations

from dataclasses import replace

from artemis.core.capabilities import (
    CapabilityId,
    capability_fingerprint,
)
from artemis.data_engine.capability_evidence import (
    CapabilityArtifactStore,
    SqliteCapabilityEvidenceStore,
)
from artemis.devices.descriptors import build_ios_descriptor, capability_fields_for
from artemis.drivers.ios.evidence import record_smoke_evidence
from artemis.drivers.ios.smoke import SmokeReport, SmokeStep


def _descriptor():
    descriptor = build_ios_descriptor(
        device_id="ios-uvc",
        host_id="host-a",
        screen_size=(1080, 2400),
        uvc_device="USB Video",
        esp32_port="/dev/cu.usbmodem21101",
    )
    return replace(
        descriptor,
        fingerprint=capability_fields_for(
            descriptor, adapter_id="esp32_hid", adapter_version="1.2.3"
        ),
    )


def _report(*, tap_ok=True, snapshot_ok=True, with_target=True):
    report = SmokeReport(ok=True)
    steps = [
        SmokeStep("connect", True, "connected", 10),
        SmokeStep("snapshot", snapshot_ok, "health=ok" if snapshot_ok else "health=black", 10),
    ]
    if not snapshot_ok:
        report.steps = steps
        report.ok = False
        return report
    steps.append(SmokeStep("frame_fit", True, "fitted", 5))
    steps.append(SmokeStep("pointer_motion", True, "moved", 5))
    if with_target:
        steps.append(SmokeStep("locate", True, "found", 50))
        steps.append(SmokeStep("pointer_precheck", True, "visible", 5))
        steps.append(SmokeStep("tap", tap_ok, "clicked" if tap_ok else "no_effect", 900))
    report.steps = steps
    report.ok = all(step.ok for step in steps)
    return report


def _store(tmp_path):
    artifacts = CapabilityArtifactStore(tmp_path / "artifacts")
    return artifacts, SqliteCapabilityEvidenceStore(tmp_path / "evidence.db", artifacts=artifacts)


def _lookup(store, descriptor, capability):
    fields = capability_fields_for(descriptor, adapter_id="esp32_hid", adapter_version="1.2.3")
    return store.lookup(
        capability_id=capability,
        device_id=descriptor.device_id,
        endpoint_ids=descriptor.endpoint_ids,
        fingerprint=capability_fingerprint(fields),
    )


def test_a_green_smoke_records_verified_capabilities_and_an_artifact(tmp_path):
    artifacts, store = _store(tmp_path)
    descriptor = _descriptor()

    ids = record_smoke_evidence(
        store=store,
        descriptor=descriptor,
        report=_report(),
        test_version="1.2.3",
        absolute_ready=True,
        now=1000.0,
        capture_mode="frame",
    )

    assert len(ids) == 7
    for capability in (
        CapabilityId.SERIAL_COMMAND,
        CapabilityId.USB_HID,
        CapabilityId.FRAME_CAPTURE,
        CapabilityId.CAPTURE_HEALTH,
        CapabilityId.OCR_READ,
        CapabilityId.POINTER_RELATIVE,
        CapabilityId.POINTER_ABSOLUTE,
    ):
        evidence = _lookup(store, descriptor, capability)
        assert evidence is not None, capability
        assert evidence.conclusion == "verified"
        assert evidence.revalidate_after == 1000.0 + 30 * 24 * 60 * 60

    report_ref = store.list()[0].artifact_refs[0]
    assert artifacts.exists(report_ref)


def test_a_failed_snapshot_records_failed_capture_evidence(tmp_path):
    artifacts, store = _store(tmp_path)
    descriptor = _descriptor()

    record_smoke_evidence(
        store=store,
        descriptor=descriptor,
        report=_report(snapshot_ok=False),
        test_version="1.2.3",
        absolute_ready=True,
    )

    capture = _lookup(store, descriptor, CapabilityId.FRAME_CAPTURE)
    assert capture is not None and capture.conclusion == "failed"
    health = _lookup(store, descriptor, CapabilityId.CAPTURE_HEALTH)
    assert health is not None and health.conclusion == "failed"
    assert _lookup(store, descriptor, CapabilityId.POINTER_RELATIVE) is None


def test_a_failed_tap_records_failed_absolute_pointer_evidence(tmp_path):
    artifacts, store = _store(tmp_path)
    descriptor = _descriptor()

    record_smoke_evidence(
        store=store,
        descriptor=descriptor,
        report=_report(tap_ok=False),
        test_version="1.2.3",
        absolute_ready=True,
    )

    absolute = _lookup(store, descriptor, CapabilityId.POINTER_ABSOLUTE)
    assert absolute is not None and absolute.conclusion == "failed"


def test_without_absolute_support_no_absolute_evidence_is_written(tmp_path):
    artifacts, store = _store(tmp_path)
    descriptor = _descriptor()

    record_smoke_evidence(
        store=store,
        descriptor=descriptor,
        report=_report(),
        test_version="1.2.3",
        absolute_ready=False,
    )

    assert _lookup(store, descriptor, CapabilityId.POINTER_ABSOLUTE) is None
    assert _lookup(store, descriptor, CapabilityId.POINTER_RELATIVE) is not None


def test_a_report_without_a_target_does_not_invent_ocr_evidence(tmp_path):
    artifacts, store = _store(tmp_path)
    descriptor = _descriptor()

    record_smoke_evidence(
        store=store,
        descriptor=descriptor,
        report=_report(with_target=False),
        test_version="1.2.3",
        absolute_ready=True,
    )

    assert _lookup(store, descriptor, CapabilityId.OCR_READ) is None
    assert _lookup(store, descriptor, CapabilityId.FRAME_CAPTURE) is not None
