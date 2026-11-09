"""Helper tunnels take the ADR-011 host-resource lease."""

from __future__ import annotations

import pytest

from artemis.runtime.helper_manager import AccessibilityHelperManager, HelperUnavailable


def test_a_second_process_cannot_take_the_same_helper_tunnel(monkeypatch, tmp_path):
    monkeypatch.setenv("ARTEMIS_RESOURCE_LEASE", "1")
    monkeypatch.setenv("ARTEMIS_RESOURCE_LEASE_DIR", str(tmp_path))
    monkeypatch.setenv("ARTEMIS_HOST_ID", "host-a")

    first = AccessibilityHelperManager(bundled=None)
    lease = first._acquire_forward_lease("pixel-1")
    assert lease is not None
    assert lease.path.exists()

    second = AccessibilityHelperManager(bundled=None)
    with pytest.raises(HelperUnavailable, match="Another ARTEMIS process"):
        second._acquire_forward_lease("pixel-1")

    lease.release()
    assert not lease.path.exists()


def test_the_lease_switch_disables_helper_leases(monkeypatch, tmp_path):
    monkeypatch.setenv("ARTEMIS_RESOURCE_LEASE", "0")
    monkeypatch.setenv("ARTEMIS_RESOURCE_LEASE_DIR", str(tmp_path))
    manager = AccessibilityHelperManager(bundled=None)

    assert manager._acquire_forward_lease("pixel-1") is None
