# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The rig smoke is the operator's answer to "is it the phone or the agent?"."""

import asyncio
import time
from io import BytesIO
from types import SimpleNamespace

from PIL import Image
import pytest

from artemis.devices.ios_uvc import build_ios_uvc_session
from artemis.drivers.ios.ios_uvc_hid_driver import IOSUvcHidDriver
from artemis.drivers.ios.smoke import run_smoke
from artemis.perception.base import TextBox


def _frame(color=(120, 120, 120)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (449, 974), color).save(buffer, "PNG")
    return buffer.getvalue()


class Capture:
    """A card whose picture changes once the board moves the pointer."""

    def __init__(self, control=None):
        self.control = control
        self.grabs = 0

    def grab(self):
        self.grabs += 1
        moved = bool(getattr(self.control, "moved", False))
        return SimpleNamespace(
            image=_frame((150, 150, 150) if moved else (120, 120, 120)),
            width=449,
            height=974,
            captured_at=time.monotonic(),
        )


class Esp32:
    def __init__(self):
        self.clicks = 0
        self.moved = False

    def preflight(self):
        return {}

    def mouse_home(self):
        pass

    def mouse_move(self, dx, dy):
        self.moved = True

    def mouse_click(self):
        self.clicks += 1


class Ocr:
    def recognize(self, image, *, width, height):
        return [TextBox("闪购", (0.3, 0.1, 0.4, 0.2), 0.99)]


class CenteredDetector:
    def detect(self, image, near=None):
        from artemis.reflex.vision import Pointer

        return Pointer(0.35, 0.15, 0.95, "fake")


class BlindDetector:
    def detect(self, image, near=None):
        return None


class ClickCapture(Capture):
    def __init__(self, control):
        super().__init__(control)

    def grab(self):
        self.grabs += 1
        if self.control.clicks:
            color = (200, 40, 40)
        elif self.control.moved:
            color = (150, 150, 150)
        else:
            color = (120, 120, 120)
        return SimpleNamespace(
            image=_frame(color), width=449, height=974, captured_at=time.monotonic()
        )


def _driver(control, detector):
    session = build_ios_uvc_session(
        ClickCapture(control),
        control,
        frame_size=(449, 974),
        screen_size=(1080, 2400),
        detector=detector,
        ocr_engine=Ocr(),
    )
    return IOSUvcHidDriver(session)


def test_a_healthy_rig_passes_every_stage():
    control = Esp32()
    report = asyncio.run(run_smoke(_driver(control, CenteredDetector()), target="闪购"))

    assert [step.name for step in report.steps] == [
        "connect",
        "snapshot",
        "pointer_motion",
        "locate",
        "pointer_precheck",
        "tap",
    ]
    assert all(step.ok for step in report.steps), report.summary()
    assert report.ok is True
    assert control.clicks == 1
    assert "闪购" in report.summary()


def test_a_missing_label_fails_the_locate_stage_and_stops():
    control = Esp32()
    report = asyncio.run(run_smoke(_driver(control, CenteredDetector()), target="不存在的按钮"))

    assert report.ok is False
    assert [step.name for step in report.steps] == [
        "connect",
        "snapshot",
        "pointer_motion",
        "locate",
    ]
    assert report.steps[-1].ok is False
    assert "OCR boxes" in report.steps[-1].detail, "the failure must say what OCR did see"
    assert control.clicks == 0


def test_a_blind_pointer_is_reported_and_nothing_is_clicked():
    control = Esp32()
    report = asyncio.run(run_smoke(_driver(control, BlindDetector()), target="闪购"))

    assert report.ok is False
    steps = {step.name: step for step in report.steps}
    assert steps["pointer_precheck"].ok is False
    assert "pointer_missing" in steps["pointer_precheck"].detail
    assert steps["tap"].ok is False
    assert "pointer_missing" in steps["tap"].detail
    assert control.clicks == 0


def test_a_black_frame_stops_before_any_ocr():
    class BlackCapture(Capture):
        def grab(self):
            return SimpleNamespace(
                image=_frame((0, 0, 0)), width=449, height=974, captured_at=time.monotonic()
            )

    session = build_ios_uvc_session(
        BlackCapture(),
        Esp32(),
        frame_size=(449, 974),
        screen_size=(1080, 2400),
        ocr_engine=Ocr(),
    )
    report = asyncio.run(run_smoke(IOSUvcHidDriver(session), target="闪购"))

    assert report.ok is False
    assert [step.name for step in report.steps] == ["connect", "snapshot"]
    assert "black" in report.steps[-1].detail


def test_the_report_is_json_serializable():
    import json

    report = asyncio.run(run_smoke(_driver(Esp32(), CenteredDetector()), target="闪购"))

    payload = json.loads(json.dumps(report.to_dict()))
    assert payload["ok"] is True
    assert payload["steps"][0]["name"] == "connect"


@pytest.mark.parametrize("tap", [False])
def test_tap_stage_can_be_skipped(tap):
    report = asyncio.run(run_smoke(_driver(Esp32(), CenteredDetector()), target="闪购", tap=tap))

    assert "tap" not in [step.name for step in report.steps]


def test_the_cli_exits_two_when_the_rig_is_not_configured(monkeypatch, capsys):
    from typer.testing import CliRunner

    from artemis.interfaces.cli.main import app

    monkeypatch.delenv("ARTEMIS_ESP32_PORT", raising=False)
    result = CliRunner().invoke(app, ["ios-smoke"])

    assert result.exit_code == 2
    assert "ARTEMIS_ESP32_PORT" in result.output


class WakeOnMoveDetector:
    """A pointer that is only visible after the board moved it (the real fade/wake)."""

    def __init__(self, control):
        self.control = control

    def detect(self, image, near=None):
        from artemis.reflex.vision import Pointer

        if not self.control.moved:
            return None
        return Pointer(0.35, 0.15, 0.9, "fake")


def test_a_faded_pointer_is_woken_by_a_nudge_before_the_precheck():
    class MovingEsp32(Esp32):
        def __init__(self):
            super().__init__()
            self.moved = False

        def mouse_move(self, dx, dy):
            self.moved = True

    control = MovingEsp32()
    report = asyncio.run(
        run_smoke(_driver(control, WakeOnMoveDetector(control)), target="闪购", tap=False)
    )

    steps = {step.name: step for step in report.steps}
    assert steps["pointer_precheck"].ok is True, report.summary()
    assert control.clicks == 0


def test_a_visible_pointer_away_from_the_target_is_not_a_rig_failure():
    class FarDetector:
        def detect(self, image, near=None):
            from artemis.reflex.vision import Pointer

            return Pointer(0.05, 0.05, 0.9, "fake")

    report = asyncio.run(run_smoke(_driver(Esp32(), FarDetector()), target="闪购", tap=False))

    steps = {step.name: step for step in report.steps}
    assert steps["pointer_precheck"].ok is True
    assert "the tap will drive it" in steps["pointer_precheck"].detail
    assert report.ok is True


def test_locate_retries_on_a_frame_the_ocr_could_not_read():
    class FlakyOcr:
        def __init__(self):
            self.calls = 0

        def recognize(self, image, *, width, height):
            self.calls += 1
            if self.calls == 1:
                return []  # a blurred frame right after a pointer move
            return [TextBox("闪购", (0.3, 0.1, 0.4, 0.2), 0.99)]

    control = Esp32()
    session = build_ios_uvc_session(
        ClickCapture(control),
        control,
        frame_size=(449, 974),
        screen_size=(1080, 2400),
        detector=CenteredDetector(),
        ocr_engine=FlakyOcr(),
    )

    report = asyncio.run(run_smoke(IOSUvcHidDriver(session), target="闪购", tap=False))

    steps = {step.name: step for step in report.steps}
    assert steps["locate"].ok is True, report.summary()
    assert "frame 2" in steps["locate"].detail


def test_a_capture_that_never_produces_a_frame_is_named_in_the_report():
    from artemis.reflex.contract import CaptureError

    class DeadCapture:
        def grab(self):
            raise CaptureError("uvc capture failed: Command ... timed out after 12.0 seconds")

    session = build_ios_uvc_session(
        DeadCapture(), Esp32(), frame_size=(449, 974), screen_size=(1080, 2400)
    )

    report = asyncio.run(run_smoke(IOSUvcHidDriver(session), target="闪购"))

    assert report.ok is False
    assert [step.name for step in report.steps] == ["connect", "snapshot"]
    assert "capture_timeout" in report.steps[-1].detail


def test_a_black_frame_is_named_in_the_report():
    class BlackCapture(Capture):
        def grab(self):
            return SimpleNamespace(
                image=_frame((0, 0, 0)), width=449, height=974, captured_at=time.monotonic()
            )

    session = build_ios_uvc_session(
        BlackCapture(), Esp32(), frame_size=(449, 974), screen_size=(1080, 2400)
    )

    report = asyncio.run(run_smoke(IOSUvcHidDriver(session), target="闪购"))

    assert report.ok is False
    assert "uvc_no_signal" in report.steps[-1].detail


def test_a_no_effect_tap_names_the_hid_button_hypothesis():
    """Measured live 2026-10-21 after a reboot: the pointer moved, the button did nothing."""

    class DeadButtonEsp32(Esp32):
        """The click is accepted by the board and ignored by the phone."""

        def mouse_click(self):
            self.clicks += 1

    control = DeadButtonEsp32()
    # The screen never changes: a click that lands and does nothing looks exactly like this.
    session = build_ios_uvc_session(
        Capture(control),
        control,
        frame_size=(449, 974),
        screen_size=(1080, 2400),
        detector=CenteredDetector(),
        ocr_engine=Ocr(),
    )
    report = asyncio.run(run_smoke(IOSUvcHidDriver(session), target="闪购"))

    steps = {step.name: step for step in report.steps}
    assert steps["tap"].ok is False
    assert steps["tap"].detail.startswith("screen=")
    assert "incident=no_effect" in steps["tap"].detail
    assert "replug" in steps["tap"].detail
