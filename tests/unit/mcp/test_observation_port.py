"""Observation Port compatibility adapter (ADR-006 Phase 3d)."""

from __future__ import annotations

import asyncio
import base64
from types import SimpleNamespace

from artemis.mcp.observation import ControllerObservationPort, ObservationPort

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode("ascii")


class _Controller:
    def __init__(self, *, fail=False):
        self.fail = fail

    async def get_screen_data(self, skip_settling=False):
        if self.fail:
            raise RuntimeError("device gone")
        return SimpleNamespace(
            base64=PNG,
            elements="<hierarchy><node text='Hi' bounds='[0,0][100,100]' /></hierarchy>",
            width=1080,
            height=2400,
        )


def _ctx():
    return SimpleNamespace(device=None, data_engine=None)


def test_the_controller_adapter_satisfies_the_observation_port():
    port = ControllerObservationPort(_ctx(), _Controller())

    assert isinstance(port, ObservationPort)
    result, image = asyncio.run(port.observe(settle_ms=0))

    assert result.ok is True
    assert image is not None
    assert result.width == 1080
    assert result.screenshot_path


def test_capture_failures_keep_the_historical_observe_result():
    port = ControllerObservationPort(_ctx(), _Controller(fail=True))
    result, image = asyncio.run(port.observe(settle_ms=0))

    assert result.ok is False
    assert image is None
    assert "Failed to capture screen state" in result.message


def test_session_observation_port_prefers_the_session_source():
    from artemis.mcp.observation import SessionObservationPort
    from artemis.devices.session import DeviceSession

    class Source:
        async def snapshot(self):
            from artemis.perception.base import Frame

            return Frame(
                image=base64.b64decode(PNG),
                captured_at=1.0,
                width=1080,
                height=2400,
                source="fake",
            )

        async def ui_tree(self):
            return None

    session = DeviceSession(perception=[Source()], control=[])
    ctx = SimpleNamespace(device=None, data_engine=None, device_session=session)
    port = SessionObservationPort(ctx)

    result, image = asyncio.run(port.observe(settle_ms=0))

    assert result.ok is True
    assert image is not None
    assert result.width == 1080


def test_a_session_source_without_screen_data_uses_snapshot_and_ui_tree():
    import asyncio

    from artemis.devices.session import DeviceSession
    from artemis.mcp.observation import get_screen_data
    from artemis.perception.base import Frame, UIElement

    class Source:
        async def snapshot(self):
            return Frame(
                image=base64.b64decode(PNG),
                captured_at=1.0,
                width=1080,
                height=2400,
                source="ios-uvc",
            )

        async def ui_tree(self):
            return [UIElement("Settings", (10, 20, 100, 60), "settings")]

    session = DeviceSession(perception=[Source()], control=[])
    ctx = SimpleNamespace(device=None, data_engine=None, device_session=session)
    data = asyncio.run(get_screen_data(ctx, controller=object()))

    assert data.width == 1080
    assert data.elements[0]["text"] == "Settings"
    assert data.platform == "ios-uvc"


def test_a_failed_session_cannot_bypass_the_controller_fallback():
    import asyncio
    import pytest

    from artemis.devices.session import DeviceSession, DeviceSessionError
    from artemis.mcp.observation import get_screen_data
    from artemis.perception.base import Frame

    class Source:
        async def snapshot(self):
            return Frame(b"x", 1.0, 1, 1, "fake")

    session = DeviceSession(perception=[Source()], control=[])
    session.mark_failed("lost")
    ctx = SimpleNamespace(device=None, data_engine=None, device_session=session)

    with pytest.raises(DeviceSessionError):
        asyncio.run(get_screen_data(ctx, controller=object()))
