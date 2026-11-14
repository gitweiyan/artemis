"""Host Agent owns a bounded Session cleanup recovery pass."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from third_party.mobile_use.sdk.agent import AgentBase


@pytest.mark.asyncio
async def test_host_agent_runs_pending_cleanup_recovery():
    registry = SimpleNamespace(retry_pending=AsyncMock(return_value=[]))
    agent = object.__new__(AgentBase)
    agent._cleanup_registry = registry

    await agent._recover_pending_sessions()

    registry.retry_pending.assert_awaited_once_with(max_attempts=1, timeout_s=5.0)


@pytest.mark.asyncio
async def test_host_agent_recovers_a_real_session_after_a_cleanup_failure():
    from artemis.core.capabilities import CapabilitySet
    from artemis.devices.geometry import ScreenGeometry
    from artemis.devices.session import DeviceSession
    from artemis.runtime.session_cleanup import SessionCleanupRegistry

    class Sink:
        capabilities = CapabilitySet(supports_absolute_tap=True)

        def __init__(self):
            self.fail = True

        def close(self):
            if self.fail:
                raise ValueError("adapter still open")

    sink = Sink()
    session = DeviceSession([], [sink], ScreenGeometry((10, 20), (10, 20)))
    report = await session.close()
    assert report.cleanup_complete is False
    registry = SessionCleanupRegistry()
    registry.register(session)

    sink.fail = False
    agent = object.__new__(AgentBase)
    agent._cleanup_registry = registry
    await agent._recover_pending_sessions()

    assert session.cleanup_state.value == "complete"
    assert registry.pending() == []
