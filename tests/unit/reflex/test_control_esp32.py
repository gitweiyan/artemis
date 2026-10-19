"""The ESP32-S3 HID touch transport: the third ``Control``, and its command layer.

The board is a USB HID touchscreen for the phone; the Mac talks to it over its UART
USB-C port as a plain serial console. What this file pins down is the **host** half of
that contract: the exact text commands the reference firmware parses (``tap``,
``long_press``, ``swipe``), the coordinate frame they are expressed in, and the failure
behaviour the reflex loop relies on. No board is needed: the writer is a seam.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from artemis.reflex.contract import Gesture
from artemis.reflex.control import Esp32HidControl

SKILLS = Path(__file__).resolve().parents[3] / "skills"


class Recorder:
    """A stand-in for the serial console: collects the exact lines ``_send`` writes."""

    def __init__(self):
        self.lines: list[str] = []

    def __call__(self, line: str) -> None:
        self.lines.append(line)


@pytest.fixture
def esp32():
    """A control wired to a recorder, in the reference frame the firmware expects."""

    def build(**kwargs):
        recorder = Recorder()
        control = Esp32HidControl("FAKE", writer=recorder, **kwargs)
        return control, recorder

    return build


def test_a_tap_is_the_firmware_tap_command(esp32):
    control, recorder = esp32()

    control.dispatch(Gesture.tap(540, 1200))

    assert recorder.lines == ["tap 540 1200\n"]


def test_a_swipe_is_the_firmware_swipe_command(esp32):
    control, recorder = esp32()

    control.dispatch(Gesture.swipe(100, 200, 300, 400, duration_ms=250))

    assert recorder.lines == ["swipe 100 200 300 400 250\n"]


def test_a_long_held_tap_becomes_a_long_press(esp32):
    control, recorder = esp32()

    control.dispatch(Gesture(kind="tap", points=((540.0, 1200.0),), duration_ms=800))

    assert recorder.lines == ["long_press 540 1200 800\n"]


def test_gesture_coordinates_are_scaled_into_the_firmware_reference_frame(esp32):
    """The skill works in the device frame (720x1600); the board wants its 1080x2400 reference."""
    control, recorder = esp32(screen=(720, 1600))

    control.dispatch(Gesture.tap(360, 800))

    assert recorder.lines == ["tap 540 1200\n"], "360/720 * 1080 = 540, 800/1600 * 2400 = 1200"


def test_a_gesture_outside_the_frame_is_clamped_not_refused(esp32):
    control, recorder = esp32(screen=(720, 1600))

    control.dispatch(Gesture.tap(1000, -50))

    assert recorder.lines == ["tap 1080 0\n"]


def test_dispatch_returns_its_latency_in_milliseconds(esp32):
    control, _ = esp32()

    elapsed = control.dispatch(Gesture.tap(540, 1200))

    assert isinstance(elapsed, float) and elapsed >= 0.0


def test_a_missing_port_is_a_runtime_error_the_loop_can_catch():
    """No writer: the real serial path. A bad port must fail the way ``_CONTROL_ERRORS`` expects."""
    control = Esp32HidControl("/dev/cu.definitely-not-a-real-esp32-port")

    with pytest.raises(RuntimeError):
        control.dispatch(Gesture.tap(1, 2))


def test_an_unknown_gesture_kind_is_a_value_error(esp32):
    control, _ = esp32()

    with pytest.raises(ValueError):
        control.dispatch(Gesture(kind="pinch", points=((1.0, 2.0),)))


def test_the_control_is_not_built_until_a_gesture_needs_it(esp32):
    """Construction must not touch the device: a skill can be loaded with no board present."""
    control, recorder = esp32()
    assert control._fd is None
    control.dispatch(Gesture.tap(1, 2))
    assert control._fd is None, "the recorder seam is used, so no serial fd is ever opened"


def test_the_esp32_port_is_named_by_the_environment(monkeypatch):
    from artemis.reflex.control import esp32_port

    monkeypatch.delenv("ARTEMIS_ESP32_PORT", raising=False)
    assert esp32_port() is None

    monkeypatch.setenv("ARTEMIS_ESP32_PORT", "  /dev/cu.usbmodem123  ")
    assert esp32_port() == "/dev/cu.usbmodem123", "a named port is stripped, not used verbatim"


# --------------------------------------------------------------------------- #
# The board is a full HID: keyboard and mouse, not just a touchscreen
# --------------------------------------------------------------------------- #


def test_typing_sends_the_firmware_type_command(esp32):
    control, recorder = esp32()
    control.type_text("hello 42")
    assert recorder.lines == ["type hello 42\n"]


def test_a_newline_is_typed_as_enter(esp32):
    control, recorder = esp32()
    control.type_text("one\ntwo")
    assert recorder.lines == ["type one\n", "key 40\n", "type two\n"]


def test_typing_refuses_what_the_board_cannot_type(esp32):
    """The firmware maps ASCII keys only; silently dropping characters types the wrong thing."""
    control, recorder = esp32()
    with pytest.raises(ValueError):
        control.type_text("你好")
    assert recorder.lines == []


def test_pressing_a_key_sends_its_hid_usage(esp32):
    control, recorder = esp32()
    control.press_key(40)  # Enter
    assert recorder.lines == ["key 40\n"]


def test_a_key_can_be_held_and_released(esp32):
    control, recorder = esp32()
    control.key_down(80)
    control.key_up(80)
    assert recorder.lines == ["kdown 80\n", "kup 80\n"]


def test_the_mouse_moves_relative_and_clicks(esp32):
    control, recorder = esp32()
    control.mouse_move(10, -5)
    control.mouse_click()
    assert recorder.lines == ["move 10 -5\n", "click\n"]


def test_mouse_buttons_are_separate_press_and_release(esp32):
    control, recorder = esp32()
    control.mouse_down()
    control.mouse_up()
    assert recorder.lines == ["mdown\n", "mup\n"]


def test_keyboard_and_mouse_go_through_the_same_lazy_port(esp32):
    """No writer seam open, only a gesture opens the port — a test never touches a device."""
    control, _ = esp32()
    control.type_text("x")
    assert control._fd is None


def test_long_type_text_is_split_into_bounded_commands(esp32):
    control, recorder = esp32()
    text = "a" * 300

    control.type_text(text)

    assert all(len(line) <= 126 for line in recorder.lines)
    typed = "".join(line[len("type ") :].rstrip("\n") for line in recorder.lines)
    assert typed == text


def test_home_sends_the_firmware_home_command(esp32):
    control, recorder = esp32()
    control.mouse_home()
    assert recorder.lines == ["home\n"]


def test_ios_home_sends_cmd_h(esp32):
    control, recorder = esp32()
    control.ios_home()
    assert recorder.lines == ["keymod 8 11\n"]
