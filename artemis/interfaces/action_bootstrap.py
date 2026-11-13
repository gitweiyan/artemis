"""L1 composition hook: attach the L3 ActionService for an agent run."""

from __future__ import annotations

from typing import Any


def attach_action_service(ctx: Any) -> None:
    """Compose the explicit ActionService and ObservationPort at L1."""
    from artemis.actions.service import ActionService
    from artemis.mcp.action_executor import McpActionExecutor
    from artemis.mcp.action_port import McpActionPort
    from artemis.mcp.action_transport import McpActionTransport
    from artemis.perception.observation import (
        ControllerObservationPort,
        SessionObservationPort,
    )

    # The observation dependency is independent from the action service. A real
    # DeviceSession is authoritative; only a run without one gets the legacy
    # Controller adapter.
    if getattr(ctx, "observation_port", None) is None:
        if getattr(ctx, "device_session", None) is not None:
            ctx.observation_port = SessionObservationPort(ctx)
        else:
            if getattr(ctx, "controller", None) is None:
                from artemis.controllers.unified_controller import UnifiedMobileController

                ctx.controller = UnifiedMobileController(ctx)
            ctx.observation_port = ControllerObservationPort(ctx, ctx.controller)

    if getattr(ctx, "action_service", None) is not None:
        if getattr(ctx, "device_session", None) is not None:
            try:
                ctx.device_session.register_cleanup_owner(
                    "action_service", ctx.action_service.close
                )
            except ValueError:
                pass
        return
    executor = McpActionExecutor(ctx)
    transport = McpActionTransport(ctx)
    ctx.action_service = ActionService(
        ctx,
        McpActionPort(transport),
        observation_port=ctx.observation_port,
        helper_port=executor,
    )
    if getattr(ctx, "device_session", None) is not None:
        try:
            ctx.device_session.register_cleanup_owner("action_service", ctx.action_service.close)
        except ValueError:
            # Already registered by an earlier composition pass.
            pass
    # Optional Reflex declarations are composed at L1, not imported by Agent code.
    from artemis.tools.reflex_tool import reflex_tool_declarations

    ctx.reflex_tool_declarations = reflex_tool_declarations
