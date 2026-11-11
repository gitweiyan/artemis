"""Compatibility shim: PointerControl moved to adapters (Phase 9)."""

from __future__ import annotations

from artemis.adapters.pointer_control import PointerControl  # noqa: F401

__all__ = ["PointerControl"]
