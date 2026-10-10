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

"""I2 · the run states what it is about to execute, before it executes it.

The 2026-10-09 incident (P7): a device run "verified" zero degradations while
executing a workspace whose fix had been reverted by a branch operation. Branch
discipline (I1) stops *creating* that state, and ``run_outcome.environment.code``
(I3) reports it afterwards — for Pro runs only, since the Flash reactive loop
writes no verdict at all.

What was missing is the moment the run starts. These tests pin the three things
that moment owes a reader: the revision, the local modifications, the provider
tiers. Deliberately *not* a checklist of "is fix X present" — such a list goes
stale with the next fix (the retrospective's own risk note), and a branch name has
never been evidence: the 18:10 branch was named after the fix it did not contain.
"""

from __future__ import annotations

import importlib
import json
import os
import re
import shutil
import subprocess
from types import SimpleNamespace

import pytest

SESSION_ID = "11111111-2222-3333-4444-555555555555"

#: A dirty revision the run must admit to, in the shape ``code_identity`` reports.
DIRTY = {
    "head": "abc1234",
    "branch": "feat/10101507-run-code-identity",
    "dirty_files": 2,
    "dirty_paths": ["config/artemis.jsonc", "reports/"],
    "dirty_paths_truncated": False,
}
CLEAN = {
    "head": "abc1234",
    "branch": "integration/10101205-all-fixes",
    "dirty_files": 0,
    "dirty_paths": [],
    "dirty_paths_truncated": False,
}


def _module():
    """The identity recorder, failing cleanly while it does not exist yet."""
    try:
        module = importlib.import_module("artemis.runtime.run_identity")
    except ImportError as exc:
        raise AssertionError(f"the run-identity recorder is missing: {exc}") from exc
    return module


def _ctx(tmp_path) -> SimpleNamespace:
    base = tmp_path / "traces" / SESSION_ID
    base.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(data_engine=SimpleNamespace(base_dir=base))


def _pinned_code(monkeypatch, module, identity: dict) -> None:
    monkeypatch.setattr(module, "code_identity", lambda *_a, **_k: dict(identity))


# --------------------------------------------------------------------------- #
# What the identity says
# --------------------------------------------------------------------------- #


def test_the_identity_names_the_revision_the_profile_and_the_provider_tiers(tmp_path, monkeypatch):
    module = _module()
    _pinned_code(monkeypatch, module, DIRTY)

    identity = module.startup_identity(profile="flash")

    assert identity["code"]["head"] == "abc1234"
    assert identity["code"]["dirty_paths"] == ["config/artemis.jsonc", "reports/"]
    assert identity["profile"] == "flash"
    assert identity["providers"] == module.provider_tiers()
    assert identity["recorded_at"] > 0


def test_provider_tiers_name_the_configured_provider_per_node():
    """The tier that 2026-10-09 could not see: the workspace ran openai/deepseek-flash
    while the Flash lens built a Google client, and no artifact said which was which."""
    from artemis.config.llm import get_default_llm_config

    tiers = _module().provider_tiers()
    config = get_default_llm_config()

    assert tiers, "no provider tier was reported"
    assert tiers["operator"] == config.operator.provider
    assert tiers["summarizer"] == config.summarizer.provider
    # Node names, not endpoints: nothing that could carry a credential.
    assert "utils" not in tiers and not any("." in node for node in tiers)
    for node, provider in tiers.items():
        assert isinstance(provider, str) and provider, f"{node} has no provider"
        assert not re.search(r"key|token|secret|password|@|http", provider, re.I), provider


def test_the_identity_carries_no_credential_material():
    """A file next to the run's traces is read, copied and attached to reports.

    ``settings`` holds credentials as ``SecretStr``, so unwrap before looking: a
    check that only compares ``str`` values passes vacuously.
    """
    from artemis.config import settings

    payload = json.dumps(_module().startup_identity(profile="pro"), ensure_ascii=False)
    secrets = []
    for name in dir(settings):
        if not re.search(r"key|token|secret", name, re.I):
            continue
        value = getattr(settings, name, None)
        unwrapped = value.get_secret_value() if hasattr(value, "get_secret_value") else value
        if isinstance(unwrapped, str) and len(unwrapped) >= 8:
            secrets.append(unwrapped)
    for secret in secrets:
        assert secret not in payload
    assert os.environ.get("OPENAI_API_KEY", "x") not in payload


# --------------------------------------------------------------------------- #
# Where it goes, and what it says about a dirty tree
# --------------------------------------------------------------------------- #


def test_the_identity_lands_in_the_session_directory(tmp_path, monkeypatch):
    module = _module()
    _pinned_code(monkeypatch, module, DIRTY)
    ctx = _ctx(tmp_path)

    module.record_start_identity(ctx, profile="flash")

    path = ctx.data_engine.base_dir / module.IDENTITY_FILENAME
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["code"]["head"] == "abc1234"
    assert written["profile"] == "flash"
    assert written["providers"]


def test_a_dirty_tree_warns_and_names_what_is_uncommitted(tmp_path, monkeypatch, caplog):
    """The run must say which revision it ran — and that this revision exists in no
    commit. Warning, not refusal: a workspace may legitimately carry local work."""
    import logging

    module = _module()
    _pinned_code(monkeypatch, module, DIRTY)

    with caplog.at_level(logging.WARNING):
        module.record_start_identity(_ctx(tmp_path), profile="pro")

    warnings = "\n".join(r.message for r in caplog.records if r.levelno >= logging.WARNING)
    assert "config/artemis.jsonc" in warnings
    assert "abc1234" in warnings


def test_a_clean_tree_reports_the_identity_without_warning(tmp_path, monkeypatch, caplog):
    import logging

    module = _module()
    _pinned_code(monkeypatch, module, CLEAN)

    with caplog.at_level(logging.INFO):
        module.record_start_identity(_ctx(tmp_path), profile="pro")

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    line = "\n".join(r.message for r in caplog.records)
    assert "abc1234" in line and "integration/10101205-all-fixes" in line


def test_strict_mode_refuses_to_start_on_a_dirty_tree(tmp_path, monkeypatch):
    """Opt-in: a verification run may demand a committed revision, and must be told
    so before it spends the device, not after."""
    module = _module()
    _pinned_code(monkeypatch, module, DIRTY)
    monkeypatch.setenv(module.STRICT_ENV, "1")

    with pytest.raises(RuntimeError) as excinfo:
        module.record_start_identity(_ctx(tmp_path), profile="pro")

    assert "config/artemis.jsonc" in str(excinfo.value)


def test_a_sessionless_context_still_reports_the_identity(tmp_path, monkeypatch, caplog):
    """No session directory to write into is not a reason to say nothing."""
    import logging

    module = _module()
    _pinned_code(monkeypatch, module, CLEAN)

    with caplog.at_level(logging.INFO):
        identity = module.record_start_identity(SimpleNamespace(), profile="pro")

    assert identity["code"]["head"] == "abc1234"
    assert "abc1234" in "\n".join(r.message for r in caplog.records)


# --------------------------------------------------------------------------- #
# The code half: which paths a run modified
# --------------------------------------------------------------------------- #


def _git(repo, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(repo),
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
        },
    )


@pytest.fixture
def temp_repo(tmp_path):
    if not shutil.which("git"):
        pytest.skip("git is not on PATH")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "committed.txt").write_text("one\n", encoding="utf-8")
    _git(repo, "add", "committed.txt")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def test_code_identity_names_the_paths_a_run_modified(temp_repo):
    from artemis.runtime import degradations

    (temp_repo / "committed.txt").write_text("two\n", encoding="utf-8")
    (temp_repo / "untracked.txt").write_text("new\n", encoding="utf-8")

    identity = degradations.code_identity(temp_repo)

    assert identity["dirty_files"] == 2
    assert set(identity["dirty_paths"]) == {"committed.txt", "untracked.txt"}


def test_code_identity_is_empty_for_a_committed_tree(temp_repo):
    from artemis.runtime import degradations

    identity = degradations.code_identity(temp_repo)

    assert identity["dirty_files"] == 0
    assert identity["dirty_paths"] == []
    assert identity["dirty_paths_truncated"] is False


def test_code_identity_bounds_the_path_list(temp_repo):
    """A verdict is not a file listing: the count stays exact, the names are capped."""
    from artemis.runtime import degradations

    for index in range(degradations.DIRTY_PATHS_LIMIT + 5):
        (temp_repo / f"file_{index}.txt").write_text("x\n", encoding="utf-8")

    identity = degradations.code_identity(temp_repo)

    assert identity["dirty_files"] == degradations.DIRTY_PATHS_LIMIT + 5
    assert len(identity["dirty_paths"]) == degradations.DIRTY_PATHS_LIMIT
    assert identity["dirty_paths_truncated"] is True


# --------------------------------------------------------------------------- #
# The wiring: the run records its identity before it touches the device
# --------------------------------------------------------------------------- #


def test_preparing_a_run_records_its_identity_before_any_device_work(tmp_path, monkeypatch):
    """``Agent._prepare_tracing`` is the first moment that has both a session
    directory and no device work behind it: it runs straight after the device lock
    is acquired and before the screen client, the app install and the recording."""
    import uuid
    from unittest.mock import MagicMock

    from artemis.context import ArtemisContext
    from artemis.sdk.agent import Agent

    recorded: list[tuple[str | None, str]] = []

    def _spy(ctx, *, profile=None):
        recorded.append((profile, str(getattr(ctx.data_engine, "base_dir", ""))))
        return {}

    monkeypatch.setattr("artemis.runtime.run_identity.record_start_identity", _spy)

    agent = object.__new__(Agent)
    # The config the agent builds for itself: pydantic fields are invisible to
    # ``MagicMock(spec=…)`` (they live in ``model_fields``), and a bare MagicMock
    # rejects ``assert_failure_policy`` as a mistyped assertion.
    from artemis.sdk.builders import Builders

    agent._config = Builders.AgentConfig.build().model_copy(
        update={"video_recording_tools_enabled": False}
    )
    agent._tmp_traces_dir = tmp_path
    agent._session_id = str(uuid.uuid4())
    agent._prepare_trace_paths = lambda _task: "web_1_deadbeef"

    task = MagicMock()
    task.request.goal = "check the battery level"
    task.request.profile = "flash"
    task.request.record_trace = False
    task.request.enable_remote_tracing = False

    context = MagicMock(spec=ArtemisContext)
    context.device = None

    Agent._prepare_tracing(agent, task, context)

    assert len(recorded) == 1, "the run's identity was not recorded while preparing it"
    profile, session_dir = recorded[0]
    assert profile == "flash"
    assert session_dir.endswith(agent._session_id), session_dir


def test_a_session_path_that_is_not_a_path_writes_nothing(tmp_path, monkeypatch):
    """``Agent._prepare_tracing`` is exercised with mock contexts elsewhere, and a
    mock satisfies ``os.PathLike``: without a real-path guard the recorder creates a
    literal ``MagicMock/…`` tree in the working directory (it did)."""
    from unittest.mock import MagicMock

    module = _module()
    _pinned_code(monkeypatch, module, CLEAN)
    monkeypatch.chdir(tmp_path)

    identity = module.record_start_identity(SimpleNamespace(data_engine=MagicMock()), profile="pro")

    assert identity["code"]["head"] == "abc1234"
    assert list(tmp_path.iterdir()) == [], "the recorder wrote into a mocked session path"
