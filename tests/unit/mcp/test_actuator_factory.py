"""Actuator selection is no longer hard-coded in the generic executor."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from artemis.mcp.actuators.adb import AdbActuator
from artemis.mcp.actuators.factory import build_actuator


def test_an_injected_actuator_wins():
    sentinel = object()
    ctx = SimpleNamespace(actuator=sentinel)

    assert build_actuator(ctx) is sentinel


def test_the_default_is_an_adb_actuator():
    ctx = SimpleNamespace()
    controller = object()

    actuator = build_actuator(ctx, controller)

    assert isinstance(actuator, AdbActuator)
    assert actuator.controller is controller


def test_the_executor_uses_the_factory():
    from artemis.mcp.action_executor import McpActionExecutor

    ctx = SimpleNamespace(device=SimpleNamespace(device_width=1080, device_height=2400))
    controller = object()
    sentinel = SimpleNamespace(controller=controller, capabilities=lambda: frozenset())

    with patch("artemis.mcp.action_executor.build_actuator", return_value=sentinel) as factory:
        executor = McpActionExecutor(ctx, controller=controller)

    factory.assert_called_once_with(ctx, controller)
    assert executor.actuator is sentinel
