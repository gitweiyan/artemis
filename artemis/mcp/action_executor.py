"""Compatibility shim: the action executor moved to the application layer.

Import :mod:`artemis.actions.executor` for new code. This module remains for
existing MCP callers and tests until the Phase 9 cleanup window.
"""

from __future__ import annotations

from artemis.actions.executor import (  # noqa: F401
    AGENT_TOOL_NAMES,
    McpActionExecutor,
    _ArgError,
    build_actuator,
)

__all__ = ["AGENT_TOOL_NAMES", "McpActionExecutor", "build_actuator"]
