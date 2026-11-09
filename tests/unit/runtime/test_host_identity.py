"""Stable host identity for resource scoping (ADR-003)."""

from __future__ import annotations

from artemis.runtime import host_identity


def test_explicit_host_id_wins(monkeypatch):
    monkeypatch.setenv("ARTEMIS_HOST_ID", "lab-host")
    assert host_identity.host_id() == "lab-host"


def test_host_id_is_deterministic_without_an_override(monkeypatch):
    monkeypatch.delenv("ARTEMIS_HOST_ID", raising=False)
    first = host_identity.host_id()
    second = host_identity.host_id()

    assert first == second
    assert first
    assert first.replace("-", "").isalnum()


def test_host_id_changes_with_the_host_identity(monkeypatch):
    monkeypatch.delenv("ARTEMIS_HOST_ID", raising=False)
    monkeypatch.setattr(host_identity.socket, "gethostname", lambda: "host-a")
    first = host_identity.host_id()
    monkeypatch.setattr(host_identity.socket, "gethostname", lambda: "host-b")
    second = host_identity.host_id()

    assert first != second


def test_invalid_override_falls_back_to_the_derived_id(monkeypatch):
    monkeypatch.setenv("ARTEMIS_HOST_ID", "bad host\nid")
    value = host_identity.host_id()

    assert "\n" not in value
    assert value
