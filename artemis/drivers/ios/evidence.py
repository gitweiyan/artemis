"""Turn an ``ios-smoke`` report into capability evidence (ADR-001/008).

Only capabilities the smoke actually exercised are recorded. A failed stage
writes ``conclusion=failed`` (which supersedes an older ``verified``); a stage
that never ran is simply absent. The report itself is stored as the artifact so
every record can be traced back to the run that produced it.
"""

from __future__ import annotations

import json
import time
import uuid

from artemis.core.capabilities import (
    CapabilityEvidence,
    CapabilityId,
    capability_fingerprint,
)
from artemis.data_engine.capability_evidence import SqliteCapabilityEvidenceStore
from artemis.devices.descriptors import DeviceDescriptor, capability_fields_for
from artemis.drivers.ios.smoke import SmokeReport
from artemis.runtime.host_identity import host_id as resolve_host_id

__all__ = ["record_smoke_evidence"]

#: (capability, stages, record_failure). All present stages decide the conclusion.
_PLANS = (
    (CapabilityId.SERIAL_COMMAND, ("connect",), True),
    (CapabilityId.USB_HID, ("connect",), False),
    (CapabilityId.FRAME_CAPTURE, ("snapshot", "frame_fit"), True),
    (CapabilityId.CAPTURE_HEALTH, ("snapshot",), True),
    (CapabilityId.OCR_READ, ("locate",), True),
    (CapabilityId.POINTER_RELATIVE, ("pointer_motion",), True),
)

#: Evidence is revalidated after 30 days; expiry only makes it stale (ADR-001).
REVALIDATE_SECONDS = 30 * 24 * 60 * 60


def record_smoke_evidence(
    *,
    store: SqliteCapabilityEvidenceStore,
    descriptor: DeviceDescriptor,
    report: SmokeReport,
    test_version: str,
    absolute_ready: bool,
    now: float | None = None,
    capture_mode: str | None = None,
) -> tuple[str, ...]:
    steps = {step.name: step for step in report.steps}
    executed_at = time.time() if now is None else float(now)
    fields = capability_fields_for(
        descriptor,
        adapter_id="esp32_hid",
        adapter_version=test_version,
    )
    fingerprint = capability_fingerprint(fields)

    plans = list(_PLANS)
    if absolute_ready and "tap" in steps:
        plans.append((CapabilityId.POINTER_ABSOLUTE, ("tap",), True))

    selected: list[tuple[CapabilityId, bool]] = []
    for capability, stage_names, record_failure in plans:
        present = [steps[name] for name in stage_names if name in steps]
        if not present:
            continue
        ok = len(present) == len(stage_names) and all(step.ok for step in present)
        if ok or record_failure:
            selected.append((capability, ok))

    if not selected:
        return ()

    artifact = store.artifacts.put(
        json.dumps(report.to_dict(), ensure_ascii=False).encode("utf-8"),
        kind="ios_smoke_report",
        content_type="application/json",
    )
    environment = {
        "host_id": resolve_host_id(),
        "artemis_version": test_version,
        "capture_mode": str(capture_mode or "unknown"),
        "screen": f"{descriptor.display.width_px}x{descriptor.display.height_px}"
        if descriptor.display
        else "unknown",
    }
    ids: list[str] = []
    for capability, ok in selected:
        evidence = CapabilityEvidence(
            evidence_id=uuid.uuid4().hex,
            capability_id=capability,
            conclusion="verified" if ok else "failed",
            device_id=descriptor.device_id,
            endpoint_ids=descriptor.endpoint_ids,
            fingerprint=fingerprint,
            fingerprint_fields=fields,
            test_id=f"ios-smoke.{capability.value}",
            test_version=test_version,
            evidence_kind="smoke",
            executed_at=executed_at,
            environment=environment,
            artifact_refs=(artifact,),
            revalidate_after=executed_at + REVALIDATE_SECONDS,
        )
        store.record(evidence)
        ids.append(evidence.evidence_id)
    return tuple(ids)
