"""Capability view resolution against a fake evidence store (ADR-001)."""

from __future__ import annotations

from artemis.core.capabilities import (
    CapabilityDeclaration,
    CapabilityEvidence,
    CapabilityId,
    CapabilityManifest,
    CapabilityState,
    capability_fingerprint,
    fingerprint_fields,
)
from artemis.core.capability_registry import CapabilityRegistry, ProbeResult, declared_view
from artemis.core.identity import PlatformFamily
from artemis.devices.descriptors import build_ios_descriptor

FIELDS = fingerprint_fields(platform_family="ios", adapter_id="esp32_hid", adapter_version="1")
FINGERPRINT = capability_fingerprint(FIELDS)


def _descriptor():
    return build_ios_descriptor(
        device_id="ios-uvc",
        host_id="host-a",
        screen_size=(1080, 2400),
        uvc_device="USB Video",
        esp32_port="/dev/cu.usbmodem21101",
    )


def _manifest(*ids):
    return CapabilityManifest(
        "esp32_hid",
        "1",
        tuple(CapabilityDeclaration(capability) for capability in ids),
    )


def _evidence(conclusion="verified", *, kind="smoke", executed=100.0, revalidate=None):
    return CapabilityEvidence(
        evidence_id=f"ev-{conclusion}",
        capability_id=CapabilityId.POINTER_RELATIVE,
        conclusion=conclusion,
        device_id="ios-uvc",
        endpoint_ids=_descriptor().endpoint_ids,
        fingerprint=FINGERPRINT,
        fingerprint_fields=FIELDS,
        test_id="ios-smoke.pointer",
        test_version="1",
        evidence_kind=kind,
        executed_at=executed,
        artifact_refs=("a" * 64,),
        revalidate_after=revalidate,
    )


class Store:
    def __init__(self, evidence=None):
        self.evidence = evidence
        self.calls = 0

    def record(self, evidence):
        self.evidence = evidence

    def lookup(self, **kwargs):
        self.calls += 1
        return self.evidence


def test_declared_view_has_no_runtime_claim():
    view = declared_view([_manifest(CapabilityId.POINTER_RELATIVE)])

    assert view.supports(CapabilityId.POINTER_RELATIVE) is True
    assert view.supports(CapabilityId.POINTER_RELATIVE, for_execution=True) is False
    assert view[CapabilityId.POINTER_RELATIVE].observed_at is None


def test_a_declared_capability_is_not_verified_without_a_probe():
    store = Store(_evidence())
    registry = CapabilityRegistry(store)
    view = registry.resolve(
        descriptor=_descriptor(),
        manifests=[_manifest(CapabilityId.POINTER_RELATIVE)],
        fingerprint_fields=FIELDS,
        now=200.0,
    )

    assert view[CapabilityId.POINTER_RELATIVE].state is CapabilityState.DECLARED
    assert store.calls == 0, "evidence must not upgrade an unprobed capability"


def test_available_plus_matching_evidence_is_verified():
    registry = CapabilityRegistry(Store(_evidence()))
    view = registry.resolve(
        descriptor=_descriptor(),
        manifests=[_manifest(CapabilityId.POINTER_RELATIVE)],
        probes={CapabilityId.POINTER_RELATIVE: ProbeResult(True, "pointer_detector")},
        fingerprint_fields=FIELDS,
        now=200.0,
    )

    record = view[CapabilityId.POINTER_RELATIVE]
    assert record.state is CapabilityState.VERIFIED
    assert record.source == "evidence:ev-verified"
    assert record.evidence_ref == "ev-verified"
    assert record.fingerprint == FINGERPRINT
    assert view.supports(CapabilityId.POINTER_RELATIVE, for_execution=True) is True


def test_failed_evidence_keeps_the_capability_available_but_unverified():
    registry = CapabilityRegistry(Store(_evidence("failed")))
    view = registry.resolve(
        descriptor=_descriptor(),
        manifests=[_manifest(CapabilityId.POINTER_RELATIVE)],
        probes={CapabilityId.POINTER_RELATIVE: ProbeResult(True, "pointer_detector")},
        fingerprint_fields=FIELDS,
        now=200.0,
    )

    record = view[CapabilityId.POINTER_RELATIVE]
    assert record.state is CapabilityState.AVAILABLE
    assert record.reason == "verification_failed"
    assert record.evidence_ref == "ev-failed"


def test_stale_evidence_requires_revalidation():
    registry = CapabilityRegistry(Store(_evidence(revalidate=150.0)))
    view = registry.resolve(
        descriptor=_descriptor(),
        manifests=[_manifest(CapabilityId.POINTER_RELATIVE)],
        probes={CapabilityId.POINTER_RELATIVE: ProbeResult(True, "pointer_detector")},
        fingerprint_fields=FIELDS,
        now=200.0,
    )

    record = view[CapabilityId.POINTER_RELATIVE]
    assert record.state is CapabilityState.AVAILABLE
    assert record.reason == "revalidation_required"


def test_manual_evidence_is_diagnostic_only():
    registry = CapabilityRegistry(Store(_evidence(kind="manual", conclusion="failed")))
    view = registry.resolve(
        descriptor=_descriptor(),
        manifests=[_manifest(CapabilityId.POINTER_RELATIVE)],
        probes={CapabilityId.POINTER_RELATIVE: ProbeResult(True, "pointer_detector")},
        fingerprint_fields=FIELDS,
        now=200.0,
    )

    assert view[CapabilityId.POINTER_RELATIVE].state is CapabilityState.AVAILABLE
    assert view[CapabilityId.POINTER_RELATIVE].reason == "manual_evidence_only"


def test_a_failed_probe_is_unavailable_and_an_inconclusive_probe_is_unknown():
    registry = CapabilityRegistry()
    view = registry.resolve(
        descriptor=_descriptor(),
        manifests=[_manifest(CapabilityId.POINTER_RELATIVE, CapabilityId.KEYBOARD_KEY)],
        probes={
            CapabilityId.POINTER_RELATIVE: ProbeResult(False, "serial", "no_serial"),
            CapabilityId.KEYBOARD_KEY: ProbeResult(None, "serial", "not_tested"),
        },
        fingerprint_fields=FIELDS,
        now=200.0,
    )

    assert view[CapabilityId.POINTER_RELATIVE].state is CapabilityState.UNAVAILABLE
    assert view[CapabilityId.POINTER_RELATIVE].reason == "no_serial"
    assert view[CapabilityId.KEYBOARD_KEY].state is CapabilityState.UNKNOWN
    assert view[CapabilityId.KEYBOARD_KEY].reason == "not_tested"


def test_a_uvc_endpoint_and_serial_endpoint_are_one_device_for_evidence():
    descriptor = _descriptor()
    assert descriptor.platform_family is PlatformFamily.IOS
    assert len(descriptor.endpoint_ids) == 2
