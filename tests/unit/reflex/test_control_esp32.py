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


def test_software_home_walks_back_in_segments(esp32):
    """Contract change (2026-10-21): the firmware's 2 ms burst loses reports, so the
    anchor is a paced walk. The raw `home` command is covered separately."""
    from artemis.reflex.control import ESP32_HOME_SPAN

    control, recorder = esp32()
    control.mouse_home()
    moves = [line for line in recorder.lines if line.startswith("move ")]
    assert moves, "the walk goes out as relative moves"
    assert sum(int(line.split()[1]) for line in moves) == -ESP32_HOME_SPAN


def test_ios_home_sends_cmd_h(esp32):
    control, recorder = esp32()
    control.ios_home()
    assert recorder.lines == ["keymod 8 11\n"]


def test_a_partial_serial_write_is_completed():
    """os.write may accept fewer bytes than asked; a short write loses the newline.

    The firmware only executes a line that ends with '\n' and answers a fragment with
    `ERR line too long` once the next lines pile up behind it (observed live 2026-10-21).
    """
    from artemis.reflex.control import write_line

    written = bytearray()

    def fake_write(_fd, data):
        chunk = bytes(data[:3])  # a tty that only accepts 3 bytes at a time
        written.extend(chunk)
        return len(chunk)

    write_line(7, b"move 26 -5\n", write=fake_write)

    assert bytes(written) == b"move 26 -5\n"


def test_a_zero_byte_write_fails_loudly():
    import pytest

    from artemis.reflex.control import write_line

    with pytest.raises(RuntimeError):
        write_line(7, b"home\n", write=lambda fd, data: 0)


class _FakeSerial:
    """Drive _send_with_response through an in-memory serial line."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.written = []

    def send_once(self, command, *, timeout=None):
        self.written.append(command)
        outcome = self.responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_a_line_too_long_error_resynchronises_and_retries_once():
    """`ERR line too long` means the board discarded a fragment; the command never ran.

    Observed live (2026-10-21) on a `move` right after a burst of frame grabs. The board
    already resynchronises itself, so a single retry is safe and turns a rare transport
    hiccup into a completed action instead of an incident.
    """
    from artemis.reflex.control import Esp32HidControl

    fake = _FakeSerial(
        [
            RuntimeError("ESP32 command 'move 81 200' failed: ERR line too long"),
            "[cmd] move 81 200\r\nOK",
        ]
    )
    control = Esp32HidControl("/dev/fake", writer=lambda line: None)
    control._send_once = fake.send_once

    assert "OK" in control._send_with_response("move 81 200")
    assert fake.written == ["move 81 200", "move 81 200"]


def test_other_errors_are_not_retried():
    import pytest

    from artemis.reflex.control import Esp32HidControl

    fake = _FakeSerial([RuntimeError("ESP32 command 'usb' failed: ERR bad args")])
    control = Esp32HidControl("/dev/fake", writer=lambda line: None)
    control._send_once = fake.send_once

    with pytest.raises(RuntimeError):
        control._send_with_response("usb")
    assert fake.written == ["usb"]


def test_a_large_move_is_segmented_to_fit_the_firmware_int8():
    """The firmware truncates each axis to int8, so a 200-unit move wraps backwards.

    Documented in the iOS HID pointer knowledge note and observed live: `move 81 200`
    is a wrap, not a long move. The host must segment and pace instead.
    """
    from artemis.reflex.control import ESP32_MAX_MOVE, Esp32HidControl

    lines = []
    control = Esp32HidControl("/dev/fake", writer=lambda line: lines.append(line))
    control._writer = None  # exercise the real segmentation path
    control._send_with_response = lambda command, timeout=None: lines.append(command + "\n") or ""

    control.mouse_move(200, -300)

    moves = [line.strip() for line in lines if line.startswith("move")]
    assert moves, "the move must reach the wire"
    for line in moves:
        _cmd, dx, dy = line.split()
        assert abs(int(dx)) <= ESP32_MAX_MOVE
        assert abs(int(dy)) <= ESP32_MAX_MOVE
    assert sum(int(line.split()[1]) for line in moves) == 200
    assert sum(int(line.split()[2]) for line in moves) == -300


def test_named_keys_map_to_hid_usages_and_home_is_cmd_h():
    """The firmware only understands numeric usages; iOS has no Back key at all.

    Live (2026-10-21): the Operator pressed BACK on iOS, the name reached the firmware
    verbatim, and it answered `usage: key <usage 0-255>`.
    """
    import pytest

    from artemis.reflex.control import Esp32HidControl

    sent = []
    control = Esp32HidControl("/dev/fake", writer=lambda line: sent.append(line.strip()))
    control._writer = lambda line: sent.append(line.strip())  # no OK ack needed

    control.press_named_key("enter")
    control.press_named_key("delete")
    control.press_named_key("home")

    assert sent == ["key 40", "key 42", "keymod 8 11"]

    with pytest.raises(ValueError, match="no Back key"):
        control.press_named_key("back")


def test_home_is_sent_as_paced_relative_moves_not_one_burst():
    """The firmware's `home` blasts 50 reports at 2 ms while USB polls at ~10 ms, so the
    pointer lands somewhere unpredictable (project knowledge note). Software home walks
    back in ACK-paced int8 segments instead."""
    from artemis.reflex.control import ESP32_HOME_SPAN, Esp32HidControl

    lines = []
    control = Esp32HidControl("/dev/fake")
    control._writer = lambda line: lines.append(line.strip())

    control.mouse_home()

    assert "home" not in lines, "the firmware burst is not used for anchoring"
    moves = [line for line in lines if line.startswith("move ")]
    assert len(moves) >= 2, "the walk back is paced by the ack channel"
    assert sum(int(line.split()[1]) for line in moves) == -ESP32_HOME_SPAN
    assert sum(int(line.split()[2]) for line in moves) == -ESP32_HOME_SPAN
    for line in moves:
        _cmd, dx, dy = line.split()
        assert abs(int(dx)) <= 127 and abs(int(dy)) <= 127


def test_the_firmware_home_command_is_still_available_for_diagnostics():
    from artemis.reflex.control import Esp32HidControl

    lines = []
    control = Esp32HidControl("/dev/fake")
    control._writer = lambda line: lines.append(line.strip())

    control.firmware_home()

    assert lines == ["home"]


def test_named_shortcuts_are_cmd_combinations():
    """Keyboard first on iOS: Spotlight/Home/app-switch/closing a sheet need no pointer.

    Verified on the rig 2026-10-21: Cmd+Space → type "taobao" → Down → Enter launched
    Taobao, which is far more reliable than aiming the pointer at an icon.
    """
    from artemis.reflex.control import Esp32HidControl

    sent: list[str] = []
    control = Esp32HidControl("/dev/fake", writer=lambda line: sent.append(line.strip()))

    control.press_named_key("spotlight")
    control.press_named_key("home")
    control.press_named_key("app_switch")
    control.press_named_key("close")

    assert sent == ["keymod 8 44", "keymod 8 11", "keymod 8 43", "keymod 8 26"]


def test_escape_and_enter_are_plain_usages():
    from artemis.reflex.control import Esp32HidControl

    sent: list[str] = []
    control = Esp32HidControl("/dev/fake", writer=lambda line: sent.append(line.strip()))

    control.press_named_key("escape")
    control.press_named_key("enter")

    assert sent == ["key 41", "key 40"]
