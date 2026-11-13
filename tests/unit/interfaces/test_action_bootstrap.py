"""L1 composition injects observation independently of action dispatch."""

from __future__ import annotations

from types import SimpleNamespace

from artemis.interfaces.action_bootstrap import attach_action_service
from artemis.perception.observation import SessionObservationPort


class _Session:
    def __init__(self):
        self.owners = []

    def register_cleanup_owner(self, name, closer):
        self.owners.append((name, closer))


class _Action:
    async def close(self):
        return None


def test_session_composition_injects_observation_without_a_controller():
    session = _Session()
    ctx = SimpleNamespace(device_session=session, action_service=_Action())

    attach_action_service(ctx)

    assert getattr(ctx, "controller", None) is None
    assert isinstance(ctx.observation_port, SessionObservationPort)
    assert session.owners == [("action_service", ctx.action_service.close)]
