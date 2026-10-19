"""P2: click verification must see a real screen change, not just pointer movement."""

from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageDraw

from artemis.reflex.vision import changed


def _png(image: Image.Image) -> bytes:
    out = BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def test_identical_frames_are_not_a_change():
    image = Image.new("RGB", (100, 100), (120, 120, 120))

    assert changed(_png(image), _png(image)) is False


def test_a_content_change_is_a_change():
    before = Image.new("RGB", (100, 100), (120, 120, 120))
    after = before.copy()
    ImageDraw.Draw(after).rectangle([20, 40, 80, 70], fill=(240, 40, 40))

    assert changed(_png(before), _png(after)) is True


def test_a_status_bar_only_change_is_ignored():
    before = Image.new("RGB", (100, 100), (120, 120, 120))
    after = before.copy()
    ImageDraw.Draw(after).rectangle([0, 0, 100, 3], fill=(250, 250, 250))

    assert changed(_png(before), _png(after)) is False
