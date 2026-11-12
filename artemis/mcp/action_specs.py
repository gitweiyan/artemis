"""Canonical action wire helpers and compatibility re-exports (L1)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import inspect
from typing import Annotated, Any

from mcp.types import CallToolResult

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
    operator_shell_tool,
    tool_declaration,
    wire_dialects,
)
from artemis.core.action_types import ActionResult

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


def make_wire_handler(
    spec: ActionSpec,
    actuator: Any,
    wrap: Callable[[ActionResult], CallToolResult],
    wrap_exception: Callable[[str, Exception], CallToolResult],
) -> Callable[..., Awaitable[CallToolResult]]:
    """Builds the FastMCP tool function for one wire dialect.

    The returned coroutine carries an explicit ``__signature__`` so FastMCP derives
    the same argument model a literal ``async def`` produced historically, and the
    same ``Annotated[CallToolResult, ActionResult]`` return annotation so the
    structured-output schema is unchanged.
    """
    wire = spec.wire
    if wire is None:
        raise ValueError(f"Action '{spec.name}' has no wire dialect.")

    async def handler(**kwargs: Any) -> Any:
        try:
            return wrap(await wire.bind(actuator, kwargs))
        except Exception as e:  # pylint: disable=broad-exception-caught
            return wrap_exception(spec.name, e)

    return_annotation = Annotated[CallToolResult, ActionResult]
    parameters = [
        inspect.Parameter(
            p.name,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=p.annotation,
            default=inspect.Parameter.empty if p.required else p.default,
        )
        for p in wire.params
    ]
    handler.__name__ = spec.name
    handler.__qualname__ = spec.name
    handler.__doc__ = wire.description
    handler.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
        parameters, return_annotation=return_annotation
    )
    handler.__annotations__ = {p.name: p.annotation for p in wire.params}
    handler.__annotations__["return"] = return_annotation
    return handler
