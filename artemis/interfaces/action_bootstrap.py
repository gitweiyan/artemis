"""L1 composition hook: attach the L3 ActionService for an agent run."""

from __future__ import annotations

from typing import Any


def attach_action_service(ctx: Any) -> None:
    """Build the compatibility MCP port and attach it to the task context."""
    if getattr(ctx, "action_service", None) is not None:
        return
    from artemis.actions.service import ActionService
    from artemis.controllers.unified_controller import UnifiedMobileController
    from artemis.mcp.action_executor import McpActionExecutor
    from artemis.mcp.action_port import McpActionPort

    if getattr(ctx, "controller", None) is None:
        # L1 compatibility observation source for Planner/Checker initial
        # screens before a Session-backed observation path is available.
        ctx.controller = UnifiedMobileController(ctx)
    executor = McpActionExecutor(ctx)
    ctx.action_service = ActionService(ctx, McpActionPort(executor), helper_port=executor)
    # Optional Reflex declarations are composed at L1, not imported by Agent code.
    from artemis.tools.reflex_tool import reflex_tool_declarations

    ctx.reflex_tool_declarations = reflex_tool_declarations
