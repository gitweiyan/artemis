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

"""Vision's boxes are bottom-left; the perception layer's are top-left 0-1."""

import pytest

from artemis.perception.text_locator import locate_text
from artemis.perception.vision_ocr import VisionOcrEngine


def test_the_engine_is_only_available_on_a_mac_with_vision():
    from artemis.perception.vision_ocr import vision_available

    assert isinstance(vision_available(), bool)


def test_vision_observations_become_top_left_normalized_boxes():
    # A label in the lower quarter of the screen: Vision y grows upward from the bottom.
    observations = [("设置", 0.99, (0.80, 0.10, 0.90, 0.14))]
    engine = VisionOcrEngine(observer=lambda image: observations)

    boxes = engine.recognize(b"\x89PNG", width=449, height=974)

    assert len(boxes) == 1
    box = boxes[0]
    assert box.text == "设置"
    assert box.confidence == pytest.approx(0.99)
    assert box.box == pytest.approx((0.80, 0.86, 0.90, 0.90)), "top-left origin"


def test_low_confidence_and_empty_text_are_dropped():
    observations = [
        ("设置", 0.99, (0.8, 0.1, 0.9, 0.14)),
        ("噪", 0.05, (0.1, 0.1, 0.2, 0.14)),
        ("", 0.99, (0.3, 0.1, 0.4, 0.14)),
        ("照片", float("nan"), (0.5, 0.1, 0.6, 0.14)),
    ]
    engine = VisionOcrEngine(observer=lambda image: observations, min_confidence=0.3)

    boxes = engine.recognize(b"x", width=100, height=100)

    assert [box.text for box in boxes] == ["设置"]


def test_a_vision_frame_locates_a_text_target():
    observations = [
        ("闪购", 0.95, (0.30, 0.85, 0.40, 0.89)),
        ("设置", 0.99, (0.80, 0.10, 0.90, 0.14)),
    ]
    engine = VisionOcrEngine(observer=lambda image: observations)

    boxes = engine.recognize(b"x", width=449, height=974)
    target = locate_text(boxes, "闪购")

    assert target is not None
    assert target.source == "ocr"
    assert target.box[1] == pytest.approx(0.11, abs=0.01), "top of the frame after the flip"


@pytest.mark.asyncio
async def test_the_uvc_source_can_use_vision_end_to_end():
    from io import BytesIO
    from types import SimpleNamespace

    from PIL import Image

    from artemis.perception.base import Target
    from artemis.perception.uvc import UvcSource

    buffer = BytesIO()
    Image.new("RGB", (10, 10), (200, 200, 200)).save(buffer, "PNG")

    class Capture:
        def grab(self):
            return SimpleNamespace(image=buffer.getvalue(), width=10, height=10, captured_at=1.0)

    engine = VisionOcrEngine(observer=lambda image: [("设置", 0.99, (0.8, 0.1, 0.9, 0.14))])
    source = UvcSource(Capture(), ocr_engine=engine)

    frame = await source.snapshot()
    target = await source.locate(frame, Target("设置", (0, 0, 0, 0), 0, "ocr"))

    assert target is not None
    assert target.confidence == pytest.approx(0.99)
