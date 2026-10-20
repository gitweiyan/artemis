"""Gestures on the device, dispatched where they cost least.

Same two paths as capture: the helper dispatches through
``AccessibilityService.dispatchGesture`` (one loopback call, no process spawn),
``adb shell input`` is the fallback. The runtime measures what each call costs,
so the choice can be made on numbers rather than on hope.
"""

from __future__ import annotations

from typing import Any, Callable

import os
import re
import select
import subprocess
import time

from artemis.reflex.attach import AttachTimeout, attach_client
from artemis.reflex.contract import Control, Gesture
from third_party.mobile_use.utils.logger import get_logger

logger = get_logger(__name__)


#: Transport failures worth turning into "no frame"/"no gesture": ``TimeoutExpired``
#: is a ``SubprocessError``, not an ``OSError`` — the same "slow is not the same as
#: absent" lesson that cost a real run 59.8 s elsewhere in this repository.
_ATTACH_ERRORS = (
    RuntimeError,
    OSError,
    subprocess.SubprocessError,
    ValueError,
    KeyError,
    AttributeError,
    ImportError,
)


class HelperControl(Control):
    """Tap/swipe through the accessibility helper."""

    def __init__(
        self, serial: str, *, client: object | None = None, attach_deadline_s: float | None = None
    ):
        self.serial = serial
        self._client: Any = client
        self._owns_client = client is None
        self.attach_deadline_s = attach_deadline_s

    def _ensure_client(self):
        if self._client is None:
            self._client = attach_client(self.serial, deadline_s=self.attach_deadline_s or 20.0)
        return self._client

    def dispatch(self, gesture: Gesture) -> float:
        try:
            client = self._ensure_client()
        except _ATTACH_ERRORS + (AttachTimeout,) as exc:
            raise RuntimeError(
                f"the accessibility helper is not usable ({exc}); enable it on the device or"
                " run with capture='screencap'"
            ) from exc
        started = time.monotonic()
        if gesture.kind == "tap":
            ((x, y),) = gesture.points
            ok = client.tap(x, y)
        elif gesture.kind == "swipe":
            (x1, y1), (x2, y2) = gesture.points
            ok = client.swipe(x1, y1, x2, y2, gesture.duration_ms)
        else:
            raise ValueError(f"unsupported gesture kind: {gesture.kind!r}")
        elapsed = (time.monotonic() - started) * 1000.0
        if not ok:
            raise RuntimeError(f"helper refused {gesture.kind} ({gesture.description})")
        return elapsed


class AdbInputControl(Control):
    """Tap/swipe through ``adb shell input`` (the always-available fallback)."""

    def __init__(self, serial: str, *, timeout_s: float = 20.0):
        self.serial = serial
        self.timeout_s = timeout_s

    def _shell(self, command: str) -> None:
        try:
            result = subprocess.run(
                ["adb", "-s", self.serial, "shell", command],
                capture_output=True,
                timeout=self.timeout_s,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            # One exception type for callers: the loop treats any dispatch failure
            # the same way, and the message says what happened.
            raise RuntimeError(f"adb input failed: {exc}") from exc
        if result.returncode != 0:
            raise RuntimeError(f"adb input failed: {result.stderr[:200]!r}")

    def dispatch(self, gesture: Gesture) -> float:
        started = time.monotonic()
        if gesture.kind == "tap":
            ((x, y),) = gesture.points
            self._shell(f"input tap {int(x)} {int(y)}")
        elif gesture.kind == "swipe":
            (x1, y1), (x2, y2) = gesture.points
            self._shell(
                f"input swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {int(gesture.duration_ms)}"
            )
        else:
            raise ValueError(f"unsupported gesture kind: {gesture.kind!r}")
        return (time.monotonic() - started) * 1000.0


# --------------------------------------------------------------------------- #
# ESP32-S3 as a USB HID touchscreen
# --------------------------------------------------------------------------- #

#: The board's UART USB-C console speed (the reference firmware's monitor default).
DEFAULT_ESP32_BAUDRATE = 115200
#: Firmware line buffer is 128 bytes; the host never sends a line (with newline) above this.
MAX_ESP32_LINE = 126
#: The firmware's reference touch frame (its ``TOUCH_REF_WIDTH/HEIGHT``). The host states
#: phone pixels in this frame and the board rescales them to HID's 0..65535, so only the
#: aspect has to match the phone — the phone's real resolution does not matter.
ESP32_REF_WIDTH = 1080
ESP32_REF_HEIGHT = 2400
#: A tap held at least this long is sent as the firmware's separate ``long_press`` command.
ESP32_LONG_PRESS_MS = 500
#: HID keyboard usages the firmware documents (decimal): Enter, Esc, Tab, Space, arrows.
KEY_ENTER = 40
KEY_ESCAPE = 41
KEY_TAB = 43
KEY_SPACE = 44
KEY_RIGHT = 79
KEY_LEFT = 80
KEY_DOWN = 81
KEY_UP = 82
#: The firmware casts each ``move`` axis through ``int8_t``, so a bigger value wraps to
#: the opposite direction (a 200-unit move becomes a backwards jump). The host segments.
ESP32_MAX_MOVE = 127

#: How far a software ``home`` walks back: larger than any phone's pointer grid, so the
#: cursor clamps at the top-left corner whatever the gain is.
ESP32_HOME_SPAN = 2400

#: When set, gestures go through the ESP32-S3 HID board on this serial port (its UART
#: USB-C console) instead of the assisted paths. Unset keeps the previous choice.
ENV_ESP32_PORT = "ARTEMIS_ESP32_PORT"


def esp32_port() -> str | None:
    """The ESP32-S3 HID board's serial port if one is named for this run, else ``None``."""
    value = os.environ.get(ENV_ESP32_PORT)
    return value.strip() if value and value.strip() else None


def _serial_speed(baudrate: int) -> int:
    """The ``termios`` speed constant for ``baudrate`` (``B115200`` …)."""
    import termios

    speed = getattr(termios, f"B{int(baudrate)}", None)
    if speed is None:
        raise ValueError(f"unsupported serial baud rate {baudrate!r}")
    return speed


def _open_serial(port: str, baudrate: int) -> int:
    """Open ``port`` in raw mode: 8N1, no echo, no line discipline, no flow control.

    ``O_NONBLOCK`` is used only to get past the open (a modem line can block there); it is
    cleared right after, so a write to a full board buffer **waits** instead of raising
    ``EAGAIN``. That wait is required: a burst of swipe segments overran the board's serial
    buffer and aborted mid-draw with ``BlockingIOError`` before this was cleared. ``VMIN`` /
    ``VTIME`` are zero so a read never blocks. The caller owns closing the descriptor.
    """
    import fcntl
    import termios

    fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        fcntl.fcntl(fd, fcntl.F_SETFL, fcntl.fcntl(fd, fcntl.F_GETFL) & ~os.O_NONBLOCK)
        _iflag, _oflag, _cflag, _lflag, _ispeed, _ospeed, cc = termios.tcgetattr(fd)
        speed = _serial_speed(baudrate)
        cc = list(cc)
        cc[termios.VMIN] = 0
        cc[termios.VTIME] = 0
        termios.tcsetattr(
            fd,
            termios.TCSANOW,
            [
                0,  # iflag: no CR/NL translation, no flow control
                0,  # oflag: no output post-processing
                termios.CS8 | termios.CREAD | termios.CLOCAL,  # cflag: 8N1
                0,  # lflag: raw, no echo, no canonical
                speed,
                speed,
                cc,
            ],
        )
        termios.tcflush(fd, termios.TCIOFLUSH)
    except (OSError, ValueError):
        os.close(fd)
        raise
    return fd


#: The firmware's reply to a line it could not parse as one complete command. It always
#: drains to the next newline first, so the stream is resynchronised when this arrives.
_LINE_TOO_LONG = "ERR line too long"

#: The firmware's diagnostic line. ``mode`` is optional so old firmware still parses;
#: ``touch`` is informational — iOS builds legitimately report ``touch=0``.
_USB_STATE_RE = re.compile(
    r"usb mounted=(\d+) suspended=(\d+) mouse=(\d+) keyboard=(\d+) touch=(\d+)"
    r"(?: mode=(\w+))?"
)


def parse_usb_state(text: str) -> dict[str, int | str | None]:
    """Parse the last ``usb mounted=...`` line in a console response.

    Raises ``ValueError`` when the response has no state line, so the caller can turn
    that into an actionable preflight error rather than treating boot noise as ready.
    """
    matches = _USB_STATE_RE.findall(text or "")
    if not matches:
        raise ValueError("no 'usb mounted=...' line in the response")
    mounted, suspended, mouse, keyboard, touch, mode = matches[-1]
    return {
        "mounted": int(mounted),
        "suspended": int(suspended),
        "mouse": int(mouse),
        "keyboard": int(keyboard),
        "touch": int(touch),
        "mode": mode or None,
    }


def write_line(fd: int, data: bytes, *, write=None) -> None:
    """Write ``data`` completely to ``fd``, looping over short writes.

    ``os.write`` on a tty may accept fewer bytes than asked. The firmware executes only
    a line terminated by ``'\n'`` and discards a fragment, replying ``ERR line too long``
    once later commands pile up behind it — so a silently short write turns into a
    failure several commands later, which is exactly what a live run showed.
    """
    writer = write or os.write
    view = memoryview(data)
    while view:
        written = writer(fd, view)
        if written is None:
            written = len(view)  # a seam that writes everything by contract
        if written <= 0:
            raise RuntimeError(f"ESP32 serial write stalled after {len(data) - len(view)} bytes")
        view = view[written:]


class Esp32HidControl(Control):
    """Tap/swipe through an ESP32-S3 acting as a USB HID touchscreen for the phone.

    The Mac talks to the board over its **UART** USB-C port as a plain serial console; the
    board turns each line into USB HID reports (on its ``USB`` port) to the phone. The
    command set is the reference firmware's: ``tap x y``, ``long_press x y ms``,
    ``swipe x1 y1 x2 y2 ms``. Gestures arrive in the device frame the skill is calibrated
    in; they are scaled into the firmware's reference frame and clamped, so a device whose
    aspect differs by a pixel does not silently drop taps.

    The port opens lazily, on the first gesture: a skill can be loaded, calibrated and
    planned with no board attached, and a missing board surfaces as the same
    :class:`RuntimeError` the other controls raise — which the loop already handles.
    """

    def __init__(
        self,
        port: str,
        *,
        baudrate: int = DEFAULT_ESP32_BAUDRATE,
        reference: tuple[int, int] = (ESP32_REF_WIDTH, ESP32_REF_HEIGHT),
        screen: tuple[int, int] | None = None,
        writer=None,
        reader: Callable[[str], str] | None = None,
        ack_timeout_s: float = 3.0,
    ):
        self.port = port
        self.baudrate = int(baudrate)
        self.reference = (int(reference[0]), int(reference[1]))
        self.screen = (int(screen[0]), int(screen[1])) if screen else None
        #: A seam for tests (and for a different transport): called with the exact line.
        self._writer = writer
        #: A preflight seam: called with a command name, returns the console response.
        self._reader = reader
        #: The firmware prints ``OK`` after each command; the next line is not sent first.
        self.ack_timeout_s = float(ack_timeout_s)
        self._fd: int | None = None

    def _frame(self, x: float, y: float) -> tuple[int, int]:
        """Scale a device-frame point into the firmware reference frame, then clamp."""
        rx, ry = self.reference
        if self.screen:
            sx, sy = self.screen
            x = float(x) * rx / sx
            y = float(y) * ry / sy
        return max(0, min(rx, round(x))), max(0, min(ry, round(y)))

    def _ensure_open(self) -> int:
        """Open the serial port lazily and translate open failures for the reflex loop."""
        if self._fd is None:
            try:
                self._fd = _open_serial(self.port, self.baudrate)
            except (OSError, ValueError) as exc:
                raise RuntimeError(
                    f"ESP32 serial port {self.port} could not be opened: {exc}"
                ) from exc
        return self._fd

    def _send_with_response(self, command: str, *, timeout: float | None = None) -> str:
        """Send one command and wait for the firmware's ``OK``.

        The firmware prints ``OK`` after every command (including a slow ``home``), so the
        host never has a second line in flight while the UART is busy. This is the fix for
        the 2026-10-19 overrun that turned ``move`` into ``ove``.

        ``ERR line too long`` is special: it means the firmware read a line *fragment*,
        discarded it to resynchronise, and did not execute anything. It was observed
        live on a ``move`` right after a burst of frame grabs. Retrying once (after a
        resync character) is therefore safe and keeps a transport hiccup from becoming
        a task-level incident.
        """
        try:
            return self._send_once(command, timeout=timeout)
        except RuntimeError as exc:
            if _LINE_TOO_LONG not in str(exc):
                raise
            self._resync_line()
            return self._send_once(command, timeout=timeout)

    def _resync_line(self) -> None:
        """Terminate any orphan fragment in the firmware's line parser."""
        if self._writer is not None or self._reader is not None:
            return
        fd = self._ensure_open()
        try:
            write_line(fd, b"\n")
        except OSError:
            return
        deadline = time.monotonic() + 0.2
        while time.monotonic() < deadline:
            if not select.select([fd], [], [], 0.02)[0]:
                continue
            try:
                os.read(fd, 4096)
            except (BlockingIOError, OSError):
                return

    def _send_once(self, command: str, *, timeout: float | None = None) -> str:
        line = command + "\n"
        if len(line.encode("ascii")) > MAX_ESP32_LINE:
            raise ValueError(
                f"ESP32 command is too long ({len(line) - 1} bytes); "
                f"the firmware line limit is {MAX_ESP32_LINE - 1}"
            )
        if self._writer is not None:
            self._writer(line)
            return ""
        if self._reader is not None:
            return self._reader(command)

        fd = self._ensure_open()
        while select.select([fd], [], [], 0)[0]:
            try:
                os.read(fd, 4096)
            except BlockingIOError:
                break

        try:
            write_line(fd, line.encode("ascii"))
        except OSError as exc:
            raise RuntimeError(f"ESP32 serial write to {self.port} failed: {exc}") from exc

        wait_for = self.ack_timeout_s if timeout is None else float(timeout)
        deadline = time.monotonic() + wait_for
        data = b""
        while time.monotonic() < deadline:
            wait = min(0.05, max(0.0, deadline - time.monotonic()))
            readable, _, _ = select.select([fd], [], [], wait)
            if not readable:
                continue
            try:
                chunk = os.read(fd, 4096)
            except BlockingIOError:
                continue
            if not chunk:
                break
            data += chunk
            text = data.decode("utf-8", "replace")
            lines = [part.strip() for part in text.splitlines()]
            if any(part == "OK" for part in lines):
                errors = [
                    part for part in lines if part.startswith("ERR ") or part.startswith("usage:")
                ]
                if errors:
                    raise RuntimeError(f"ESP32 command {command!r} failed: {errors[0]}")
                return text

        raise RuntimeError(
            f"ESP32 command {command!r} got no OK ack within {wait_for:.1f}s; "
            f"response={data[-300:]!r}"
        )

    def _send(self, command: str) -> None:
        self._send_with_response(command)

    def preflight(self) -> dict[str, int | str | None]:
        """Fail fast unless the board's HID interfaces can accept input.

        Queries the firmware's ``usb`` command and requires ``mounted=1``,
        ``suspended=0``, ``mouse=1`` and ``keyboard=1``. ``touch`` is informational:
        the iOS-only build has no digitizer and legitimately reports ``touch=0``.
        Returns the parsed state; raises ``RuntimeError`` with a remediation hint
        otherwise. The opened port is reused by the first gesture.
        """
        try:
            text = self._send_with_response("usb")
        except OSError as exc:
            raise RuntimeError(f"ESP32 serial preflight failed on {self.port}: {exc}") from exc

        try:
            state = parse_usb_state(text)
        except ValueError as exc:
            raise RuntimeError(
                f"ESP32 HID preflight could not read the board's usb state: {exc}"
            ) from exc

        problems: list[str] = []
        if state["mounted"] != 1:
            problems.append(
                "mounted=0 (check the OTG cable, hub data port/cable, camera adapter, "
                "and the Allow prompt; try a direct connection to isolate the hub)"
            )
        if state["suspended"] != 0:
            problems.append(
                "suspended=1 (unlock the phone, set Auto-Lock to Never, or send 'wake')"
            )
        if state["mouse"] != 1:
            problems.append("mouse=0 (the HID mouse interface is not ready)")
        if state["keyboard"] != 1:
            problems.append("keyboard=0 (the HID keyboard interface is not ready)")
        if problems:
            raise RuntimeError("ESP32 HID not ready: " + "; ".join(problems))
        return state

    def dispatch(self, gesture: Gesture) -> float:
        started = time.monotonic()
        if gesture.kind == "tap":
            ((x, y),) = gesture.points
            px, py = self._frame(x, y)
            if int(gesture.duration_ms) >= ESP32_LONG_PRESS_MS:
                self._send(f"long_press {px} {py} {int(gesture.duration_ms)}")
            else:
                self._send(f"tap {px} {py}")
        elif gesture.kind == "swipe":
            (x1, y1), (x2, y2) = gesture.points
            ax, ay = self._frame(x1, y1)
            bx, by = self._frame(x2, y2)
            self._send(f"swipe {ax} {ay} {bx} {by} {int(gesture.duration_ms)}")
        else:
            raise ValueError(f"unsupported gesture kind: {gesture.kind!r}")
        return (time.monotonic() - started) * 1000.0

    def type_text(self, text: str) -> float:
        """Type ``text`` on the phone; a newline becomes the Enter key.

        The firmware's ``type`` maps ASCII letters, digits and spaces, one report per
        character. Anything outside ASCII is refused rather than silently dropped, because a
        half-typed string is a wrong instruction, not a lost one.
        """
        started = time.monotonic()
        for index, line in enumerate(str(text).split("\n")):
            if index:
                self._send(f"key {KEY_ENTER}")
            if line:
                if not line.isascii():
                    raise ValueError(f"the board types ASCII only: {line!r}")
                # "type " + text + newline must fit the firmware's 126-byte line.
                chunk_size = MAX_ESP32_LINE - len("type \n")
                for start in range(0, len(line), chunk_size):
                    self._send(f"type {line[start : start + chunk_size]}")
        return (time.monotonic() - started) * 1000.0

    def press_key(self, usage: int) -> float:
        """Press and release one HID key by its decimal usage code (e.g. 40 = Enter)."""
        return self._timed(f"key {int(usage)}")

    #: Key names the agent layer uses, mapped to HID usages. ``home`` is special: iOS has
    #: no Home *key*, the shortcut is Cmd+H (modifier 8, usage 11).
    KEY_NAMES: dict[str, int] = {
        "enter": KEY_ENTER,
        "return": KEY_ENTER,
        "space": KEY_SPACE,
        "tab": KEY_TAB,
        "escape": KEY_ESCAPE,
        "esc": KEY_ESCAPE,
        "left": KEY_LEFT,
        "right": KEY_RIGHT,
        "up": KEY_UP,
        "down": KEY_DOWN,
        "delete": 42,
        "backspace": 42,
        # Android-style names the Operator may emit for the same keys.
        "dpad_up": KEY_UP,
        "dpad_down": KEY_DOWN,
        "dpad_left": KEY_LEFT,
        "dpad_right": KEY_RIGHT,
    }

    #: Named shortcuts that need Cmd (left GUI, HID usage 8). Verified on the rig
    #: 2026-10-21: Cmd+Space opens Spotlight (type an app name + Down + Enter to launch),
    #: Cmd+H goes Home, Cmd+Tab is the app switcher, Cmd+W closes a sheet.
    KEY_SHORTCUTS: dict[str, int] = {
        "spotlight": 44,  # Cmd+Space
        "search": 44,
        "app_switch": 43,  # Cmd+Tab
        "close": 26,  # Cmd+W
        "close_window": 26,
    }

    def press_named_key(self, name: str) -> float:
        """Press a key or a named shortcut on the phone.

        The firmware only understands numeric HID usages, so an unmapped name used to
        reach it verbatim and come back as ``usage: key <usage 0-255>`` — observed live
        when the Operator pressed BACK on iOS.

        Keyboard shortcuts are the *first* choice on this platform: launching an app
        through Spotlight, going Home and dismissing a sheet all avoid the relative
        pointer, its calibration and its precheck entirely.
        """
        key = str(name or "").strip().lower()
        if key == "home":
            self.ios_home()
            return 0.0
        shortcut = self.KEY_SHORTCUTS.get(key)
        if shortcut is not None:
            return self._timed(f"keymod 8 {shortcut}")
        if key == "back":
            raise ValueError(
                "iOS has no Back key: use 'escape' to dismiss, the on-screen back arrow to"
                " go back, or 'home' (Cmd+H) for the Home screen"
            )
        usage = self.KEY_NAMES.get(key)
        if usage is None and key.isdigit():
            usage = int(key)
        if usage is None:
            raise ValueError(f"unsupported key {name!r} for the HID keyboard")
        return self.press_key(usage)

    def key_down(self, usage: int) -> float:
        return self._timed(f"kdown {int(usage)}")

    def key_up(self, usage: int) -> float:
        return self._timed(f"kup {int(usage)}")

    def mouse_move(self, dx: int, dy: int) -> float:
        """Move the pointer by a relative ``(dx, dy)`` — iOS assistive-touch style.

        Each axis is sent in segments no larger than ``ESP32_MAX_MOVE``: the firmware
        truncates to ``int8_t``, so a 200-unit axis would wrap and move the pointer
        backwards. Segments are separate commands, so the firmware's ``OK`` paces them
        (USB HID polls at ~10 ms; flooding reports is how positions get lost).
        """
        started = time.monotonic()
        remaining_x, remaining_y = int(dx), int(dy)
        while remaining_x or remaining_y:
            step_x = max(-ESP32_MAX_MOVE, min(ESP32_MAX_MOVE, remaining_x))
            step_y = max(-ESP32_MAX_MOVE, min(ESP32_MAX_MOVE, remaining_y))
            self._send(f"move {step_x} {step_y}")
            remaining_x -= step_x
            remaining_y -= step_y
        return (time.monotonic() - started) * 1000.0

    def mouse_home(self) -> float:
        """Walk the pointer back to the top-left corner, paced by the ack channel.

        The firmware's ``home`` sends ~50 reports 2 ms apart while the USB HID host polls
        every ~10 ms, so most of that burst is dropped and the landing spot is
        unpredictable (project knowledge note, 2026-10-19). Relative moves in int8-sized
        segments each wait for ``OK``, which is what paces the reports; the pointer
        clamps at the corner regardless of the pointer gain.
        """
        started = time.monotonic()
        self.mouse_move(-ESP32_HOME_SPAN, -ESP32_HOME_SPAN)
        return (time.monotonic() - started) * 1000.0

    def firmware_home(self) -> float:
        """The firmware's one-shot ``home`` burst — kept for diagnostics only."""
        return self._timed("home")

    def ios_home(self) -> float:
        """Go to the iOS Home screen (Cmd+H); distinct from ``mouse_home``."""
        return self._timed("keymod 8 11")

    def mouse_click(self) -> float:
        return self._timed("click")

    def mouse_down(self) -> float:
        return self._timed("mdown")

    def mouse_up(self) -> float:
        return self._timed("mup")

    def _timed(self, command: str) -> float:
        started = time.monotonic()
        self._send(command)
        return (time.monotonic() - started) * 1000.0

    def close(self) -> None:
        """Release the serial port, if it was opened."""
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None


__all__ = [
    "ENV_ESP32_PORT",
    "AdbInputControl",
    "Esp32HidControl",
    "HelperControl",
    "ESP32_HOME_SPAN",
    "ESP32_MAX_MOVE",
    "esp32_port",
    "parse_usb_state",
    "write_line",
]
