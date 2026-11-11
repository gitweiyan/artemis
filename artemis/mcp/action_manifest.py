"""Compatibility shim: action manifest moved to artemis.actions (Phase 9)."""

from __future__ import annotations

from artemis.actions.manifest import (  # noqa: F401
    AGENT_ROLES,
    ALL_KNOWN_TOOLS,
    BACKEND_INDEPENDENT_TOOLS,
    DEVICE_ACTIONS,
    INTERNAL_ACTIONS,
    MIN_EXTENSION_DESCRIPTION_LENGTH,
    OPTIONAL_ACTIONS,
    REQUIRED_ACTIONS,
    ActuatorContractError,
    ExtensionTool,
    available_device_actions,
    filter_declarations,
    validate_actuator,
)

__all__ = [
    "AGENT_ROLES",
    "ALL_KNOWN_TOOLS",
    "BACKEND_INDEPENDENT_TOOLS",
    "DEVICE_ACTIONS",
    "INTERNAL_ACTIONS",
    "MIN_EXTENSION_DESCRIPTION_LENGTH",
    "OPTIONAL_ACTIONS",
    "REQUIRED_ACTIONS",
    "ActuatorContractError",
    "ExtensionTool",
    "available_device_actions",
    "filter_declarations",
    "validate_actuator",
]
