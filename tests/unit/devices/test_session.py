import asyncio

import pytest

from artemis.devices.capabilities import CapabilitySet
from artemis.devices.geometry import ScreenGeometry
from artemis.devices.session import DeviceSession, DeviceSessionError
from artemis.perception.base import Frame


class Source:
    def __init__(self, frame=None, error=None):
        self.capabilities = CapabilitySet(supports_ocr=True)
        self.frame = frame
        self.error = error

    async def snapshot(self):
        if self.error:
            raise self.error
        return self.frame


class Sink:
    def __init__(self, absolute=False, relative=False):
        self.capabilities = CapabilitySet(
            supports_absolute_tap=absolute,
            supports_relative_pointer=relative,
            requires_pointer_precheck=relative,
        )


def geometry():
    return ScreenGeometry((449, 974), (1080, 2400))


def test_snapshot_uses_first_healthy_source():
    bad = Frame(b"bad", 1.0, 10, 10, "bad", health="black")
    good = Frame(b"ok", 2.0, 10, 10, "good")
    session = DeviceSession([Source(bad), Source(good)], [], geometry())

    assert asyncio.run(session.snapshot()).image == b"ok"


def test_all_sources_fail_with_typed_error():
    session = DeviceSession([Source(error=RuntimeError("x"))], [], geometry())

    with pytest.raises(DeviceSessionError):
        asyncio.run(session.snapshot())


def test_control_selection_is_capability_based():
    abs_sink, rel_sink = Sink(absolute=True), Sink(relative=True)
    session = DeviceSession([], [rel_sink, abs_sink], geometry())

    assert session.control_for(absolute=True) is abs_sink
    assert session.control_for(absolute=False) is rel_sink


def test_missing_control_raises():
    session = DeviceSession([], [], geometry())

    with pytest.raises(DeviceSessionError):
        session.control_for(absolute=False)


def test_capabilities_union_across_sources_and_sinks():
    session = DeviceSession(
        [Source()],
        [Sink(relative=True)],
        geometry(),
    )
    assert session.capabilities.supports_ocr is True
    assert session.capabilities.supports_relative_pointer is True
    assert session.capabilities.supports_absolute_tap is False


def test_session_lifecycle_is_idempotent_and_closed_sessions_refuse_work():
    from artemis.devices.session import SessionClosedError, SessionState

    session = DeviceSession([], [], geometry())
    session.open()
    session.open()
    assert session.state is SessionState.READY

    asyncio.run(session.close())
    asyncio.run(session.close())
    assert session.state is SessionState.CLOSED

    with pytest.raises(SessionClosedError):
        asyncio.run(session.snapshot())
    with pytest.raises(SessionClosedError):
        session.control_for(absolute=False)


def test_close_releases_inputs_handles_then_leases():
    events: list[str] = []

    class ClosingSink(Sink):
        def close(self):
            events.append("control")

    class ClosingSource(Source):
        def close(self):
            events.append("perception")

    class Leases:
        def release(self):
            events.append("leases")

    session = DeviceSession(
        [ClosingSource()],
        [ClosingSink()],
        geometry(),
        leases=Leases(),
    )
    asyncio.run(session.close())

    assert events == ["control", "perception", "leases"]


def test_close_continues_after_a_failing_participant():
    events: list[str] = []

    class BrokenSink(Sink):
        def close(self):
            raise RuntimeError("boom")

    class ClosingSource(Source):
        def close(self):
            events.append("perception")

    class Leases:
        def release(self):
            events.append("leases")

    session = DeviceSession(
        [ClosingSource()],
        [BrokenSink()],
        geometry(),
        leases=Leases(),
    )
    asyncio.run(session.close())

    assert events == ["perception", "leases"]
    assert session.state.value == "closed"


def test_pointer_is_a_read_only_adapter_state_view():
    pointer = object()
    session = DeviceSession([], [], geometry(), adapter_state={"pointer": pointer})

    assert session.pointer is pointer
    with pytest.raises(AttributeError):
        session.pointer = None  # type: ignore[misc]


def test_session_capability_view_is_declaration_only_without_evidence():
    from artemis.core.capabilities import CapabilityId

    class OcrSource:
        capabilities = CapabilitySet(supports_ocr=True, supports_app_launch=False)

    class PointerSink:
        capabilities = CapabilitySet(supports_relative_pointer=True, supports_app_launch=False)

    session = DeviceSession([OcrSource()], [PointerSink()], geometry())
    view = session.capability_view

    assert view.supports(CapabilityId.OCR_READ) is True
    assert view.supports(CapabilityId.OCR_READ, for_execution=True) is False
    assert view.supports(CapabilityId.POINTER_RELATIVE) is True
    assert view.supports(CapabilityId.TOUCH_ABSOLUTE) is False


def test_session_capability_view_can_use_evidence_and_probes():
    from dataclasses import replace

    from artemis.core.capabilities import (
        CapabilityEvidence,
        CapabilityId,
        capability_fingerprint,
        fingerprint_fields,
    )
    from artemis.core.capability_registry import CapabilityRegistry, ProbeResult
    from artemis.devices.descriptors import build_mock_descriptor

    class PointerSink:
        capabilities = CapabilitySet(supports_relative_pointer=True, supports_app_launch=False)

    fields = fingerprint_fields(
        platform_family="mock", adapter_id="PointerSink", adapter_version="1"
    )
    descriptor = replace(
        build_mock_descriptor(device_id="mock-device", screen_size=(1080, 2400)),
        fingerprint=fields,
    )
    evidence = CapabilityEvidence(
        evidence_id="ev-pointer",
        capability_id=CapabilityId.POINTER_RELATIVE,
        conclusion="verified",
        device_id=descriptor.device_id,
        endpoint_ids=descriptor.endpoint_ids,
        fingerprint=capability_fingerprint(fields),
        fingerprint_fields=fields,
        test_id="test.pointer",
        test_version="1",
        evidence_kind="smoke",
        executed_at=100.0,
        artifact_refs=("a" * 64,),
    )

    class Store:
        def record(self, evidence):
            raise AssertionError("not used")

        def lookup(self, **kwargs):
            return evidence

    session = DeviceSession(
        [],
        [PointerSink()],
        geometry(),
        descriptor=descriptor,
        capability_registry=CapabilityRegistry(Store()),
        capability_probes={CapabilityId.POINTER_RELATIVE: ProbeResult(True, "test_probe")},
    )

    record = session.capability_view[CapabilityId.POINTER_RELATIVE]
    assert record.state == "verified"
    assert record.evidence_ref == "ev-pointer"
