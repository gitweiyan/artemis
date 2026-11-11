"""Host resource leases (ADR-011 PR-3b)."""

from __future__ import annotations

import json
import os
import threading
import time

import pytest

from artemis.runtime import lease_primitives
from artemis.runtime.resource_lease import (
    LeaseSet,
    ResourceBusyError,
    ResourceLease,
    cleanup_stale,
    get_active_leases,
)

RESOURCE = "uvc:host-a:usb-1-2.3/capture"
OTHER = "serial:host-a:cu.usbmodem21101/command"


def _lease(tmp_path, resource_id=RESOURCE, *, session="session-1", **kwargs):
    return ResourceLease(
        resource_id,
        host_id="host-a",
        description="test",
        session_id=session,
        lease_dir=tmp_path,
        **kwargs,
    )


def _write_owner(path, *, pid, created_at, token="other", session=None, resource_id=RESOURCE):
    payload = {
        "schema_version": 1,
        "resource_id": resource_id,
        "host_id": "host-a",
        "pid": pid,
        "process_created_at": created_at,
        "token": token,
        "description": "other owner",
        "acquired_at": "2026-11-09T00:00:00+00:00",
    }
    if session:
        payload["session_id"] = session
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_a_second_owner_is_refused(tmp_path):
    first = _lease(tmp_path, session="one")
    second = _lease(tmp_path, session="two")
    first.acquire()
    try:
        with pytest.raises(ResourceBusyError, match="busy") as excinfo:
            second.acquire(blocking=False)
        assert excinfo.value.resource_id == RESOURCE
        assert excinfo.value.owner is not None
        assert excinfo.value.owner.pid == os.getpid()
    finally:
        first.release()


def test_the_same_session_can_reenter_and_release_by_refcount(tmp_path):
    first = _lease(tmp_path, session="shared")
    second = _lease(tmp_path, session="shared")
    first.acquire()
    second.acquire()
    assert first.path.exists()

    first.release()
    assert first.path.exists(), "one reference is still held"
    second.release()
    assert not first.path.exists()


def test_the_same_process_with_another_session_is_busy(tmp_path):
    first = _lease(tmp_path, session="one")
    second = _lease(tmp_path, session="two")
    first.acquire()
    try:
        with pytest.raises(ResourceBusyError):
            second.acquire(blocking=False)
    finally:
        first.release()


def test_a_dead_owner_is_reclaimed(tmp_path):
    lease = _lease(tmp_path)
    _write_owner(lease.path, pid=999_999, created_at=1.0)
    lease.acquire(blocking=False)
    try:
        assert lease.path.exists()
    finally:
        lease.release()


def test_a_live_owner_is_not_preempted_by_an_old_heartbeat(tmp_path):
    lease = _lease(tmp_path, session="mine")
    _write_owner(
        lease.path,
        pid=os.getpid(),
        created_at=lease_primitives.current_process_created_at(),
        token="someone-else",
        session="another-session",
    )
    old = time.time() - 86_400
    os.utime(lease.path, (old, old))

    with pytest.raises(ResourceBusyError):
        lease.acquire(blocking=False)


def test_a_fresh_malformed_file_is_respected_until_the_grace_expires(tmp_path):
    lease = _lease(tmp_path)
    lease.path.parent.mkdir(parents=True, exist_ok=True)
    lease.path.write_text("not-json", encoding="utf-8")

    with pytest.raises(ResourceBusyError):
        lease.acquire(blocking=False)

    old = time.time() - lease_primitives.MALFORMED_GRACE_SECONDS - 1
    os.utime(lease.path, (old, old))
    lease.acquire(blocking=False)
    try:
        assert lease.path.exists()
    finally:
        lease.release()


def test_blocking_acquire_times_out(tmp_path):
    first = _lease(tmp_path, session="one")
    second = _lease(tmp_path, session="two")
    first.acquire()
    started = time.monotonic()
    try:
        with pytest.raises(ResourceBusyError):
            second.acquire(timeout=0.2)
        assert time.monotonic() - started >= 0.15
    finally:
        first.release()


def test_cancel_event_aborts_the_wait(tmp_path):
    first = _lease(tmp_path, session="one")
    second = _lease(tmp_path, session="two")
    first.acquire()
    cancel = threading.Event()
    cancel.set()
    try:
        with pytest.raises(ResourceBusyError):
            second.acquire(timeout=10, cancel_event=cancel)
    finally:
        first.release()


def test_lease_set_acquires_in_order_and_rolls_back_on_conflict(tmp_path):
    # Sorted order is serial (OTHER) then uvc (RESOURCE); block the second one so
    # the first acquisition has to be rolled back.
    blocker = _lease(tmp_path, RESOURCE, session="blocker")
    blocker.acquire()
    leases = LeaseSet(host_id="host-a", session_id="worker", lease_dir=tmp_path)
    try:
        with pytest.raises(ResourceBusyError):
            leases.acquire([OTHER, RESOURCE], timeout=0.2)
        reacquire = _lease(tmp_path, OTHER, session="worker")
        reacquire.acquire(blocking=False)
        reacquire.release()
    finally:
        blocker.release()


def test_lease_set_releases_in_reverse_order(tmp_path):
    leases = LeaseSet(host_id="host-a", session_id="worker", lease_dir=tmp_path)
    leases.acquire([OTHER, RESOURCE])
    assert leases.resource_ids == tuple(sorted((RESOURCE, OTHER)))
    leases.release()
    assert leases.resource_ids == ()
    assert not any(tmp_path.glob("resource-*.lock"))


def test_resource_ids_cannot_escape_the_lease_directory(tmp_path):
    lease = _lease(tmp_path, "../../etc/passwd")
    assert lease.path.parent == tmp_path
    assert lease.path.name.startswith("resource-")


def test_metadata_is_limited_to_diagnostic_keys(tmp_path):
    with pytest.raises(ValueError, match="metadata"):
        _lease(tmp_path, metadata={"business": "x"})

    lease = _lease(tmp_path, metadata={"local_port": "50000"})
    lease.acquire()
    try:
        assert lease.owner is not None
        assert lease.owner.metadata == {"local_port": "50000"}
    finally:
        lease.release()


def test_release_is_idempotent_and_does_not_delete_a_foreign_owner(tmp_path):
    lease = _lease(tmp_path, session="mine")
    lease.acquire()
    _write_owner(lease.path, pid=os.getpid(), created_at=1.0, token="foreign")
    lease.release()
    assert lease.path.exists(), "a lease that no longer belongs to us must not be deleted"

    lease.release()
    assert lease.path.exists()

    cleanup_stale(tmp_path)


def test_touch_updates_the_heartbeat(tmp_path):
    lease = _lease(tmp_path)
    lease.acquire()
    try:
        assert lease.touch() is True
        payload = json.loads(lease.path.read_text(encoding="utf-8"))
        assert payload["heartbeat_at"]
    finally:
        lease.release()


def test_active_and_stale_leases_are_visible_and_cleaned(tmp_path):
    lease = _lease(tmp_path)
    lease.acquire()
    try:
        active = get_active_leases(tmp_path)
        assert RESOURCE in active
        assert active[RESOURCE].pid == os.getpid()
    finally:
        lease.release()

    _write_owner(lease.path, pid=999_999, created_at=1.0)
    assert cleanup_stale(tmp_path) == 1
    assert not lease.path.exists()


def test_lease_set_releases_later_leases_after_one_release_error(tmp_path):
    class FakeLease:
        def __init__(self, resource_id, error=None):
            self.resource_id = resource_id
            self.error = error
            self.released = 0

        def release(self):
            self.released += 1
            if self.error is not None:
                raise self.error

    broken = FakeLease(RESOURCE, RuntimeError("broken"))
    healthy = FakeLease(OTHER)
    leases = LeaseSet(host_id="host-a", session_id="worker", lease_dir=tmp_path)
    leases._leases = [broken, healthy]

    with pytest.raises(RuntimeError, match="broken"):
        leases.release()

    assert broken.released == 1
    assert healthy.released == 1
    assert leases.resource_ids == ()
