"""Cross-process resource exclusion (ADR-011)."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

from artemis.runtime.resource_lease import ResourceBusyError, ResourceLease

ROOT = Path(__file__).resolve().parents[3]
RESOURCE = "uvc:host-a:usb-1-2.3/capture"

_CHILD = """
import sys, time
from artemis.runtime.resource_lease import ResourceLease

lease = ResourceLease(sys.argv[1], lease_dir=sys.argv[2], session_id="child")
lease.acquire(blocking=False)
print("acquired", flush=True)
time.sleep(30)
"""


def test_two_processes_cannot_hold_the_same_resource(tmp_path):
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    child = subprocess.Popen(
        [sys.executable, "-c", _CHILD, RESOURCE, str(tmp_path)],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        line = child.stdout.readline().strip()
        assert line == "acquired", child.stderr.read() if child.stderr else line

        parent = ResourceLease(RESOURCE, lease_dir=tmp_path, session_id="parent")
        with pytest.raises(ResourceBusyError):
            parent.acquire(blocking=False)
    finally:
        child.kill()
        child.wait(timeout=10)

    # The child is gone; its lease is stale and the parent can take it over.
    parent = ResourceLease(RESOURCE, lease_dir=tmp_path, session_id="parent")
    parent.acquire(blocking=False)
    parent.release()
