"""Replay one recorded device action and compare its evidence.

"Every failure must be replayable" is an acceptance criterion. A replay here means: take
the *device action* a recorded step dispatched, dispatch the same one again, and compare the
structured evidence the driver reports now with the evidence it reported then. It does not
re-run the model — the interesting question after a failure is "does the device still do
this?", not "would the model decide the same?".

Two outcomes are both "reproduced":

* the action succeeds now, exactly as it succeeded then;
* the action fails now with the *same incident* it failed with then (that is a bug you can
  keep chasing);

A replay that fails with a *different* incident is the useful surprise: the device's
behaviour moved.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping

__all__ = [
    "ReplayPlan",
    "ReplayVerdict",
    "compare_evidence",
    "plan_from_step",
    "replay_plan",
]

#: Metrics compared as positions (0–1000 normalized pointer coordinates).
_POINTER_METRICS = ("pointer_x", "pointer_y")


@dataclass(frozen=True)
class ReplayPlan:
    """What a recorded step asked the device to do, and what it observed."""

    action: str
    target_px: tuple[int, int] | None
    expected: Mapping[str, float | str] = field(default_factory=dict)
    incident: str | None = None
    ok: bool = True


@dataclass(frozen=True)
class ReplayVerdict:
    """Whether the replayed action reproduced the recorded behaviour."""

    ok: bool
    reproduced: bool
    drift_px: float | None = None
    incident: str | None = None
    note: str = ""


def _action_items(step: Mapping[str, Any]) -> list[dict]:
    """Every action record a step may carry (DataEngine step, trace step, bare item)."""
    items: list[dict] = []
    result = step.get("last_execution_result")
    if isinstance(result, Mapping):
        for entry in result.get("execution") or []:
            if isinstance(entry, dict):
                items.append(entry)
    for key in ("action_taken", "actions", "execution"):
        value = step.get(key)
        if isinstance(value, list):
            items.extend(entry for entry in value if isinstance(entry, dict))
        elif isinstance(value, dict):
            items.append(value)
    if not items and ("action" in step or "action_metrics" in step):
        items.append(dict(step))
    return items


def plan_from_step(step: Mapping[str, Any]) -> ReplayPlan | None:
    """The first action in ``step`` that has coordinates worth replaying."""
    for item in _action_items(step):
        metrics = item.get("action_metrics")
        if not isinstance(metrics, Mapping):
            metrics = (item.get("action_result") or {}).get("metrics")
        metrics = dict(metrics or {})
        coordinates = item.get("coordinates")
        if isinstance(coordinates, (list, tuple)) and len(coordinates) >= 2:
            target = (int(coordinates[0]), int(coordinates[1]))
        elif "target_x" in metrics and "target_y" in metrics:
            target = (int(float(metrics["target_x"])), int(float(metrics["target_y"])))
        else:
            continue
        incident = item.get("incident") or (item.get("action_result") or {}).get("incident")
        return ReplayPlan(
            action=str(item.get("action") or "tap"),
            target_px=target,
            expected=metrics,
            incident=incident,
            ok=incident is None,
        )
    return None


def _pointer(metrics: Mapping[str, Any]) -> tuple[float, float] | None:
    try:
        return float(metrics["pointer_x"]), float(metrics["pointer_y"])
    except (KeyError, TypeError, ValueError):
        return None


def compare_evidence(
    plan: ReplayPlan,
    actual: Mapping[str, Any] | None,
    *,
    incident: str | None,
    ok: bool,
    tolerance: float = 15.0,
) -> ReplayVerdict:
    """Decide whether the replay reproduced the recorded behaviour."""
    actual = dict(actual or {})
    drift = None
    expected_pointer = _pointer(plan.expected)
    actual_pointer = _pointer(actual)
    if expected_pointer is not None and actual_pointer is not None:
        drift = math.hypot(
            actual_pointer[0] - expected_pointer[0], actual_pointer[1] - expected_pointer[1]
        )

    if plan.incident is None:
        if ok:
            note = "the action succeeded again"
            if drift is not None and drift > tolerance:
                note += f"; the pointer landed {drift:.0f} off the recorded position"
            return ReplayVerdict(True, True, drift, None, note)
        return ReplayVerdict(
            False,
            False,
            drift,
            incident,
            f"recorded as successful, but now failed with {incident!r}",
        )

    if incident == plan.incident:
        return ReplayVerdict(
            True,
            True,
            drift,
            incident,
            f"the recorded failure reproduced ({incident})",
        )
    return ReplayVerdict(
        False,
        False,
        drift,
        incident,
        f"recorded failure {plan.incident!r} did not reproduce; now {incident!r}",
    )


async def replay_plan(driver, plan: ReplayPlan, *, tolerance: float = 15.0) -> ReplayVerdict:
    """Dispatch the plan's action through ``driver`` and compare the evidence."""
    if plan.target_px is None:
        return ReplayVerdict(False, False, None, None, "the plan has no coordinates")
    x, y = plan.target_px
    tap_with_result = getattr(driver, "tap_with_result", None)
    if callable(tap_with_result):
        result = await tap_with_result(x, y)
        return compare_evidence(
            plan,
            getattr(result, "metrics", None),
            incident=getattr(result, "incident", None),
            ok=bool(getattr(result, "ok", False)),
            tolerance=tolerance,
        )
    ok = await driver.tap(x, y)
    return compare_evidence(
        plan, None, incident=None if ok else "tap_failed", ok=ok, tolerance=tolerance
    )
