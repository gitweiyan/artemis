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

"""A recorded action can be replayed; a reproduced failure is a successful replay."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from artemis.runtime.action_replay import (
    ReplayPlan,
    compare_evidence,
    plan_from_step,
    replay_plan,
)

STEP = {
    "step": 4,
    "last_execution_result": {
        "status": "failed",
        "execution": [
            {
                "action": "tap",
                "coordinates": [540, 1200],
                "incident": "pointer_missing",
                "action_metrics": {
                    "target_x": 540.0,
                    "target_y": 1200.0,
                    "pointer_x": 120.0,
                    "pointer_y": 900.0,
                    "frame_age_s": 0.9,
                },
                "action_result": {"ok": False, "incident": "pointer_missing"},
            }
        ],
    },
}


def test_a_plan_comes_from_the_recorded_action_and_its_evidence():
    plan = plan_from_step(STEP)

    assert plan is not None
    assert plan.action == "tap"
    assert plan.target_px == (540, 1200)
    assert plan.incident == "pointer_missing"
    assert plan.expected["pointer_x"] == 120.0


def test_a_step_without_coordinates_has_no_plan():
    assert plan_from_step({"last_execution_result": {"execution": [{"action": "tap"}]}}) is None


def test_a_reproduced_failure_is_a_successful_replay():
    plan = plan_from_step(STEP)

    verdict = compare_evidence(
        plan,
        {"pointer_x": 130.0, "pointer_y": 905.0},
        incident="pointer_missing",
        ok=False,
    )

    assert verdict.ok is True
    assert verdict.reproduced is True
    assert verdict.drift_px is not None and verdict.drift_px < 15.0
    assert "reproduced" in verdict.note


def test_a_different_incident_is_the_useful_surprise():
    plan = plan_from_step(STEP)

    verdict = compare_evidence(plan, {}, incident="no_effect", ok=False)

    assert verdict.ok is False
    assert verdict.reproduced is False
    assert "did not reproduce" in verdict.note


def test_a_step_that_used_to_succeed_now_failing_is_not_reproduced():
    plan = ReplayPlan(action="tap", target_px=(10, 10), expected={}, incident=None, ok=True)

    verdict = compare_evidence(plan, {}, incident="pointer_missing", ok=False)

    assert verdict.ok is False
    assert "recorded as successful" in verdict.note


def test_success_reproducing_success_reports_pointer_drift():
    plan = ReplayPlan(
        action="tap",
        target_px=(540, 1200),
        expected={"pointer_x": 500.0, "pointer_y": 500.0},
    )

    verdict = compare_evidence(
        plan, {"pointer_x": 560.0, "pointer_y": 500.0}, incident=None, ok=True
    )

    assert verdict.ok is True
    assert verdict.drift_px == 60.0
    assert "landed" in verdict.note


class FakeDriver:
    def __init__(self, *, ok=True, incident=None, metrics=None):
        self.ok = ok
        self.incident = incident
        self.metrics = metrics or {}
        self.taps = []

    async def tap_with_result(self, x, y):
        self.taps.append((x, y))
        return SimpleNamespace(ok=self.ok, incident=self.incident, metrics=self.metrics)


def test_a_plan_is_dispatched_through_the_driver_and_compared():
    plan = plan_from_step(STEP)
    driver = FakeDriver(
        ok=False, incident="pointer_missing", metrics={"pointer_x": 118.0, "pointer_y": 902.0}
    )

    verdict = asyncio.run(replay_plan(driver, plan))

    assert driver.taps == [(540, 1200)]
    assert verdict.ok is True, "the recorded failure reproduced"
    assert verdict.reproduced is True


def test_a_driver_without_structured_results_still_replays():
    class PlainDriver:
        def __init__(self):
            self.calls = []

        async def tap(self, x, y):
            self.calls.append((x, y))
            return True

    plan = ReplayPlan(action="tap", target_px=(5, 6), expected={})
    driver = PlainDriver()

    verdict = asyncio.run(replay_plan(driver, plan))

    assert driver.calls == [(5, 6)]
    assert verdict.ok is True


def test_a_plan_without_coordinates_is_refused():
    plan = ReplayPlan(action="tap", target_px=None)

    verdict = asyncio.run(replay_plan(FakeDriver(), plan))

    assert verdict.ok is False
    assert "no coordinates" in verdict.note
