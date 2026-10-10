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

"""I4 · a lens that cannot exist must not be retried per step.

The 2026-10-10 pair of runs spent 4 attempts on each of 20 steps (76 failed
dispatches, 20 "Giving up" lines) to re-learn one fact that was already decided
when the lens could not be built: a configuration error is not a transient
failure. The bounded retry stays exactly as it was for transient failures.
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import Mock

import pytest

from artemis.memory.step_memory import StepMemoryService


class _CountingService(StepMemoryService):
    """A service with a scripted attempt and a status log."""

    #: Set by a subclass to answer "could another attempt change anything?".
    can_succeed = True

    def __init__(self, **kwargs):
        super().__init__(Mock(), **kwargs)
        self.attempts = 0
        self.status_events: list[tuple] = []
        self._retry_delays = (0.0,)

    async def _attempt(self, key) -> bool:
        self.attempts += 1
        return False

    def _on_status(self, key, status) -> None:
        self.status_events.append((key, status))

    def job_can_succeed(self, key) -> bool:
        return self.can_succeed


class _LenslessService(_CountingService):
    """A service whose lens could not be built on this run at all."""

    can_succeed = False


def _service(cls, **kwargs) -> StepMemoryService:
    service = cls(retry_limit=3, **kwargs)
    service._step_inputs["step:1"] = {"step_number": 1}
    service._step_inputs["step:2"] = {"step_number": 2}
    return service


@pytest.mark.asyncio
async def test_a_job_that_cannot_succeed_is_attempted_once(caplog):
    service = _service(_LenslessService)

    with caplog.at_level(logging.INFO):
        await service._run_attempts("step:1")

    assert service.attempts == 1, "an impossible job must not be retried"
    assert service.status_events == [("step:1", "failed")]
    assert "Giving up on Step" not in caplog.text
    assert "Retrying Step" not in caplog.text


@pytest.mark.asyncio
async def test_the_impossible_lens_is_reported_once_per_run(caplog):
    """One configuration error, one line — not one line per step."""
    service = _service(_LenslessService)

    with caplog.at_level(logging.WARNING):
        await service._run_attempts("step:1")
        await service._run_attempts("step:2")

    warnings = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1, warnings
    assert "cannot produce a summary" in warnings[0]


@pytest.mark.asyncio
async def test_a_transient_failure_keeps_the_bounded_retry(caplog):
    """The short-circuit must not swallow the retry the service was built for."""
    service = _service(_CountingService)

    with caplog.at_level(logging.WARNING):
        await service._run_attempts("step:1")

    assert service.attempts == 4, "1 + retry_limit attempts, as before"
    assert "Giving up on Step 1" in caplog.text
    assert service.status_events == [("step:1", "failed")]


@pytest.mark.asyncio
async def test_a_job_that_succeeds_never_reaches_the_short_circuit():
    class _Succeeding(_LenslessService):
        async def _attempt(self, key) -> bool:
            self.attempts += 1
            self._summaries[key] = "summary"
            return True

    service = _service(_Succeeding)

    await service._run_attempts("step:1")

    assert service.attempts == 1
    assert service.has_summary("step:1")
