"""A described coordinate click is resolved by LocateAnything on iOS."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

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
    return McpActionExecutor(ctx, actuator=actuator)


def _state(tmp_path):
    shot = tmp_path / "shot.png"
    shot.write_bytes(b"\x89PNG\r\n\x1a\n")
    return SimpleNamespace(latest_screenshot=str(shot))


def test_a_described_coordinate_click_uses_the_service_point(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTEMIS_IOS_LOCATE_URL", "http://127.0.0.1:7862")
    executor = _executor()
    executor._locate_service = _FakeLocator((0.968, 0.696))

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
    assert executor._locate_service.calls == ["关闭天天免单弹框的X"]


def test_an_index_target_is_not_relocated(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTEMIS_IOS_LOCATE_URL", "http://127.0.0.1:7862")
    executor = _executor()
    executor._locate_service = _FakeLocator((0.5, 0.5))
    args = {"target": 3, "target_description": "搜索"}

    out, record = asyncio.run(executor._locate_coordinate_target("click", args, _state(tmp_path)))

    assert out is args
    assert record is None
    assert executor._locate_service.calls == []


def test_a_service_failure_keeps_the_model_point(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTEMIS_IOS_LOCATE_URL", "http://127.0.0.1:7862")
    executor = _executor()
    executor._locate_service = _FakeLocator(None)
    args = {"target": [900, 800], "target_description": "搜索按钮"}

    out, record = asyncio.run(executor._locate_coordinate_target("click", args, _state(tmp_path)))

    assert out is args
    assert record is not None
    assert record["source"] == "locate-anything"
    assert record["error"]
    assert record["model_point"] == [900, 800]
