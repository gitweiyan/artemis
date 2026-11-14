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
