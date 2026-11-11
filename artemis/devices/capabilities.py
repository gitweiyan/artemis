"""Compatibility shim: CapabilitySet moved to core.capabilities (Phase 9)."""

from __future__ import annotations

from artemis.core.capabilities import CapabilitySet  # noqa: F401

__all__ = ["CapabilitySet"]
