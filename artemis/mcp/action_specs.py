"""Compatibility shim: action specs moved to artemis.actions (Phase 9)."""

from __future__ import annotations

from artemis.actions.specs import (  # noqa: F401
    ACTION_SPECS,
    EXCEPTION_PREFIXES,
    OPERATOR_SHELL_ORDER,
    ActionSpec,
    DeclarationDialect,
    OperatorDialect,
    ParamSpec,
    WireDialect,
    exception_prefix,
    make_wire_handler,
    operator_shell_tool,
    tool_declaration,
    wire_dialects,
)

__all__ = [
    "ACTION_SPECS",
    "ActionSpec",
    "DeclarationDialect",
    "EXCEPTION_PREFIXES",
    "OPERATOR_SHELL_ORDER",
    "OperatorDialect",
    "ParamSpec",
    "WireDialect",
    "exception_prefix",
    "make_wire_handler",
    "operator_shell_tool",
    "tool_declaration",
    "wire_dialects",
]
