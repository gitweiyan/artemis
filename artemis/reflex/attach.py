"""Compatibility shim: helper attachment moved to adapters (Phase 9)."""

from __future__ import annotations

from artemis.adapters.attach import (  # noqa: F401
    ATTACH_DEADLINE_S,
    AttachTimeout,
    attach_client,
)

__all__ = ["ATTACH_DEADLINE_S", "AttachTimeout", "attach_client"]
