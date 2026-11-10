"""A described coordinate click is resolved by LocateAnything on iOS."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from artemis.core.capabilities import CapabilityDeclaration, CapabilityId, CapabilityManifest
from artemis.core.platform_extension import PlatformExtension
from artemis.mcp.action_executor import McpActionExecutor


class _FakeLocator:
    url = "http://127.0.0.1:7862"

    def __init__(self, point):
        self.point = point
        self.calls: list[str] = []

    def locate(self, image, prompt):
        self.calls.append(prompt)
        return self.point


def _executor():
    ctx = Mock()
    ctx.device.mobile_platform = "ios"
    ctx.device.device_width = 1080
    ctx.device.device_height = 2400
    actuator = Mock()
    actuator.controller = Mock()
    actuator.extensions.return_value = []
    return McpActionExecutor(ctx, actuator=actuator)


def _attach_locator(executor, locator):
    extension = PlatformExtension(
        extension_id="test.grounding",
        platform_family="test",
        capabilities=CapabilityManifest(
            adapter_id="test",
            adapter_version="1",
            declarations=(CapabilityDeclaration(CapabilityId.GROUNDING_LOCATE),),
        ),
        grounding=locator,
    )
    executor.ctx.device_session = SimpleNamespace(adapter_state={"platform_extension": extension})


def _state(tmp_path):
    shot = tmp_path / "shot.png"
    shot.write_bytes(b"\x89PNG\r\n\x1a\n")
    return SimpleNamespace(latest_screenshot=str(shot))


def test_a_described_coordinate_click_uses_the_service_point(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTEMIS_IOS_LOCATE_URL", "http://127.0.0.1:7862")
    executor = _executor()
    locator = _FakeLocator((0.968, 0.696))
    _attach_locator(executor, locator)

    args, record = asyncio.run(
        executor._locate_coordinate_target(
            "click",
            {"target": [900, 800], "target_description": "关闭天天免单弹框的X"},
            _state(tmp_path),
        )
    )

    assert args["target"] == [968, 696]
    assert record["point"] == [968, 696]
    assert record["model_point"] == [900, 800]
    assert locator.calls == ["关闭天天免单弹框的X"]


def test_an_index_target_is_not_relocated(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTEMIS_IOS_LOCATE_URL", "http://127.0.0.1:7862")
    executor = _executor()
    locator = _FakeLocator((0.5, 0.5))
    _attach_locator(executor, locator)
    args = {"target": 3, "target_description": "搜索"}

    out, record = asyncio.run(executor._locate_coordinate_target("click", args, _state(tmp_path)))

    assert out is args
    assert record is None
    assert locator.calls == []


def test_a_service_failure_keeps_the_model_point(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTEMIS_IOS_LOCATE_URL", "http://127.0.0.1:7862")
    executor = _executor()
    _attach_locator(executor, _FakeLocator(None))
    args = {"target": [900, 800], "target_description": "搜索按钮"}

    out, record = asyncio.run(executor._locate_coordinate_target("click", args, _state(tmp_path)))

    assert out is args
    assert record is not None
    assert record["source"] == "grounding"
    assert record["error"]
    assert record["model_point"] == [900, 800]


@pytest.mark.asyncio
async def test_a_timed_out_target_is_reported_to_the_model_as_unverified(tmp_path, monkeypatch):
    """The model only reads ``text_summary``: a silent fallback looks like a verified tap.

    2026-11-06 15:58: five clicks ran on the model's own coordinates (the locator was
    cooling down) and each was reported as a plain success; the run "randomly" clicked
    the account page, the address page and the wrong tab.
    """
    from artemis.mcp.action_types import ActionResult

    monkeypatch.setenv("ARTEMIS_IOS_LOCATE_URL", "http://127.0.0.1:7862")
    executor = _executor()

    class _TimedOutLocator:
        url = "http://127.0.0.1:7862"
        last_error = "TimeoutError: timed out"

        def locate(self, image, prompt):
            return None

    _attach_locator(executor, _TimedOutLocator())
    session = Mock()
    session.call = AsyncMock(
        return_value=ActionResult.success("click", "Tapped at [169, 76] (normalized).")
    )
    session.observe = AsyncMock(return_value=SimpleNamespace(ok=False))
    executor._session_or_start = AsyncMock(return_value=session)

    result = await executor.execute(
        "click",
        {"target": [169, 76], "target_description": "闪购 tab in top navigation"},
        "tc",
        _state(tmp_path),
    )

    assert "Tapped at [169, 76]" in result.text_summary
    assert "not verified" in result.text_summary.lower()
    assert result.metadata["target_unverified"] is True


def test_ask_explorer_uses_the_profile_grounding_locator(tmp_path):
    executor = _executor()
    _attach_locator(executor, _FakeLocator((0.25, 0.75)))

    text, ok = asyncio.run(executor._ask_explorer("gear icon", None, _state(tmp_path)))

    assert ok is True
    assert "normalized [250, 750]" in text
    assert "click(target=[250, 750]" in text
