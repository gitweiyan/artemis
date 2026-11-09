"""Capability resolution: declared manifest + probe + evidence → view.

The registry is a pure resolver and a thin writer over the evidence store; it
keeps no authority of its own (ADR-008). Callers can rebuild a view at any time
from the same inputs, and deleting a cache never loses evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Iterable, Mapping, Protocol, Sequence

from artemis.core.capabilities import (
    CapabilityEvidence,
    CapabilityId,
    CapabilityManifest,
    CapabilityRecord,
    CapabilityState,
    CapabilityView,
    canonical_endpoint_ids,
    capability_fingerprint,
)
from artemis.core.identity import DeviceDescriptor

__all__ = [
    "CapabilityEvidenceStore",
    "CapabilityRegistry",
    "ProbeResult",
    "declared_view",
]


class CapabilityEvidenceStore(Protocol):
    """Storage port implemented by the global SQLite store (ADR-008)."""

    def record(self, evidence: CapabilityEvidence) -> None: ...

    def lookup(
        self,
        *,
        capability_id: str,
        device_id: str,
        endpoint_ids: Sequence[str],
        fingerprint: str,
    ) -> CapabilityEvidence | None: ...


@dataclass(frozen=True)
class ProbeResult:
    available: bool | None
    source: str
    reason: str | None = None
    observed_at: float | None = None

    def __post_init__(self) -> None:
        if not str(self.source).strip():
            raise ValueError("probe source must not be empty")
        if self.available is not True and not self.reason:
            raise ValueError("a non-available probe must carry a reason")


def declared_view(manifests: Iterable[CapabilityManifest]) -> CapabilityView:
    records: list[CapabilityRecord] = []
    for manifest in manifests:
        for declaration in manifest.declarations:
            records.append(
                CapabilityRecord(
                    capability_id=declaration.capability_id,
                    state=CapabilityState.DECLARED,
                    source=f"manifest:{manifest.adapter_id}",
                    preconditions=declaration.preconditions,
                )
            )
    return CapabilityView(records)


class CapabilityRegistry:
    def __init__(self, store: CapabilityEvidenceStore | None = None) -> None:
        self.store = store

    def resolve(
        self,
        *,
        descriptor: DeviceDescriptor,
        manifests: Iterable[CapabilityManifest],
        probes: Mapping[str, ProbeResult] | None = None,
        fingerprint_fields: Mapping[str, str] | None = None,
        now: float | None = None,
    ) -> CapabilityView:
        current_time = time.time() if now is None else float(now)
        manifests = tuple(manifests)
        probes = probes or {}
        fields = fingerprint_fields if fingerprint_fields is not None else descriptor.fingerprint
        fingerprint = capability_fingerprint(fields)
        endpoint_ids = canonical_endpoint_ids(descriptor.endpoint_ids)
        records: list[CapabilityRecord] = []

        for manifest in manifests:
            for declaration in manifest.declarations:
                capability_id = declaration.capability_id
                probe = probes.get(capability_id)
                state = CapabilityState.DECLARED
                source = f"manifest:{manifest.adapter_id}"
                reason: str | None = None
                evidence_ref: str | None = None
                observed_at: float | None = None

                if probe is not None:
                    observed_at = probe.observed_at or current_time
                    if probe.available is True:
                        state = CapabilityState.AVAILABLE
                        source = f"probe:{probe.source}"
                    elif probe.available is False:
                        state = CapabilityState.UNAVAILABLE
                        source = f"probe:{probe.source}"
                        reason = probe.reason or "probe_failed"
                    else:
                        state = CapabilityState.UNKNOWN
                        source = f"probe:{probe.source}"
                        reason = probe.reason or "probe_inconclusive"

                if state is CapabilityState.AVAILABLE and self.store is not None:
                    evidence = self.store.lookup(
                        capability_id=capability_id,
                        device_id=descriptor.device_id,
                        endpoint_ids=endpoint_ids,
                        fingerprint=fingerprint,
                    )
                    if evidence is not None:
                        evidence_ref = evidence.evidence_id
                        if evidence.evidence_kind == "manual":
                            # Manual evidence is diagnostic only (ADR-001 D5).
                            reason = "manual_evidence_only"
                        elif evidence.is_stale(current_time):
                            reason = "revalidation_required"
                        elif evidence.conclusion == "failed":
                            reason = "verification_failed"
                        else:
                            state = CapabilityState.VERIFIED
                            source = f"evidence:{evidence.evidence_id}"

                records.append(
                    CapabilityRecord(
                        capability_id=capability_id,
                        state=state,
                        source=source,
                        preconditions=declaration.preconditions,
                        observed_at=observed_at,
                        reason=reason,
                        evidence_ref=evidence_ref,
                        fingerprint=fingerprint,
                    )
                )
        return CapabilityView(records)

    def record(self, evidence: CapabilityEvidence) -> None:
        if self.store is None:
            raise RuntimeError("capability registry has no evidence store")
        self.store.record(evidence)

    def lookup(
        self,
        *,
        capability_id: str,
        device_id: str,
        endpoint_ids: Sequence[str],
        fingerprint: str,
    ) -> CapabilityEvidence | None:
        if self.store is None:
            return None
        return self.store.lookup(
            capability_id=capability_id,
            device_id=device_id,
            endpoint_ids=endpoint_ids,
            fingerprint=fingerprint,
        )
