"""Compatibility shim: action names moved to artemis.actions (Phase 9)."""

from __future__ import annotations

from artemis.actions.names import OPERATOR_ACTION_TO_CANONICAL, to_canonical_call  # noqa: F401

__all__ = ["OPERATOR_ACTION_TO_CANONICAL", "to_canonical_call"]
