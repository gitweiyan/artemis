"""Shared lease primitives (ADR-011 PR-3a)."""

from __future__ import annotations

import os
import time

from artemis.runtime import lease_primitives


def test_current_process_is_alive_with_its_creation_time():
    created = lease_primitives.current_process_created_at()
    assert created > 0
    assert lease_primitives.pid_owned_by_artemis(os.getpid(), created) is True


def test_dead_pid_with_creation_time_is_dead():
    assert lease_primitives.pid_owned_by_artemis(999_999, 1.0) is False


def test_non_positive_pid_is_dead():
    assert lease_primitives.pid_owned_by_artemis(0, time.time()) is False


def test_read_payload_returns_none_for_missing_or_malformed(tmp_path):
    missing = tmp_path / "missing.lock"
    assert lease_primitives.read_payload(missing) is None

    malformed = tmp_path / "malformed.lock"
    malformed.write_text("not json", encoding="utf-8")
    assert lease_primitives.read_payload(malformed) is None

    valid = tmp_path / "valid.lock"
    valid.write_text('{"pid": 1}', encoding="utf-8")
    assert lease_primitives.read_payload(valid) == {"pid": 1}


def test_atomic_write_roundtrip(tmp_path):
    target = tmp_path / "owner.lock"
    assert lease_primitives.atomic_write_json(target, {"pid": 1, "name": "ü"}) is True
    assert lease_primitives.read_payload(target) == {"pid": 1, "name": "ü"}


def test_safe_unlink_missing_file_is_success(tmp_path):
    assert lease_primitives.safe_unlink(tmp_path / "missing") is True
