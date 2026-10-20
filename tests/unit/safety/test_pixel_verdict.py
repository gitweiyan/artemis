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

"""Post-action verification: the target crop first, the whole frame as the fallback."""

from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageDraw
import pytest

from artemis.safety.pixel import changed_fraction, crop_around, verify_effect


def _image(draw=None, size=(400, 800), color=(230, 230, 230)) -> bytes:
    image = Image.new("RGB", size, color)
    if draw is not None:
        draw(ImageDraw.Draw(image))
    out = BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def test_an_identical_screen_is_no_effect():
    frame = _image()

    verdict = verify_effect(frame, frame, (200, 400))

    assert verdict.ok is False
    assert verdict.incident == "no_effect"
    assert verdict.crop_change == pytest.approx(0.0)
    assert verdict.frame_change == pytest.approx(0.0)


def test_a_change_at_the_target_is_detected():
    before = _image()
    after = _image(lambda d: d.rectangle((180, 380, 240, 440), fill=(20, 120, 220)))

    verdict = verify_effect(before, after, (200, 400))

    assert verdict.ok is True
    assert verdict.incident is None
    assert verdict.crop_change > 0.05
    assert verdict.crop_change >= verdict.frame_change


def test_a_change_elsewhere_still_counts_as_a_reaction():
    """A sheet can open away from the target; the click was not a no-op."""
    before = _image()
    after = _image(lambda d: d.rectangle((0, 700, 400, 800), fill=(255, 255, 255)))

    verdict = verify_effect(before, after, (200, 200))

    assert verdict.ok is True
    assert verdict.frame_change > 0.0


def test_a_minute_rollover_in_the_status_bar_is_not_a_click_effect():
    before = _image()
    after = _image(lambda d: d.rectangle((10, 4, 60, 20), fill=(0, 0, 0)))

    verdict = verify_effect(before, after, (200, 400))

    assert verdict.ok is False, "the clock area is ignored"


def test_the_crop_is_clamped_at_the_frame_edge():
    frame = _image()

    crop = crop_around(frame, (2, 2), size_px=240)

    assert Image.open(BytesIO(crop)).size[0] > 0


def test_changed_fraction_reports_the_share_of_pixels():
    before = _image()
    after = _image(lambda d: d.rectangle((0, 0, 400, 400), fill=(0, 0, 0)))

    fraction = changed_fraction(before, after)

    assert 0.4 < fraction < 0.6


def test_a_size_mismatch_is_never_reported_as_unchanged():
    assert changed_fraction(_image(size=(10, 10)), _image(size=(20, 20))) == 1.0
