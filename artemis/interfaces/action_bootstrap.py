"""L1 composition hook: attach the L3 ActionService for an agent run."""

from __future__ import annotations

from typing import Any


def attach_action_service(ctx: Any) -> None:
    """Build the compatibility MCP port and attach it to the task context."""
    if getattr(ctx, "action_service", None) is not None:
        return
    from artemis.actions.service import ActionService
    from artemis.mcp.action_executor import McpActionExecutor

    ctx.action_service = ActionService(McpActionExecutor(ctx), ctx)
