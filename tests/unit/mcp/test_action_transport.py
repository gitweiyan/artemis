"""L1 transport reports fatal action incidents to the owning DeviceSession."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from artemis.core.action_types import ActionCode, ActionResult
from artemis.mcp.action_transport import McpActionTransport


class _Session:
    started = True

    async def call(self, name, args):
        return ActionResult(
            ok=False,
            code=ActionCode.DEVICE_ERROR,
            action=name,
            message="gone",
            incident="device_disconnected",
        )


class _DeviceSession:
    def __init__(self):
        self.faults = []

    def record_fault(self, fault):
        self.faults.append(fault)


def test_transport_does_not_expose_controller_and_reports_fault():
    device_session = _DeviceSession()
    ctx = SimpleNamespace(device_session=device_session)
    transport = McpActionTransport(ctx, actuator=SimpleNamespace(controller=object()))
    transport._session = _Session()

    assert not hasattr(transport, "controller")
    asyncio.run(transport.call("click", {}))

    assert len(device_session.faults) == 1
    assert device_session.faults[0].kind.value == "device_disconnected"
