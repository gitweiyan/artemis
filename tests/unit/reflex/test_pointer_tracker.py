"""P1: the Pointer Tracker must find the AssistiveTouch pointer, not the menu button.

The menu button and the pointer are both gray circles in the top-left area. A
template match without an exclusion box confidently returns the menu button
(observed score 0.95), which is worse than no reading at all. These tests pin the
exclusion and the confidence contract.
"""

from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageDraw

from artemis.reflex.vision import PointerTracker


def _png(image: Image.Image) -> bytes:
    out = BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def _frame(pointer: tuple[int, int] | None) -> bytes:
    image = Image.new("RGB", (400, 600), (200, 200, 200))
    draw = ImageDraw.Draw(image)
    # AssistiveTouch menu button: filled circle, top-left.
    draw.ellipse([8, 8, 40, 40], fill=(80, 80, 80), outline=(245, 245, 245), width=2)
    if pointer is not None:
        x, y = pointer
        # Pointer: light ring, not a filled button.
        draw.ellipse([x - 8, y - 8, x + 8, y + 8], outline=(250, 250, 250), width=3)
        draw.ellipse([x - 3, y - 3, x + 3, y + 3], fill=(120, 120, 120))
    return _png(image)


def _template() -> bytes:
    # Crop the pointer from a frame where it is at (200, 300).
    frame = Image.open(BytesIO(_frame((200, 300))))
    return _png(frame.crop((190, 290, 212, 312)))


def test_template_detection_returns_the_pointer_outside_the_menu_box():
    tracker = PointerTracker(template=_template(), menu_box=(0, 0, 60, 60), threshold=0.8)

    found = tracker.detect(_frame((200, 300)))

    assert found is not None
    assert abs(found.x - 0.50) < 0.03
    assert abs(found.y - 0.50) < 0.03
    assert found.confidence >= 0.8
    assert found.method == "template"


def test_the_menu_button_alone_is_not_reported_as_the_pointer():
    tracker = PointerTracker(template=_template(), menu_box=(0, 0, 60, 60), threshold=0.8)

    assert tracker.detect(_frame(None)) is None


def test_two_frame_locate_ignores_the_menu_box_and_returns_confidence():
    tracker = PointerTracker(menu_box=(0, 0, 60, 60), threshold=0.8)
    before = _frame((160, 300))
    after = _frame((180, 300))  # moved right by 0.05 of the width

    found = tracker.locate(before, after, near=(0.40, 0.50), shift=0.05)

    assert found is not None
    assert abs(found.x - 0.40) < 0.03
    assert abs(found.y - 0.50) < 0.03
    assert found.confidence > 0.5
    assert found.method == "diff"
