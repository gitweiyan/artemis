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
