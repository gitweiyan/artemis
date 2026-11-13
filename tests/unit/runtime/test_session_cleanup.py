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
