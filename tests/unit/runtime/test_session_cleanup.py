"""Session cleanup registry retains incomplete sessions until retry."""

from __future__ import annotations

import pytest

from artemis.runtime.session_cleanup import SessionCleanupRegistry


class _Session:
    session_id = "session-1"

    def __init__(self):
        self.retries = 0
        self.cleanup_complete = False

    async def retry_cleanup(self):
        self.retries += 1
        self.cleanup_complete = True
        return type(
            "Report",
            (),
            {"cleanup_complete": True},
        )()


@pytest.mark.asyncio
async def test_registry_retains_and_retries_a_session():
    session = _Session()
    registry = SessionCleanupRegistry()
    registry.register(session)

    report = await registry.retry("session-1")

    assert report.cleanup_complete is True
    assert session.retries == 1
    assert registry.get_report("session-1") is report


@pytest.mark.asyncio
async def test_retry_pending_completes_and_removes_a_recovered_session():
    class Report:
        cleanup_complete = True
        cleanup_state = type("State", (), {"value": "complete"})()
        session_id = "runtime-1"
        retained = ()

    class Session:
        runtime_id = "runtime-1"
        last_close_report = type(
            "Report",
            (),
            {
                "cleanup_complete": False,
                "cleanup_state": type("State", (), {"value": "retryable"})(),
            },
        )()

        async def retry_cleanup(self):
            return Report()

    registry = SessionCleanupRegistry()
    registry.register(Session())

    reports = await registry.retry_pending(max_attempts=1)

    assert len(reports) == 1
    assert reports[0].cleanup_complete is True
    assert registry.pending() == []


@pytest.mark.asyncio
async def test_retry_pending_marks_an_unrecoverable_session_manual_required():
    class Report:
        cleanup_complete = False
        cleanup_state = type("State", (), {"value": "retryable"})()
        session_id = "runtime-2"
        retained = ("uvc:host:one/capture",)

    class Session:
        runtime_id = "runtime-2"
        last_close_report = Report()

        async def retry_cleanup(self):
            return Report()

        def mark_manual_required(self, reason):
            report = Report()
            report.cleanup_state = type("State", (), {"value": "manual_required"})()
            return report

    registry = SessionCleanupRegistry()
    registry.register(Session())

    reports = await registry.retry_pending(max_attempts=1)

    assert reports[0].cleanup_state.value == "manual_required"


@pytest.mark.asyncio
async def test_real_context_session_and_lease_retry_closure(tmp_path, monkeypatch):
    from artemis.context import ArtemisContext, DeviceContext
    from artemis.core.capabilities import CapabilitySet
    from artemis.devices.geometry import ScreenGeometry
    from artemis.devices.session import DeviceSession
    from artemis.runtime import lease_primitives
    from artemis.runtime.resource_lease import LeaseSet

    class Sink:
        capabilities = CapabilitySet(supports_absolute_tap=True)

        def __init__(self):
            self.closes = 0

        def close(self):
            self.closes += 1

    leases = LeaseSet(host_id="host", session_id="lease-session", lease_dir=tmp_path)
    leases.acquire(["uvc:host:one/capture"])
    sink = Sink()
    session = DeviceSession(
        [],
        [sink],
        ScreenGeometry((10, 20), (10, 20)),
        leases=leases,
    )
    registry = SessionCleanupRegistry()
    context = ArtemisContext(
        device=DeviceContext(device_id="dummy"),
        device_session=session,
        cleanup_registry=registry,
    )
    original = lease_primitives.safe_unlink
    monkeypatch.setattr(lease_primitives, "safe_unlink", lambda _path: False)

    await context.__aexit__(None, None, None)

    assert context.device_session is None
    assert registry.pending()
    assert registry.pending()[0].cleanup_complete is False
    assert leases.resource_ids == ("uvc:host:one/capture",)
    assert sink.closes == 1

    monkeypatch.setattr(lease_primitives, "safe_unlink", original)
    reports = await registry.retry_pending(max_attempts=2)

    assert reports[-1].cleanup_complete is True
    assert leases.resource_ids == ()
    assert session.leases is None
    assert sink.closes == 1, "successful adapters must not be closed twice"

    await session.close()
    assert sink.closes == 1
