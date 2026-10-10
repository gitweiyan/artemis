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

"""Real-device case: read the battery level from the Settings Battery page.

Case contract
-------------
The 2026-10-09 retrospective (``reports/复盘-20261009-获取电池信息测试.html``) found
this case under-specified: the goal named neither a start screen nor what
"check" means, so the planner invented the acceptance criterion, and the run
began two levels deep in Settings because the previous session left the device
there. The case therefore carries the three things that were missing:

* ``GOAL`` -- names the app, the page and the artifact to report, so nothing has
  to be inferred;
* ``PRECONDITION`` -- enforced here by pressing HOME before each run, because
  calibration on 2026-10-09 showed a run can otherwise "pass" by reading the
  screen the previous run left open;
* ``ACCEPTANCE`` -- judged on the device's own captured UI tree (the Battery page
  is open and shows a percentage), not on what the model says it saw.

The case also carries the archive contract (I7): the run's compiled trace must
document the steps it took, name only images it actually holds, carry the run's
``notes/`` and compile its frames into a GIF. The 2026-10-09 archives satisfied
none of that — they shipped ``steps.json = []`` and nothing but video — which is
why both retrospectives had to read the database instead of the artifact.

Calibration history
-------------------
* 2026-10-09, flash profile: the session died 12 s in because the flash lenses
  built a Google client on a workspace configured for another provider (F1,
  fixed in ``fix/provider-aware-lenses``). ``PROVIDER_CRASH_MARKERS`` below is
  that regression and is a hard failure.
* 2026-10-09, after the fix, flash + pro both started and finished with the
  configured provider. Flash spent a single LLM call and reported "100%" without
  touching the device (the pro run had left the Battery page open), which is why
  the precondition is enforced and why flash's task-completion bar is split out
  (see ``PROFILES``): its zero-device-action shortcut is reported as an explicit
  xfail, never as a pass.

Running it
----------
Marked ``e2e`` (by directory) and ``manual``: it drives a real phone for
minutes, so the default suite never picks it up (``addopts`` excludes both
markers). Trigger this one case explicitly::

    .venv/bin/python -m pytest tests/e2e/test_device_battery_level.py -m e2e -q

The ``-m e2e`` is required: naming the file alone collects nothing. The case is
also reachable as ``make test-case-battery`` or, with the whole device tree,
``make test-device``.

It skips itself when no device is connected or when the configured LLM provider
has no usable credential. The CLI routes through the Artemis Daemon, so traces
land in the repository's ``traces/`` root; the case reads them there, keyed by
the session id the CLI prints.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

CASE_ID = "battery-level"

#: The goal states the page to open and the artifact to report. Deliberately
#: verbose: a one-line goal is what let the original run invent its own
#: acceptance criterion.
GOAL = (
    "Open the Settings app, open the Battery page (电池), and report the current "
    "battery level as a percentage together with its charging status."
)

#: Enforced by the case (see ``_reset_to_home``): the goal navigates on its own,
#: but a run that starts on the screen the previous run left open can report the
#: right value without doing any work.
PRECONDITION = "launcher home screen"

#: What the case must produce on the device: a concrete percentage.
ACCEPTANCE = re.compile(r"(\d{1,3})\s?%")

#: Markers that the Battery page itself is open, matched against the decoded
#: text of the run's last captured UI tree (device-side evidence, independent of
#: the model's claims).
BATTERY_PAGE_MARKERS = ("电池", "Battery", "battery")

#: Failure shapes that mean the lenses went to a provider this workspace cannot
#: reach. Their absence is the F1 regression check, and it is hard for every
#: profile: a crash must never be excused.
PROVIDER_CRASH_MARKERS = (
    "ChatGoogleGenerativeAI",
    "API key required for Gemini Developer API",
    "Unknown LLM provider",
)

#: Tools that report rather than act: a step whose only action is one of these
#: did not touch the device.
NON_DEVICE_TOOLS = ("report_task_status",)

RUN_TIMEOUT_SECONDS = 900
MIN_LEVEL, MAX_LEVEL = 0, 100
SESSION_ID = re.compile(
    r"Session:\s*([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
)

#: The session's own end state. It lives in the run's log, not in the CLI output:
#: through the daemon the CLI only prints a queue summary and "task completed".
END_STATE = re.compile(r"Session ended:.*?with status:\s*(\w+)")


@dataclass(frozen=True)
class Expectation:
    """What each profile owes this case, decided by calibration, not by hope."""

    require_outcome: bool
    completion_is_hard: bool
    note: str


PROFILES: dict[str, Expectation] = {
    "pro": Expectation(
        require_outcome=True,
        completion_is_hard=True,
        note="reference profile: must settle a run_outcome with no failed checks",
    ),
    "flash": Expectation(
        require_outcome=False,
        completion_is_hard=False,
        note=(
            "flash's reactive loop writes no run_outcome and on 2026-10-09 reported the "
            "value from the visible screen without acting; starting without a provider "
            "crash is hard, reaching the page is judged below"
        ),
    ),
}


# --------------------------------------------------------------------------- #
# Environment gates
# --------------------------------------------------------------------------- #


def _artemis_cli() -> str:
    """The CLI shipped next to the interpreter running these tests."""
    candidate = Path(sys.executable).parent / "artemis"
    if candidate.is_file():
        return str(candidate)
    found = shutil.which("artemis")
    if not found:
        pytest.skip("the artemis CLI is not installed in this interpreter's environment")
    return found


def _adb(*args: str) -> subprocess.CompletedProcess:
    adb = shutil.which("adb")
    if not adb:
        pytest.skip("adb is not on PATH")
    return subprocess.run([adb, *args], capture_output=True, text=True, timeout=120)


def _connected_devices() -> list[tuple[str, str]]:
    out = _adb("devices", "-l").stdout
    devices: list[tuple[str, str]] = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            model = next((p.split(":", 1)[1] for p in parts if p.startswith("model:")), "?")
            devices.append((parts[0], model))
    return devices


def _require_usable_endpoint() -> None:
    """Skip (loudly) when the workspace cannot reach any provider.

    A real-device case that fails because the environment has no credential is
    noise; saying so is not.
    """
    from artemis.config.llm import get_default_llm_config

    try:
        get_default_llm_config().validate_providers()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        pytest.skip(f"no usable LLM endpoint for this workspace: {exc}")


def _reset_to_home(serial: str) -> None:
    """Enforce the case's precondition instead of trusting the previous run's end state.

    Calibration on 2026-10-09: without this, a flash run reported the battery
    level the *previous* run had left on screen, with zero device actions.
    """
    _adb("-s", serial, "shell", "input", "keyevent", "KEYCODE_HOME")


# --------------------------------------------------------------------------- #
# Evidence readers (read-only)
# --------------------------------------------------------------------------- #


def _traces_root() -> Path:
    from artemis.config import ROOT_DIR

    return Path(ROOT_DIR) / "traces"


def _session_dir(session_id: str) -> Path | None:
    root = _traces_root()
    exact = root / session_id
    if exact.is_dir():
        return exact
    short = session_id.split("-")[0]
    candidates = [p for p in root.glob(f"*{short}*") if p.is_dir()]
    return candidates[0] if candidates else None


def _read_outcome(session_dir: Path) -> dict | None:
    """The run's own settlement, wherever this launch shape put it."""
    candidates = [session_dir / "run_outcome.json"]
    candidates.extend(p for p in session_dir.glob("*/run_outcome.json"))
    for path in candidates:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    return None


def _record_text(session_dir: Path) -> str:
    parts: list[str] = []
    for path in sorted(session_dir.rglob("*")):
        if path.is_file() and path.suffix in {".md", ".json", ".jsonl", ".log", ".txt"}:
            try:
                parts.append(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
    return "\n".join(parts)


def _step_actions(session_id: str) -> list[str]:
    """The actions the run recorded on the device (raw, as stored)."""
    db = _traces_root() / "data_engine.db"
    if not db.is_file():
        return []
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = list(
            con.execute(
                "select action_taken from steps where session_id = ? order by step_number",
                (session_id,),
            )
        )
    finally:
        con.close()
    actions: list[str] = []
    for (raw,) in rows:
        text = raw or ""
        try:
            parsed = json.loads(text)
        except ValueError:
            actions.append(text[:80])
            continue
        if isinstance(parsed, list) and parsed:
            actions.append(str(parsed[0].get("action") or parsed[0].get("name") or "?"))
        else:
            actions.append(str(parsed)[:80])
    return actions


def _archive_dir(session_id: str) -> Path | None:
    """The compiled archive for this run: the folder that holds its video.

    The tracer compiles into a temp folder and renames it to
    ``<trace_name><STATUS>_<timestamp>``, so the session id is only a substring.
    """
    short = session_id.split("-")[0]
    candidates = [
        path
        for path in _traces_root().glob(f"*{short}*")
        if path.is_dir() and (path / "recording.mp4").exists()
    ]
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None


def _steps_with_images(session_id: str) -> int:
    """How many of the session's steps recorded a screenshot to archive."""
    db = _traces_root() / "data_engine.db"
    if not db.is_file():
        return 0
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        row = con.execute(
            "select count(*) from steps where session_id = ? "
            "and (pre_image_name is not null or post_image_name is not null)",
            (session_id,),
        ).fetchone()
    finally:
        con.close()
    return int(row[0]) if row else 0


def _final_ui_tree(session_id: str) -> list[dict]:
    """The device's own last captured screen for this session."""
    db = _traces_root() / "data_engine.db"
    if not db.is_file():
        return []
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        row = con.execute(
            "select ui_tree from images where ui_tree is not null and image_name in "
            "(select coalesce(post_image_name, pre_image_name) from steps where session_id = ?) "
            "order by timestamp desc limit 1",
            (session_id,),
        ).fetchone()
    finally:
        con.close()
    if not row or not row[0]:
        return []
    try:
        nodes = json.loads(row[0])
    except ValueError:
        return []
    return nodes if isinstance(nodes, list) else []


def _page_seen_on_device(nodes: list[dict]) -> tuple[bool, list[int]]:
    """Whether the captured screen is the Battery page, and the levels it shows."""
    if not nodes:
        return False, []
    blob = json.dumps(nodes, ensure_ascii=False)
    on_page = any(marker in blob for marker in BATTERY_PAGE_MARKERS)
    levels = [
        int(value) for value in ACCEPTANCE.findall(blob) if MIN_LEVEL <= int(value) <= MAX_LEVEL
    ]
    return on_page, levels


# --------------------------------------------------------------------------- #
# The case
# --------------------------------------------------------------------------- #


@pytest.mark.manual
@pytest.mark.parametrize("profile", list(PROFILES))
def test_battery_level_case(profile: str) -> None:
    """Run the battery case on a real device and assert what it actually did."""
    devices = _connected_devices()
    if not devices:
        pytest.skip("no adb device in 'device' state")
    device_id, device_model = devices[0]
    _require_usable_endpoint()
    _reset_to_home(device_id)

    proc = subprocess.run(
        [
            _artemis_cli(),
            "run",
            GOAL,
            "--profile",
            profile,
            "--test-name",
            f"e2e-{CASE_ID}-{profile}",
        ],
        capture_output=True,
        text=True,
        timeout=RUN_TIMEOUT_SECONDS,
        env={**os.environ, "ARTEMIS_DEFAULT_PROFILE": profile},
        cwd=str(_traces_root().parent),
    )
    output = f"{proc.stdout}\n{proc.stderr}"
    match = SESSION_ID.search(" ".join(output.split()))
    session_id = match.group(1) if match else ""
    session_dir = _session_dir(session_id) if session_id else None
    expectation = PROFILES[profile]

    context = (
        f"\ncase={CASE_ID} profile={profile} device={device_id} ({device_model}) "
        f"profile_note={expectation.note!r}"
        f"\nsession={session_id or '(not printed)'} exit={proc.returncode} dir={session_dir}"
        f"\ncli output (tail):\n{output[-1200:]}"
    )

    # 1. Hard for every profile: the lenses must not reach for a provider this
    #    workspace cannot use. This is the F1 regression.
    crashed = [marker for marker in PROVIDER_CRASH_MARKERS if marker in output]
    assert not crashed, f"the run fell back to an unreachable provider {crashed}{context}"

    # 2. Hard for every profile: the run must exist, have reached the device and
    #    have ended cleanly. The end state is read from the run's own log: through
    #    the daemon the CLI only prints a queue summary.
    assert session_dir is not None, f"the run produced no trace directory{context}"
    record = _record_text(session_dir)
    actions = _step_actions(session_id)
    device_actions = [a for a in actions if a not in NON_DEVICE_TOOLS]
    assert actions, f"the run recorded no step at all{context}"
    end_state = END_STATE.search(record)
    assert end_state is not None, f"the session logged no end state{context}"
    assert end_state.group(1) == "completed", (
        f"the session ended with status {end_state.group(1)}{context}"
    )

    # 3. The case's acceptance criterion, on the device's own capture.
    nodes = _final_ui_tree(session_id)
    on_page, levels = _page_seen_on_device(nodes)
    reported = [
        int(value)
        for value in ACCEPTANCE.findall(record + output)
        if MIN_LEVEL <= int(value) <= MAX_LEVEL
    ]
    if expectation.completion_is_hard:
        assert on_page, f"the Battery page was never on screen (device tree markers){context}"
        assert levels, f"the screen never showed a battery percentage{context}"
        assert reported, f"the run never reported a battery percentage{context}"
    elif not device_actions:
        pytest.xfail(
            f"{profile} reported {sorted(set(reported)) or 'no'}% without touching the device "
            f"(0 device actions, {len(actions)} recorded step(s)); reaching the page is the "
            f"task-level bar and is tracked separately from the provider regression this case "
            f"guards{context}"
        )
    else:
        assert on_page, f"the Battery page was never on screen (device tree markers){context}"
        assert levels, f"the screen never showed a battery percentage{context}"

    # 4. Profile-specific settlement.
    outcome = _read_outcome(session_dir)
    if expectation.require_outcome:
        assert outcome is not None, f"the run left no run_outcome.json{context}"
        tests = outcome.get("tests") or {}
        assert outcome.get("task_status") == "completed", (
            f"task_status={outcome.get('task_status')}{context}"
        )
        assert not tests.get("failed"), f"failed checks: {tests.get('failed_items')}{context}"
    elif outcome is not None:
        assert outcome.get("task_status") == "completed", (
            f"task_status={outcome.get('task_status')}{context}"
        )

    # 5. The archive contract (I7). Both 2026-10-09 retrospectives had to read the
    #    database because their own archives held `steps.json = []`, no frames and
    #    no notes — the run's video, and nothing about what the run did. Judged on
    #    the archive's own files, not on the exporter's logs.
    archive = _archive_dir(session_id)
    assert archive is not None, (
        f"the run compiled no archive (no recording.mp4 under {_traces_root()}){context}"
    )
    archive_context = f"{context}\narchive={archive}\narchive listing: {sorted(p.name for p in archive.iterdir())}"
    manifest = archive / "steps.json"
    assert manifest.is_file(), f"the archive holds no steps.json{archive_context}"
    rows = json.loads(manifest.read_text(encoding="utf-8"))
    assert isinstance(rows, list) and len(rows) == len(actions), (
        f"the archive documents {len(rows) if isinstance(rows, list) else rows!r} step(s) "
        f"while the run recorded {len(actions)}{archive_context}"
    )
    for row in rows:
        assert row.get("step") and row.get("action"), (
            f"an archived step does not say what happened: {row}{archive_context}"
        )

    # Every image the manifest names must be inside the archive: a name into the
    # shared content-addressed store is a dangling path once the archive is read.
    dangling = [
        ref
        for row in rows
        for ref in (row.get("pre_image"), row.get("post_image"))
        if ref and not (archive / ref).is_file()
    ]
    assert not dangling, (
        f"the manifest points at files the archive lacks: {dangling}{archive_context}"
    )

    # The run's notes travel with the archive when the run wrote any.
    run_notes = _traces_root() / session_id / "notes"
    if run_notes.is_dir() and any(run_notes.iterdir()):
        archived_notes = archive / "notes"
        assert archived_notes.is_dir() and any(archived_notes.iterdir()), (
            f"the run wrote notes ({sorted(p.name for p in run_notes.iterdir())}) "
            f"that the archive does not carry{archive_context}"
        )

    # Frames on disk are what the compiler turns into the GIF; if the run captured
    # screenshots, the archive has to show them.
    if _steps_with_images(session_id):
        assert (archive / "trace.gif").is_file(), (
            f"the run captured screenshots but the archive holds no trace.gif{archive_context}"
        )
    if outcome is not None:
        counts = ((outcome.get("environment") or {}).get("degradations") or {}).get("counts") or {}
        assert not counts.get("archive_incomplete"), (
            f"the run reported its own archive as incomplete: {counts}{archive_context}"
        )
