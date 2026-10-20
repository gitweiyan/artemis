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

"""A UI tree is the strongest perception a phone offers; expose it like any other source."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from artemis.perception.app_automation import AppAutomationSource


def _driver(elements=None, *, image=b"jpeg-bytes", width=1080, height=2400):
    data = SimpleNamespace(
        screenshot_bytes=image,
        ui_elements=elements or [],
        width=width,
        height=height,
        timestamp=12.5,
    )

    async def get_screen_data():
        return data

    return SimpleNamespace(get_screen_data=get_screen_data)


def test_a_snapshot_carries_the_screenshot_and_the_tree_size():
    source = AppAutomationSource(_driver())

    frame = asyncio.run(source.snapshot())

    assert frame.image == b"jpeg-bytes"
    assert (frame.width, frame.height) == (1080, 2400)
    assert frame.health == "ok"
    assert frame.source == "app"
    assert source.capabilities.supports_ui_tree is True


def test_an_empty_screenshot_is_reported_as_black_not_ok():
    source = AppAutomationSource(_driver(image=b""))

    assert asyncio.run(source.snapshot()).health == "black"


def test_the_tree_becomes_platform_neutral_elements():
    source = AppAutomationSource(
        _driver(
            [
                {"text": "设置", "bounds": [100, 200, 300, 400], "resource_id": "btn"},
                {"text": "", "bounds": None},
            ]
        )
    )

    tree = asyncio.run(source.ui_tree())

    assert tree[0].text == "设置"
    assert tree[0].bounds == (100, 200, 300, 400)
    assert tree[0].resource_id == "btn"
    assert tree[1].bounds is None


def test_ocr_and_pixel_locating_are_declared_unavailable():
    """The tree already carries text; a second OCR pass would only add latency."""
    source = AppAutomationSource(_driver())

    frame = asyncio.run(source.snapshot())

    assert asyncio.run(source.ocr(frame)) is None
    assert asyncio.run(source.locate(frame, SimpleNamespace(label="设置"))) is None
