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

    assert events == ["perception"]
    assert session.state.value == "closed"
    assert session.cleanup_state.value == "retryable"
    assert session.leases is not None


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


def test_a_target_from_another_frame_is_rejected_as_stale():
    from artemis.perception.base import Target

    session = DeviceSession([], [], geometry())
    session.last_frame_id = "frame-new"

    fresh = Target("设置", (0.1, 0.1, 0.2, 0.2), 1.0, "ocr", frame_id="frame-new")
    stale = Target("设置", (0.1, 0.1, 0.2, 0.2), 1.0, "ocr", frame_id="frame-old")

    assert session.validate_target(fresh) is fresh
    with pytest.raises(DeviceSessionError, match="stale_frame"):
        session.validate_target(stale)


def test_a_failed_session_is_terminal_and_cannot_serve_requests():
    from artemis.devices.session import SessionState

    session = DeviceSession([Source()], [Sink(absolute=True)], geometry())
    session.mark_failed("adapter lost")

    assert session.state is SessionState.FAILED
    with pytest.raises(DeviceSessionError, match="terminal"):
        session.open()
    with pytest.raises(DeviceSessionError, match="failed"):
        asyncio.run(session.snapshot())
    with pytest.raises(DeviceSessionError, match="failed"):
        session.control_for(absolute=True)
    with pytest.raises(DeviceSessionError, match="failed"):
        asyncio.run(session.probe_capabilities())


def test_close_finishes_handles_and_leases_when_a_closer_is_cancelled():
    events: list[str] = []

    class SlowSink(Sink):
        async def aclose(self):
            events.append("control-start")
            await asyncio.sleep(0.05)
            events.append("control-end")

    class ClosingSource(Source):
        async def aclose(self):
            events.append("perception")

    class Leases:
        def release(self):
            events.append("leases")

    session = DeviceSession(
        [ClosingSource()],
        [SlowSink(absolute=True)],
        geometry(),
        leases=Leases(),
    )

    async def scenario():
        task = asyncio.create_task(session.close())
        while "control-start" not in events:
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    assert events == ["control-start", "control-end", "perception", "leases"]
    assert session.state.value == "closed"
    assert session.leases is None


def test_close_continues_after_an_arbitrary_adapter_exception():
    events: list[str] = []

    class TypeErrorSink(Sink):
        def close(self):
            raise TypeError("vendor adapter bug")

    class ClosingSource(Source):
        def close(self):
            events.append("perception")

    class Leases:
        def release(self):
            events.append("leases")

    session = DeviceSession(
        [ClosingSource()],
        [TypeErrorSink(absolute=True)],
        geometry(),
        leases=Leases(),
    )

    asyncio.run(session.close())

    assert events == ["perception"]
    assert session.state.value == "closed"
    assert session.cleanup_state.value == "retryable"
    assert session.leases is not None


def test_close_swallows_an_arbitrary_lease_exception_and_marks_closed():
    events: list[str] = []

    class ClosingSource(Source):
        def close(self):
            events.append("perception")

    class BrokenLeases:
        def release(self):
            events.append("leases")
            raise TypeError("vendor lease bug")

    session = DeviceSession(
        [ClosingSource()],
        [],
        geometry(),
        leases=BrokenLeases(),
    )

    asyncio.run(session.close())

    assert events == ["perception", "leases"]
    assert session.leases is not None
    assert session.state.value == "closed"
    assert session.cleanup_state.value == "retryable"


def test_close_finishes_cleanup_when_both_adapter_and_lease_raise():
    events: list[str] = []

    class TypeErrorSink(Sink):
        def close(self):
            events.append("control")
            raise TypeError("vendor adapter bug")

    class BrokenLeases:
        def release(self):
            events.append("leases")
            raise TypeError("vendor lease bug")

    session = DeviceSession([], [TypeErrorSink(absolute=True)], geometry(), leases=BrokenLeases())

    asyncio.run(session.close())

    assert events == ["control"]
    assert session.state.value == "closed"
    assert session.cleanup_state.value == "retryable"
    assert session.leases is not None


def test_close_preserves_cancellation_from_a_lease_release():
    class CancelledLeases:
        def release(self):
            raise asyncio.CancelledError()

    session = DeviceSession([], [], geometry(), leases=CancelledLeases())

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(session.close())

    assert session.leases is not None
    assert session.state.value == "closed"
    assert session.cleanup_state.value == "retryable"


def test_close_reports_failure_and_retry_releases_leases():
    events: list[str] = []

    class FailingSink(Sink):
        def __init__(self):
            super().__init__(absolute=True)
            self.fail = True

        def close(self):
            events.append("control")
            if self.fail:
                raise RuntimeError("still open")

    class Leases:
        def __init__(self):
            self.released = 0
            self.resource_ids = ("uvc:host:one/capture",)

        def release_with_report(self):
            from artemis.runtime.resource_lease import LeaseReleaseReport

            self.released += 1
            self.resource_ids = ()
            events.append("leases")
            return LeaseReleaseReport(released=("uvc:host:one/capture",))

    sink = FailingSink()
    leases = Leases()
    session = DeviceSession([], [sink], geometry(), leases=leases)

    first = asyncio.run(session.close())
    assert first.cleanup_complete is False
    assert first.retryable is True
    assert first.retained == ("uvc:host:one/capture",)
    assert events == ["control"]
    assert leases.released == 0

    sink.fail = False
    second = asyncio.run(session.retry_cleanup())
    assert second.cleanup_complete is True
    assert session.cleanup_state.value == "complete"
    assert session.leases is None
    assert events == ["control", "control", "leases"]
    assert leases.released == 1
    assert asyncio.run(session.close()) is not None


def test_external_cleanup_owner_runs_before_hardware_and_blocks_leases_on_failure():
    events: list[str] = []

    class Owner:
        def __init__(self):
            self.fail = False

        async def close(self):
            events.append("action")
            if self.fail:
                raise RuntimeError("action still open")

    class LocalSink(Sink):
        def close(self):
            events.append("control")

    class Leases:
        resource_ids = ("uvc:host:one/capture",)

        def release_with_report(self):
            from artemis.runtime.resource_lease import LeaseReleaseReport

            events.append("leases")
            self.resource_ids = ()
            return LeaseReleaseReport(released=("uvc:host:one/capture",))

    owner = Owner()
    owner.fail = True
    leases = Leases()
    session = DeviceSession([], [LocalSink(absolute=True)], geometry(), leases=leases)
    session.register_cleanup_owner("action", owner.close)

    first = asyncio.run(session.close())
    assert events == ["action", "control"]
    assert first.retryable is True
    assert first.retained == ("uvc:host:one/capture",)

    owner.fail = False
    second = asyncio.run(session.retry_cleanup())
    assert events == ["action", "control", "action", "leases"]
    assert second.cleanup_complete is True


def test_fatal_device_fault_marks_the_session_failed():
    from artemis.core.errors import DeviceDisconnectedError

    class LostSource(Source):
        async def snapshot(self):
            raise DeviceDisconnectedError("uvc unplugged", source="uvc")

    session = DeviceSession([LostSource()], [], geometry())
    session.open()

    with pytest.raises(DeviceDisconnectedError):
        asyncio.run(session.snapshot())

    assert session.state.value == "failed"
    assert session.fault_source == "uvc"
    assert "device_disconnected" in (session.fault_reason or "")


def test_non_fatal_source_error_does_not_fail_the_session():
    session = DeviceSession([Source(error=RuntimeError("ocr parse"))], [], geometry())
    session.open()

    with pytest.raises(DeviceSessionError):
        asyncio.run(session.snapshot())

    assert session.state.value == "ready"


def test_repeated_unhealthy_frames_fail_the_session():
    bad = Frame(b"black", 1.0, 10, 10, "uvc", health="black")
    session = DeviceSession([Source(bad)], [], geometry())
    session.open()

    for _ in range(2):
        with pytest.raises(DeviceSessionError):
            asyncio.run(session.snapshot())
        assert session.state.value == "ready"

    with pytest.raises(DeviceSessionError):
        asyncio.run(session.snapshot())
    assert session.state.value == "failed"
    assert session.fault_source == "perception"


def test_cancelled_close_keeps_partial_progress_in_the_report():
    events: list[str] = []

    class SlowSink(Sink):
        async def aclose(self):
            events.append("control-start")
            await asyncio.sleep(0.05)
            events.append("control-end")

    class ClosingSource(Source):
        async def aclose(self):
            events.append("perception")

    class Leases:
        def release_with_report(self):
            from artemis.runtime.resource_lease import LeaseReleaseReport

            events.append("leases")
            return LeaseReleaseReport(released=("uvc:host:one/capture",))

    session = DeviceSession(
        [ClosingSource()],
        [SlowSink(absolute=True)],
        geometry(),
        leases=Leases(),
    )

    async def scenario():
        task = asyncio.create_task(session.close())
        while "control-start" not in events:
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    report = session.last_close_report
    assert report is not None
    assert report.closed_participants_now == ("0:SlowSink", "1:ClosingSource")
    assert report.released_now == ("uvc:host:one/capture",)
    assert report.cleanup_complete is True


def test_cancelled_retry_keeps_retryable_report():
    class FlakySink(Sink):
        def __init__(self):
            super().__init__(absolute=True)
            self.fail = True

        def close(self):
            if self.fail:
                raise RuntimeError("not yet")
            raise asyncio.CancelledError()

    session = DeviceSession([], [FlakySink()], geometry())
    first = asyncio.run(session.close())
    assert first.retryable is True
    assert session.cleanup_state.value == "retryable"

    session.control[0].fail = False
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(session.retry_cleanup())

    assert session.cleanup_state.value == "retryable"
    assert session.last_close_report is not None
    assert session.last_close_report.cleanup_state.value == "retryable"


def test_processing_errors_do_not_fail_the_session():
    class OcrSource(Source):
        async def snapshot(self):
            raise RuntimeError("OCR service unavailable")

    session = DeviceSession([OcrSource()], [], geometry())
    session.open()

    from artemis.core.errors import ObservationProcessingError

    for _ in range(3):
        with pytest.raises(ObservationProcessingError):
            asyncio.run(session.snapshot())
        assert session.state.value == "ready"
    assert session.fault_source is None


def test_xml_parse_errors_are_not_device_disconnects():
    class XmlSource(Source):
        async def snapshot(self):
            raise ValueError("XML parse failed: malformed hierarchy")

    session = DeviceSession([XmlSource()], [], geometry())
    session.open()

    from artemis.core.errors import ObservationProcessingError

    for _ in range(3):
        with pytest.raises(ObservationProcessingError):
            asyncio.run(session.snapshot())
        assert session.state.value == "ready"
    assert session.fault_source is None


def test_a_fatal_source_fault_fails_even_if_another_source_is_healthy():
    from artemis.core.errors import DeviceDisconnectedError

    class LostSource(Source):
        async def snapshot(self):
            raise DeviceDisconnectedError("usb gone", source="adapter")

    good = Frame(b"ok", 2.0, 10, 10, "good")
    session = DeviceSession([LostSource(), Source(good)], [], geometry())
    session.open()

    with pytest.raises(DeviceDisconnectedError):
        asyncio.run(session.snapshot())

    assert session.state.value == "failed"
    assert session.fault_source == "adapter"


def test_black_frames_report_capture_unhealthy_fault_kind():
    from artemis.core.errors import DeviceFaultKind

    bad = Frame(b"black", 1.0, 10, 10, "uvc", health="black")
    session = DeviceSession([Source(bad)], [], geometry())
    session.open()

    for _ in range(3):
        with pytest.raises(DeviceSessionError):
            asyncio.run(session.snapshot())

    assert session.state.value == "failed"
    assert session.fault_source == "perception"
    assert session.fault_reason is not None
    assert DeviceFaultKind.CAPTURE_UNHEALTHY.value in session.fault_reason


def test_a_processing_failure_in_one_source_uses_the_healthy_source():
    good = Frame(b"ok", 2.0, 10, 10, "good")
    session = DeviceSession(
        [Source(error=RuntimeError("OCR parse failed")), Source(good)],
        [],
        geometry(),
    )

    frame = asyncio.run(session.snapshot())
    assert frame.image == b"ok"
    assert session.state.value == "ready"


def test_a_black_source_downshifts_to_the_healthy_source():
    bad = Frame(b"black", 1.0, 10, 10, "uvc", health="black")
    good = Frame(b"ok", 2.0, 10, 10, "camera")
    session = DeviceSession([Source(bad), Source(good)], [], geometry())

    frame = asyncio.run(session.snapshot())
    assert frame.image == b"ok"
    assert session.state.value == "ready"


def test_runtime_id_is_uuid_even_without_an_explicit_session_id():
    session = DeviceSession([], [], geometry())
    runtime_id = session.runtime_id

    assert len(runtime_id) == 32
    assert session.runtime_id == runtime_id
    report = asyncio.run(session.close())
    assert report.session_id == runtime_id


def test_retry_report_separates_current_and_previous_errors():
    class FlakySink(Sink):
        def __init__(self):
            super().__init__(absolute=True)
            self.fail = True

        def close(self):
            if self.fail:
                raise ValueError("old failure")

    sink = FlakySink()
    session = DeviceSession([], [sink], geometry())
    first = asyncio.run(session.close())
    assert any("old failure" in error for error in first.errors)

    sink.fail = False
    second = asyncio.run(session.retry_cleanup())

    assert second.cleanup_complete is True
    assert second.errors == ()
    assert any("old failure" in error for error in second.previous_errors)


def test_unexpected_external_owner_error_still_produces_a_retryable_report():
    class Owner:
        async def close(self):
            raise TypeError("bad owner")

    session = DeviceSession([], [], geometry())
    session.register_cleanup_owner("action", Owner().close)

    report = asyncio.run(session.close())

    assert report.cleanup_complete is False
    assert report.retryable is True
    assert any("bad owner" in error for error in report.errors)
