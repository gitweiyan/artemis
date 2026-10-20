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

"""A bad picture has a name, and the name decides what the operator does next.

- `uvc_no_signal`  — a frame arrived, but the card is dark (HDMI/HDCP/adapter, or the
  phone's screen is off). Nothing further can be read from it.
- `capture_timeout` — no frame arrived at all (ffmpeg never produced one, or the device
  is gone). Retrying the same capture is the whole fix space.
- `uvc_decode_error` — bytes arrived that are not an image (a torn MJPEG chunk).

Collapsing these into "capture failed" loses the one piece of information that makes a
task recoverable.
"""

from __future__ import annotations

import pytest

from artemis.perception.incidents import (
    CAPTURE_TIMEOUT,
    UVC_DECODE_ERROR,
    UVC_NO_SIGNAL,
    UnhealthyFrame,
    incident_for_error,
    incident_for_health,
)


@pytest.mark.parametrize(
    "health,expected",
    [
        ("black", UVC_NO_SIGNAL),
        ("decode_error", UVC_DECODE_ERROR),
        ("ok", None),
        ("", None),
    ],
)
def test_frame_health_maps_to_a_named_incident(health, expected):
    assert incident_for_health(health) == expected


def test_a_capture_timeout_is_told_apart_from_no_signal():
    from artemis.reflex.contract import CaptureError

    timeout = CaptureError("uvc capture failed: Command ... timed out after 12.0 seconds")
    assert incident_for_error(timeout) == CAPTURE_TIMEOUT

    still_running = CaptureError("uvc stream produced no frame within 3.00s")
    assert incident_for_error(still_running) == CAPTURE_TIMEOUT

    assert incident_for_error(RuntimeError("uvc capture returned 1: b'timeout'")) == CAPTURE_TIMEOUT


def test_any_other_failure_is_reported_as_no_signal():
    from artemis.reflex.contract import CaptureError

    assert incident_for_error(CaptureError("uvc capture returned 1: b'Input/output error'")) == (
        UVC_NO_SIGNAL
    )
    assert incident_for_error(ValueError("something else")) == UVC_NO_SIGNAL


def test_unhealthy_frame_carries_its_code_and_a_readable_message():
    error = UnhealthyFrame(UVC_NO_SIGNAL, "frame health='black'")

    assert error.incident == UVC_NO_SIGNAL
    assert "black" in str(error)
    assert isinstance(error, RuntimeError)
