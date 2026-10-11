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

"""The reflex layer: a bounded, deterministic inner loop with no model in it.

Three promises are pinned here, each the answer to a measured problem in the
operator loop (≈3.6 s per step, a safety net worth up to ~7 s, one image per frame
written to the database):

* the loop is bounded — time, blind frames and control failures all end it;
* the loop is honest — every stop reason says why, and skill bugs are reported,
  not raised into the caller's run;
* the loop is out of the way — no database, no a11y tree, no model, so 10 Hz does
  not become 600 images a minute.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from artemis.reflex import contract, control as control_mod, registry, runtime
from artemis.reflex.contract import (
    Action,
    CaptureError,
    Frame,
    Gesture,
    SkillHandle,
    StopReason,
)

REPO_SKILLS = Path(__file__).resolve().parents[3] / "skills"


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


class FakeCapture:
    def __init__(
        self, *, frames: int = 10, fail_after: int | None = None, capture_ms: float = 12.0
    ):
        self.remaining = frames
        self.fail_after = fail_after
        self.capture_ms = capture_ms
        self.grabbed = 0
        self.closed = 0

    def grab(self) -> Frame:
        if self.fail_after is not None and self.grabbed >= self.fail_after:
            raise CaptureError("no device")
        if self.remaining <= 0:
            raise CaptureError("out of frames")
        self.remaining -= 1
        self.grabbed += 1
        return Frame(image=b"\xff\xd8\xff" + b"x" * 10, captured_at=0.0, capture_ms=self.capture_ms)

    def close(self) -> None:
        self.closed += 1


class FakeControl:
    def __init__(self, *, fail: bool = False, latency_ms: float = 8.0):
        self.fail = fail
        self.latency_ms = latency_ms
        self.dispatched: list[Gesture] = []

    def dispatch(self, gesture: Gesture) -> float:
        if self.fail:
            raise RuntimeError("gesture rejected")
        self.dispatched.append(gesture)
        return self.latency_ms


def _skill(
    *,
    parse=None,
    decide=None,
    to_gestures=None,
    name: str = "fake",
) -> SkillHandle:
    return SkillHandle(
        name=name,
        version="0",
        directory=REPO_SKILLS / "noop",
        parse=parse or (lambda frame: {"ok": True}),
        decide=decide or (lambda state, cal: Action()),
        to_gestures=to_gestures or (lambda action: []),
        calibration={},
    )


# --------------------------------------------------------------------------- #
# Registry: a skill that cannot load must say why
# --------------------------------------------------------------------------- #


def _write_skill(root: Path, name: str, manifest: dict, *, files: dict[str, str] | None = None):
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "skill.json").write_text(json.dumps(manifest), encoding="utf-8")
    for filename, body in (files or {}).items():
        (skill_dir / filename).write_text(body, encoding="utf-8")
    return skill_dir


def test_the_shipped_noop_skill_loads_and_validates():
    handle = registry.load_skill("noop", directory=REPO_SKILLS)

    assert handle.name == "noop"
    assert handle.calibration["stop_after_frames"] == 0
    assert callable(handle.parse) and callable(handle.decide) and callable(handle.to_gestures)
    assert "capture rate" in handle.plan_hint


def test_available_skills_reports_a_broken_skill_instead_of_raising(tmp_path):
    _write_skill(tmp_path, "broken", {"name": "broken"})

    found = registry.available_skills(directory=tmp_path)

    assert found["broken"]["ok"] is False
    assert "entry" in found["broken"]["error"]


def test_a_manifest_that_names_another_skill_is_refused(tmp_path):
    _write_skill(
        tmp_path,
        "alpha",
        {
            "name": "beta",
            "entry": {"perception": "p:parse", "policy": "p:decide", "control": "c:g"},
        },
    )

    with pytest.raises(registry.SkillError) as excinfo:
        registry.load_skill("alpha", directory=tmp_path)

    assert "directory name is the skill name" in str(excinfo.value)


def test_a_missing_entry_module_is_refused_by_name(tmp_path):
    _write_skill(
        tmp_path,
        "alpha",
        {
            "name": "alpha",
            "entry": {
                "perception": "perception:parse",
                "policy": "policy:decide",
                "control": "c:g",
            },
        },
        files={"perception.py": "def parse(frame):\n    return 1\n"},
    )

    with pytest.raises(registry.SkillError) as excinfo:
        registry.load_skill("alpha", directory=tmp_path)

    assert "policy.py is missing" in str(excinfo.value)


def test_an_unmet_declared_need_names_the_environment_variable(tmp_path, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    _write_skill(
        tmp_path,
        "alpha",
        {
            "name": "alpha",
            "entry": {"perception": "p:parse", "policy": "p:decide", "control": "c:g"},
            "requires": {"api_key_env": "TYPESAFE_API_KEY"},
        },
    )

    with pytest.raises(registry.SkillError) as excinfo:
        registry.load_skill("alpha", directory=tmp_path)

    assert "TYPESAFE_API_KEY" in str(excinfo.value)
    assert ".env" in str(excinfo.value)


def test_the_skills_directory_can_be_pointed_outside_the_repository(monkeypatch, tmp_path):
    monkeypatch.setenv(registry.ENV_SKILLS_DIR, str(tmp_path))

    assert registry.skills_dir() == tmp_path


# --------------------------------------------------------------------------- #
# Runtime: bounded, honest, and out of the way
# --------------------------------------------------------------------------- #


def test_a_session_ends_on_the_time_budget_and_reports_its_numbers():
    capture = FakeCapture(frames=5)
    control = FakeControl()
    seen: list[int] = []

    def decide(state, calibration):
        seen.append(len(seen))
        return Action(gestures=(Gesture.tap(1, 2),))

    result = runtime.ReflexRuntime(capture=capture, control=control, fps_cap=50).run(
        _skill(decide=decide), seconds=0.2
    )

    assert result.stop_reason is StopReason.TIME_BUDGET
    assert result.frames == 5
    assert result.actions == 5
    assert result.fps > 0
    assert result.capture_ms_p50 == pytest.approx(12.0)
    assert capture.closed == 1
    assert len(control.dispatched) == 5


def test_a_skill_can_end_its_own_session():
    result = runtime.ReflexRuntime(capture=FakeCapture(), control=FakeControl()).run(
        _skill(decide=lambda state, cal: Action(done=True)), seconds=5
    )

    assert result.stop_reason is StopReason.SKILL_DONE
    assert result.achieved is True


def test_unparsable_frames_end_the_session_instead_of_acting_blind():
    result = runtime.ReflexRuntime(
        capture=FakeCapture(frames=100),
        control=FakeControl(),
        max_blind_frames=3,
    ).run(_skill(parse=lambda frame: None), seconds=5)

    assert result.stop_reason is StopReason.LOST
    assert result.blind_frames == 3
    assert result.actions == 0


def test_a_dead_capture_ends_the_session_as_lost():
    result = runtime.ReflexRuntime(
        capture=FakeCapture(frames=0), control=FakeControl(), max_blind_frames=2
    ).run(_skill(), seconds=5)

    assert result.stop_reason is StopReason.LOST


def test_a_skill_bug_is_reported_not_raised():
    def decide(state, calibration):
        raise ValueError("policy exploded")

    result = runtime.ReflexRuntime(capture=FakeCapture(), control=FakeControl()).run(
        _skill(decide=decide), seconds=5
    )

    assert result.stop_reason is StopReason.SKILL_ERROR
    assert "policy exploded" in (result.skill_error or "")


def test_a_policy_that_returns_the_wrong_type_is_reported():
    result = runtime.ReflexRuntime(capture=FakeCapture(), control=FakeControl()).run(
        _skill(decide=lambda state, cal: "not an action"), seconds=5
    )

    assert result.stop_reason is StopReason.SKILL_ERROR
    assert "expected Action" in (result.skill_error or "")


def test_a_device_that_stops_accepting_gestures_ends_the_session():
    result = runtime.ReflexRuntime(
        capture=FakeCapture(frames=50), control=FakeControl(fail=True), max_control_failures=2
    ).run(_skill(decide=lambda state, cal: Action(gestures=(Gesture.tap(1, 1),))), seconds=5)

    assert result.stop_reason is StopReason.CONTROL_LOST
    assert result.control_failures == 2
    assert result.actions == 0


def test_an_external_stop_request_wins_over_the_budget():
    result = runtime.ReflexRuntime(capture=FakeCapture(), control=FakeControl()).run(
        _skill(), seconds=30, should_stop=lambda: True
    )

    assert result.stop_reason is StopReason.CANCELLED


def test_counters_a_skill_reports_reach_the_result():
    def decide(state, calibration):
        return Action(done=True, state={"counters": {"lines": 12, "score": 340}})

    result = runtime.ReflexRuntime(capture=FakeCapture(), control=FakeControl()).run(
        _skill(decide=decide), seconds=5
    )

    assert result.counters == {"lines": 12, "score": 340}
    assert "lines=12" in result.summary_line()
    assert result.as_dict()["counters"]["score"] == 340


def test_a_gesture_is_dispatched_with_its_measured_latency():
    control = FakeControl(latency_ms=21.0)

    result = runtime.ReflexRuntime(capture=FakeCapture(frames=2), control=control).run(
        _skill(decide=lambda state, cal: Action(gestures=(Gesture.swipe(0, 0, 5, 5),))),
        seconds=1,
    )

    assert result.action_ms_p50 == pytest.approx(21.0)
    assert control.dispatched[0].kind == "swipe"


def test_the_loop_touches_neither_the_database_nor_the_hierarchy():
    """The design promise, pinned: reflex frames stay in memory.

    ``observe()`` persists every capture (a step row, an image, a trace). At 10 Hz
    that is 600 images a minute, which is why the inner loop must not go near the
    data engine. Checked on the module's *code* (imports and attribute names), so
    the docstring may keep explaining the rule it exists to enforce.
    """
    import ast

    tree = ast.parse(Path(runtime.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    attrs: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Attribute):
            attrs.add(node.attr)

    for forbidden_module in ("artemis.data_engine", "artemis.services.llm", "artemis.graph"):
        assert not any(name.startswith(forbidden_module) for name in imported), forbidden_module
    for forbidden_attr in ("record_trace", "start_session", "update_step_summary"):
        assert forbidden_attr not in attrs, forbidden_attr


def test_percentiles_are_interpolated_and_total_on_one_value():
    assert runtime._percentile([], 50) == 0.0
    assert runtime._percentile([7.0], 95) == 7.0
    assert runtime._percentile([1.0, 2.0, 3.0, 4.0], 50) == pytest.approx(2.5)


# --------------------------------------------------------------------------- #
# Contract: gestures are the whole vocabulary
# --------------------------------------------------------------------------- #


def test_gesture_constructors_shape_the_points():
    tap = Gesture.tap(10, 20, description="rotate")
    swipe = Gesture.swipe(1, 2, 3, 4, duration_ms=250)

    assert tap.points == ((10.0, 20.0),) and tap.kind == "tap"
    assert swipe.points == ((1.0, 2.0), (3.0, 4.0)) and swipe.duration_ms == 250


def test_an_unknown_gesture_kind_is_refused_by_both_controls():
    bogus = Gesture(kind="pinch", points=((0.0, 0.0), (1.0, 1.0)))

    with pytest.raises(ValueError):
        control_mod.AdbInputControl("serial").dispatch(bogus)
    with pytest.raises(ValueError):
        control_mod.HelperControl("serial", client=object()).dispatch(bogus)


def test_the_helper_control_maps_gestures_and_reports_refusal():
    class FakeClient:
        def __init__(self, ok=True):
            self.ok = ok
            self.calls: list[tuple] = []

        def tap(self, x, y):
            self.calls.append(("tap", x, y))
            return self.ok

        def swipe(self, x1, y1, x2, y2, duration_ms):
            self.calls.append(("swipe", x1, y1, x2, y2, duration_ms))
            return self.ok

    client = FakeClient()
    latency = control_mod.HelperControl("serial", client=client).dispatch(Gesture.tap(5, 6))
    assert client.calls == [("tap", 5.0, 6.0)]
    assert latency >= 0.0

    control_mod.HelperControl("serial", client=client).dispatch(
        Gesture.swipe(1, 2, 3, 4, duration_ms=90)
    )
    assert client.calls[-1] == ("swipe", 1.0, 2.0, 3.0, 4.0, 90)

    with pytest.raises(RuntimeError):
        control_mod.HelperControl("serial", client=FakeClient(ok=False)).dispatch(Gesture.tap(1, 1))


def test_stop_reasons_are_a_closed_vocabulary():
    assert {r.value for r in StopReason} == {
        "skill_done",
        "time_budget",
        "cancelled",
        "lost",
        "control_lost",
        "skill_error",
    }
    assert contract.now() > 0


# --------------------------------------------------------------------------- #
# Attaching: a slow device must fail fast, not hang the loop
# --------------------------------------------------------------------------- #


def test_an_attach_that_takes_too_long_is_reported_inside_the_deadline():
    from artemis.reflex import attach as attach_mod

    def never_finishes(_serial):
        import time

        time.sleep(2.0)
        return object()

    started = __import__("time").monotonic()
    with pytest.raises(attach_mod.AttachTimeout) as excinfo:
        attach_mod.attach_client("SERIAL", deadline_s=0.2, factory=never_finishes)
    elapsed = __import__("time").monotonic() - started

    assert elapsed < 1.0, "the deadline must bound the caller's wait"
    assert "adb is slow" in str(excinfo.value)
    assert "screencap" in str(excinfo.value)


def test_a_failed_attach_raises_its_own_error():
    from artemis.reflex import attach as attach_mod

    def explodes(_serial):
        raise RuntimeError("helper unavailable")

    with pytest.raises(RuntimeError) as excinfo:
        attach_mod.attach_client("SERIAL", deadline_s=1.0, factory=explodes)

    assert "helper unavailable" in str(excinfo.value)


def test_a_quick_attach_returns_the_client():
    from artemis.reflex import attach as attach_mod

    sentinel = object()

    assert attach_mod.attach_client("S", factory=lambda _s: sentinel) is sentinel


def test_the_helper_capture_turns_a_slow_attach_into_a_frame_error():
    from artemis.reflex import capture as capture_mod
    from artemis.reflex.attach import AttachTimeout

    def slow(_serial, deadline_s=None):
        raise AttachTimeout("attaching to the accessibility helper did not finish")

    monkey = pytest.MonkeyPatch()
    monkey.setattr(capture_mod, "attach_client", slow)
    try:
        with pytest.raises(CaptureError) as excinfo:
            capture_mod.HelperCapture("SERIAL").grab()
    finally:
        monkey.undo()

    assert "screencap" in str(excinfo.value)


def test_the_helper_capture_prefers_the_device_side_snapshot_over_adb_screencap():
    """Measured: the snapshot is 440 ms, ``get_screenshot_base64`` is 1347 ms.

    The latter shells out to ``adb exec-out screencap -p`` and re-encodes with PIL.
    An inner loop must not pay that, so the preference is pinned here.
    """
    import base64 as b64

    from artemis.reflex import capture as capture_mod

    calls: list[str] = []

    class FakeClient:
        def get_atomic_snapshot(self):
            calls.append("snapshot")
            return {
                "screenshot_base64": b64.b64encode(b"\xff\xd8\xffjpeg").decode(),
                "width": 720,
                "height": 1600,
            }

        def get_screenshot_base64(self):
            calls.append("adb")
            return b64.b64encode(b"\xff\xd8\xffadb").decode()

    frame = capture_mod.HelperCapture("SERIAL", client=FakeClient()).grab()

    assert calls == ["snapshot"]
    assert frame.image.startswith(b"\xff\xd8\xff")
    assert (frame.width, frame.height) == (720, 1600)


def test_the_helper_capture_falls_back_when_no_snapshot_is_available():
    import base64 as b64

    from artemis.reflex import capture as capture_mod

    calls: list[str] = []

    class FakeClient:
        def get_atomic_snapshot(self):
            calls.append("snapshot")
            return None

        def get_screenshot_base64(self):
            calls.append("adb")
            return b64.b64encode(b"\xff\xd8\xffadb").decode()

    frame = capture_mod.HelperCapture("SERIAL", client=FakeClient()).grab()

    assert calls == ["snapshot", "adb"]
    assert frame.image.startswith(b"\xff\xd8\xff")
