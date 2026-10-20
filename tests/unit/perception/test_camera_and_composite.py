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

"""A camera is the last eye; a composite source falls back to it and says which one ran."""

from __future__ import annotations

import asyncio
from io import BytesIO
from types import SimpleNamespace

from PIL import Image
import pytest

from artemis.devices.capabilities import CapabilitySet
from artemis.devices.session import DeviceSessionError
from artemis.perception.base import Frame
from artemis.perception.camera import CameraSource
from artemis.perception.composite import CompositePerception


def _png(color, size=(16, 16)) -> bytes:
    out = BytesIO()
    Image.new("RGB", size, color).save(out, "PNG")
    return out.getvalue()


def test_a_camera_frame_is_classified_like_any_other():
    source = CameraSource(lambda: (_png((200, 200, 200)), 16, 16))

    frame = asyncio.run(source.snapshot())

    assert frame.health == "ok"
    assert frame.source == "camera"
    assert (frame.width, frame.height) == (16, 16)


def test_a_dark_camera_frame_is_black():
    source = CameraSource(lambda: (_png((0, 0, 0)), 16, 16))

    assert asyncio.run(source.snapshot()).health == "black"


def test_a_missing_camera_is_a_black_frame_not_an_exception():
    def broken():
        raise OSError("no camera attached")

    frame = asyncio.run(CameraSource(broken).snapshot())

    assert frame.health == "black"
    assert frame.image == b""


def test_a_camera_source_needs_a_grabber():
    with pytest.raises(ValueError):
        CameraSource()


class _Source:
    capabilities = CapabilitySet(supports_ui_tree=False)

    def __init__(self, frame):
        self.frame = frame
        self.calls = 0

    async def snapshot(self):
        self.calls += 1
        return self.frame

    async def ui_tree(self):
        return None

    async def ocr(self, frame):
        return None

    async def locate(self, frame, target):
        return None


def test_the_composite_skips_a_dark_source_and_reports_the_one_that_saw():
    dark = _Source(Frame(b"", 0.0, 16, 16, "uvc", "black"))
    camera = _Source(Frame(_png((120, 120, 120)), 0.0, 16, 16, "camera", "ok"))
    composite = CompositePerception([dark, camera])

    frame = asyncio.run(composite.snapshot())

    assert frame.source == "camera", "telemetry must say which eye was used"
    assert dark.calls == 1


def test_the_composite_raises_when_no_source_is_healthy():
    dark = _Source(Frame(b"", 0.0, 16, 16, "uvc", "black"))
    composite = CompositePerception([dark, dark])

    with pytest.raises(DeviceSessionError) as excinfo:
        asyncio.run(composite.snapshot())

    assert "black" in str(excinfo.value)


def test_the_composite_aggregates_capabilities():
    class WithTree(_Source):
        capabilities = CapabilitySet(supports_ui_tree=True, supports_ocr=True)

    composite = CompositePerception(
        [_Source(Frame(b"x", 0.0, 1, 1, "uvc")), WithTree(Frame(b"x", 0.0, 1, 1, "app"))]
    )

    assert composite.capabilities.supports_ui_tree is True
    assert composite.capabilities.supports_ocr is True


def test_a_composite_needs_at_least_one_source():
    with pytest.raises(ValueError):
        CompositePerception([])
