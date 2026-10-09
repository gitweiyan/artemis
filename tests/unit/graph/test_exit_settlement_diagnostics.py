# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""A failed final review must record WHY it failed.

The 2026-10-08 run wrote ``"evidence": "final check error: "`` — an empty reason,
because ``str(TimeoutError())`` is ``""``. The ledger and the UI could not tell
"the Checker timed out after 180s" apart from "the Checker crashed", so the
incident stayed opaque until the rejected request was dug out of the DataEngine
by hand. Same defect as upstream PR #19, kept out of the release path by
fail-open releasing the run with four ``inconclusive`` verdicts.
"""

from unittest.mock import MagicMock, patch

import pytest

from artemis.context import ArtemisContext
from artemis.graph.checkpoints import read_ledger
from artemis.graph.graph import exit_settlement_node
from artemis.graph.state import State

PLAN = """# Test Plan
- [x] Milestone 1
  - verify: Verify item 1
  - assert: The device shows the expected value
"""


def _ctx(tmp_path):
    ctx = MagicMock(spec=ArtemisContext)
    ctx.data_engine = MagicMock()
    ctx.data_engine.base_dir = str(tmp_path)
    ctx.execution_setup = MagicMock()
    ctx.execution_setup.checkpoint_timeout = 180.0
    ctx.execution_setup.final_check_enabled = True
    ctx.execution_setup.settlement_timeout = 5.0
    ctx.pending_checkpoints = []
    ctx.checkpoint_tasks = {}
    ctx.final_check_attempts = 0
    notes = tmp_path / "notes"
    notes.mkdir(parents=True, exist_ok=True)
    (notes / "task_plan.md").write_text(PLAN, encoding="utf-8")
    return ctx


def _final_records(ctx):
    return [r for r in read_ledger(ctx.data_engine.base_dir) if r["checkpoint_id"] == "final"]


@pytest.mark.asyncio
async def test_timeout_records_the_deadline(tmp_path):
    ctx = _ctx(tmp_path)

    async def _timeout(*args, **kwargs):
        raise TimeoutError()

    with patch("artemis.graph.graph.run_final_check", side_effect=_timeout):
        await exit_settlement_node(State(initial_goal="goal"), ctx)

    records = _final_records(ctx)
    assert records
    assert all(r["status"] == "inconclusive" for r in records)
    assert all("timed out after 180.0s" in r["evidence"] for r in records)


@pytest.mark.asyncio
async def test_an_exception_without_a_message_records_its_type(tmp_path):
    ctx = _ctx(tmp_path)

    class _SilentError(Exception):
        def __str__(self):
            return ""

    async def _silent(*args, **kwargs):
        raise _SilentError()

    with patch("artemis.graph.graph.run_final_check", side_effect=_silent):
        await exit_settlement_node(State(initial_goal="goal"), ctx)

    records = _final_records(ctx)
    assert records
    assert all("_SilentError" in r["evidence"] for r in records)


@pytest.mark.asyncio
async def test_an_exception_with_a_message_keeps_it(tmp_path):
    ctx = _ctx(tmp_path)

    async def _boom(*args, **kwargs):
        raise RuntimeError("checker tool loop aborted")

    with patch("artemis.graph.graph.run_final_check", side_effect=_boom):
        await exit_settlement_node(State(initial_goal="goal"), ctx)

    records = _final_records(ctx)
    assert records
    assert all("checker tool loop aborted" in r["evidence"] for r in records)
