"""I3 wiring: a lens that cannot run is counted on the run, not just logged.

The 18:32 run on 2026-10-09 produced eight of these and its conclusion still said
``passed=2 / failed=0``; this is the call site that decides whether the verdict
learns about it.
"""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from artemis.agents.summarizer.summarizer import SummarizerNode
from artemis.context import ArtemisContext
from artemis.graph.state import State
from artemis.runtime import degradations as dg


@pytest.fixture
def ctx():
    context = Mock(spec=ArtemisContext)
    context.data_engine = Mock()
    return context


@pytest.mark.asyncio
async def test_a_failed_visual_summary_is_counted_on_the_run(ctx):
    node = SummarizerNode(ctx)
    state = State(initial_goal="Open Settings", current_step_id="step-1")

    with patch.object(
        SummarizerNode, "_dispatch_visual_summary", side_effect=RuntimeError("lens down")
    ):
        result = await node(state)

    assert result == {}
    summary = dg.log_for(ctx).summary()
    assert summary["counts"].get(dg.VISUAL_SUMMARY_FAILURE) == 1
    assert "lens down" in summary["examples"][dg.VISUAL_SUMMARY_FAILURE]
    assert summary["capability_degraded"] is True


@pytest.mark.asyncio
async def test_a_clean_summary_records_nothing(ctx):
    node = SummarizerNode(ctx)
    state = State(initial_goal="Open Settings", current_step_id="step-1")

    with patch.object(SummarizerNode, "_dispatch_visual_summary"):
        await node(state)

    assert dg.log_for(ctx).summary()["total"] == 0
