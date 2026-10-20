"""I1: the ESP32 HID path must prove it is ready before a reflex skill runs.

The 2026-10-19 run spent ~11 minutes blind-clicking because nothing checked
``mounted/suspended/mouse/keyboard`` before the loop started. These tests pin the
gate: the runtime asks the board for its ``usb`` state, refuses to run on a bad
state, and reports the reason instead of letting the agent act blind.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from artemis.reflex.control import Esp32HidControl
from artemis.tools import reflex_tool as reflex_tool_module
from artemis.tools.reflex_tool import play_reflex

READY_COMPOSITE = "usb mounted=1 suspended=0 mouse=1 keyboard=1 touch=1 mode=composite\n"
READY_IOS = "usb mounted=1 suspended=0 mouse=1 keyboard=1 touch=0 mode=ios\n"


class FakeCapture:
    def __init__(self, *args, **kwargs):
        self.grabbed = 0

    def grab(self):
        self.grabbed += 1
        from artemis.reflex.contract import Frame

        return Frame(image=b"\xff\xd8\xff" + b"x" * 8, captured_at=0.0, capture_ms=9.0)

    def close(self) -> None:
        return None


class FakeEspControl:
    def __init__(self, port: str):
        self.port = port
        self.preflights = 0
        self.calls: list = []

    def preflight(self) -> None:
        self.preflights += 1

    def dispatch(self, gesture) -> float:
        self.calls.append(gesture)
        return 1.0


def _ctx():
    return SimpleNamespace(device=SimpleNamespace(device_id="SERIAL"), data_engine=None)


@pytest.fixture
def wired(monkeypatch):
    monkeypatch.setattr(reflex_tool_module, "default_capture", FakeCapture)
    monkeypatch.setattr(reflex_tool_module, "esp32_port", lambda: "FAKE")
    controls: list[FakeEspControl] = []

    def factory(port: str):
        control = FakeEspControl(port)
        controls.append(control)
        return control

    monkeypatch.setattr(reflex_tool_module, "Esp32HidControl", factory)
    return controls


def test_play_reflex_preflights_the_esp32_before_running(wired):
    result = asyncio.run(play_reflex.execute(driver=None, ctx=_ctx(), skill="noop", seconds=0.3))

    assert "reflex[noop]" in result
    assert wired, "the ESP32 control must be constructed"
    assert wired[0].preflights == 1, "the gate must run exactly once, before the session"


def test_play_reflex_reports_a_failed_esp32_preflight(monkeypatch, wired):
    class Refusing(FakeEspControl):
        def preflight(self) -> None:
            raise RuntimeError("suspended=1; unlock the phone and disable Auto-Lock")

    monkeypatch.setattr(reflex_tool_module, "Esp32HidControl", lambda port: Refusing(port))

    result = asyncio.run(play_reflex.execute(driver=None, ctx=_ctx(), skill="noop", seconds=0.3))

    assert result.startswith("Error:")
    assert "suspended=1" in result and "unlock" in result


def _control(response: str) -> Esp32HidControl:
    return Esp32HidControl("FAKE", reader=lambda line: response)


def test_preflight_accepts_a_ready_composite_board():
    state = _control(READY_COMPOSITE).preflight()

    assert state["mounted"] == 1
    assert state["suspended"] == 0
    assert state["mouse"] == 1
    assert state["keyboard"] == 1
    assert state["mode"] == "composite"


def test_preflight_accepts_the_ios_build_without_a_digitizer():
    state = _control(READY_IOS).preflight()

    assert state["touch"] == 0
    assert state["mode"] == "ios"


def test_preflight_fails_fast_when_the_bus_is_suspended():
    response = "usb mounted=1 suspended=1 mouse=0 keyboard=0 touch=1 mode=composite\n"

    with pytest.raises(RuntimeError) as exc:
        _control(response).preflight()

    message = str(exc.value)
    assert "suspended=1" in message
    assert "unlock" in message.lower()


def test_preflight_fails_fast_when_the_board_is_not_mounted():
    response = "usb mounted=0 suspended=0 mouse=0 keyboard=0 touch=0 mode=composite\n"

    with pytest.raises(RuntimeError) as exc:
        _control(response).preflight()

    message = str(exc.value)
    assert "mounted=0" in message
    assert "hub" in message.lower() or "otg" in message.lower()


def test_preflight_rejects_a_response_without_a_usb_state_line():
    with pytest.raises(RuntimeError) as exc:
        _control("boot noise, no state here\n").preflight()

    assert "usb" in str(exc.value).lower()


def test_the_usb_state_line_is_parsed_without_assuming_mode():
    from artemis.reflex.control import parse_usb_state

    state = parse_usb_state("boot\nusb mounted=1 suspended=0 mouse=1 keyboard=0 touch=0 mode=ios\n")

    assert state == {
        "mounted": 1,
        "suspended": 0,
        "mouse": 1,
        "keyboard": 0,
        "touch": 0,
        "mode": "ios",
    }


def test_play_reflex_uses_the_drivers_pointer_control_when_touch_is_relative():
    """iOS has no digitizer: the skill must be driven through the pointer adapter."""
    from artemis.devices.capabilities import CapabilitySet

    dispatched: list = []
    preflights: list[bool] = []

    class FakePointerControl:
        def preflight(self):
            preflights.append(True)
            return {"mounted": 1}

        def dispatch(self, gesture):
            dispatched.append(gesture)
            return 1.0

    driver = SimpleNamespace(
        capabilities=CapabilitySet(supports_relative_pointer=True, requires_pointer_precheck=True),
        pointer_control=lambda: FakePointerControl(),
    )
    monkeypatch_targets = reflex_tool_module
    original_capture = monkeypatch_targets.default_capture
    monkeypatch_targets.default_capture = FakeCapture
    try:
        result = asyncio.run(
            play_reflex.execute(driver=driver, ctx=_ctx(), skill="noop", seconds=0.3)
        )
    finally:
        monkeypatch_targets.default_capture = original_capture

    assert "reflex[noop]" in result
    assert preflights == [True], "the pointer path proves the board is ready first"


def test_the_pointer_control_is_only_chosen_when_absolute_tap_is_missing(monkeypatch):
    from artemis.devices.capabilities import CapabilitySet

    calls: list[str] = []

    class Androidish:
        capabilities = CapabilitySet(supports_absolute_tap=True)

        def pointer_control(self):
            calls.append("pointer_control")
            return SimpleNamespace(preflight=lambda: {}, dispatch=lambda gesture: 1.0)

    monkeypatch.setattr(reflex_tool_module, "default_capture", FakeCapture)
    monkeypatch.setattr(reflex_tool_module, "esp32_port", lambda: None)
    monkeypatch.setattr(
        reflex_tool_module,
        "AdbInputControl",
        lambda serial: SimpleNamespace(dispatch=lambda g: 1.0),
    )

    asyncio.run(play_reflex.execute(driver=Androidish(), ctx=_ctx(), skill="noop", seconds=0.3))

    assert calls == [], "a device with absolute touch keeps its own path"
